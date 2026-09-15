"""services.py의 DB 없이 검증 가능한 순수 로직 테스트.

python-reviewer가 최우선 테스트 대상으로 꼽은 함수들(evaluate_eligibility,
sort_policies_by_deadline/filter_policies_by_career, priority_sort,
update_profile이 의존하는 날짜 계산 등) 중, DB 접근 없이 순수하게 동작하는
부분만 다룬다. DB를 다루는 함수(update_profile 자체 등)는 여기서 다루지 않는다.
"""
import json

import pytest

from services import (
    check_stage_complete,
    compute_progress,
    evaluate_eligibility,
    filter_policies_by_career,
    generate_listing,
    parse_eligibility_rules,
    priority_sort,
    sort_policies_by_deadline,
)


# ---------- parse_eligibility_rules ----------

def test_parse_eligibility_rules_valid_json():
    assert parse_eligibility_rules('{"region": "서울"}') == {"region": "서울"}


@pytest.mark.parametrize("value", [None, "", "not json", "{broken"])
def test_parse_eligibility_rules_invalid_or_empty(value):
    assert parse_eligibility_rules(value) == {}


# ---------- evaluate_eligibility ----------

def test_evaluate_eligibility_no_rules_needs_status_check():
    assert evaluate_eligibility({"region": "서울"}, "") == "모집 상태 확인 필요"
    assert evaluate_eligibility({"region": "서울"}, None) == "모집 상태 확인 필요"


def test_evaluate_eligibility_all_match():
    rules = json.dumps({"region": "서울 마포구", "rent_status": "임차"}, ensure_ascii=False)
    profile = {"region": "서울 마포구", "rent_status": "임차"}
    assert evaluate_eligibility(profile, rules) == "입력 조건 부합"


def test_evaluate_eligibility_mismatch_wins_over_unknown():
    """불일치 필드가 하나라도 있으면, 다른 필드가 '모름'이어도 불일치가 우선한다
    (코드 순서: unmet을 먼저 확인)."""
    rules = json.dumps({"region": "서울 마포구", "rent_status": "임차"}, ensure_ascii=False)
    profile = {"region": "부산", "rent_status": "모름"}
    assert evaluate_eligibility(profile, rules) == "현재 조건 불일치"


def test_evaluate_eligibility_unknown_field():
    rules = json.dumps({"rent_status": "임차"}, ensure_ascii=False)
    profile = {"rent_status": "모름"}
    assert evaluate_eligibility(profile, rules) == "추가 정보 필요"

    profile_missing = {}
    assert evaluate_eligibility(profile_missing, rules) == "추가 정보 필요"


# ---------- filter_policies_by_career ----------

def test_filter_policies_by_career_unknown_returns_all():
    policies = [{"id": 1, "target_career": "재창업"}, {"id": 2, "target_career": "취업"}]
    assert filter_policies_by_career(policies, None) == policies
    assert filter_policies_by_career(policies, "모름") == policies


def test_filter_policies_by_career_filters_to_matching_common_and_untagged():
    policies = [
        {"id": 1, "target_career": "재창업"},
        {"id": 2, "target_career": "취업"},
        {"id": 3, "target_career": "공통"},
        {"id": 4, "target_career": None},
    ]
    result = filter_policies_by_career(policies, "재창업")
    assert {p["id"] for p in result} == {1, 3, 4}


# ---------- sort_policies_by_deadline ----------

def test_sort_policies_by_deadline_orders_ascending_and_none_last():
    policies = [
        {"id": 1, "application_deadline": "2026-12-01"},
        {"id": 2, "application_deadline": None},
        {"id": 3, "application_deadline": "2026-09-30"},
    ]
    result = sort_policies_by_deadline(policies)
    assert [p["id"] for p in result] == [3, 1, 2]


# ---------- priority_sort ----------

def test_priority_sort_overdue_first_then_soonest_then_no_due_date():
    tasks = [
        {"id": 1, "due_date": None},
        {"id": 2, "due_date": "2020-01-01"},  # overdue (past)
        {"id": 3, "due_date": "2099-01-01"},  # far future, not overdue
        {"id": 4, "due_date": "2098-01-01"},  # sooner than #3, not overdue
    ]
    result = priority_sort(tasks)
    assert [t["id"] for t in result] == [2, 4, 3, 1]


# ---------- compute_progress / check_stage_complete ----------

def test_compute_progress_counts_completed():
    tasks = [{"status": "사용자 완료"}, {"status": "시작 전"}, {"status": "사용자 완료"}]
    assert compute_progress(tasks) == (2, 3)


def test_compute_progress_empty():
    assert compute_progress([]) == (0, 0)


def test_check_stage_complete_requires_at_least_one_task():
    assert check_stage_complete([]) is False


def test_check_stage_complete_true_only_when_all_done():
    assert check_stage_complete([{"status": "사용자 완료"}, {"status": "시작 전"}]) is False
    assert check_stage_complete([{"status": "사용자 완료"}, {"status": "사용자 완료"}]) is True


# ---------- generate_listing ----------

def test_generate_listing_short_uses_fallback_for_missing_fields():
    item = {"name": "테스트 냉장고", "condition": "중"}
    text = generate_listing(item, style="short")
    assert "[테스트 냉장고] 판매합니다." in text
    assert "모델: 확인 필요" in text
    assert "상태: 중" in text


def test_generate_listing_treats_moreum_as_missing():
    """'모름'으로 입력된 필드도 미입력과 동일하게 '확인 필요'로 표기해야 한다."""
    item = {"name": "테스트", "used_period": "모름"}
    text = generate_listing(item, style="short")
    assert "사용 기간: 확인 필요" in text


def test_generate_listing_blog_style_has_disclaimer():
    item = {"name": "테스트"}
    text = generate_listing(item, style="blog")
    assert "본 글은 판매자가 입력한 정보로 자동 작성" in text
