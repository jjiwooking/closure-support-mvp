import json
from datetime import date, timedelta


def seed_if_empty(conn, user_id):
    ensure_reference_data(conn)

    existing = conn.execute(
        "SELECT COUNT(*) AS c FROM profiles WHERE user_id=?", (user_id,)
    ).fetchone()
    if existing["c"] > 0:
        return

    conn.execute(
        """
        INSERT INTO profiles
            (user_id, region, planned_close_date, reported_closed, employee_status, rent_status, demolition_status, career_path, confirmed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            "서울 마포구",
            (date.today() + timedelta(days=45)).isoformat(),
            "미신고",
            "있음",
            "임차",
            "미정",
            "모름",
            date.today().isoformat(),
        ),
    )

    cur = conn.execute(
        """
        INSERT INTO sources (agency, title, url, retrieved_at, reviewed_at, version, review_status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "서울시",
            "소상공인 폐업 지원 안내(시연용 예시 자료)",
            "https://example-official-site.invalid/notice-1",
            date.today().isoformat(),
            date.today().isoformat(),
            "v1",
            "검토완료",
        ),
    )
    source_id = cur.lastrowid

    cur = conn.execute(
        """
        INSERT INTO policies
            (source_id, title, period, eligibility_rules, application_link_id, availability_status, target_career, application_deadline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            "희망리턴패키지(시연용 예시)",
            "상시",
            json.dumps({"region": "서울 마포구", "rent_status": "임차"}, ensure_ascii=False),
            "policy_apply_1",
            "모집중",
            "공통",
            None,
        ),
    )
    policy_id = cur.lastrowid

    # 진로별 필터링/마감일 정렬이 실제로 눈에 보이도록 재창업 전용 예시 정책을 하나 더 둔다.
    conn.execute(
        """
        INSERT INTO policies
            (source_id, title, period, eligibility_rules, application_link_id, availability_status, target_career, application_deadline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            "재창업 지원금(시연용 예시)",
            (date.today() + timedelta(days=20)).isoformat() + "까지",
            json.dumps({"region": "서울 마포구"}, ensure_ascii=False),
            "policy_apply_2",
            "모집중",
            "재창업",
            (date.today() + timedelta(days=20)).isoformat(),
        ),
    )

    # offset_days: 폐업 예정일로부터 며칠 전이 기한인지. due_type이 '사용자 예정일'인
    # 업무에만 의미가 있으며, 폐업일이 바뀌면 이 값을 기준으로 기한을 재계산한다.
    task_defs = [
        ("준비", "상황 등록", "모든 이용자에게 적용됩니다.", 42),
        ("준비", "일정 계획", "마지막 영업일과 반출 일정이 필요한 경우 적용됩니다.", 35),
        ("준비", "지원사업 후보 확인", "임차 사업장에 적용될 수 있는 지원사업이 있습니다.", None),
        ("진행", "신청서 작성 안내", "지원사업을 선택한 경우 적용됩니다.", None),
        ("진행", "폐업 관련 신고", "폐업신고가 아직 완료되지 않은 경우 적용됩니다.", None),
        ("후", "남은 업무 확인", "이전 단계 미완료 업무가 있는 경우 적용됩니다.", None),
    ]
    template_ids = []
    for stage, title, note, offset_days in task_defs:
        cur = conn.execute(
            """
            INSERT INTO task_templates (stage, title, applicability_rules, dependency_ids, deadline_rule, offset_days, source_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (stage, title, note, None, "사용자 예정일 기준", offset_days, source_id),
        )
        template_ids.append(cur.lastrowid)

    due_dates = [
        (date.today() + timedelta(days=3)).isoformat(),
        (date.today() + timedelta(days=10)).isoformat(),
        (date.today() + timedelta(days=15)).isoformat(),
        None,
        (date.today() - timedelta(days=1)).isoformat(),
        None,
    ]
    due_types = ["사용자 예정일", "사용자 예정일", "공식 계산 규칙", "미정", "공식 고정일", "미정"]
    statuses = ["진행 중", "시작 전", "시작 전", "시작 전", "확인 필요", "시작 전"]

    task_ids = []
    for idx, (tid, due, dtype, status) in enumerate(zip(template_ids, due_dates, due_types, statuses)):
        policy = policy_id if idx == 2 else None
        cur = conn.execute(
            """
            INSERT INTO user_tasks (user_id, template_id, policy_id, status, due_date, due_type, rule_version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, tid, policy, status, due, dtype, "v1", date.today().isoformat()),
        )
        task_ids.append(cur.lastrowid)

    cur = conn.execute(
        """
        INSERT INTO document_guides (source_id, name, issuance_link_id, instructions, submission_link_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            source_id,
            "폐업신고서",
            "doc_issue_1",
            "정부24에서 온라인 발급 또는 세무서 방문 발급 (시연용 예시 경로)",
            "doc_submit_1",
        ),
    )
    doc_id = cur.lastrowid

    conn.execute(
        """
        INSERT INTO document_checks (user_task_id, document_guide_id, user_checked, checked_at)
        VALUES (?, ?, ?, ?)
        """,
        (task_ids[4], doc_id, 0, None),
    )

    conn.execute(
        """
        INSERT INTO equipment
            (user_id, name, model, used_period, condition, defects, ownership_status, asking_price,
             pickup_terms, draft, status, payment_status, category, region)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            "에스프레소 머신",
            "라마르조꼬 리네아(시연용 예시)",
            "2년",
            "중",
            "없음",
            "본인 소유",
            "150만원",
            "매장 방문 수거",
            "",
            "보관 중",
            "미입금",
            "카페/음료기기",
            "서울 마포구",
        ),
    )

    conn.commit()


