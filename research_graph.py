"""
백그라운드 트랙(정책리서치 → 매칭알림)을 위한 LangGraph 서브그래프.
설계문서의 "정책리서치 → 매칭알림" 흐름을 실제 스케줄러 없이, 화면의
"지금 리서치 실행" 버튼으로 동기 실행한다(app.py의 screen_policies()에서 호출).

노드 흐름:
  policy_research : 기업마당 API에서 원문을 수집해 sources에 '검토 필요'로 쌓는다
                     (기존 services.sync_bizinfo_policies 그대로 재사용 — 필드가
                     검증되지 않은 원문이라 policies로 자동 승격하지 않는다는
                     원칙은 그대로 유지된다). 실행 로그를 research_runs에 남긴다.
  match_notify    : 이미 사람이 검토해 policies에 등록된('검토완료') 정책 중
                     이 사용자에게 아직 알리지 않았고 조건/진로가 맞는 것만 골라
                     policy_matches + notifications를 만든다.
"""
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import services


class ResearchState(TypedDict):
    conn: Any
    user_id: str
    profile: dict
    keyword: str
    source_count: int
    new_matches: int


def policy_research(state: ResearchState) -> dict:
    conn = state["conn"]
    result = services.sync_bizinfo_policies(conn, state["keyword"])
    source_count = len(result.get("items") or [])
    services.log_research_run(conn, source_count)
    return {"source_count": source_count}


def match_notify(state: ResearchState) -> dict:
    conn = state["conn"]
    matches = services.find_new_policy_matches(conn, state["user_id"], state["profile"])
    for policy in matches:
        services.record_policy_match_and_notify(conn, state["user_id"], policy)
    return {"new_matches": len(matches)}


def _build_graph():
    workflow = StateGraph(ResearchState)
    workflow.add_node("policy_research", policy_research)
    workflow.add_node("match_notify", match_notify)
    workflow.add_edge(START, "policy_research")
    workflow.add_edge("policy_research", "match_notify")
    workflow.add_edge("match_notify", END)
    return workflow.compile()


_GRAPH = _build_graph()


def run_research(conn, user_id, profile: dict, keyword: str = "폐업") -> dict:
    initial: ResearchState = {
        "conn": conn,
        "user_id": user_id,
        "profile": profile,
        "keyword": keyword,
        "source_count": 0,
        "new_matches": 0,
    }
    result = _GRAPH.invoke(initial)
    return {
        "source_count": result.get("source_count", 0),
        "new_matches": result.get("new_matches", 0),
    }
