"""9장: 가상 대기열(번호표·전광판)과 입장권. docs/queue.md 참고.

번호 부여·전광판 따라잡기·입장권 발급 시각 기록은 Lua 하나로 원자적으로 한다.
현재 시각은 모두 호출자가 넘기는 now(get_now)만 쓴다(Redis TIME·TTL로 판단하지 않는다).
입장권은 HMAC 서명이라 Redis 없이 검증한다.
"""
import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .cache import _client, _us
from .errors import ApiError

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class QueueConfig:
    batch: int
    interval_us: int
    ttl_us: int
    secret: bytes

    @classmethod
    def from_env(cls) -> "QueueConfig":
        env = os.environ
        return cls(
            batch=int(env.get("QUEUE_BATCH") or 100),
            interval_us=max(1, round(float(env.get("QUEUE_INTERVAL_SECONDS") or 10) * 1_000_000)),
            ttl_us=round(float(env.get("ENTRY_TTL_SECONDS") or 120) * 1_000_000),
            secret=(env.get("ENTRY_TOKEN_SECRET") or "dev-entry-token-secret").encode(),
        )


def _from_us(us: int) -> datetime:
    return _EPOCH + timedelta(microseconds=us)


# ---------- 번호표·전광판 ----------
# 키: q:<concert> (해시: serving, anchor, issued), qu:<concert> (해시: 사용자 -> "번호|입장권 만료 us", 0이면 아직 입장 전)
# ARGV: 사용자, now(us), 묶음, 간격(us), 입장권 수명(us), 번호표 발급 허용(1=POST, 0=GET me)
# 반환: {0}(내 번호 없음) 또는 {1, 번호, 전광판, anchor, 입장권 만료 us(0=없음), 새 번호 여부}
_TICKET = """
local now = tonumber(ARGV[2])
local batch = tonumber(ARGV[3])
local itv = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
local join = ARGV[6] == '1'
local function fmt(n) return string.format('%.0f', n) end

local serving = tonumber(redis.call('HGET', KEYS[1], 'serving'))
local anchor
if not serving then
  if not join then return {0} end
  serving = batch
  anchor = now
  redis.call('HSET', KEYS[1], 'serving', fmt(serving), 'anchor', fmt(anchor), 'issued', '0')
else
  anchor = tonumber(redis.call('HGET', KEYS[1], 'anchor'))
  local issued = tonumber(redis.call('HGET', KEYS[1], 'issued'))
  local ticks = math.floor((now - anchor) / itv)
  if ticks > 0 then
    local up = math.min(serving + ticks * batch, issued + batch)
    if up > serving then serving = up end
    anchor = anchor + ticks * itv
    redis.call('HSET', KEYS[1], 'serving', fmt(serving), 'anchor', fmt(anchor))
  end
end

local number, exp = nil, 0
local rec = redis.call('HGET', KEYS[2], ARGV[1])
if rec then
  local n, e = string.match(rec, '^(%d+)|(%d+)$')
  number = tonumber(n)
  exp = tonumber(e)
  if join and exp > 0 and now >= exp then rec = nil end
end
local created = 0
if not rec then
  if not join then return {0} end
  number = redis.call('HINCRBY', KEYS[1], 'issued', 1)
  exp = 0
  created = 1
end
if exp == 0 and number <= serving then exp = now + ttl end
redis.call('HSET', KEYS[2], ARGV[1], fmt(number) .. '|' .. fmt(exp))
return {1, number, serving, anchor, exp, created}
"""