def ensure_reference_data(conn):
    """profiles와 무관하게, 이미 데모 유저가 있는 기존 로컬 DB에서도 새로 추가된
    참고 데이터(시세 샘플/정책 승인 이력/타 판매자 매물)가 비어 있으면 채운다.
    각 데이터셋을 독립적으로 확인하므로 seed_if_empty가 프로필 존재로 일찍
    return하더라도 이 함수는 항상 먼저 호출되어 누락을 채운다."""
    if conn.execute("SELECT COUNT(*) AS c FROM market_price_samples").fetchone()["c"] == 0:
        _seed_price_samples(conn)
    if conn.execute("SELECT COUNT(*) AS c FROM policy_outcome_history").fetchone()["c"] == 0:
        _seed_policy_outcome_history(conn)
    if conn.execute(
        "SELECT COUNT(*) AS c FROM equipment WHERE user_id = 'demo_seller_2'"
    ).fetchone()["c"] == 0:
        _seed_marketplace_listings(conn)
    _backfill_equipment_category_region(conn)
    conn.commit()


_CATEGORY_KEYWORDS = {
    "카페/음료기기": ["에스프레소", "커피", "원두", "그라인더", "제빙기"],
    "냉장/냉동": ["냉장", "냉동"],
    "주방/조리기기": ["가스레인지", "오븐", "조리", "튀김", "인덕션"],
    "집기/가구": ["테이블", "의자", "선반", "진열대"],
    "전자기기": ["포스", "카드단말기", "컴퓨터", "모니터", "프린터"],
}


def _guess_equipment_category(name: str) -> str:
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(k in name for k in keywords):
            return category
    return "기타"


def _backfill_equipment_category_region(conn):
    """이번 업그레이드 이전에 등록된 물품(category/region 컬럼이 생기기 전)은
    두 값이 비어 있어 AI 추천가를 볼 수 없다. 물품명 키워드로 카테고리를
    추정하고, 판매자 프로필의 지역으로 region을 채운다."""
    rows = conn.execute(
        "SELECT id, user_id, name FROM equipment WHERE category IS NULL OR region IS NULL"
    ).fetchall()
    for r in rows:
        category = _guess_equipment_category(r["name"] or "")
        profile = conn.execute(
            "SELECT region FROM profiles WHERE user_id=?", (r["user_id"],)
        ).fetchone()
        region = profile["region"] if profile else None
        conn.execute(
            "UPDATE equipment SET category=COALESCE(category, ?), region=COALESCE(region, ?) WHERE id=?",
            (category, region, r["id"]),
        )


