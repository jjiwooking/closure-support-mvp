"""
AI 처리 설계(팀플.md 7장)의 규칙 기반 구현 + 실제 LLM 연동.

각 단계(준비/진행/후)에는 챗봇이 하나씩 있고, 업무를 먼저 고를 필요 없이
그 단계에 속한 모든 업무의 근거(적용 이유·기한·서류 안내)를 한꺼번에 모아
LLM에 건네 질문과 관련된 부분 위주로 답하게 한다.

LLM_API_KEY가 없거나 호출이 실패하면 모아둔 근거를 그대로 보여줘 답변이
항상 가능하게 한다(API 장애에도 기본 안내는 계속 제공되어야 한다는 검수
기준을 만족).
"""

from config import llm_configured
from llm_client import generate_text


def _fallback_response():
    return {
        "answer": "근거를 확인할 수 없어 답변을 표시할 수 없습니다. 공식 문의 경로를 이용해주세요.",
        "source_ids": [],
        "actions": [],
    }


def validate_response(conn, response: dict) -> dict:
    """서버 측 검증: 출처 ID가 실존하는지, 액션이 허용된 형식인지 확인한다."""
    required_keys = {"answer", "source_ids", "actions"}
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


def _llm_explain(question: str, grounding_context: str):
    """단계 안의 모든 업무 근거를 한꺼번에 주고, 질문과 관련된 업무 위주로
    답하도록 LLM에 요청한다. 실패하면 None을 반환해 근거 텍스트 그대로
    폴백하게 한다."""
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
        return _fallback_response()

    grounding_context = "\n\n".join(blocks)

    answer = grounding_context
    if llm_configured():
        llm_text = _llm_explain(question, grounding_context)
        if llm_text:
            answer = llm_text
        else:
            answer = f"{grounding_context}\n\n*(LLM 응답을 받지 못해 기본 정보를 그대로 보여드립니다.)*"

    response = {"answer": answer, "source_ids": source_ids, "actions": actions}
    return validate_response(conn, response)
