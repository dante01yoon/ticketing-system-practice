"""docs/openapi.yaml 이 docs/stories 의 인수 조건을 표현하는지 검사하는 계약 테스트.

스토리는 동작만 정하고 경로·필드명·상태 코드는 정하지 않으므로, 오퍼레이션은 경로 키워드와
파라미터 모양으로 찾고 상태 코드는 아래 상수에 적은 가정을 따른다. 가정이 틀리면 lead 가 이 상수를 바꾼다.
각 테스트의 @pytest.mark.story("US-xx", "AC-n") 가 스토리 번호 표시다.
"""
import copy
import os

import pytest
import yaml

SPEC_PATH = os.environ.get("OPENAPI_PATH") or os.path.join(os.path.dirname(__file__), "..", "..", "docs", "openapi.yaml")

# 가정한 상태 코드 (스토리에는 없고, 409 만 사용자가 예로 지정함)
CONFLICT = {"409"}                 # 이미 홀드됨 / 이미 판매됨 / 홀드 필요(중 하나) / 동시 요청 패배
NOT_FOUND = {"404"}                # 공연 없음 / 좌석 없음
NO_USER = {"401", "400"}           # 사용자 식별 필요 (X-User-Id 없음)
EXPIRED = {"409", "410"}           # 홀드 만료
NOT_MINE = {"403", "409"}          # 내 홀드 아님
HOLD_REQUIRED = {"409", "412"}     # 홀드 필요
OK = {"200", "201"}
SEAT_STATUSES = {"AVAILABLE", "HELD", "SOLD"}
USER_FIELD_HINTS = ("user", "holder", "held_by", "owner")


@pytest.fixture(scope="module")
def spec():
    assert os.path.exists(SPEC_PATH), "docs/openapi.yaml 이 없습니다 (API 계약 미작성)"
    with open(SPEC_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict) and data.get("paths"), "openapi.yaml 에 paths 가 없습니다"
    return data


# ---------- 헬퍼 ----------
def deref(spec, node):
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 20:
        cur = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        node, seen = cur, seen + 1
    return node


def props(spec, schema):
    """allOf/배열 items 를 펼친 속성 딕셔너리."""
    schema = deref(spec, schema) or {}
    out = {}
    for sub in schema.get("allOf", []):
        out.update(props(spec, sub))
    if schema.get("type") == "array" or "items" in schema:
        out.update(props(spec, schema.get("items", {})))
    for k, v in (schema.get("properties") or {}).items():
        out[k] = deref(spec, v)
    # 목록이 {items: [...]} 같은 래퍼 객체인 경우 안쪽 배열 속성도 펼친다
    for v in list(out.values()):
        if isinstance(v, dict) and v.get("type") == "array":
            out.update(props(spec, v.get("items", {})))
    return out


def find_ops(spec, method, keywords, params=None):
    """(path, op) 목록. keywords 중 하나가 경로에 있고, 경로 파라미터 개수가 params 인 것."""
    found = []
    for path, item in spec["paths"].items():
        item = deref(spec, item)
        if method not in item:
            continue
        if keywords and not any(k in path.lower() for k in keywords):
            continue
        if params is not None and path.count("{") != params:
            continue
        found.append((path, item[method], item))
    return found


def one_op(spec, method, keywords, params=None, what=""):
    found = find_ops(spec, method, keywords, params)
    assert found, f"{what}: {method.upper()} 오퍼레이션을 찾지 못했습니다 (경로 키워드 {keywords})"
    return found[0]


def responses(spec, op):
    return {str(k): deref(spec, v) for k, v in (op.get("responses") or {}).items()}


def body_schema(spec, resp):
    return ((resp or {}).get("content") or {}).get("application/json", {}).get("schema", {})


def header_params(spec, op, item):
    ps = [deref(spec, p) for p in (item.get("parameters", []) + op.get("parameters", []))]
    return [p for p in ps if p.get("in") == "header"]


def requires_user_header(spec, op, item):
    return any(p["name"].lower() == "x-user-id" and p.get("required") for p in header_params(spec, op, item))


def declares_any(spec, op, codes):
    return bool(codes & set(responses(spec, op)))


def list_op(spec):
    ops = [o for o in find_ops(spec, "get", ["concert", "show", "performance", "event"], 0)]
    assert ops, "공연 목록 GET 오퍼레이션(경로 파라미터 없음)을 찾지 못했습니다"
    return ops[0]


def seatmap_op(spec):
    return one_op(spec, "get", ["seat"], 1, "좌석맵")


def hold_op(spec):
    return one_op(spec, "post", ["hold"], None, "좌석 홀드")


def confirm_op(spec):
    return one_op(spec, "post", ["confirm", "booking", "reservation", "purchase"], None, "예매 확정")


