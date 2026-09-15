"""analytics.py의 DB 없이 검증 가능한 순수 스코어링 로직 테스트."""
import json

from analytics import score_equipment_match, score_policy_fit


# ---------- score_policy_fit ----------

def test_score_policy_fit_no_rules_returns_neutral():
    assert score_policy_fit({}, "") == 50
    assert score_policy_fit({}, None) == 50


def test_score_policy_fit_all_match_returns_100():
    rules = json.dumps({"region": "서울 마포구", "rent_status": "임차"}, ensure_ascii=False)
    profile = {"region": "서울 마포구", "rent_status": "임차"}
    assert score_policy_fit(profile, rules) == 100


def test_score_policy_fit_mismatch_scores_zero_for_that_field():
    """단일 필드 규칙에서 불일치면 0점(가중치 분모는 그대로라 100 * 0 / weight = 0)."""
    rules = json.dumps({"region": "서울 마포구"}, ensure_ascii=False)
    profile = {"region": "부산"}
    assert score_policy_fit(profile, rules) == 0


def test_score_policy_fit_unknown_scores_partial_credit():
    """'모름'/누락 필드는 UNKNOWN_PENALTY_RATIO(0.4)만큼 감점된 60%를 받는다."""
    rules = json.dumps({"region": "서울 마포구"}, ensure_ascii=False)
    profile = {"region": "모름"}
    assert score_policy_fit(profile, rules) == 60


def test_score_policy_fit_weighted_mix_of_match_and_mismatch():
    # region(가중치 30, 일치) + rent_status(가중치 25, 불일치) = 30/55 = 54.5 -> round 55
    rules = json.dumps({"region": "서울 마포구", "rent_status": "임차"}, ensure_ascii=False)
    profile = {"region": "서울 마포구", "rent_status": "자가"}
    assert score_policy_fit(profile, rules) == round(100 * 30 / 55)


# ---------- score_equipment_match ----------

def test_score_equipment_match_no_business_type_returns_neutral():
    assert score_equipment_match({"category": "카페/음료기기"}, None) == 50


def test_score_equipment_match_no_category_returns_neutral():
    assert score_equipment_match({}, "카페") == 50


def test_score_equipment_match_top_priority_category_scores_highest():
    # 카페 업종의 최우선 카테고리는 "카페/음료기기"(rank 0) -> 100 - 0*15 = 100
    assert score_equipment_match({"category": "카페/음료기기"}, "카페") == 100


def test_score_equipment_match_lower_priority_category_scores_less():
    # "전자기기"는 카페 업종 우선순위 리스트의 마지막(rank 3) -> 100 - 3*15 = 55
    assert score_equipment_match({"category": "전자기기"}, "카페") == 55


def test_score_equipment_match_category_not_in_priority_list_scores_low():
    assert score_equipment_match({"category": "기타"}, "카페") == 20