def ticket(cfg: QueueConfig, concert_id: str, user_id: str, now: datetime, join: bool):
    """(본문 dict, 새 번호 여부, retry_after) 또는 내 번호가 없으면 None. Redis 오류는 QUEUE_UNAVAILABLE(503)."""
    try:
        r = _client().eval(
            _TICKET, 2, f"q:{concert_id}", f"qu:{concert_id}",
            user_id, _us(now), cfg.batch, cfg.interval_us, cfg.ttl_us, "1" if join else "0",
        )
    except Exception:
        raise ApiError(503, "QUEUE_UNAVAILABLE", "대기열을 사용할 수 없습니다. 잠시 뒤 다시 시도하세요.")
    if r[0] == 0:
        return None
    _, number, serving, anchor, exp, created = r
    now_us = _us(now)
    token = expires_at = retry_after = None
    if exp > 0 and now_us >= exp:
        status, ahead = "EXPIRED", 0
    elif exp > 0:
        status, ahead = "ADMITTED", 0
        token, expires_at = make_token(cfg, user_id, concert_id, exp), _from_us(exp).isoformat()
    else:
        status, ahead = "WAITING", number - serving - 1
        retry_after = max(1, -(-(anchor + cfg.interval_us - now_us) // 1_000_000))
    body = {
        "concert_id": concert_id,
        "number": number,
        "serving": serving,
        "ahead": ahead,
        "status": status,
        "retry_after": retry_after,
        "entry_token": token,
        "entry_expires_at": expires_at,
    }
    return body, bool(created), retry_after


# ---------- 입장권 ----------
# 형식: <b64(user)>.<b64(concert)>.<만료 us>.<hex HMAC-SHA256(앞 세 부분)>

def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def _sign(cfg: QueueConfig, payload: str) -> str:
    return hmac.new(cfg.secret, payload.encode(), hashlib.sha256).hexdigest()


def make_token(cfg: QueueConfig, user_id: str, concert_id: str, exp_us: int) -> str:
    payload = f"{_b64(user_id)}.{_b64(concert_id)}.{exp_us}"
    return f"{payload}.{_sign(cfg, payload)}"


def verify_token(cfg: QueueConfig, token: str | None, user_id: str) -> tuple[str, int]:
    """서명·소유자를 검사해 (입장권의 공연 ID, 만료 us)를 돌려준다. 만료는 보지 않는다. 거절이면 403 ApiError."""
    if token is None or not token.strip():
        raise ApiError(403, "ENTRY_TOKEN_REQUIRED", "입장권(X-Entry-Token)이 필요합니다.")
    invalid = ApiError(403, "ENTRY_TOKEN_INVALID", "입장권이 올바르지 않습니다.")
    parts = token.split(".")
    if len(parts) != 4:
        raise invalid
    payload = ".".join(parts[:3])
    try:
        if not hmac.compare_digest(_sign(cfg, payload).encode(), parts[3].encode()):
            raise invalid
        user, concert, exp = _unb64(parts[0]), _unb64(parts[1]), int(parts[2])
    except ApiError:
        raise
    except Exception:
        raise invalid
    if user != user_id:
        raise invalid
    return concert, exp


def ensure_not_expired(exp_us: int, now: datetime) -> None:
    if _us(now) >= exp_us:
        raise ApiError(403, "ENTRY_TOKEN_EXPIRED", "입장권이 만료되었습니다. 대기열에 다시 서세요.")


def check_token(cfg: QueueConfig, token: str | None, user_id: str, now: datetime, concert_id: str | None = None) -> str:
    """입장권을 검사해 입장권의 공연 ID를 돌려준다. 거절이면 403 ApiError. 공연 불일치는 만료보다 먼저 INVALID."""
    concert, exp = verify_token(cfg, token, user_id)
    if concert_id is not None and concert != concert_id:
        raise ApiError(403, "ENTRY_TOKEN_INVALID", "입장권이 올바르지 않습니다.")
    ensure_not_expired(exp, now)
    return concert


def issue_entry_token(user_id: str, concert_id: str, expires_at: datetime) -> str:
    """테스트용 접점: create_app 없이, 대기열이 주는 것과 같은 형식·서명(ENTRY_TOKEN_SECRET)의 입장권을 만든다."""
    return make_token(QueueConfig.from_env(), user_id, concert_id, _us(expires_at))
