"""
기업마당 지원사업정보 API 호출 뼈대 (seoul_restaurant_api_data_guide.md 2.1절 기준).

BIZINFO_API_KEY가 없으면 실제 호출을 시도하지 않고 그 사실을 명확히 반환한다
(호출 실패를 감추거나 대신 데이터를 지어내지 않는다).

주의: 기업마당 API의 정확한 응답 필드명은 아직 검증되지 않았다(가이드에도 미확정으로
명시됨). 그래서 필드명을 추측해 매핑하지 않고, 응답 item의 원문 태그를 그대로
dict로 보존한다. 실제 키 발급 후 응답을 받아보고 정확한 필드명으로 매핑을
업데이트해야 한다.
"""
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from config import BIZINFO_API_KEY, bizinfo_configured

BIZINFO_ENDPOINT = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"


def fetch_bizinfo_policies(keyword: str = "폐업", page_unit: int = 10, page_index: int = 1) -> dict:
    """반환 형식: {"configured": bool, "items": list[dict], "message": str | None}"""
    if not bizinfo_configured():
        return {
            "configured": False,
            "items": [],
            "message": "BIZINFO_API_KEY가 설정되지 않아 호출하지 않았습니다. .env.example을 참고해 키를 설정하세요.",
        }

    params = {
        "crtfcKey": BIZINFO_API_KEY,
        "dataType": "xml",
        "hashtags": keyword,
        "pageUnit": page_unit,
        "pageIndex": page_index,
    }
    url = f"{BIZINFO_ENDPOINT}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read()
    except urllib.error.URLError as exc:
        return {"configured": True, "items": [], "message": f"API 호출 실패: {exc}"}

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return {"configured": True, "items": [], "message": f"응답 파싱 실패(필드명 확인 필요): {exc}"}

    items = [
        {child.tag: (child.text or "").strip() for child in item}
        for item in root.iter("item")
    ]
    return {"configured": True, "items": items, "message": None}
