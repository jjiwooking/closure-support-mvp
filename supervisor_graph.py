"""
단계별 챗봇의 오케스트레이터(라우팅 에이전트) — 심사평 "멀티 에이전트 역할 분배·연계
방식 고도화" 심화 대응.

이번 개선판은 두 가지를 더한다.
1) 거래상담 에이전트가 "글도 써줘" 같은 요청을 받으면 단순 안내에 그치지 않고
   trade_graph(가격추정→글초안→채널안내) 에이전트를 직접 호출해 판매 글까지
   채팅에서 완성한다 — 에이전트가 다른 에이전트 파이프라인을 실제로 위임 실행.
2) 질문이 한 도메인에 그치지 않으면(예: "폐업 준비 다 됐는데 뭐 팔아야 돼?")
   여러 전문 에이전트를 순서대로 호출해 각자의 답을 얻고, synthesize 노드가
   하나의 답으로 합성한다(복합 질문 처리).

노드 흐름:
  route_question : 키워드 휴리스틱(항상 동작, 여러 카테고리에 동시에 걸리면 복합
                    질문으로 표시) + 아무 키워드도 안 걸리면 LLM 보완. 실패 시
                    guide로 폴백(가장 안전한 기본값).
  dispatch       : state["routes"]에 담긴 도메인(1개든 여러 개든)마다 해당
                    전문 에이전트(_run_*)를 순서대로 호출해 결과를 모은다 —
                    단일/복합 질문을 별도 노드로 나누지 않고 이 노드 하나로
                    처리한다.
  synthesize     : agent_results가 1건이면 그대로 통과, 여러 건이면 LLM으로
                    하나의 답으로 합성(미설정/실패 시 에이전트별 섹션으로 이어붙임).
"""
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import analytics
import pricing_model
import services
import trade_graph
from coaching import build_stage_response
from config import llm_configured
from llm_client import generate_text
from services import evaluate_eligibility, filter_policies_by_career

GUIDE_KEYWORDS = ["준비", "완료", "체크리스트", "서류", "신고", "기한", "단계", "절차"]
POLICY_KEYWORDS = ["지원사업", "지원금", "정책", "신청자격", "보조금", "재창업", "취업", "지원 대상", "지원되는", "지원받"]
TRADE_KEYWORDS = ["판매", "팔", "가격", "얼마", "시세", "중고", "집기", "설비", "당근마켓", "매물"]
LISTING_KEYWORDS = ["글", "써줘", "작성", "올려줘", "포스팅"]
REFERENCE_WORDS = ["그거", "그것", "저거", "이거", "그 물건", "그 설비", "그거로"]


class SupervisorState(TypedDict):
    conn: Any
    tasks: list
    question: str
    user_id: str
    profile: dict
    focus: dict
    routes: list
    route_reason: str
    agent_results: list
    answer: str | None
    agent_name: str
    source_type: str
    source_ids: list
    actions: list


# ---------- 라우팅 ----------

def _keyword_routes(question: str):
    hits = []
    if any(k in question for k in GUIDE_KEYWORDS):
        hits.append("guide")
    if any(k in question for k in POLICY_KEYWORDS):
        hits.append("policy")
    if any(k in question for k in TRADE_KEYWORDS):
        hits.append("trade")
    return hits


def _llm_route(question: str):
    system_instruction = (
        "사용자 질문이 아래 세 카테고리 중 어디에 해당하는지 판단하세요. "
        "관련된 카테고리를 쉼표로 구분해 최대 2개까지만 출력하세요(다른 설명 금지).\n"
        "guide: 폐업 절차, 서류, 신고, 기한 등 업무 진행 관련 질문\n"
        "policy: 지원사업/지원금 신청 자격, 대상, 정책 추천 관련 질문\n"
        "trade: 중고 집기/설비 판매, 가격, 시세 관련 질문"
    )
    result = generate_text(question, system_instruction=system_instruction)
    if not (result["ok"] and result["text"]):
        return None
    text = result["text"].strip().lower()
    found = [c for c in ("guide", "policy", "trade") if c in text]
    return found or None


