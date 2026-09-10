"""
AI 처리 설계(팀플.md 7장)의 규칙 기반 구현.

실제 LLM 호출 없이도 다음을 전부 실동작하게 만든다:
1. 의도 추출 (키워드 기반)
2. 해당 업무의 공식 근거(sources/document_guides)만 검색
3. 근거 범위 안에서 설명 생성 (템플릿 — 추후 LLM으로 교체 가능한 지점)
4. 출처 ID·허용 링크·출력 형식 서버 검증
5. 근거가 없으면 "확인 필요"로 표시 (지어내지 않음)

LLM_API_KEY가 설정되면(3번) 근거 문장을 그대로 보여주는 대신, 그 근거만 사용해
쉬운 말로 풀어 설명하도록 LLM에 요청한다. LLM 호출이 실패하거나 키가 없으면
근거 문장을 그대로 사용해 항상 답변 가능하게 한다(API 장애에도 기본 안내는
계속 제공되어야 한다는 검수 기준을 만족).
"""

from config import llm_configured
from llm_client import generate_text

INTENT_PATTERNS = [
    ("일정_변경", ["일정 변경", "날짜를 바꿔", "미루고 싶어", "당기고 싶어", "연기"]),
    ("서류_발급_경로", ["어디서 받", "발급", "어디서 해"]),
    ("작성_코칭", ["어떻게 써", "작성 도와", "작성 방법", "이 칸"]),
    ("보완_요청_설명", ["보완", "무슨 뜻이야", "안내문"]),
    ("완료_기록", ["했어", "완료했어", "끝냈어", "다 했"]),
    ("판매_글_생성", ["판매 글", "글 써줘", "팔고 싶어"]),
    ("정책_설명", ["지원사업", "지원금", "정책"]),
    ("상황_등록", ["상황을 알려", "정보를 입력", "등록할게"]),
    ("다음_할일", ["뭐부터", "다음은", "다음 할 일", "쉽게 설명"]),
]


def classify_intent(text: str) -> str:
    for intent, keywords in INTENT_PATTERNS:
        if any(k in text for k in keywords):
            return intent
    return "다음_할일"


def _fallback_response(task_id):
    return {
        "intent": "unknown",
        "task_id": task_id,
        "answer": "근거를 확인할 수 없어 답변을 표시할 수 없습니다. 공식 문의 경로를 이용해주세요.",
        "source_ids": [],
        "actions": [],
        "proposed_changes": [],
        "needs_confirmation": False,
    }


def validate_response(conn, response: dict) -> dict:
    """서버 측 검증: 출처 ID가 실존하는지, 액션이 허용된 형식인지 확인한다."""
    required_keys = {
        "intent", "task_id", "answer", "source_ids",
        "actions", "proposed_changes", "needs_confirmation",
    }
    if not required_keys.issubset(response.keys()):
        return _fallback_response(response.get("task_id"))

    for source_id in response["source_ids"]:
        row = conn.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return _fallback_response(response.get("task_id"))

    for action in response["actions"]:
        if action.get("type") != "open_official_link" or not action.get("link_id"):
            return _fallback_response(response.get("task_id"))

    return response


def _llm_explain(question: str, grounding_fact: str, task_title: str):
    """근거 문장만 재료로 주고 쉬운 말로 풀어달라고 LLM에 요청한다.
    실패하면 None을 반환해 호출부가 근거 문장 그대로 폴백하게 한다."""
    system_instruction = (
        "당신은 폐업을 준비하는 소상공인을 돕는 코칭 챗봇입니다. "
        "아래 '근거 정보'에 있는 사실만 사용해서 쉬운 한국어로 2~4문장으로 설명하세요. "
        "근거에 없는 서류명, 금액, 조건, 절차, 기한을 지어내지 마세요. "
        "근거만으로 답할 수 없는 부분은 '확인이 필요합니다'라고 답하세요."
    )
    prompt = (
        f"업무: {task_title}\n"
        f"사용자 질문: {question}\n"
        f"근거 정보: {grounding_fact}\n\n"
        "위 근거 정보만 바탕으로 사용자 질문에 답해주세요."
    )
    result = generate_text(prompt, system_instruction=system_instruction)
    if result["ok"] and result["text"]:
        return result["text"].strip()
    return None


def build_response(conn, intent: str, task_row: dict, question: str) -> dict:
    source_id = task_row.get("source_id")
    source = None
    if source_id:
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()

    if not source:
        return _fallback_response(task_row["id"])

    docs = conn.execute(
        """
        SELECT dg.* FROM document_checks dc
        JOIN document_guides dg ON dc.document_guide_id = dg.id
        WHERE dc.user_task_id = ?
        """,
        (task_row["id"],),
    ).fetchall()

    actions = []
    for d in docs:
        if d["issuance_link_id"]:
            actions.append({"type": "open_official_link", "link_id": d["issuance_link_id"]})
        if d["submission_link_id"]:
            actions.append({"type": "open_official_link", "link_id": d["submission_link_id"]})

    doc_instructions = "; ".join(d["instructions"] for d in docs) if docs else None

    # 이 templates 딕셔너리가 향후 실제 LLM 호출로 교체될 지점이다.
    templates = {
        "서류_발급_경로": doc_instructions or "등록된 서류 안내가 없습니다. 확인이 필요합니다.",
        "작성_코칭": "공식 사이트의 안내 문구를 그대로 따라 입력하세요. 항목별 작성 예시는 아직 등록되지 않았습니다(확인 필요).",
        "정책_설명": task_row.get("applicability_rules") or "확인 필요",
        "다음_할일": f"현재 업무는 '{task_row['task_title']}'이며, 기한은 {task_row.get('due_date') or '미정'}입니다.",
        "완료_기록": "완료 여부는 화면의 상태 변경 버튼으로 직접 기록해주세요. 자동으로 반영되지 않습니다.",
        "보완_요청_설명": "붙여넣은 안내문의 핵심 요구사항과 기한을 확인해 담당 업무에 반영하세요. (자동 해석 기능은 아직 연결되지 않았습니다)",
        "일정_변경": "일정 변경은 사용자가 직접 입력해야 하며, 확인 후 관련 업무의 기한이 갱신됩니다.",
        "판매_글_생성": "집기 판매 글은 '내 집기' 화면에서 생성할 수 있습니다.",
        "상황_등록": "현재 상황 정보는 이미 등록되어 있습니다. 변경이 필요하면 알려주세요.",
    }
    grounding_fact = templates.get(intent, "해당 질문에 대한 검토된 답변이 아직 준비되지 않았습니다. 확인이 필요합니다.")

    answer = grounding_fact
    if llm_configured():
        llm_text = _llm_explain(question, grounding_fact, task_row["task_title"])
        if llm_text:
            answer = llm_text
        else:
            answer = f"{grounding_fact}\n\n*(LLM 응답을 받지 못해 기본 안내로 대신합니다.)*"

    response = {
        "intent": intent,
        "task_id": task_row["id"],
        "answer": answer,
        "source_ids": [source["id"]],
        "actions": actions,
        "proposed_changes": [],
        "needs_confirmation": False,
    }
    return validate_response(conn, response)
