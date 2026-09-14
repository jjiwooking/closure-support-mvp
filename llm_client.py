"""
LLM 호출 뼈대. 현재는 Gemini REST API만 지원한다(LLM_PROVIDER="gemini").
키가 없거나 호출이 실패하면 예외를 삼키지 않고 명확한 오류를 반환해서,
호출부(coaching.py, guide_graph.py, rag_store.py)가 규칙 기반 문구로 안전하게
폴백할 수 있게 한다.

이 모듈은 어떤 UI 프레임워크도 몰라야 한다 — services.py/analytics.py/
guide_graph.py 등 나머지 로직 계층과 마찬가지로, 지금의 app.py(Streamlit)를
나중에 다른 프레임워크로 바꾸더라도 이 파일은 손댈 필요가 없어야 한다는
원칙을 지킨다.

할당량 보호: generate_text/generate_text_with_search/generate_embedding
세 호출 모두 호출 수 상한을 공유한다. 이 카운터를 "어디에 저장할지"는
실행 환경마다 다르므로(Streamlit 세션, 웹 요청 컨텍스트 등) 직접 정하지
않고 set_quota_backend()로 주입받는다 — 기본값은 프로세스 전역 카운터라
오프라인 스크립트에서 이 모듈만 불러 써도 바로 동작한다. app.py는
시작할 때 st.session_state 기반 카운터를 넣어, 로그인으로 여러 사용자가
한 프로세스를 같이 써도 한 사용자가 다른 사용자 몫을 깎아먹지 않게 한다.
상한에 도달하면 API를 부르지 않고 바로 실패를 반환해 호출부가 조용히
폴백하게 한다(검수기준 "API 장애에도 기본 안내 제공"과 같은 취지).
"""
import base64
import json
import urllib.error
import urllib.request

from config import EMBEDDING_MODEL, LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, llm_configured

GEMINI_ENDPOINT_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_EMBED_ENDPOINT_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"

MAX_LLM_CALLS_PER_SESSION = 60

_fallback_call_count = 0  # 기본 카운터: set_quota_backend()를 아무도 안 부르면 이걸 쓴다.


def _default_get_count() -> int:
    return _fallback_call_count


def _default_increment() -> None:
    global _fallback_call_count
    _fallback_call_count += 1


_get_count = _default_get_count
_increment_count = _default_increment


def set_quota_backend(get_count_fn, increment_fn) -> None:
    """호출 횟수 카운터의 저장 위치를 갈아끼운다. get_count_fn()은 현재
    카운트(int)를, increment_fn()은 그 카운트를 1 늘리는 부수효과를 낸다.
    app.py가 Streamlit 세션별 카운터를 여기에 연결한다. 이 함수를 아무도
    호출하지 않으면(예: 이 모듈만 불러 쓰는 오프라인 스크립트) 프로세스
    전역 폴백 카운터가 그대로 쓰인다."""
    global _get_count, _increment_count
    _get_count = get_count_fn
    _increment_count = increment_fn


def _quota_available() -> bool:
    return _get_count() < MAX_LLM_CALLS_PER_SESSION


def _record_call():
    _increment_count()


