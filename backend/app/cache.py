"""8장: Redis 캐시(좌석맵)와 홀드 관문. docs/cache.md 참고.

진짜 기준은 PostgreSQL이다. 이 모듈의 모든 함수는 Redis가 없거나 오류여도 예외를 밖으로 내지 않고
"Redis 없음"으로 처리한다(호출한 쪽은 DB만으로 진행한다). 만료 판단은 호출자가 넘기는 now(get_now)만 쓴다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import redis

SEATMAP_TTL_S = 5
GATE_LEASE_MS = 20_000  # 처음 쓴 표시의 실제 시간 임대(≤30초): DB 전에 앱이 죽어도 이만큼만 잘못 거절된다
CONFIRMED_PX = 5 * 60_000 + 60_000  # 201 뒤 확정한 표시의 실제 시간 수명(홀드 5분 + 여유)
HOLD_TTL = timedelta(minutes=5)

_clients: dict[str, redis.Redis] = {}


def _client() -> redis.Redis:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    c = _clients.get(url)
    if c is None:
        c = _clients[url] = redis.Redis.from_url(
            url, socket_connect_timeout=0.3, socket_timeout=0.3, decode_responses=True
        )
    return c


def _us(dt: datetime) -> int:
    """UTC 마이크로초 정수(정밀도 손실 없이 비교하려고 float 대신 정수)."""
    d = dt.astimezone(timezone.utc)
    return int(d.timestamp() // 1) * 1_000_000 + d.microsecond


def flush_db() -> None:
    try:
        _client().flushdb()
    except Exception:
        pass


# ---------- ② 좌석맵 캐시 ----------

def _map_key(concert_id: str) -> str:
    return f"seatmap:{concert_id}"


def seatmap_get(concert_id: str, now: datetime) -> str | None:
    """캐시된 본문(JSON 문자열). 없거나, 가장 이른 홀드 만료 시각이 지났으면 지우고 None."""
    try:
        raw = _client().get(_map_key(concert_id))
        if raw is None:
            return None
        entry = json.loads(raw)
        if entry["exp"] is not None and _us(now) >= entry["exp"]:
            _client().delete(_map_key(concert_id))
            return None
        return entry["body"]
    except Exception:
        return None


def seatmap_put(concert_id: str, body: str, earliest_expiry: datetime | None) -> None:
    try:
        exp = None if earliest_expiry is None else _us(earliest_expiry)
        _client().set(_map_key(concert_id), json.dumps({"exp": exp, "body": body}), ex=SEATMAP_TTL_S)
    except Exception:
        pass


def seatmap_invalidate(concert_id: str) -> None:
    """DB 커밋에 성공한 '뒤에' 부른다."""
    try:
        _client().delete(_map_key(concert_id))
    except Exception:
        pass


# ---------- ③ 홀드 관문 ----------
# 표시 값: "H|<만료 us>|<user>"(홀드), "S"(판매됨). 키: hold:<seat_id>

def _key(seat_id: str) -> str:
    return f"hold:{seat_id}"


def _held(user_id: str, expires: datetime) -> str:
    return f"H|{_us(expires)}|{user_id}"


# 반환: 0=통과(내가 새로 씀) 1=통과(이미 내 표시) 2=거절 HELD 3=거절 SOLD
_GATE = """
local v = redis.call('GET', KEYS[1])
if not v then
  redis.call('SET', KEYS[1], ARGV[3], 'PX', ARGV[4]); return 0
end
if v == 'S' then return 3 end
local exp, user = string.match(v, '^H|(%d+)|(.*)$')
if not exp then
  redis.call('SET', KEYS[1], ARGV[3], 'PX', ARGV[4]); return 0
end
if tonumber(exp) <= tonumber(ARGV[2]) then
  redis.call('SET', KEYS[1], ARGV[3], 'PX', ARGV[4]); return 0
end
if user == ARGV[1] then return 1 end
return 2
"""

# 값이 expected와 같을 때만 지운다(compare-and-delete)
_CAD = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

# DB가 내 유효 홀드를 확인한 뒤 표시를 DB 진실(만료 시각)로 맞춘다.
# 표시가 없거나, 내 것이거나, 만료가 더 이른 다른 사용자의 것(낡은 표시)이면 덮어쓴다.
# 더 늦게 만료되는 다른 사용자의 표시(더 새 홀드)와 판매 표시('S')는 건드리지 않는다.
_CONFIRM = """
local v = redis.call('GET', KEYS[1])
if v and v ~= 'S' then
  local exp, user = string.match(v, '^H|(%d+)|(.*)$')
  if exp and user ~= ARGV[1] and tonumber(exp) > tonumber(ARGV[4]) then return 0 end
elseif v then
  return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3]); return 1
"""


class Gate:
    """hold_gate 결과. reject_code가 있으면 DB에 가지 않고 그 코드로 409."""

    def __init__(self, reject_code: str | None = None, wrote: str | None = None):
        self.reject_code = reject_code
        self.wrote = wrote  # 이번 요청이 쓴 표시 값(되돌리기 대상)


def hold_gate(seat_id: str, user_id: str, now: datetime) -> Gate:
    if "\x00" in seat_id:
        return Gate()
    try:
        mine = _held(user_id, now + HOLD_TTL)
        r = _client().eval(_GATE, 1, _key(seat_id), user_id, _us(now), mine, GATE_LEASE_MS)
        if r == 0:
            return Gate(wrote=mine)
        if r == 2:
            return Gate("SEAT_ALREADY_HELD")
        if r == 3:
            return Gate("SEAT_ALREADY_SOLD")
        return Gate()
    except Exception:
        return Gate()


def hold_revert(seat_id: str, gate: Gate) -> None:
    """DB 결과가 201·200이 아닐 때: 내가 쓴 표시가 아직 내 것일 때만 지운다."""
    if gate.wrote is None or "\x00" in seat_id:
        return
    try:
        _client().eval(_CAD, 1, _key(seat_id), gate.wrote)
    except Exception:
        pass


def hold_confirm(seat_id: str, user_id: str, held_at: datetime) -> None:
    """DB 201·200 뒤: 표시를 DB의 실제 홀드 만료 시각으로 바로잡는다."""
    if "\x00" in seat_id:
        return
    try:
        exp = held_at + HOLD_TTL
        _client().eval(_CONFIRM, 1, _key(seat_id), user_id, _held(user_id, exp), CONFIRMED_PX, _us(exp))
    except Exception:
        pass


def mark_sold(seat_id: str) -> None:
    """좌석이 SOLD로 커밋된 뒤: 표시를 판매 상태로 바꾼다(SEAT_ALREADY_SOLD로 거절되게). 실패하면 한 번 더 시도한다."""
    for _ in range(2):
        try:
            _client().set(_key(seat_id), "S", px=CONFIRMED_PX)
            return
        except Exception:
            pass
