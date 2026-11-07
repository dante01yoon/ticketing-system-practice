import json
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import cache
from .db import SessionLocal
from .deps import get_now, require_idempotency_key, require_user_id
from .errors import ApiError
from .models import Booking, Concert, IdempotencyKey, Seat

HOLD_TTL = timedelta(minutes=5)


def _reject_nul(v: str) -> str:
    """PostgreSQL text는 NUL(U+0000)을 못 담는다. DB까지 가면 500이 되므로 입력 단계에서 막는다."""
    if "\x00" in v:
        raise ValueError("NUL 문자는 쓸 수 없습니다.")
    return v


class BookingRequest(BaseModel):
    seat_id: str

    _no_nul = field_validator("seat_id")(_reject_nul)


async def booking_request(
    request: Request, _user: str = Depends(require_user_id), _key: str = Depends(require_idempotency_key)
) -> BookingRequest:
    """판단 순서(401 사용자 → 400 멱등 키 → 422 본문)를 지키려고 본문은 두 헤더 검사 뒤에 직접 읽는다."""
    try:
        return BookingRequest.model_validate(await request.json())
    except Exception:
        raise ApiError(422, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다.")


class CallbackRequest(BaseModel):
    booking_id: str
    result: Literal["SUCCEEDED", "FAILED"]

    _no_nul = field_validator("booking_id")(_reject_nul)


def _hold_body(seat: Seat, held_at: datetime) -> dict:
    return {
        "seat_id": seat.id,
        "status": "HELD",
        "held_at": _iso(held_at),
        "expires_at": _iso(held_at + HOLD_TTL),
    }


def _booking_body(b: Booking) -> dict:
    return {
        "id": b.id,
        "seat_id": b.seat_id,
        "concert_id": b.concert_id,
        "user_id": b.user_id,
        "status": b.status,
        "created_at": _iso(b.created_at),
        "expires_at": _iso(b.expires_at),
    }


def _confirm_once(seat_id: str, user_id: str, key: str, clock: Callable[[], datetime]) -> JSONResponse:
    with SessionLocal() as db, db.begin():
        # 1. 멱등 키 — 잠금 전(잠금 없이 재생) 한 번, 잠금 뒤 한 번 더 본다.
        # 같은 키로 잠금을 기다리던 요청은, 앞선 요청이 커밋한 처음 응답을 만료·홀드 판단보다 먼저 재생해야 한다.
        def replay() -> JSONResponse | None:
            saved = db.execute(
                select(IdempotencyKey)
                .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if saved is None:
                return None
            if saved.seat_id != seat_id:
                raise ApiError(422, "IDEMPOTENCY_KEY_REUSED", "다른 요청에 이미 쓴 멱등 키입니다.")
            return JSONResponse(saved.response_body, status_code=saved.response_status)

        if (hit := replay()) is not None:
            return hit

        seat = _lock_seat(db, seat_id)
        if (hit := replay()) is not None:  # 시계를 읽기 전에 (잠금 뒤 첫 시계 읽기 규칙 유지)
            return hit
        now = clock()  # 잠금 뒤에 읽는다

        def done(status: int, booking: Booking) -> JSONResponse:
            payload = _booking_body(booking)
            db.add(IdempotencyKey(user_id=user_id, key=key, seat_id=seat_id, response_status=status, response_body=payload))
            db.flush()
            return JSONResponse(payload, status_code=status)

        # 2. 이미 팔린 좌석
        confirmed = db.execute(
            select(Booking).where(Booking.seat_id == seat.id, Booking.status == "CONFIRMED")
        ).scalar_one_or_none()
        if confirmed is not None:
            if confirmed.user_id == user_id:
                return done(200, confirmed)
            raise ApiError(409, "SEAT_ALREADY_SOLD", "이미 판매된 좌석입니다.")
        if seat.status == "SOLD":
            raise ApiError(409, "SEAT_ALREADY_SOLD", "이미 판매된 좌석입니다.")
        # 3. 홀드 확인
        if seat.status == "AVAILABLE":
            raise ApiError(409, "HOLD_REQUIRED", "먼저 좌석을 홀드하세요.")
        if not _is_live_hold(seat, now):
            if seat.held_by == user_id:
                raise ApiError(409, "HOLD_EXPIRED", "홀드가 만료되었습니다.")
            raise ApiError(409, "HOLD_REQUIRED", "먼저 좌석을 홀드하세요.")
        if seat.held_by != user_id:
            raise ApiError(403, "HOLD_NOT_OWNED", "내 홀드가 아닙니다.")
        # 4. 이 홀드에 묶인 내 PENDING 예매
        hold_expires = seat.held_at + HOLD_TTL
        pending = db.execute(
            select(Booking).where(
                Booking.seat_id == seat.id,
                Booking.user_id == user_id,
                Booking.status == "PENDING",
                Booking.expires_at == hold_expires,
            )
        ).scalars().first()
        if pending is not None:
            return done(200, pending)
        # 5. 새 PENDING 예매 (좌석은 팔지 않는다)
        booking = Booking(
            id=str(uuid.uuid4()),
            seat_id=seat.id,
            concert_id=seat.concert_id,
            user_id=user_id,
            status="PENDING",
            created_at=now,
            expires_at=hold_expires,
        )
        db.add(booking)
        db.flush()
        return done(201, booking)


def _hold_db(seat_id: str, user_id: str, clock: Callable[[], datetime]):
    """홀드의 DB 판단. (응답, concert_id, held_at)을 돌려준다. 반환 시점에 커밋은 끝나 있다."""
    with SessionLocal() as db, db.begin():
        seat = _lock_seat(db, seat_id)  # 행 잠금: 같은 좌석의 동시 요청은 직렬화된다
        now = clock()  # 잠금 뒤에 읽는다
        concert_id = seat.concert_id
        if seat.status == "SOLD":
            raise ApiError(409, "SEAT_ALREADY_SOLD", "이미 판매된 좌석입니다.")
        if _is_live_hold(seat, now):
            if seat.held_by == user_id:  # 자기 유효 홀드: 기존 홀드 그대로 (만료 연장 없음)
                return JSONResponse(_hold_body(seat, seat.held_at), status_code=200), concert_id, seat.held_at
            raise ApiError(409, "SEAT_ALREADY_HELD", "이미 홀드된 좌석입니다.")
        seat.status, seat.held_by, seat.held_at = "HELD", user_id, now
        return JSONResponse(_hold_body(seat, now), status_code=201), concert_id, now


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _is_live_hold(seat: Seat, now: datetime) -> bool:
    return seat.status == "HELD" and now < seat.held_at + HOLD_TTL


def _lock_seat(db, seat_id: str) -> Seat:
    seat = None if "\x00" in seat_id else db.execute(select(Seat).where(Seat.id == seat_id).with_for_update()).scalar_one_or_none()
    if seat is None:
        raise ApiError(404, "SEAT_NOT_FOUND", "좌석을 찾을 수 없습니다.")
    return seat


def _seat_sort_key(seat: Seat):
    """열 문자(A..Z, AA..: 길이 먼저, 사전순) → 번호 숫자 순."""
    m = re.fullmatch(r"([A-Za-z]*)(\d*)", seat.number)
    row, col = (m.group(1), int(m.group(2) or 0)) if m else (seat.number, 0)
    return (len(row), row, col, seat.number)


def lock_clock(request: Request) -> Callable[[], datetime]:
    """좌석 잠금을 얻은 '뒤에' 호출해 시각을 읽는다 (잠금 대기 중 만료되는 홀드를 놓치지 않기 위함).
    테스트의 app.dependency_overrides[get_now] 도 호출 시점에 반영된다."""
    return request.app.dependency_overrides.get(get_now, get_now)


class AppIdMiddleware:
    """모든 응답(처리되지 않은 예외의 500 포함)에 X-App-Id 를 붙이는 ASGI 미들웨어."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        app_id = os.environ.get("APP_ID")
        if scope["type"] != "http" or not app_id:
            return await self.app(scope, receive, send)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-app-id"]
                headers.append((b"x-app-id", app_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_id)


def create_app() -> FastAPI:
    app = FastAPI(title="Ticketing API (MVP)", version="0.2.0")

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse({"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse({"code": code, "message": str(exc.detail)}, status_code=exc.status_code)

    @app.get("/concerts")
    def list_concerts():
        with SessionLocal() as db:
            rows = db.execute(select(Concert).order_by(Concert.starts_at, Concert.id)).scalars()
            return {"items": [{"id": c.id, "title": c.title, "starts_at": _iso(c.starts_at)} for c in rows]}

    @app.get("/concerts/{concert_id}/seats")
    def seat_map(concert_id: str, now: datetime = Depends(get_now)):
        cached = None if "\x00" in concert_id else cache.seatmap_get(concert_id, now)
        if cached is not None:
            return Response(cached, media_type="application/json", headers={"X-Cache": "HIT"})
        with SessionLocal() as db:
            if "\x00" in concert_id or db.get(Concert, concert_id) is None:
                raise ApiError(404, "CONCERT_NOT_FOUND", "공연을 찾을 수 없습니다.")
            seats = db.execute(select(Seat).where(Seat.concert_id == concert_id)).scalars().all()
            seats.sort(key=_seat_sort_key)
            out = []
            earliest = None  # 유효 홀드 중 가장 이른 만료 시각(그 뒤엔 캐시가 낡는다)
            for s in seats:
                status = s.status
                if status == "HELD":
                    if _is_live_hold(s, now):
                        exp = s.held_at + HOLD_TTL
                        earliest = exp if earliest is None else min(earliest, exp)
                    else:
                        status = "AVAILABLE"
                out.append({"id": s.id, "number": s.number, "status": status})
            body = json.dumps({"concert_id": concert_id, "seats": out}, ensure_ascii=False, separators=(",", ":"))
        cache.seatmap_put(concert_id, body, earliest)
        return Response(body, media_type="application/json", headers={"X-Cache": "MISS"})

    @app.post("/seats/{seat_id}/hold", status_code=201)
    def hold_seat(
        seat_id: str, user_id: str = Depends(require_user_id), clock: Callable[[], datetime] = Depends(lock_clock)
    ):
        # Redis 관문: DB 트랜잭션 전에 원자적으로 확인+표시. 이미 잡힌 좌석은 DB에 가지 않고 거절한다.
        gate = cache.hold_gate(seat_id, user_id, clock())
        if gate.reject_code is not None:
            msg = "이미 판매된 좌석입니다." if gate.reject_code == "SEAT_ALREADY_SOLD" else "이미 홀드된 좌석입니다."
            return JSONResponse(
                {"code": gate.reject_code, "message": msg}, status_code=409, headers={"X-Hold-Gate": "REDIS"}
            )
        try:
            resp, concert_id, held_at = _hold_db(seat_id, user_id, clock)
        except ApiError as e:
            cache.hold_revert(seat_id, gate)
            return JSONResponse(
                {"code": e.code, "message": e.message}, status_code=e.status, headers={"X-Hold-Gate": "DB"}
            )
        except BaseException:
            cache.hold_revert(seat_id, gate)
            raise
        resp.headers["X-Hold-Gate"] = "DB"
        # DB 성공(200·201) 뒤 표시는 항상 DB 진실(held_at+5분)을 반영한다. 캐시 삭제는 새 홀드(201)만.
        cache.hold_confirm(seat_id, user_id, held_at)
        if resp.status_code == 201:
            cache.seatmap_invalidate(concert_id)
        return resp

    @app.post("/bookings")
    def confirm_booking(
        body: BookingRequest = Depends(booking_request),
        user_id: str = Depends(require_user_id),
        key: str = Depends(require_idempotency_key),
        clock: Callable[[], datetime] = Depends(lock_clock),
    ):
        # 같은 사용자·같은 키가 동시에(특히 다른 좌석으로) 오면 idempotency_keys PK 충돌이 난다.
        # 진 쪽은 롤백하고 다시 판단하면 이긴 쪽의 기록을 보고 재생(또는 REUSED)한다.
        for _ in range(3):
            try:
                return _confirm_once(body.seat_id, user_id, key, clock)
            except IntegrityError as e:
                if "idempotency_keys" not in str(e.orig):
                    raise
        raise ApiError(409, "SEAT_ALREADY_SOLD", "요청을 처리하지 못했습니다. 다시 시도하세요.")

    @app.get("/bookings/{booking_id}")
    def get_booking(booking_id: str, user_id: str = Depends(require_user_id)):
        with SessionLocal() as db:
            booking = None if "\x00" in booking_id else db.get(Booking, booking_id)
            if booking is None or booking.user_id != user_id:
                raise ApiError(404, "BOOKING_NOT_FOUND", "예매를 찾을 수 없습니다.")
            return _booking_body(booking)

    @app.post("/payments/callback")
    def payment_callback(body: CallbackRequest, clock: Callable[[], datetime] = Depends(lock_clock)):
        sold: tuple[str, str] | None = None
        with SessionLocal() as db, db.begin():
            probe = db.get(Booking, body.booking_id)
            if probe is None:
                raise ApiError(404, "BOOKING_NOT_FOUND", "예매를 찾을 수 없습니다.")
            seat = _lock_seat(db, probe.seat_id)  # 확정 요청·재홀드와 같은 좌석 잠금으로 직렬화
            now = clock()  # 잠금 뒤에 읽는다
            # 상태는 좌석 잠금 아래에서만 바뀌므로, 잠금 뒤 새로 읽은 값이 최신이다.
            booking = db.execute(
                select(Booking).where(Booking.id == body.booking_id).execution_options(populate_existing=True)
            ).scalar_one()
            if booking.status == "PENDING":
                if body.result == "FAILED":
                    booking.status = "FAILED"  # 홀드 유지
                elif now < booking.expires_at:
                    booking.status = "CONFIRMED"
                    seat.status = "SOLD"
                else:
                    booking.status = "REFUND_REQUIRED"  # 좌석은 건드리지 않는다
                db.flush()
            if booking.status == "CONFIRMED":  # 방금 확정했거나 중복 콜백: 판매 표시를 (다시) 쓴다
                sold = (seat.id, seat.concert_id)
            result = _booking_body(booking)
        if sold is not None:  # DB 커밋 뒤에: 판매 표시 + 좌석맵 캐시 삭제
            cache.mark_sold(sold[0])
            cache.seatmap_invalidate(sold[1])
        return result

    # ServerErrorMiddleware(가장 바깥) 밖에 감싸야 500 응답에도 헤더가 붙는다.
    build = app.build_middleware_stack
    app.build_middleware_stack = lambda: AppIdMiddleware(build())
    return app


app = create_app()
