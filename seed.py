import json
from datetime import date, timedelta


def seed_if_empty(conn, user_id):
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
            (user_id, name, model, used_period, condition, defects, ownership_status, asking_price, pickup_terms, draft, status, payment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            "에스프레소 머신",
            "라마르조꼬 리네아(시연용 예시)",
            "2년",
            "양호",
            "없음",
            "본인 소유",
            "150만원",
            "매장 방문 수거",
            "",
            "보관 중",
            "미입금",
        ),
    )

    conn.commit()
