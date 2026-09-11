"""
공공데이터포털(data.go.kr) 지원정책 API 호출 뼈대.

bizinfo_client.py와 같은 원칙: 키가 없으면 호출을 시도하지 않고 그 사실을 명확히
반환하며, 확인되지 않은 데이터를 지어내지 않는다.

주의: data.go.kr은 "활용신청"한 API마다 서로 다른 엔드포인트/서비스키를 발급한다.
지금 이 파일은 어떤 구체적인 지원정책 API를 신청했는지(엔드포인트 경로, 요청
파라미터명, 응답 필드명)가 아직 확인되지 않아 실제 호출부를 채우지 않았다.
URL을 추측해 넣지 않는다 — data.go.kr 마이페이지의 "개발계정 상세보기"에서 발급된
End Point와 활용가이드(Swagger/명세서)를 확인한 뒤 DATAGO_ENDPOINT와 요청/응답
처리 로직을 bizinfo_client.py 패턴대로 채워 넣으면 된다.
"""
from config import DATA_GO_KR_API_KEY, datago_configured

# TODO: data.go.kr에서 실제 활용신청한 지원정책 API의 End Point로 교체.
DATAGO_ENDPOINT = None


def fetch_datago_policies(keyword: str = "폐업", page: int = 1) -> dict:
    """반환 형식: {"configured": bool, "items": list[dict], "message": str | None}"""
    if not datago_configured():
        return {
            "configured": False,
            "items": [],
            "message": "DATA_GO_KR_API_KEY가 설정되지 않아 호출하지 않았습니다. .env.example을 참고해 키를 설정하세요.",
        }

    if not DATAGO_ENDPOINT:
        return {
            "configured": True,
            "items": [],
            "message": (
                "DATA_GO_KR_API_KEY는 설정됐지만 실제 API 엔드포인트가 아직 "
                "채워지지 않았습니다. data.go.kr에서 활용신청한 지원정책 API의 "
                "End Point/명세를 확인해 datago_client.py의 DATAGO_ENDPOINT와 "
                "호출 로직을 채워주세요."
            ),
        }

    # TODO: 실제 엔드포인트가 정해지면 bizinfo_client.fetch_bizinfo_policies처럼
    # urllib.request로 호출하고, 응답 필드명을 검증한 뒤에만 매핑한다.
    raise NotImplementedError("DATAGO_ENDPOINT 확정 후 구현 필요")