def route_question(state: SupervisorState) -> dict:
    question = state["question"]
    hits = _keyword_routes(question)
    if hits:
        reason = "키워드 매칭" if len(hits) == 1 else f"복합 질문 감지({'+'.join(hits)})"
        return {"routes": hits, "route_reason": reason}

    if llm_configured():
        llm_hits = _llm_route(question)
        if llm_hits:
            reason = "AI 판단" if len(llm_hits) == 1 else f"AI 판단(복합: {'+'.join(llm_hits)})"
            return {"routes": llm_hits, "route_reason": reason}

    return {"routes": ["guide"], "route_reason": "기본값"}


# ---------- 전문 에이전트 본체 (단일/복합 경로 공용) ----------

def _run_guide(state: SupervisorState) -> dict:
    response = build_stage_response(state["conn"], state["tasks"], state["question"], state["user_id"])
    return {
        "answer": response["answer"],
        "agent_name": "🧭 가이드 에이전트",
        "source_type": response.get("source_type", "none"),
        "source_ids": response.get("source_ids", []),
        "actions": response.get("actions", []),
    }


def _llm_policy_answer(question: str, grounding: str):
    system_instruction = (
        "당신은 폐업/재창업 소상공인에게 지원사업을 안내하는 상담원입니다. "
        "아래 '후보 지원사업' 목록에 없는 사실을 지어내지 마세요. "
        "적합도 점수와 상태를 참고해 이해하기 쉽게 2~4문장으로 답하세요."
    )
    prompt = f"사용자 질문: {question}\n\n후보 지원사업:\n{grounding}"
    result = generate_text(prompt, system_instruction=system_instruction)
    if result["ok"] and result["text"]:
        return result["text"].strip()
    return None


def _run_policy(state: SupervisorState) -> dict:
    conn = state["conn"]
    profile = state["profile"]
    agent_name = "📋 정책상담 에이전트"

    policies = services.get_reviewed_policies(conn)
    policies = filter_policies_by_career(policies, profile.get("career_path"))

    scored = []
    for p in policies:
        label = evaluate_eligibility(profile, p["eligibility_rules"])
        score = analytics.score_policy_fit(profile, p["eligibility_rules"])
        scored.append((score, label, p))
    scored.sort(key=lambda x: -x[0])
    top = scored[:3]

    if not top:
        return {
            "answer": "현재 진로 기준으로 등록된 지원사업이 없어요. '지원정책' 화면에서 진로를 바꿔보시거나 잠시 후 다시 확인해주세요.",
            "agent_name": agent_name, "source_type": "none", "source_ids": [], "actions": [],
        }

    grounding = "\n".join(
        f"- {p['title']}: 적합도 {score}/100({label}), 마감 {p.get('application_deadline') or '상시/미정'}"
        for score, label, p in top
    )

    answer = _llm_policy_answer(state["question"], grounding) if llm_configured() else None
    if not answer:
        lines = ["관련도 높은 지원사업을 찾았어요:"] + [
            f"· {p['title']} — 적합도 {score}/100({label})" for score, label, p in top
        ]
        answer = "\n".join(lines)

    answer += "\n\n*'지원정책' 화면에서 상세 조건을 확인하고 관심 사업으로 등록할 수 있어요.*"
    return {"answer": answer, "agent_name": agent_name, "source_type": "local", "source_ids": [], "actions": []}


def _run_trade_price(conn, item: dict, agent_name: str) -> dict:
    predicted = pricing_model.predict_price(
        conn, item.get("category"), item.get("used_period"), item.get("condition")
    )
    if predicted.get("ok"):
        low, high = predicted["price_range"]
        answer = (
            f"'{item['name']}'의 AI 추천 가격은 약 {predicted['predicted_price']:,}원"
            f"(유사 사례 {low:,}~{high:,}원, {predicted['sample_size']}건 기준)입니다."
        )
    else:
        answer = f"'{item['name']}'은(는) {predicted.get('message') or '추천가를 계산할 정보가 부족합니다.'}"
    answer += "\n\n*판매 글까지 필요하면 '~글 써줘'처럼 요청해보세요. '중고품 관리' 화면에서도 바로 만들 수 있어요.*"
    return {"answer": answer, "agent_name": agent_name, "source_type": "local", "source_ids": [], "actions": []}