def success(spec, op):
    r = responses(spec, op)
    codes = OK & set(r)
    assert codes, f"성공 응답(200/201)이 없습니다: {sorted(r)}"
    return r[sorted(codes)[0]]


def has_field(p, *hints):
    return any(any(h in name.lower() for h in hints) for name in p)


# ---------- US-01 공연 목록 ----------
@pytest.mark.story("US-01", "AC-1")
def test_us01_ac1_list_returns_id_title_datetime(spec):
    _, op, _ = list_op(spec)
    p = props(spec, body_schema(spec, success(spec, op)))
    assert "id" in p or has_field(p, "concert_id", "show_id"), f"공연 ID 필드 없음: {list(p)}"
    assert has_field(p, "title", "name"), f"제목 필드 없음: {list(p)}"
    assert has_field(p, "date", "time", "_at", "start"), f"공연 일시 필드 없음: {list(p)}"


@pytest.mark.story("US-01", "AC-2")
def test_us01_ac2_empty_list_is_success(spec):
    _, op, _ = list_op(spec)
    schema = deref(spec, body_schema(spec, success(spec, op)))
    arrays = [schema] + [deref(spec, v) for v in (schema.get("properties") or {}).values()]
    arrays = [a for a in arrays if a.get("type") == "array"]
    assert arrays, "목록 응답에 배열이 없습니다"
    assert all(a.get("minItems", 0) == 0 for a in arrays), "빈 목록을 허용해야 합니다(minItems 0)"
    assert not declares_any(spec, op, NOT_FOUND), "빈 목록은 404 가 아니라 성공이어야 합니다"


@pytest.mark.story("US-01", "AC-3")
def test_us01_ac3_list_needs_no_user_header(spec):
    _, op, item = list_op(spec)
    assert not requires_user_header(spec, op, item), "공연 목록은 X-User-Id 를 요구하면 안 됩니다"
    assert not op.get("security"), "공연 목록에 인증 요구가 있습니다"
    assert not declares_any(spec, op, NO_USER), "공연 목록은 401/400 사용자 오류를 선언하면 안 됩니다"


# ---------- US-02 좌석맵 ----------
@pytest.mark.story("US-02", "AC-1")
def test_us02_ac1_seatmap_has_id_number_status(spec):
    _, op, _ = seatmap_op(spec)
    p = props(spec, body_schema(spec, success(spec, op)))
    assert "id" in p or has_field(p, "seat_id"), f"좌석 ID 필드 없음: {list(p)}"
    assert has_field(p, "number", "label", "seat_no"), f"좌석 번호 필드 없음: {list(p)}"
    assert "status" in p, f"status 필드 없음: {list(p)}"
    enum = set(p["status"].get("enum", []))
    assert SEAT_STATUSES <= enum, f"status enum 에 {SEAT_STATUSES} 가 모두 있어야 함: {enum}"


@pytest.mark.story("US-02", "AC-2")
def test_us02_ac2_expired_hold_shown_as_available_not_new_status(spec):
    _, op, _ = seatmap_op(spec)
    p = props(spec, body_schema(spec, success(spec, op)))
    enum = set(p.get("status", {}).get("enum", []))
    assert enum == SEAT_STATUSES, f"만료는 AVAILABLE 로 보여야 하므로 상태는 정확히 {SEAT_STATUSES} 여야 함: {enum}"


@pytest.mark.story("US-02", "AC-3")
def test_us02_ac3_unknown_concert_404(spec):
    _, op, _ = seatmap_op(spec)
    assert declares_any(spec, op, NOT_FOUND), "좌석맵에 404(공연 없음) 응답이 없습니다"


@pytest.mark.story("US-02", "AC-4")
def test_us02_ac4_seatmap_hides_holder(spec):
    _, op, _ = seatmap_op(spec)
    p = props(spec, body_schema(spec, success(spec, op)))
    leaked = [n for n in p if any(h in n.lower() for h in USER_FIELD_HINTS)]
    assert not leaked, f"좌석맵에 홀드한 사용자 정보 필드가 있습니다: {leaked}"


# ---------- US-03 홀드 ----------
@pytest.mark.story("US-03", "AC-1")
def test_us03_ac1_hold_success_has_expiry(spec):
    _, op, _ = hold_op(spec)
    p = props(spec, body_schema(spec, success(spec, op)))
    assert has_field(p, "expire", "expiry"), f"만료 시각 필드 없음: {list(p)}"


@pytest.mark.story("US-03", "AC-2")
def test_us03_ac2_hold_conflict_409(spec):
    _, op, _ = hold_op(spec)
    assert declares_any(spec, op, CONFLICT), "홀드에 409(이미 홀드됨) 응답이 없습니다"


