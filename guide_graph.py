"""
기능1(가이드) 챗봇을 위한 LangGraph 서브그래프. `coaching.build_stage_response`가
이 그래프를 호출해 실제 답변을 만든다(설계문서의 guide 서브그래프에 대응).

노드 흐름:
  guide_search      : 이 단계에 속한 업무들의 근거를 모으고, rag_store로 질문과
                       가장 관련 있는 근거만 추려낸다(임베딩 실패/미설정 시 이
                       단계 전체 근거로 폴백해 항상 답변 가능하게 한다).
  answer_generate    : LLM으로 근거 기반 답변 생성. 근거가 하나도 없으면 구글
                       검색 그라운딩으로 보조 답변(미검증)을 시도한다.
  checklist_suggest  : 이 단계 체크리스트가 전부 완료됐고 처분 안 된 집기가
                       있으면 중고거래 이동을 답변 끝에 자연스럽게 제안한다.
"""
import functools
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import rag_store
import services
from config import llm_configured
from llm_client import generate_text, generate_text_with_search


class GuideState(TypedDict):
    question: str
    tasks: list
    stage_key: str
    conn: Any
    user_id: str
    blocks: list
    grounding_context: str
    source_ids: list
    actions: list
    answer: str | None
    source_type: str
    suggest_trade: bool


def _task_block_text(task, docs):
    doc_instructions = (
        "; ".join(d["instructions"] for d in docs) if docs else "등록된 서류 안내 없음(확인 필요)"
    )
    return (
        f"[업무: {task['task_title']}]\n"
        f"- 적용 이유: {task.get('applicability_rules') or '확인 필요'}\n"
        f"- 기한: {task.get('due_date') or '미정'} (근거: {task.get('deadline_rule') or '확인 필요'})\n"
        f"- 준비 서류: {doc_instructions}"
    )


@functools.lru_cache(maxsize=128)
def _web_search_answer(question: str):
    """등록된 근거가 없을 때만 쓰는 보조 수단. 검토되지 않은 실시간 검색
    결과이므로 항상 미확인 표시와 출처 링크를 붙인다. 실패하면 None.
    동일 질문 재요청은 캐시로 처리되어 API를 다시 부르지 않는다."""
    system_instruction = (
        "당신은 한국 소상공인의 폐업 절차를 돕는 검색 도우미입니다. "
        "정부24, 국세청, 서울시, 기업마당 등 공식 정부/지자체 사이트 정보를 우선해서 찾으세요. "
        "확실하지 않은 내용은 반드시 '확인 필요'라고 표시하세요. "
        "2~4문장으로 간결하게 답하세요."
    )
    prompt = f"다음 질문에 대해 한국 공식 정부 사이트를 검색해서 답해주세요: {question}"
    result = generate_text_with_search(prompt, system_instruction=system_instruction)
    if not (result["ok"] and result["text"]):
        return None

    text = result["text"].strip()
    if result["citations"]:
        links = "\n".join(f"- [{c['title']}]({c['uri']})" for c in result["citations"][:3])
        text += "\n\n" + links
    return text


@functools.lru_cache(maxsize=128)
def _llm_explain(question: str, grounding_context: str):
    """질문과 관련된 근거를 이미 추려서 받아 그 내용 위주로 답하도록 LLM에
    요청한다. 실패하면 None을 반환해 근거 텍스트 그대로 폴백하게 한다.
    동일한 (질문, 근거) 조합은 캐시되어 재호출하지 않는다."""
    system_instruction = (
        "당신은 폐업을 준비하는 소상공인을 돕는 코칭 챗봇입니다. "
        "아래 '근거 정보'에는 이 단계에 속한 업무의 사실이 정리돼 있습니다. "
        "사용자 질문과 가장 관련 있는 내용 위주로 쉬운 한국어로 2~4문장으로 답하세요. "
        "여러 업무가 관련되면 업무명을 밝히며 구분해서 답해도 됩니다. "
        "근거에 없는 서류명, 금액, 조건, 절차, 기한을 지어내지 마세요. "
        "근거만으로 답할 수 없는 부분은 '확인이 필요합니다'라고 답하세요."
    )
    prompt = (
        f"사용자 질문: {question}\n\n"
        f"근거 정보:\n{grounding_context}\n\n"
        "위 근거 정보만 바탕으로 사용자 질문에 답해주세요."
    )
    result = generate_text(prompt, system_instruction=system_instruction)
    if result["ok"] and result["text"]:
        return result["text"].strip()
    return None


