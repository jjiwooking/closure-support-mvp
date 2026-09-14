"""
정책 적합도 스코어링 + 과거 심사결과 통계 (심사평 "머신러닝/데이터분석 요소 추가" 대응).

services.evaluate_eligibility()의 4단계 라벨(입력 조건 부합/추가 정보 필요/
현재 조건 불일치/모집 상태 확인 필요)은 그대로 두고, 이 모듈은 그 위에
0~100 점수와 과거 데이터 기반 승인율 통계를 더해 사용자가 우선순위를
가늠할 수 있게 돕는다. 두 함수 모두 LLM을 호출하지 않는다.
"""
from services import parse_eligibility_rules

# 필드별 감점/가점 가중치. eligibility_rules에 걸린 조건 하나하나를 같은 비중으로
# 보지 않기 위함 — region/rent_status처럼 자주 등장하는 필드는 기본 가중치,
# 나머지는 약간 낮게 잡는다.
FIELD_WEIGHT = {"region": 30, "rent_status": 25, "employee_status": 20, "demolition_status": 15}
DEFAULT_WEIGHT = 15
UNKNOWN_PENALTY_RATIO = 0.4  # 불일치보다는 약하게 감점(아직 모르는 것뿐이므로)


def score_policy_fit(profile: dict, eligibility_rules_json: str) -> int:
    """조건 충족도를 0~100 점수로 환산한다. 규칙이 없으면 50점(중립, 확인 필요)."""
    rules = parse_eligibility_rules(eligibility_rules_json)
    if not rules:
        return 50

    total_weight = 0
    earned = 0.0
    for field, expected in rules.items():
        weight = FIELD_WEIGHT.get(field, DEFAULT_WEIGHT)
        total_weight += weight
        actual = profile.get(field)
        if actual is None or actual == "모름":
            earned += weight * (1 - UNKNOWN_PENALTY_RATIO)
        elif actual == expected:
            earned += weight
        # 불일치는 0점 그대로

    if total_weight == 0:
        return 50
    return round(100 * earned / total_weight)


# 업종별로 우선순위가 높은 설비 카테고리 순서(앞쪽일수록 그 업종에 더 핵심적인 설비).
# 심사평 "폐업자-창업자 매칭"이 단순 필터 조회에 그쳤던 것을 실제 추천 로직으로
# 보완한다 — 예비 창업자가 업종만 고르면 관련도 높은 매물이 위로 올라온다.
BUSINESS_TYPE_CATEGORIES = {
    "카페": ["카페/음료기기", "주방/조리기기", "집기/가구", "전자기기"],
    "식당/분식": ["주방/조리기기", "냉장/냉동", "집기/가구", "전자기기"],
    "편의점/소매": ["냉장/냉동", "전자기기", "집기/가구"],
    "미용/뷰티": ["전자기기", "집기/가구"],
}


def score_equipment_match(item: dict, business_type: str | None) -> int:
    """이 매물의 카테고리가 예비 창업자의 업종에 얼마나 맞는지 0~100점으로
    매긴다. 업종을 안 골랐거나 매물에 카테고리가 없으면 중립 50점."""
    priority = BUSINESS_TYPE_CATEGORIES.get(business_type) if business_type else None
    category = item.get("category")
    if not priority or not category:
        return 50
    if category not in priority:
        return 20
    rank = priority.index(category)
    return max(100 - rank * 15, 40)


def policy_approval_stats(conn, target_career: str | None = None) -> dict:
    """과거 심사 이력(policy_outcome_history)에서 승인율을 집계한다.
    target_career가 주어지면 그 진로(+'공통')만, 아니면 전체를 본다.
    반환: {"rate": float(0~1) | None, "approved": int, "decided": int}"""
    if target_career and target_career != "모름":
        rows = conn.execute(
            "SELECT decision_status FROM policy_outcome_history WHERE target_career IN (?, '공통')",
            (target_career,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT decision_status FROM policy_outcome_history").fetchall()

    statuses = [r["decision_status"] for r in rows]
    decided = [s for s in statuses if s in ("승인", "불승인")]
    if not decided:
        return {"rate": None, "approved": 0, "decided": 0}

    approved = sum(1 for s in decided if s == "승인")
    return {"rate": approved / len(decided), "approved": approved, "decided": len(decided)}
