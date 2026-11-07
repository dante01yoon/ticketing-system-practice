from datetime import datetime, timezone

from fastapi import Header

from .errors import ApiError


def get_now() -> datetime:
    """현재 시각(UTC). 홀드·만료 판단은 모두 이 값만 쓴다 (DB now() 금지)."""
    return datetime.now(timezone.utc)


def require_user_id(x_user_id: str | None = Header(default=None)) -> str:
    if x_user_id is None or not x_user_id.strip():
        raise ApiError(401, "USER_ID_REQUIRED", "X-User-Id 헤더가 필요합니다.")
    return x_user_id


def require_idempotency_key(idempotency_key: str | None = Header(default=None)) -> str:
    if idempotency_key is None or not idempotency_key.strip():
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key 헤더가 필요합니다.")
    if len(idempotency_key) > 255:
        raise ApiError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key는 255자 이하여야 합니다.")
    return idempotency_key