def _run_trade_listing(conn, item: dict, agent_name: str) -> dict:
    """거래상담 에이전트가 trade_graph(가격추정→글초안→채널안내) 에이전트를 직접
    호출해 판매 글까지 채팅에서 완성한다 — 화면 안내를 넘어선 실제 에이전트 위임."""
    trade_result = trade_graph.run_trade(conn, item, style="short")
    conn.execute("UPDATE equipment SET draft=? WHERE id=?", (trade_result["draft"], item["id"]))
    conn.commit()

    predicted = trade_result["predicted_price"]
    price_line = f" (AI 추천가 {predicted['predicted_price']:,}원)" if predicted.get("ok") else ""
    answer = (
        f"'{item['name']}' 판매 글을 작성했어요{price_line}.\n\n"
        f"{trade_result['draft']}\n\n{trade_result['channel_tip']}\n\n"
        "*'중고품 관리' 화면에도 저장해뒀어요. 다른 스타일이 필요하면 그 화면에서 다시 만들 수 있어요.*"
    )
    return {
        "answer": answer,
        "agent_name": f"{agent_name} → 거래 에이전트 위임",
        "source_type": "local", "source_ids": [], "actions": [],
    }


def _match_equipment(items: list, question: str, focus_equipment_id=None) -> list:
    """물품명 전체가 질문에 그대로 들어있으면 그것만 쓴다. 아니면 물품명을
    구분자로 쪼갠 토큰(2글자 이상) 중 하나라도 질문에 포함되면 후보로 삼는다
    — '포스기+카드단말기'가 등록돼 있을 때 '포스기 얼마예요?'처럼 줄여
    묻는 자연스러운 질문도 인식하기 위함. 물품명이 전혀 안 나오고 '그거'류
    지시어만 있으면, 직전 턴에서 다루던 물품(focus_equipment_id)을 이어받는다
    — 짧은 대화 맥락 기억."""
    exact = [it for it in items if it.get("name") and it["name"] in question]
    if exact:
        return exact

    candidates = []
    for it in items:
        name = it.get("name") or ""
        tokens = [t for t in re.split(r"[\s+/·,()]+", name) if len(t) >= 2]
        if any(t in question for t in tokens):
            candidates.append(it)
    if candidates:
        return candidates

    if focus_equipment_id and any(k in question for k in REFERENCE_WORDS):
        focused = [it for it in items if it.get("id") == focus_equipment_id]
        if focused:
            return focused

    return []


def _run_trade(state: SupervisorState) -> dict:
    conn = state["conn"]
    question = state["question"]
    agent_name = "💰 거래상담 에이전트"
    focus_equipment_id = (state.get("focus") or {}).get("equipment_id")

    items = services.get_user_equipment(conn, state["user_id"])
    matches = _match_equipment(items, question, focus_equipment_id)

    if len(matches) == 1:
        item = matches[0]
        if any(k in question for k in LISTING_KEYWORDS):
            result = _run_trade_listing(conn, item, agent_name)
        else:
            result = _run_trade_price(conn, item, agent_name)
        result["_focus_equipment_id"] = item["id"]
        return result

    if not items:
        answer = "등록된 중고 집기가 아직 없어요. '중고품 관리' 화면에서 먼저 물품을 등록해주세요."
    else:
        names = ", ".join(it["name"] for it in items if it.get("name"))
        answer = f"어떤 물품이 궁금하신가요? 등록된 물품: {names}"
    return {"answer": answer, "agent_name": agent_name, "source_type": "none", "source_ids": [], "actions": []}


_RUNNERS = {"guide": _run_guide, "policy": _run_policy, "trade": _run_trade}


# ---------- 그래프 노드 ----------

def _collect_focus(results: list) -> dict:
    """_run_trade가 물품을 확실히 특정했을 때만 focus를 갱신한다(_focus_equipment_id
    키 존재 여부로 판단). 특정 실패(되묻기) 시에는 이전 focus를 그대로 둔다."""
    for r in results:
        if "_focus_equipment_id" in r:
            return {"equipment_id": r["_focus_equipment_id"]}
    return {}


def dispatch(state: SupervisorState) -> dict:
    """단일 도메인이든(routes 1개) 복합 질문이든(routes 여러 개) 이 노드
    하나로 처리한다 — 예전엔 dispatch_guide/policy/trade/compound 4개가
    거의 같은 패턴(_RUNNERS 호출 후 agent_results로 감싸기)을 중복해서
    구현했었다."""
    results = [_RUNNERS[route](state) for route in state["routes"]]
    update = {"agent_results": results}
    focus = _collect_focus(results)
    if focus:
        update["focus"] = focus
    return update


