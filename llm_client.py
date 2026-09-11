"""
LLM 호출 뼈대. 현재는 Gemini REST API만 지원한다(LLM_PROVIDER="gemini").
키가 없거나 호출이 실패하면 예외를 삼키지 않고 명확한 오류를 반환해서,
호출부(coaching.py, guide_graph.py, rag_store.py)가 규칙 기반 문구로 안전하게
폴백할 수 있게 한다.

할당량 보호: generate_text/generate_text_with_search/generate_embedding
세 호출 모두 이 모듈의 프로세스당 호출 수 상한을 공유한다. 상한에 도달하면
API를 부르지 않고 바로 실패를 반환해 호출부가 조용히 폴백하게 한다(검수기준
"API 장애에도 기본 안내 제공"과 같은 취지).
"""
import json
import urllib.error
import urllib.request

from config import EMBEDDING_MODEL, LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, llm_configured

GEMINI_ENDPOINT_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_EMBED_ENDPOINT_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"

MAX_LLM_CALLS_PER_PROCESS = 30

_llm_call_count = 0


def _quota_available() -> bool:
    return _llm_call_count < MAX_LLM_CALLS_PER_PROCESS


def _record_call():
    global _llm_call_count
    _llm_call_count += 1


def _post_gemini(url: str, body: dict) -> dict:
    """공통 POST 호출부. 반환 형식: {"ok": bool, "data": dict | None, "error": str | None}"""
    if not llm_configured():
        return {"ok": False, "data": None, "error": "LLM_PROVIDER/LLM_API_KEY가 설정되지 않았습니다."}

    if LLM_PROVIDER != "gemini":
        return {"ok": False, "data": None, "error": f"미지원 LLM_PROVIDER: {LLM_PROVIDER} (현재 gemini만 지원)"}

    if not _quota_available():
        return {"ok": False, "data": None, "error": "프로세스당 LLM 호출 상한에 도달했습니다."}

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