def guide_search(state: GuideState) -> dict:
    conn = state["conn"]
    tasks = state["tasks"]
    question = state["question"]

    blocks = []
    for t in tasks:
        source_id = t.get("source_id")
        if not source_id:
            continue
        source = conn.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone()
        if not source:
            continue

        docs = conn.execute(
            """
            SELECT dg.* FROM document_checks dc
            JOIN document_guides dg ON dc.document_guide_id = dg.id
            WHERE dc.user_task_id = ?
            """,
            (t["id"],),
        ).fetchall()

        actions = []
        for d in docs:
            if d["issuance_link_id"]:
                actions.append({"type": "open_official_link", "link_id": d["issuance_link_id"]})
            if d["submission_link_id"]:
                actions.append({"type": "open_official_link", "link_id": d["submission_link_id"]})

        blocks.append(
            {
                "task_id": t["id"],
                "source_id": source_id,
                "text": _task_block_text(t, docs),
                "actions": actions,
            }
        )

    if not blocks:
        return {"blocks": [], "grounding_context": "", "source_ids": [], "actions": []}

    selected = blocks
    hit_ids = rag_store.retrieve(
        question, [{"id": f"task_{b['task_id']}", "text": b["text"]} for b in blocks], top_k=3
    )
    if hit_ids:
        order = {int(h.split("_", 1)[1]): i for i, h in enumerate(hit_ids)}
        ranked = [b for b in blocks if b["task_id"] in order]
        if ranked:
            ranked.sort(key=lambda b: order[b["task_id"]])
            selected = ranked

    source_ids, actions = [], []
    for b in selected:
        if b["source_id"] not in source_ids:
            source_ids.append(b["source_id"])
        actions.extend(b["actions"])

    grounding_context = "\n\n".join(b["text"] for b in selected)
    return {
        "blocks": selected,
        "grounding_context": grounding_context,
        "source_ids": source_ids,
        "actions": actions,
    }


def answer_generate(state: GuideState) -> dict:
    question = state["question"]
    blocks = state["blocks"]

    if not blocks:
        if llm_configured():
            search_text = _web_search_answer(question)
            if search_text:
                return {"answer": search_text, "source_type": "web_search"}
        return {"answer": None, "source_type": "none"}

    grounding_context = state["grounding_context"]
    answer = grounding_context
    if llm_configured():
        llm_text = _llm_explain(question, grounding_context)
        if llm_text:
            answer = llm_text
        else:
            answer = f"{grounding_context}\n\n*(LLM 응답을 받지 못해 기본 정보를 그대로 보여드립니다.)*"
    return {"answer": answer, "source_type": "local"}


def checklist_suggest(state: GuideState) -> dict:
    if not state.get("answer"):
        return {}
    if not services.check_stage_complete(state["tasks"]):
        return {}
    if not services.should_suggest_trade(state["conn"], state["user_id"]):
        return {}

    suggestion = (
        "\n\n---\n💡 이 단계 체크리스트를 모두 완료하셨네요! "
        "아직 정리 안 된 집기가 있다면 '중고품 관리' 메뉴에서 바로 판매 글까지 만들어보세요."
    )
    return {"answer": state["answer"] + suggestion, "suggest_trade": True}


def _build_graph():
    workflow = StateGraph(GuideState)
    workflow.add_node("guide_search", guide_search)
    workflow.add_node("answer_generate", answer_generate)
    workflow.add_node("checklist_suggest", checklist_suggest)
    workflow.add_edge(START, "guide_search")
    workflow.add_edge("guide_search", "answer_generate")
    workflow.add_edge("answer_generate", "checklist_suggest")
    workflow.add_edge("checklist_suggest", END)
    return workflow.compile()


_GRAPH = _build_graph()


def run_guide(conn, tasks, question, stage_key, user_id) -> dict:
    initial: GuideState = {
        "question": question,
        "tasks": tasks,
        "stage_key": stage_key,
        "conn": conn,
        "user_id": user_id,
        "blocks": [],
        "grounding_context": "",
        "source_ids": [],
        "actions": [],
        "answer": None,
        "source_type": "none",
        "suggest_trade": False,
    }
    result = _GRAPH.invoke(initial)
    answer = result.get("answer")
    if not answer:
        return {
            "answer": "근거를 확인할 수 없어 답변을 표시할 수 없습니다. 공식 문의 경로를 이용해주세요.",
            "source_ids": [],
            "actions": [],
            "source_type": "none",
        }
    return {
        "answer": answer,
        "source_ids": result.get("source_ids", []),
        "actions": result.get("actions", []),
        "source_type": result.get("source_type", "none"),
    }
