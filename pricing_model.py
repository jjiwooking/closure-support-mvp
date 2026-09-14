"""
중고 설비 적정가 예측 (심사평 "머신러닝/데이터분석 요소 추가" 대응).

market_price_samples(시연용 시세 데이터, seed.py에서 채움)를 학습 데이터로 써서
KNeighborsRegressor로 유사 사례 기반 가격을 예측한다. 실제 서비스에서는 이용자가
등록/거래한 실제 시세가 쌓일수록 이 테이블이 커지고 예측이 정교해지는 구조다.

학습 데이터가 너무 적으면(sample_size < MIN_SAMPLES_FOR_KNN) KNN 대신 같은
카테고리의 단순 평균으로 폴백하고, 그마저도 없으면 "확인 필요"로 답한다
(다른 모듈과 동일하게 근거 없는 값을 지어내지 않는다는 원칙 유지).
"""
import re

MIN_SAMPLES_FOR_KNN = 3
CONDITION_ORDER = {"상": 2, "중": 1, "하": 0}


def parse_used_period_months(text: str):
    """'2년', '6개월', '1년 6개월' 등 자유 텍스트를 개월수로 파싱한다.
    해석할 수 없으면 None을 반환한다(호출부가 '확인 필요'로 처리)."""
    if not text:
        return None
    years = re.search(r"(\d+)\s*년", text)
    months = re.search(r"(\d+)\s*개월", text)
    if not years and not months:
        return None
    total = 0
    if years:
        total += int(years.group(1)) * 12
    if months:
        total += int(months.group(1))
    return total


def parse_price(text: str):
    """'150만원', '1,500,000원' 같은 자유 텍스트를 숫자로 파싱한다. 실패하면 None."""
    if not text:
        return None
    cleaned = text.replace(",", "")
    man = re.search(r"(\d+(?:\.\d+)?)\s*만", cleaned)
    if man:
        return int(float(man.group(1)) * 10000)
    num = re.search(r"(\d+)", cleaned)
    return int(num.group(1)) if num else None


def fetch_samples(conn, category: str):
    """카테고리별 시세 샘플을 조회한다. 화면에서 같은 카테고리 매물을 여러
    건 렌더링할 때 매물마다 다시 조회하지 않도록, 미리 한 번 불러
    predict_price(samples=...)로 재사용할 수 있게 공개 함수로 둔다."""
    rows = conn.execute(
        "SELECT used_period_months, condition, price FROM market_price_samples WHERE category=?",
        (category,),
    ).fetchall()
    return [dict(r) for r in rows]


def predict_price(conn, category: str, used_period_text: str, condition: str, samples: list = None) -> dict:
    """반환: {"ok", "predicted_price", "price_range": (low, high) | None,
    "sample_size", "message"}. samples를 넘기면 DB 조회를 건너뛰고 그대로
    쓴다(같은 카테고리를 여러 번 조회하는 N+1을 피하기 위함); 넘기지 않으면
    기존처럼 이 함수가 직접 조회한다."""
    if not category:
        return {
            "ok": False, "predicted_price": None, "price_range": None,
            "sample_size": 0, "message": "카테고리를 선택하면 AI 추천가를 볼 수 있어요.",
        }

    if samples is None:
        samples = fetch_samples(conn, category)
    if not samples:
        return {
            "ok": False, "predicted_price": None, "price_range": None,
            "sample_size": 0, "message": "이 카테고리의 시세 데이터가 아직 없어 추천가를 계산할 수 없습니다.",
        }

    if len(samples) < MIN_SAMPLES_FOR_KNN:
        prices = [s["price"] for s in samples]
        avg = round(sum(prices) / len(prices))
        return {
            "ok": True, "predicted_price": avg, "price_range": (min(prices), max(prices)),
            "sample_size": len(samples),
            "message": f"유사 사례가 적어({len(samples)}건) 단순 평균으로 추정했습니다.",
        }

    used_period_months = parse_used_period_months(used_period_text)
    condition_score = CONDITION_ORDER.get(condition)

    usable = [
        s for s in samples
        if s["used_period_months"] is not None and s["condition"] in CONDITION_ORDER
    ]
    if len(usable) < MIN_SAMPLES_FOR_KNN or used_period_months is None or condition_score is None:
        prices = [s["price"] for s in samples]
        avg = round(sum(prices) / len(prices))
        return {
            "ok": True, "predicted_price": avg, "price_range": (min(prices), max(prices)),
            "sample_size": len(samples),
            "message": "사용 기간/상태 정보가 부족해 카테고리 평균으로 추정했습니다.",
        }

    try:
        from sklearn.neighbors import KNeighborsRegressor
    except ImportError:
        prices = [s["price"] for s in samples]
        avg = round(sum(prices) / len(prices))
        return {
            "ok": True, "predicted_price": avg, "price_range": (min(prices), max(prices)),
            "sample_size": len(samples),
            "message": "scikit-learn이 설치되지 않아 카테고리 평균으로 추정했습니다.",
        }

    X = [[s["used_period_months"], CONDITION_ORDER[s["condition"]]] for s in usable]
    y = [s["price"] for s in usable]
    k = min(5, len(usable))
    model = KNeighborsRegressor(n_neighbors=k)
    model.fit(X, y)
    predicted = model.predict([[used_period_months, condition_score]])[0]

    neighbor_idx = model.kneighbors([[used_period_months, condition_score]], n_neighbors=k, return_distance=False)[0]
    neighbor_prices = [y[i] for i in neighbor_idx]

    return {
        "ok": True,
        "predicted_price": round(predicted),
        "price_range": (min(neighbor_prices), max(neighbor_prices)),
        "sample_size": len(usable),
        "message": f"유사 사례 {len(usable)}건 기반 KNN 회귀 추정입니다.",
    }
