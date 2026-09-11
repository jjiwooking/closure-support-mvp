"""
AI 처리 설계(팀플.md 7장)의 규칙 기반 구현 + 실제 LLM 연동.

실제 답변 생성은 guide_graph.py의 LangGraph 서브그래프(guide_search →
answer_generate → checklist_suggest)가 담당한다. 이 모듈은 그 결과를
서버 측에서 검증(validate_response)하는 얇은 진입점 역할만 한다.

build_stage_response는 응답에 source_type("local" | "web_search" | "none")을
항상 채워 반환하므로, 화면(app.py)에서 검증된 정보와 미검토 실시간 검색
결과를 구조적으로 구분해 표시할 수 있다.
"""

import guide_graph


def _fallback_response():
    return {
        "answer": "근거를 확인할 수 없어 답변을 표시할 수 없습니다. 공식 문의 경로를 이용해주세요.",
        "source_ids": [],
        "actions": [],
        "source_type": "none",
    }


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


def build_stage_response(conn, tasks: list, question: str, user_id: str) -> dict:
    """tasks(같은 단계에 속한 업무 목록)의 근거를 바탕으로 질문에 답한다.
    tasks가 비어있지 않다고 가정하므로(호출부에서 이미 확인), tasks[0]의
    stage 값을 그대로 이 요청의 단계로 쓴다."""
    stage_key = tasks[0]["stage"]
    response = guide_graph.run_guide(conn, tasks, question, stage_key, user_id)
    return validate_response(conn, response)
