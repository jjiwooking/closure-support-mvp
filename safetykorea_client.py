"""
제품안전정보센터(safetykorea.kr) 인증정보 조회 API 호출 뼈대.

주의(중요): 이 API는 "사진을 찍으면 제품을 알아서 인식"해주는 이미지 인식 API가
아니다. 모델명/인증번호 같은 식별 정보로 국내 안전인증 여부·정보를 조회하는
DB 조회 API다. 사진으로 제품을 식별하는 단계는 llm_client.classify_product_image
(Gemini 멀티모달)가 담당하고, 여기서는 그 결과로 얻은 모델명/제품명을 가지고
국내 인증정보를 한 번 더 확인하는 보조 단계로 쓴다.

bizinfo_client.py와 같은 원칙으로, 키가 없으면 호출하지 않고 그 사실을 명확히
반환하며, 실제 엔드포인트/응답 필드가 확인되지 않은 상태에서 데이터를 지어내지
않는다. URL은 openapi.safetykorea.kr 하위의 특정 서비스 경로여야 하는데, 정확한
경로/파라미터는 발급받은 개발가이드에서 확인해야 한다.
"""
from config import SAFETYKOREA_API_KEY, safetykorea_configured

# TODO: 발급받은 개발가이드의 실제 서비스 엔드포인트로 교체.
SAFETYKOREA_ENDPOINT = None


def lookup_certified_product(query: str) -> dict:
    """모델명 또는 인증번호로 국내 안전인증 정보를 조회한다.
    반환 형식: {"configured": bool, "items": list[dict], "message": str | None}"""
    if not safetykorea_configured():
        return {
            "configured": False,
            "items": [],
            "message": "SAFETYKOREA_API_KEY가 설정되지 않아 호출하지 않았습니다. .env.example을 참고해 키를 설정하세요.",
        }

    if not SAFETYKOREA_ENDPOINT:
        return {
            "configured": True,
            "items": [],
            "message": (
                "SAFETYKOREA_API_KEY는 설정됐지만 실제 API 엔드포인트가 아직 "
                "채워지지 않았습니다. 개발가이드에서 서비스 경로/파라미터를 확인해 "
                "safetykorea_client.py의 SAFETYKOREA_ENDPOINT와 호출 로직을 채워주세요."
            ),
        }

    # TODO: 실제 엔드포인트가 정해지면 bizinfo_client.fetch_bizinfo_policies처럼
    # urllib.request로 호출하고, 응답 필드명을 검증한 뒤에만 매핑한다.
    raise NotImplementedError("SAFETYKOREA_ENDPOINT 확정 후 구현 필요")
