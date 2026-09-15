"""api.py 엔드포인트 통합 테스트. fastapi-reviewer가 지목한 "인증/소유권 404/
status 검증에 테스트가 없다"는 항목에 대한 최소 커버리지다.

별도 DB를 모킹하지 않고, 이 프로젝트가 로컬 개발에 쓰는 실제 Postgres를 그대로
쓴다(docker-compose.yml). 실행 전 `docker compose up -d`로 컨테이너가 떠 있어야
한다. 로그인 엔드포인트에 레이트리밋(10/분)이 걸려있으므로, 모듈 전체에서
로그인 호출 횟수를 최소화하기 위해 세션을 모듈 스코프 fixture로 한 번만 만들어
재사용한다.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from api import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def user_a(client):
    """이 테스트 모듈 전용 유니크 사용자로 로그인한 세션. 매 pytest 실행마다
    새 이름으로 로그인해 seed_if_empty()가 매번 새 데모 데이터를 만들게 한다."""
    name = f"pytest_사용자A_{uuid.uuid4().hex[:8]}"
    res = client.post("/auth/login", json={"name": name})
    assert res.status_code == 200
    return client


@pytest.fixture(scope="module")
def user_b(client):
    """소유권(IDOR) 테스트용 두 번째 사용자. user_a와 같은 TestClient 인스턴스를
    쓰면 쿠키가 덮어써지므로, 별도 TestClient로 독립된 쿠키 저장소를 쓴다."""
    with TestClient(app) as c2:
        name = f"pytest_사용자B_{uuid.uuid4().hex[:8]}"
        res = c2.post("/auth/login", json={"name": name})
        assert res.status_code == 200
        yield c2


def test_dashboard_requires_auth(client):
    """로그인 없이 접근하면 401 — 세션 쿠키가 없는 새 TestClient로 확인."""
    with TestClient(app) as anon:
        res = anon.get("/dashboard")
        assert res.status_code == 401


def test_login_then_dashboard_and_tasks(user_a):
    res = user_a.get("/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert "profile" in body and "completed" in body and "total" in body

    res = user_a.get("/tasks")
    assert res.status_code == 200
    assert len(res.json()) > 0


def test_task_status_validation(user_a):
    tasks = user_a.get("/tasks").json()
    task_id = tasks[0]["id"]

    res = user_a.patch(f"/tasks/{task_id}", json={"status": "이상한값"})
    assert res.status_code == 422

    res = user_a.patch(f"/tasks/{task_id}", json={"status": "사용자 완료"})
    assert res.status_code == 200


def test_task_update_rejects_other_users_task(user_a, user_b):
    """IDOR 회귀 확인: user_b의 업무 id를 user_a 세션으로 바꾸려 하면 404."""
    b_tasks = user_b.get("/tasks").json()
    b_task_id = b_tasks[0]["id"]

    res = user_a.patch(f"/tasks/{b_task_id}", json={"status": "사용자 완료"})
    assert res.status_code == 404


def test_equipment_create_validates_category_and_condition(user_a):
    res = user_a.post(
        "/equipment",
        json={"name": "테스트품목", "category": "존재하지않는카테고리", "condition": "중"},
    )
    assert res.status_code == 422

    res = user_a.post(
        "/equipment",
        json={"name": "테스트품목", "category": "기타", "condition": "중"},
    )
    assert res.status_code == 201


def test_equipment_status_validation(user_a):
    created = user_a.post(
        "/equipment", json={"name": "상태검증품목", "category": "기타", "condition": "중"}
    )
    eq_id = created.json()["id"]

    res = user_a.patch(f"/equipment/{eq_id}", json={"status": "이상한상태"})
    assert res.status_code == 422

    res = user_a.patch(f"/equipment/{eq_id}", json={"payment_status": "이상한값"})
    assert res.status_code == 422

    res = user_a.patch(f"/equipment/{eq_id}", json={"status": "판매 중"})
    assert res.status_code == 200


def test_equipment_ownership_blocks_other_user(user_a, user_b):
    """user_a 소유 물품을 user_b가 조작하려 하면 404(남의 물품 존재 자체를 숨김)."""
    created = user_a.post(
        "/equipment", json={"name": "소유권테스트품목", "category": "기타", "condition": "중"}
    )
    eq_id = created.json()["id"]

    res = user_b.patch(f"/equipment/{eq_id}", json={"status": "판매 중"})
    assert res.status_code == 404


def test_logout_invalidates_session(user_b):
    """user_b 세션을 로그아웃 후 재사용하면 401 — 이후 다른 테스트가 user_b를
    쓰지 않으므로(모듈 마지막) 안전하게 로그아웃해도 된다."""
    res = user_b.post("/auth/logout")
    assert res.status_code == 200
    res = user_b.get("/dashboard")
    assert res.status_code == 401