def _post_gemini(url: str, body: dict) -> dict:
    """공통 POST 호출부. 반환 형식: {"ok": bool, "data": dict | None, "error": str | None}"""
    if not llm_configured():
        return {"ok": False, "data": None, "error": "LLM_PROVIDER/LLM_API_KEY가 설정되지 않았습니다."}

    if LLM_PROVIDER != "gemini":
        return {"ok": False, "data": None, "error": f"미지원 LLM_PROVIDER: {LLM_PROVIDER} (현재 gemini만 지원)"}

    if not _quota_available():
        return {"ok": False, "data": None, "error": "이 세션의 LLM 호출 상한에 도달했습니다."}

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": LLM_API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        _record_call()
        return {"ok": False, "data": None, "error": f"Gemini API 오류({exc.code}): {detail}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "data": None, "error": f"Gemini API 연결 실패: {exc}"}
    except TimeoutError:
        # urlopen(timeout=20)이 연결 자체가 아니라 응답을 읽는 도중(response.begin())
        # 타임아웃되면 URLError로 감싸지지 않고 순수 TimeoutError가 그대로 올라온다.
        # 이걸 못 잡으면 호출부가 폴백할 기회 없이 요청 전체가 처리되지 않은 예외로 죽는다.
        return {"ok": False, "data": None, "error": "Gemini API 응답 시간 초과"}

    _record_call()
    return {"ok": True, "data": data, "error": None}


def _call_gemini(body: dict) -> dict:
    return _post_gemini(GEMINI_ENDPOINT_TMPL.format(model=LLM_MODEL), body)


def _extract_text(data: dict):
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


def _extract_citations(data: dict):
    """구글 검색 그라운딩 사용 시 응답에 포함되는 출처 링크를 뽑아낸다."""
    citations = []
    try:
        chunks = data["candidates"][0]["groundingMetadata"]["groundingChunks"]
    except (KeyError, IndexError, TypeError):
        return citations
    for chunk in chunks:
        web = chunk.get("web") or {}
        uri = web.get("uri")
        if uri:
            citations.append({"uri": uri, "title": web.get("title") or uri})
    return citations


def generate_text(prompt: str, system_instruction: str = None) -> dict:
    """반환 형식: {"ok": bool, "text": str | None, "error": str | None}"""
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    result = _call_gemini(body)
    if not result["ok"]:
        return {"ok": False, "text": None, "error": result["error"]}

    text = _extract_text(result["data"])
    if text is None:
        return {"ok": False, "text": None, "error": "Gemini 응답 형식을 해석할 수 없습니다."}

    return {"ok": True, "text": text, "error": None}


def generate_text_with_search(prompt: str, system_instruction: str = None) -> dict:
    """구글 검색 그라운딩을 켜고 호출한다. 로컬에 등록된 근거가 전혀 없을 때만
    쓰는 보조 수단이며, 검색 결과는 사람이 검토한 자료가 아니므로 호출부가
    반드시 '확인 필요' 등 미검증 표시를 답변에 붙여야 한다.
    반환 형식: {"ok": bool, "text": str | None, "citations": list, "error": str | None}"""
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    result = _call_gemini(body)
    if not result["ok"]:
        return {"ok": False, "text": None, "citations": [], "error": result["error"]}

    text = _extract_text(result["data"])
    if text is None:
        return {"ok": False, "text": None, "citations": [], "error": "Gemini 응답 형식을 해석할 수 없습니다."}

    return {"ok": True, "text": text, "citations": _extract_citations(result["data"]), "error": None}


def classify_product_image(image_bytes: bytes, mime_type: str, prompt: str = None) -> dict:
    """중고 집기 사진을 보고 품목/브랜드/추정 상태를 설명하게 한다(설계문서의
    '가게에서 쓰는 전자제품 사진 판독'에 해당). 새 API 키가 필요 없다 — 이미
    쓰고 있는 Gemini LLM_API_KEY로 그대로 동작한다. 결과는 사람이 검토 없이
    바로 믿을 정보가 아니므로, 호출부가 사용자에게 확인을 받도록 안내해야 한다.
    반환 형식: {"ok": bool, "text": str | None, "error": str | None}"""
    default_prompt = (
        "이 사진 속 중고 매장 집기/전자제품을 설명해주세요. 품목명, 추정 브랜드나 "
        "모델(확실하지 않으면 '확인 필요'), 눈에 보이는 상태나 하자를 2~3문장으로 "
        "답하세요. 확실하지 않은 내용은 '확인 필요'라고 표시하세요."
    )
    body = {
        "contents": [
            {
                "parts": [
                    {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}},
                    {"text": prompt or default_prompt},
                ]
            }
        ]
    }

    result = _call_gemini(body)
    if not result["ok"]:
        return {"ok": False, "text": None, "error": result["error"]}

    text = _extract_text(result["data"])
    if text is None:
        return {"ok": False, "text": None, "error": "Gemini 응답 형식을 해석할 수 없습니다."}

    return {"ok": True, "text": text, "error": None}


def _extract_embedding(data: dict):
    try:
        return data["embedding"]["values"]
    except (KeyError, TypeError):
        return None


def generate_embedding(text: str) -> dict:
    """가이드 RAG 검색용 임베딩 벡터를 생성한다.
    반환 형식: {"ok": bool, "values": list[float] | None, "error": str | None}"""
    url = GEMINI_EMBED_ENDPOINT_TMPL.format(model=EMBEDDING_MODEL)
    body = {"content": {"parts": [{"text": text}]}}

    result = _post_gemini(url, body)
    if not result["ok"]:
        return {"ok": False, "values": None, "error": result["error"]}

    values = _extract_embedding(result["data"])
    if values is None:
        return {"ok": False, "values": None, "error": "Gemini 임베딩 응답 형식을 해석할 수 없습니다."}

    return {"ok": True, "values": values, "error": None}