def _seed_price_samples(conn):
    """가격예측 모델(pricing_model.predict_price) 학습용 합성 시세 데이터.
    카테고리별로 사용기간(개월)이 길수록/상태가 나쁠수록 가격이 낮아지는
    대략적인 경향 + 약간의 노이즈를 준다."""
    base_price_by_category = {
        "주방/조리기기": 800000,
        "냉장/냉동": 1200000,
        "카페/음료기기": 1500000,
        "집기/가구": 300000,
        "전자기기": 500000,
    }
    condition_multiplier = {"상": 1.15, "중": 1.0, "하": 0.75}
    noise_cycle = [1.0, 0.92, 1.08, 0.97, 1.05, 0.9]

    rows = []
    for category, base in base_price_by_category.items():
        i = 0
        for months in (6, 12, 24, 36, 48, 60):
            for condition in ("상", "중", "하"):
                depreciation = max(0.3, 1 - months / 120)
                price = round(base * depreciation * condition_multiplier[condition] * noise_cycle[i % len(noise_cycle)])
                rows.append((category, months, condition, price))
                i += 1

    conn.executemany(
        "INSERT INTO market_price_samples (category, used_period_months, condition, price) VALUES (?, ?, ?, ?)",
        rows,
    )


def _seed_policy_outcome_history(conn):
    """정책 적합도 옆에 보여줄 과거 승인율 통계(analytics.policy_approval_stats)용
    합성 심사 이력."""
    entries = []
    pattern = {
        "재창업": ["승인", "승인", "불승인", "승인", "불승인", "승인", "승인", "불승인"],
        "취업": ["승인", "불승인", "불승인", "승인", "승인", "불승인"],
        "공통": ["승인", "승인", "승인", "불승인", "승인", "불승인", "승인", "승인"],
    }
    for career, statuses in pattern.items():
        for idx, status in enumerate(statuses):
            entries.append(
                (f"{career} 지원사업(시연용 이력 {idx + 1})", career, "서울", status,
                 (date.today() - timedelta(days=30 * (idx + 1))).isoformat())
            )

    conn.executemany(
        """
        INSERT INTO policy_outcome_history (policy_title, target_career, region, decision_status, decided_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        entries,
    )


def _seed_marketplace_listings(conn):
    """설비 매칭 화면(screen_marketplace) 데모용, 다른 판매자 명의의 '판매 중' 매물."""
    sellers = [
        ("demo_seller_2", "제빙기", "카페/음료기기", "1년", "상", "60만원", "서울 마포구"),
        ("demo_seller_3", "업소용 냉장고", "냉장/냉동", "3년", "중", "80만원", "서울 마포구"),
        ("demo_seller_4", "4구 가스레인지", "주방/조리기기", "2년", "중", "35만원", "서울 은평구"),
        ("demo_seller_5", "매장 테이블 4개 세트", "집기/가구", "4년", "하", "20만원", "서울 은평구"),
        ("demo_seller_6", "포스기+카드단말기", "전자기기", "1년", "상", "40만원", "경기 고양시"),
        ("demo_seller_7", "그라인더", "카페/음료기기", "6개월", "상", "25만원", "서울 마포구"),
    ]
    rows = [
        (
            user_id, name, "확인 필요", used_period, condition, "확인 필요", "본인 소유",
            price, "매장 방문 수거", "", "판매 중", "미입금", category, region,
        )
        for user_id, name, category, used_period, condition, price, region in sellers
    ]
    conn.executemany(
        """
        INSERT INTO equipment
            (user_id, name, model, used_period, condition, defects, ownership_status, asking_price,
             pickup_terms, draft, status, payment_status, category, region)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