@pytest.mark.story("US-03", "AC-3")
def test_us03_ac3_rehold_after_expiry_returns_new_expiry(spec):
    _, op, _ = hold_op(spec)
    p = props(spec, body_schema(spec, success(spec, op)))
    assert has_field(p, "expire", "expiry"), "재홀드 성공 응답에도 만료 시각이 있어야 합니다"


@pytest.mark.story("US-03", "AC-4")
def test_us03_ac4_boundary_still_conflict(spec):
    _, op, _ = hold_op(spec)
    assert declares_any(spec, op, CONFLICT), "만료 경계 전 홀드도 409 로 거절되어야 합니다"


@pytest.mark.story("US-03", "AC-5")
def test_us03_ac5_sold_vs_held_distinguishable(spec):
    _, op, _ = hold_op(spec)
    r = responses(spec, op)
    code = sorted(CONFLICT & set(r))
    assert code, "409 응답 없음"
    p = props(spec, body_schema(spec, r[code[0]]))
    assert has_field(p, "code"), (
        f"'이미 홀드됨'과 '이미 판매됨'을 구분할 오류 code 필드가 409 본문에 필요합니다: {list(p)}"
    )


@pytest.mark.story("US-03", "AC-6")
def test_us03_ac6_hold_requires_user_header(spec):
    _, op, item = hold_op(spec)
    assert requires_user_header(spec, op, item), "홀드는 X-User-Id 헤더를 required 로 선언해야 합니다"
    assert declares_any(spec, op, NO_USER), f"홀드에 {sorted(NO_USER)} 응답이 없습니다"


@pytest.mark.story("US-03", "AC-7")
def test_us03_ac7_unknown_seat_404(spec):
    _, op, _ = hold_op(spec)
    assert declares_any(spec, op, NOT_FOUND), "홀드에 404(좌석 없음) 응답이 없습니다"


@pytest.mark.story("US-03", "AC-8")
def test_us03_ac8_concurrent_loser_gets_409(spec):
    # 동시성 자체는 통합 테스트 몫. 계약 수준에서는 패배자 응답(409)이 선언돼 있어야 한다.
    _, op, _ = hold_op(spec)
    assert declares_any(spec, op, CONFLICT), "동시 홀드 패배자용 409 가 선언돼야 합니다"


# ---------- US-04 예매 확정 ----------
@pytest.mark.story("US-04", "AC-1")
def test_us04_ac1_confirm_returns_booking(spec):
    _, op, _ = confirm_op(spec)
    p = props(spec, body_schema(spec, success(spec, op)))
    assert has_field(p, "booking_id", "reservation_id") or "id" in p, f"예매 ID 없음: {list(p)}"
    assert has_field(p, "seat"), f"좌석 필드 없음: {list(p)}"
    assert has_field(p, "user"), f"사용자 필드 없음: {list(p)}"


@pytest.mark.story("US-04", "AC-2")
def test_us04_ac2_expired_hold_rejected(spec):
    _, op, _ = confirm_op(spec)
    assert declares_any(spec, op, EXPIRED), f"확정에 홀드 만료 응답 {sorted(EXPIRED)} 이 없습니다"


@pytest.mark.story("US-04", "AC-3")
def test_us04_ac3_not_my_hold_rejected(spec):
    _, op, _ = confirm_op(spec)
    assert declares_any(spec, op, NOT_MINE), f"확정에 '내 홀드 아님' 응답 {sorted(NOT_MINE)} 이 없습니다"


@pytest.mark.story("US-04", "AC-4")
def test_us04_ac4_hold_required(spec):
    _, op, _ = confirm_op(spec)
    assert declares_any(spec, op, HOLD_REQUIRED), f"확정에 '홀드 필요' 응답 {sorted(HOLD_REQUIRED)} 이 없습니다"


@pytest.mark.story("US-04", "AC-5")
def test_us04_ac5_hold_on_sold_conflict(spec):
    _, op, _ = hold_op(spec)
    assert declares_any(spec, op, CONFLICT), "판매된 좌석 홀드는 409 로 거절되어야 합니다"


@pytest.mark.story("US-04", "AC-6")
def test_us04_ac6_confirm_requires_user_header(spec):
    _, op, item = confirm_op(spec)
    assert requires_user_header(spec, op, item), "확정은 X-User-Id 헤더를 required 로 선언해야 합니다"
    assert declares_any(spec, op, NO_USER), f"확정에 {sorted(NO_USER)} 응답이 없습니다"


@pytest.mark.story("US-04", "AC-7")
def test_us04_ac7_double_confirm_declares_conflict(spec):
    _, op, _ = confirm_op(spec)
    assert declares_any(spec, op, CONFLICT), "중복/동시 확정 패배자용 409 가 선언돼야 합니다"
