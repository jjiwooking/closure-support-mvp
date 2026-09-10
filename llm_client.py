"""
LLM 호출 뼈대. 현재는 Gemini REST API만 지원한다(LLM_PROVIDER="gemini").
키가 없거나 호출이 실패하면 예외를 삼키지 않고 명확한 오류를 반환해서,
호출부(coaching.py)가 규칙 기반 문구로 안전하게 폴백할 수 있게 한다.
"""
import json
import urllib.error
import urllib.request

from config import LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, llm_configured

GEMINI_ENDPOINT_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _call_gemini(body: dict) -> dict:
    """공통 호출부. 반환 형식: {"ok": bool, "data": dict | None, "error": str | None}"""
    if not llm_configured():
        return {"ok": False, "data": None, "error": "LLM_PROVIDER/LLM_API_KEY가 설정되지 않았습니다."}

    if LLM_PROVIDER != "gemini":
        return {"ok": False, "data": None, "error": f"미지원 LLM_PROVIDER: {LLM_PROVIDER} (현재 gemini만 지원)"}

    url = GEMINI_ENDPOINT_TMPL.format(model=LLM_MODEL)
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
        return {"ok": False, "data": None, "error": f"Gemini API 오류({exc.code}): {detail}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "data": None, "error": f"Gemini API 연결 실패: {exc}"}

    return {"ok": True, "data": data, "error": None}


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
