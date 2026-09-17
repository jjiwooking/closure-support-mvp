"""
공공데이터포털(data.go.kr, odcloud) "대한민국 공공서비스 정보" API 호출
(Swagger: https://infuser.odcloud.kr/api/stages/44436/api-docs 기준).

bizinfo_client.py와 같은 원칙: 키가 없으면 호출을 시도하지 않고 그 사실을 명확히
반환하며, 확인되지 않은 데이터를 지어내지 않는다.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from config import DATA_GO_KR_API_KEY, datago_configured

DATAGO_ENDPOINT = "https://api.odcloud.kr/api/gov24/v3/serviceList"


def fetch_datago_policies(keyword: str = "폐업", page: int = 1, per_page: int = 10) -> dict:
    """반환 형식: {"configured": bool, "items": list[dict], "message": str | None}"""
    if not datago_configured():
        return {
            "configured": False,
            "items": [],
            "message": "DATA_GO_KR_API_KEY가 설정되지 않아 호출하지 않았습니다. .env.example을 참고해 키를 설정하세요.",
        }

    params = {
        "page": page,
        "perPage": per_page,
        "returnType": "JSON",
        "cond[서비스명::LIKE]": keyword,
    }
    url = f"{DATAGO_ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Infuser {DATA_GO_KR_API_KEY}"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"configured": True, "items": [], "message": f"API 호출 실패({exc.code}): {detail}"}
    except urllib.error.URLError as exc:
        return {"configured": True, "items": [], "message": f"API 호출 실패: {exc}"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return {"configured": True, "items": [], "message": f"응답 파싱 실패(필드명 확인 필요): {exc}"}

    items = payload.get("data", [])
    return {"configured": True, "items": items, "message": None}