def _llm_synthesize(question: str, results: list):
    system_instruction = (
        "여러 전문 에이전트가 각자 답변한 내용을 하나의 자연스러운 답변으로 정리하세요. "
        "각 에이전트가 제공한 사실 외의 내용을 추가하거나 지어내지 마세요. "
        "핵심만 4~6문장 이내로 정리하세요."
    )
    parts = "\n\n".join(f"[{r['agent_name']}]\n{r['answer']}" for r in results)
    prompt = f"사용자 질문: {question}\n\n에이전트별 답변:\n{parts}\n\n위 내용을 하나의 답변으로 합쳐주세요."
    result = generate_text(prompt, system_instruction=system_instruction)
    if result["ok"] and result["text"]:
        return result["text"].strip()
    return None


def synthesize(state: SupervisorState) -> dict:
    results = state.get("agent_results") or []
    if not results:
        return {
            "answer": "답변을 만들 수 없습니다. 다시 시도해주세요.",
            "agent_name": "", "source_type": "none", "source_ids": [], "actions": [],
        }

    if len(results) == 1:
        r = results[0]
        return {
            "answer": r["answer"], "agent_name": r["agent_name"], "source_type": r["source_type"],
            "source_ids": r.get("source_ids", []), "actions": r.get("actions", []),
        }

    combined_agent_name = " + ".join(r["agent_name"] for r in results)
    merged_source_ids, merged_actions = [], []
    any_local = False
    any_web_search = False
    for r in results:
        merged_source_ids.extend(r.get("source_ids") or [])
        merged_actions.extend(r.get("actions") or [])
        if r.get("source_type") == "local":
            any_local = True
        elif r.get("source_type") == "web_search":
            any_web_search = True

    combined = _llm_synthesize(state["question"], results) if llm_configured() else None
    if not combined:
        combined = "\n\n".join(f"**[{r['agent_name']}]**\n{r['answer']}" for r in results)

    # 일부만 웹검색(미검증)으로 답했어도 "local"로 합쳐버리면 화면에서 미검토
    # 경고가 사라진다. 하나라도 웹검색이 섞였으면 전체를 web_search로 표시해
    # app.py가 반드시 미검증 안내를 붙이게 한다(local 판정보다 우선).
    merged_source_type = "web_search" if any_web_search else ("local" if any_local else results[0]["source_type"])

    return {
        "answer": combined, "agent_name": combined_agent_name,
        "source_type": merged_source_type,
        "source_ids": merged_source_ids, "actions": merged_actions,
    }


def _build_graph():
    workflow = StateGraph(SupervisorState)
    workflow.add_node("route_question", route_question)
    workflow.add_node("dispatch", dispatch)
    workflow.add_node("synthesize", synthesize)
    workflow.add_edge(START, "route_question")
    workflow.add_edge("route_question", "dispatch")
    workflow.add_edge("dispatch", "synthesize")
    workflow.add_edge("synthesize", END)
    return workflow.compile()


_GRAPH = _build_graph()


def run_supervisor(conn, tasks, question, user_id, profile, focus: dict = None) -> dict:
    """focus: 직전 턴에서 다루던 대상을 기억하는 짧은 대화 맥락(현재는
    {"equipment_id": int}만 씀). 호출부가 세션별로 들고 있다가 매 턴 넘기고,
    반환된 focus를 다음 턴 호출 때 다시 넘기면 '그거 얼마야?' 같은 후속 질문을
    이어받을 수 있다."""
    initial: SupervisorState = {
        "conn": conn, "tasks": tasks, "question": question, "user_id": user_id, "profile": profile,
        "focus": focus or {}, "routes": [], "route_reason": "", "agent_results": [],
        "answer": None, "agent_name": "", "source_type": "none", "source_ids": [], "actions": [],
    }
    result = _GRAPH.invoke(initial)
    return {
        "answer": result.get("answer") or "답변을 만들 수 없습니다. 다시 시도해주세요.",
        "agent_name": result.get("agent_name", ""),
        "routes": result.get("routes", []),
        "route_reason": result.get("route_reason", ""),
        "source_type": result.get("source_type", "none"),
        "source_ids": result.get("source_ids", []),
        "actions": result.get("actions", []),
        "focus": result.get("focus") or {},
    }
