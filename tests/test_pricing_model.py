"""pricing_model.py 테스트. predict_price는 samples=를 직접 넘겨 DB 없이 테스트한다
(fetch_samples를 우회하는 기존 설계를 그대로 활용 — N+1 방지용으로 이미 열려있던 파라미터)."""
from pricing_model import (
    MIN_SAMPLES_FOR_KNN,
    parse_price,
    parse_used_period_months,
    predict_price,
)


# ---------- parse_used_period_months ----------

def test_parse_used_period_months_years_only():
    assert parse_used_period_months("2년") == 24


def test_parse_used_period_months_months_only():
    assert parse_used_period_months("6개월") == 6


def test_parse_used_period_months_years_and_months():
    assert parse_used_period_months("1년 6개월") == 18


def test_parse_used_period_months_unparseable_or_empty():
    assert parse_used_period_months(None) is None
    assert parse_used_period_months("") is None
    assert parse_used_period_months("모름") is None


# ---------- parse_price ----------

def test_parse_price_man_won():
    assert parse_price("150만원") == 1_500_000


def test_parse_price_decimal_man_won():
    assert parse_price("1.5만원") == 15_000


def test_parse_price_plain_number_with_commas():
    assert parse_price("1,500,000원") == 1_500_000


def test_parse_price_unparseable_or_empty():
    assert parse_price(None) is None
    assert parse_price("") is None
    assert parse_price("협의 가능") is None


# ---------- predict_price ----------

def test_predict_price_no_category():
    result = predict_price(conn=None, category=None, used_period_text="1년", condition="중")
    assert result["ok"] is False
    assert result["predicted_price"] is None


def test_predict_price_no_samples():
    result = predict_price(conn=None, category="카페/음료기기", used_period_text="1년", condition="중", samples=[])
    assert result["ok"] is False
    assert "시세 데이터가 아직 없어" in result["message"]


def test_predict_price_few_samples_falls_back_to_average():
    samples = [
        {"used_period_months": 12, "condition": "중", "price": 100_000},
        {"used_period_months": 24, "condition": "중", "price": 200_000},
    ]
    assert len(samples) < MIN_SAMPLES_FOR_KNN
    result = predict_price(conn=None, category="카페/음료기기", used_period_text="1년", condition="중", samples=samples)
    assert result["ok"] is True
    assert result["predicted_price"] == 150_000
    assert result["price_range"] == (100_000, 200_000)
    assert "단순 평균" in result["message"]


def test_predict_price_missing_condition_or_period_falls_back_to_average():
    """샘플 수는 충분해도 사용 기간/상태를 해석할 수 없으면 평균으로 폴백한다."""
    samples = [
        {"used_period_months": 12, "condition": "중", "price": 100_000},
        {"used_period_months": 24, "condition": "중", "price": 200_000},
        {"used_period_months": 36, "condition": "중", "price": 300_000},
    ]
    result = predict_price(conn=None, category="카페/음료기기", used_period_text="모름", condition="중", samples=samples)
    assert result["ok"] is True
    assert "사용 기간/상태 정보가 부족" in result["message"]


def test_predict_price_knn_with_enough_usable_samples():
    samples = [
        {"used_period_months": 6, "condition": "상", "price": 500_000},
        {"used_period_months": 12, "condition": "중", "price": 400_000},
        {"used_period_months": 24, "condition": "중", "price": 300_000},
        {"used_period_months": 36, "condition": "하", "price": 200_000},
    ]
    result = predict_price(conn=None, category="카페/음료기기", used_period_text="1년", condition="중", samples=samples)
    assert result["ok"] is True
    assert result["predicted_price"] is not None
    assert result["sample_size"] == 4
    assert "KNN" in result["message"]
