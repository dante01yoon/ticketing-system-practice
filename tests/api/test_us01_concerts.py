"""US-01 공연 목록 보기."""
import pytest

from apihelpers import parse_dt


@pytest.mark.story("US-01", "AC-1")
def test_us01_ac1_list_has_id_title_starts_at(api):
    # 시드는 공연 1개뿐이고 공연 등록 API 가 없어 '2개' 는 만들 수 없다 (lead 에 보고). 목록의 모든 항목을 검사한다.
    r = api.concerts()
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= 1
    for c in items:
        assert isinstance(c["id"], str) and c["id"]
        assert isinstance(c["title"], str) and c["title"]
        parse_dt(c["starts_at"])


@pytest.mark.story("US-01", "AC-2")
def test_us01_ac2_empty_list_is_200(api_empty):
    r = api_empty.concerts()
    assert r.status_code == 200, r.text
    assert r.json() == {"items": []}


@pytest.mark.story("US-01", "AC-3")
def test_us01_ac3_no_user_header_needed(api):
    r = api.client.get("/concerts")  # X-User-Id 없음
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) >= 1
