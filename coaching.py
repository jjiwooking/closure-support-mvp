"""
AI 처리 설계(팀플.md 7장)의 규칙 기반 구현 + 실제 LLM 연동.

각 단계(준비/진행/후)에는 챗봇이 하나씩 있고, 업무를 먼저 고를 필요 없이
그 단계에 속한 모든 업무의 근거(적용 이유·기한·서류 안내)를 한꺼번에 모아
LLM에 건네 질문과 관련된 부분 위주로 답하게 한다.

LLM_API_KEY가 없거나 호출이 실패하면 모아둔 근거를 그대로 보여줘 답변이
항상 가능하게 한다(API 장애에도 기본 안내는 계속 제공되어야 한다는 검수
기준을 만족).

등록된 근거가 전혀 없는 경우에는 구글 검색 그라운딩으로 보조 답변을
시도한다. 이 결과는 사람이 검토한 자료가 아니므로 반드시 "확인 필요" 및
출처 링크와 함께 미검증 정보임을 밝힌다(팀플.md의 "미검토 자료의 중요
조건을 확정 안내하지 않는다" 원칙). build_stage_response는 응답에
source_type("local" | "web_search" | "none")을 항상 채워 반환하므로,
화면(app.py)에서 검증된 정보와 미검토 실시간 검색 결과를 구조적으로
구분해 표시할 수 있다.

할당량 보호: 같은 근거+질문 조합은 프로세스 안에서 캐시해 재호출하지
않고(functools.lru_cache), 프로세스당 실제 API 호출 수에도 상한을 둬서
같은 세션에서 질문을 계속 바꿔가며 물어봐도 할당량이 무한정 소모되지
않게 한다. 상한에 도달하면 API를 부르지 않고 조용히 근거 텍스트로
폴백한다(이것도 검수기준의 "API 장애에도 기본 안내 제공" 취지와 같다).
"""

import functools

from config import llm_configured
from llm_client import generate_text, generate_text_with_search

MAX_LLM_CALLS_PER_PROCESS = 30

_llm_call_count = 0


def _quota_available() -> bool:
    return _llm_call_count < MAX_LLM_CALLS_PER_PROCESS


def _record_call():
    global _llm_call_count
    _llm_call_count += 1


def _fallback_response():
    return {
        "answer": "근거를 확인할 수 없어 답변을 표시할 수 없습니다. 공식 문의 경로를 이용해주세요.",
        "source_ids": [],
        "actions": [],
        "source_type": "none",
    }


@functools.lru_cache(maxsize=128)
def _web_search_answer(question: str):
    """등록된 근거가 없을 때만 쓰는 보조 수단. 검토되지 않은 실시간 검색
    결과이므로 항상 미확인 표시와 출처 링크를 붙인다. 실패하면 None.
    동일 질문 재요청은 캐시로 처리되어 API를 다시 부르지 않는다."""
    if not _quota_available():
        return None
    _record_call()

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


def validate_response(conn, response: dict) -> dict:
    """서버 측 검증: 출처 ID가 실존하는지, 액션이 허용된 형식인지 확인한다."""
    required_keys = {"answer", "source_ids", "actions", "source_type"}
    if not required_keys.issubset(response.keys()):
        return _fallback_response()

    for source_id in response["source_ids"]:
        row = conn.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return _fallback_response()

    for action in response["actions"]:
        if action.get("type") != "open_official_link" or not action.get("link_id"):
            return _fallback_response()

    return response


@functools.lru_cache(maxsize=128)
def _llm_explain(question: str, grounding_context: str):
    """단계 안의 모든 업무 근거를 한꺼번에 주고, 질문과 관련된 업무 위주로
    답하도록 LLM에 요청한다. 실패하면 None을 반환해 근거 텍스트 그대로
    폴백하게 한다. 동일한 (질문, 근거) 조합은 캐시되어 재호출하지 않는다."""
    if not _quota_available():
        return None
    _record_call()

    system_instruction = (
        "당신은 폐업을 준비하는 소상공인을 돕는 코칭 챗봇입니다. "
        "아래 '근거 정보'에는 이 단계에 속한 여러 업무의 사실이 각각 정리돼 있습니다. "
        "사용자 질문과 가장 관련 있는 업무를 찾아 그 내용 위주로 쉬운 한국어로 2~4문장으로 답하세요. "
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


def build_stage_response(conn, tasks: list, question: str) -> dict:
    """tasks(같은 단계에 속한 업무 목록)의 근거를 모두 모아 질문에 답한다."""
    blocks = []
    source_ids = []
    actions = []

    for t in tasks:
        source_id = t.get("source_id")
        if not source_id:
            continue
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not source:
            continue
        if source["id"] not in source_ids:
            source_ids.append(source["id"])

        docs = conn.execute(
            """
            SELECT dg.* FROM document_checks dc
            JOIN document_guides dg ON dc.document_guide_id = dg.id
            WHERE dc.user_task_id = ?
            """,
            (t["id"],),
        ).fetchall()
        doc_instructions = (
            "; ".join(d["instructions"] for d in docs) if docs else "등록된 서류 안내 없음(확인 필요)"
        )

        blocks.append(
            f"[업무: {t['task_title']}]\n"
            f"- 적용 이유: {t.get('applicability_rules') or '확인 필요'}\n"
            f"- 기한: {t.get('due_date') or '미정'} (근거: {t.get('deadline_rule') or '확인 필요'})\n"
            f"- 준비 서류: {doc_instructions}"
        )
        for d in docs:
            if d["issuance_link_id"]:
                actions.append({"type": "open_official_link", "link_id": d["issuance_link_id"]})
            if d["submission_link_id"]:
                actions.append({"type": "open_official_link", "link_id": d["submission_link_id"]})

    if not blocks:
        if llm_configured():
            search_text = _web_search_answer(question)
            if search_text:
                return {
                    "answer": search_text,
                    "source_ids": [],
                    "actions": [],
                    "source_type": "web_search",
                }
        return _fallback_response()

    grounding_context = "\n\n".join(blocks)

    answer = grounding_context
    source_type = "local"
    if llm_configured():
        llm_text = _llm_explain(question, grounding_context)
        if llm_text:
            answer = llm_text
        else:
            answer = f"{grounding_context}\n\n*(LLM 응답을 받지 못해 기본 정보를 그대로 보여드립니다.)*"

    response = {
        "answer": answer,
        "source_ids": source_ids,
        "actions": actions,
        "source_type": source_type,
    }
    return validate_response(conn, response)
