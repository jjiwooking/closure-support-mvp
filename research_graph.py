"""
백그라운드 트랙(정책수집 → 정책구조화 → 매칭알림)을 위한 LangGraph 서브그래프.
설계문서의 "정책리서치 → 매칭알림" 흐름을 실제 스케줄러 없이, 화면의
"지금 리서치 실행" 버튼으로 동기 실행한다(app.py의 screen_policies()에서 호출).

세 에이전트로 역할을 분리한다(심사평 "에이전트별 역할 분배 고도화" 대응):
  policy_collect  : 기업마당 API에서 원문을 수집해 sources에 '검토 필요'로 쌓는다
                     (기존 services.sync_bizinfo_policies 그대로 재사용 — 필드가
                     검증되지 않은 원문이라 policies로 자동 승격하지 않는다는
                     원칙은 그대로 유지된다). 실행 로그를 research_runs에 남긴다.
  policy_extract  : 아직 AI 초안이 없는 '검토 필요' sources를 LLM으로 구조화해
                     sources.extracted_draft에 저장만 한다. 사람이 검토 화면에서
                     초안을 확인하고 승인해야만 policies에 등록되며, 이 노드가
                     직접 policies에 쓰지는 않는다(자동 게시 금지 원칙 유지).
                     LLM 미설정/실패 시 조용히 건너뛴다.
  match_notify    : 이미 사람이 검토해 policies에 등록된('검토완료') 정책 중
                     이 사용자에게 아직 알리지 않았고 조건/진로가 맞는 것만 골라
                     policy_matches + notifications를 만든다.
"""
import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import services
from config import llm_configured
from llm_client import generate_text

EXTRACT_BATCH_LIMIT = 5


class ResearchState(TypedDict):
    conn: Any
    user_id: str
    profile: dict
    keyword: str
    source_count: int
    extracted_count: int
    new_matches: int


def policy_collect(state: ResearchState) -> dict:
    conn = state["conn"]
    result = services.sync_bizinfo_policies(conn, state["keyword"])
    source_count = len(result.get("items") or [])
    services.log_research_run(conn, source_count)
    return {"source_count": source_count}


def _extract_one(raw_title_json: str):
    """sources.title에는 sync_bizinfo_policies가 넣어둔 원문 dict의 JSON 문자열이
    그대로 들어있다. 그 원문을 LLM에 주고 구조화 초안을 뽑는다. 실패하면 None."""
    system_instruction = (
        "당신은 한국 정부 지원사업 공고 원문을 구조화하는 도우미입니다. "
        "아래 원문에서 알 수 있는 내용만으로 JSON을 만드세요. "
        "알 수 없는 값이나 원문에 명시되지 않은 조건은 반드시 null로 두고 추측해서 채우지 마세요. "
        '형식: {"title": string, "eligibility_summary": string, '
        '"target_career_guess": "재창업"|"취업"|"공통"|null, '
        '"application_deadline_guess": "YYYY-MM-DD"|null, '
        '"eligibility_rules_guess": {'
        '"region": string|null, '
        '"rent_status": "임차"|"자가"|null, '
        '"employee_status": "있음"|"없음"|null, '
        '"demolition_status": "미정"|"예정"|"진행"|"완료"|null}}. '
        "eligibility_rules_guess는 원문에 자격 조건으로 명시된 항목만 채우세요 "
        "(예: '임차 사업장만 대상'이면 rent_status를 '임차'로). "
        "JSON 외의 다른 텍스트는 출력하지 마세요."
    )
    result = generate_text(f"원문:\n{raw_title_json}", system_instruction=system_instruction)
    if not (result["ok"] and result["text"]):
        return None
    text = result["text"].strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed


def policy_extract(state: ResearchState) -> dict:
    conn = state["conn"]
    if not llm_configured():
        return {"extracted_count": 0}

    pending = conn.execute(
        """
        SELECT id, title FROM sources
        WHERE review_status = '검토 필요' AND extracted_draft IS NULL
        LIMIT ?
        """,
        (EXTRACT_BATCH_LIMIT,),
    ).fetchall()

    extracted = 0
    for row in pending:
        draft = _extract_one(row["title"])
        if draft is None:
            continue
        conn.execute(
            "UPDATE sources SET extracted_draft=? WHERE id=?",
            (json.dumps(draft, ensure_ascii=False), row["id"]),
        )
        extracted += 1
    if extracted:
        conn.commit()
    return {"extracted_count": extracted}


def match_notify(state: ResearchState) -> dict:
    conn = state["conn"]
    matches = services.find_new_policy_matches(conn, state["user_id"], state["profile"])
    for policy in matches:
        services.record_policy_match_and_notify(conn, state["user_id"], policy)
    return {"new_matches": len(matches)}


def _build_graph():
    workflow = StateGraph(ResearchState)
    workflow.add_node("policy_collect", policy_collect)
    workflow.add_node("policy_extract", policy_extract)
    workflow.add_node("match_notify", match_notify)
    workflow.add_edge(START, "policy_collect")
    workflow.add_edge("policy_collect", "policy_extract")
    workflow.add_edge("policy_extract", "match_notify")
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
        "extracted_count": 0,
        "new_matches": 0,
    }
    result = _GRAPH.invoke(initial)
    return {
        "source_count": result.get("source_count", 0),
        "extracted_count": result.get("extracted_count", 0),
        "new_matches": result.get("new_matches", 0),
    }
