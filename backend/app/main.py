import re
import uuid
from datetime import datetime, timedelta
from typing import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .db import SessionLocal
from .deps import get_now, require_user_id
from .errors import ApiError
from .models import Booking, Concert, Seat

HOLD_TTL = timedelta(minutes=5)


class BookingRequest(BaseModel):
    seat_id: str


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _is_live_hold(seat: Seat, now: datetime) -> bool:
    return seat.status == "HELD" and now < seat.held_at + HOLD_TTL


def _lock_seat(db, seat_id: str) -> Seat:
    seat = db.execute(select(Seat).where(Seat.id == seat_id).with_for_update()).scalar_one_or_none()
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


def create_app() -> FastAPI:
    app = FastAPI(title="Ticketing API (MVP)", version="0.1.0")

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
        with SessionLocal() as db:
            if db.get(Concert, concert_id) is None:
                raise ApiError(404, "CONCERT_NOT_FOUND", "공연을 찾을 수 없습니다.")
            seats = db.execute(select(Seat).where(Seat.concert_id == concert_id)).scalars().all()
            seats.sort(key=_seat_sort_key)
            out = []
            for s in seats:
                status = s.status
                if status == "HELD" and not _is_live_hold(s, now):
                    status = "AVAILABLE"
                out.append({"id": s.id, "number": s.number, "status": status})
            return {"concert_id": concert_id, "seats": out}

    @app.post("/seats/{seat_id}/hold", status_code=201)
    def hold_seat(
        seat_id: str, user_id: str = Depends(require_user_id), clock: Callable[[], datetime] = Depends(lock_clock)
    ):
        with SessionLocal() as db, db.begin():
            seat = _lock_seat(db, seat_id)  # 행 잠금: 같은 좌석의 동시 요청은 직렬화된다
            now = clock()  # 잠금 뒤에 읽는다
            if seat.status == "SOLD":
                raise ApiError(409, "SEAT_ALREADY_SOLD", "이미 판매된 좌석입니다.")
            if _is_live_hold(seat, now):
                raise ApiError(409, "SEAT_ALREADY_HELD", "이미 홀드된 좌석입니다.")
            seat.status, seat.held_by, seat.held_at = "HELD", user_id, now
            return {
                "seat_id": seat.id,
                "status": "HELD",
                "held_at": _iso(now),
                "expires_at": _iso(now + HOLD_TTL),
            }

    @app.post("/bookings", status_code=201)
    def confirm_booking(
        body: BookingRequest,
        user_id: str = Depends(require_user_id),
        clock: Callable[[], datetime] = Depends(lock_clock),
    ):
        try:
            with SessionLocal() as db, db.begin():
                seat = _lock_seat(db, body.seat_id)
                now = clock()  # 잠금 뒤에 읽는다
                if seat.status == "SOLD":
                    raise ApiError(409, "SEAT_ALREADY_SOLD", "이미 판매된 좌석입니다.")
                if seat.status == "AVAILABLE":
                    raise ApiError(409, "HOLD_REQUIRED", "먼저 좌석을 홀드하세요.")
                if not _is_live_hold(seat, now):
                    if seat.held_by == user_id:
                        raise ApiError(409, "HOLD_EXPIRED", "홀드가 만료되었습니다.")
                    raise ApiError(409, "HOLD_REQUIRED", "먼저 좌석을 홀드하세요.")
                if seat.held_by != user_id:
                    raise ApiError(403, "HOLD_NOT_OWNED", "내 홀드가 아닙니다.")
                seat.status = "SOLD"
                booking = Booking(
                    id=str(uuid.uuid4()), seat_id=seat.id, concert_id=seat.concert_id, user_id=user_id, created_at=now
                )
                db.add(booking)
                db.flush()  # seat_id UNIQUE 가 두 번째 방어선
                return {
                    "id": booking.id,
                    "seat_id": booking.seat_id,
                    "concert_id": booking.concert_id,
                    "user_id": booking.user_id,
                    "created_at": _iso(now),
                }
        except IntegrityError:
            raise ApiError(409, "SEAT_ALREADY_SOLD", "이미 판매된 좌석입니다.")

    return app


app = create_app()
