import json
import re
from datetime import date, datetime, timedelta

import pricing_model
from bizinfo_client import fetch_bizinfo_policies

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_iso_date(value):
    """YYYY-MM-DD 형식이 아니면 None을 반환한다(호출부가 '확인 필요'로 처리하고,
    문자열 정렬에 의존하는 sort_policies_by_deadline이 깨지지 않게 한다)."""
    if value and _ISO_DATE_RE.match(value):
        return value
    return None


def parse_eligibility_rules(eligibility_rules_json) -> dict:
    """policies.eligibility_rules JSON 문자열을 dict로 안전하게 파싱한다.
    비어있거나 손상된 값이면 빈 dict를 반환한다. evaluate_eligibility와
    analytics.score_policy_fit이 이 파싱 규칙을 공유해, 한쪽만 고치고 다른
    쪽을 놓쳐 판정이 어긋나는 일을 막는다."""
    try:
        return json.loads(eligibility_rules_json) if eligibility_rules_json else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------- 기록 서비스 ----------

def log_change(conn, user_id, entity_type, entity_id, before_value, after_value):
    conn.execute(
        """
        INSERT INTO change_logs (user_id, entity_type, entity_id, before_value, after_value, user_confirmed, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        (user_id, entity_type, entity_id, str(before_value), str(after_value), datetime.utcnow().isoformat()),
    )
    conn.commit()


def update_task_status(conn, task_id, new_status, user_id):
    """user_id 소유가 아닌 task_id면 아무것도 바꾸지 않고 False를 반환한다 —
    app.py는 항상 user_id로 미리 필터링된 task["id"]만 넘기므로 영향이 없지만,
    api.py는 task_id를 URL 경로에서 그대로 받으므로 이 체크가 없으면 다른
    사용자의 task를 임의로 바꿀 수 있었다(IDOR)."""
    row = conn.execute(
        "SELECT status FROM user_tasks WHERE id=? AND user_id=?", (task_id, user_id)
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE user_tasks SET status=?, updated_at=? WHERE id=? AND user_id=?",
        (new_status, datetime.utcnow().isoformat(), task_id, user_id),
    )
    conn.commit()
    log_change(conn, user_id, "user_task", task_id, row["status"], new_status)
    return True


def toggle_document_check(conn, check_id, checked, user_id):
    row = conn.execute("SELECT user_checked FROM document_checks WHERE id=?", (check_id,)).fetchone()
    before = bool(row["user_checked"]) if row else None
    conn.execute(
        "UPDATE document_checks SET user_checked=?, checked_at=? WHERE id=?",
        (1 if checked else 0, datetime.utcnow().isoformat() if checked else None, check_id),
    )
    conn.commit()
    log_change(conn, user_id, "document_check", check_id, before, checked)


# ---------- 상황 정보 수정 (일정 변경 시 재계산 포함) ----------

def update_profile(conn, user_id, fields: dict):
    """모름은 별도 상태로 그대로 저장하며 '없음'으로 바꾸지 않는다. 변경 필드만 기록하고,
    폐업 예정일이 바뀌면 사용자 예정일 기준 업무의 기한만 재계산한다(공식 고정 기한은 건드리지 않음)."""
    row = conn.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    profile = dict(row)
    old_close_date = profile.get("planned_close_date")

    for key, new_value in fields.items():
        if profile.get(key) != new_value:
            log_change(conn, user_id, "profile", profile["id"], profile.get(key), new_value)

    conn.execute(
        """
        UPDATE profiles
        SET region=?, planned_close_date=?, reported_closed=?, employee_status=?,
            rent_status=?, demolition_status=?, confirmed_at=?
        WHERE user_id=?
        """,
        (
            fields["region"], fields["planned_close_date"], fields["reported_closed"],
            fields["employee_status"], fields["rent_status"], fields["demolition_status"],
            datetime.utcnow().isoformat(), user_id,
        ),
    )
    conn.commit()

    if fields["planned_close_date"] != old_close_date:
        recalculate_user_dates(conn, user_id, fields["planned_close_date"])


def recalculate_user_dates(conn, user_id, new_planned_close_date):
    """due_type이 '사용자 예정일'이고 offset_days가 등록된 업무만 재계산한다.
    '공식 고정일'·'공식 계산 규칙' 업무의 기한은 임의로 바꾸지 않는다."""
    rows = conn.execute(
        """
        SELECT ut.id AS user_task_id, ut.due_date AS old_due, tt.offset_days AS offset_days
        FROM user_tasks ut
        JOIN task_templates tt ON ut.template_id = tt.id
        WHERE ut.user_id = ? AND ut.due_type = '사용자 예정일' AND tt.offset_days IS NOT NULL
        """,
        (user_id,),
    ).fetchall()

    new_close = date.fromisoformat(new_planned_close_date)
    for r in rows:
        new_due = (new_close - timedelta(days=r["offset_days"])).isoformat()
        if new_due != r["old_due"]:
            conn.execute(
                "UPDATE user_tasks SET due_date=?, updated_at=? WHERE id=?",
                (new_due, datetime.utcnow().isoformat(), r["user_task_id"]),
            )
            log_change(conn, user_id, "user_task_due_date", r["user_task_id"], r["old_due"], new_due)
    conn.commit()


def update_career_path(conn, user_id, career_path):
    """기능2(정책 매칭)에서 고른 진로(재창업/취업/모름)를 프로필에 지속 저장한다."""
    row = conn.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    profile = dict(row)
    if profile.get("career_path") == career_path:
        return
    conn.execute(
        "UPDATE profiles SET career_path=? WHERE user_id=?", (career_path, user_id)
    )
    conn.commit()
    log_change(conn, user_id, "profile", profile["id"], profile.get("career_path"), career_path)


# ---------- 정책 흐름: 신청 기록 / 보완 / 심사·입금 (준비 완료와 구분되는 별도 기록) ----------

def get_or_create_application(conn, user_task_id):
    row = conn.execute(
        "SELECT * FROM policy_applications WHERE user_task_id=?", (user_task_id,)
    ).fetchone()
    if row:
        return dict(row)
    conn.execute(
        """
        INSERT INTO policy_applications (user_task_id, decision_status, payment_status, updated_at)
        VALUES (?, '결과 대기', '미입금', ?)
        """,
        (user_task_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM policy_applications WHERE user_task_id=?", (user_task_id,)
    ).fetchone()
    return dict(row)


def _update_application_fields(conn, user_task_id, user_id, fields: dict):
    """policy_applications의 일부 컬럼만 갱신하는 4개 함수(신청일/보완/심사결과/입금)가
    공유하는 조회→UPDATE→commit→log_change 절차. fields의 키는 항상 호출부에서
    고정된 컬럼명 리터럴만 넘기므로 f-string으로 컬럼을 넣어도 인젝션 위험이 없다."""
    app = get_or_create_application(conn, user_task_id)
    set_clause = ", ".join(f"{col}=?" for col in fields)
    conn.execute(
        f"UPDATE policy_applications SET {set_clause}, updated_at=? WHERE user_task_id=?",
        (*fields.values(), datetime.utcnow().isoformat(), user_task_id),
    )
    conn.commit()
    for col, new_value in fields.items():
        log_change(conn, user_id, "policy_application", app["id"], app.get(col), new_value)
    return app


def record_application_date(conn, user_task_id, applied_at, user_id):
    _update_application_fields(conn, user_task_id, user_id, {"applied_at": applied_at})


def record_supplement(conn, user_task_id, note, due_date, user_id):
    _update_application_fields(
        conn, user_task_id, user_id, {"supplement_note": note, "supplement_due": due_date}
    )


def _record_policy_outcome(conn, user_task_id, status):
    """실제 심사 결과(승인/불승인)를 policy_outcome_history에 반영해, 다음
    analytics.policy_approval_stats가 seed 데이터뿐 아니라 실사용 이력도 함께
    보게 한다(심사평 '데이터분석 요소' — 모델이 실사용으로 정교해지게 함).
    같은 user_task_id에 이미 기록이 있으면(사용자가 승인↔불승인을 정정한
    경우) 새로 INSERT하지 않고 그 행을 UPDATE한다 — 그렇지 않으면 정정할
    때마다 이력이 중복 적재돼 승인율 통계가 왜곡된다."""
    row = conn.execute(
        """
        SELECT p.title AS policy_title, p.target_career, pr.region
        FROM user_tasks ut
        JOIN policies p ON ut.policy_id = p.id
        JOIN profiles pr ON pr.user_id = ut.user_id
        WHERE ut.id = ?
        """,
        (user_task_id,),
    ).fetchone()
    if not row:
        return  # 정책과 연결되지 않은 업무(예: 신청서 작성 안내)는 기록하지 않음

    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        "SELECT id FROM policy_outcome_history WHERE user_task_id=?", (user_task_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE policy_outcome_history SET decision_status=?, decided_at=? WHERE id=?",
            (status, now, existing["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO policy_outcome_history
                (policy_title, target_career, region, decision_status, decided_at, user_task_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (row["policy_title"], row["target_career"] or "공통", row["region"], status, now, user_task_id),
        )
    conn.commit()


def update_decision_status(conn, user_task_id, status, user_id):
    _update_application_fields(conn, user_task_id, user_id, {"decision_status": status})
    if status in ("승인", "불승인"):
        _record_policy_outcome(conn, user_task_id, status)


def update_application_payment(conn, user_task_id, status, user_id):
    _update_application_fields(conn, user_task_id, user_id, {"payment_status": status})


def register_policy_interest(conn, policy_id, user_id):
    """관심 사업 선택(P03): 정책에 연결된 업무가 없으면 '신청서 작성 안내' 템플릿으로 생성한다."""
    existing = conn.execute(
        "SELECT id FROM user_tasks WHERE user_id=? AND policy_id=?", (user_id, policy_id)
    ).fetchone()
    if existing:
        return existing["id"], False

    template = conn.execute(
        "SELECT id FROM task_templates WHERE title=?", ("신청서 작성 안내",)
    ).fetchone()
    if not template:
        return None, False

    cur = conn.execute(
        """
        INSERT INTO user_tasks (user_id, template_id, policy_id, status, due_date, due_type, rule_version, updated_at)
        VALUES (?, ?, ?, '시작 전', NULL, '미정', 'v1', ?)
        """,
        (user_id, template["id"], policy_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    new_id = cur.lastrowid
    log_change(conn, user_id, "user_task", new_id, None, "관심 사업 등록")
    return new_id, True


# ---------- 외부 자료 수집 (기업마당) — 수집만 하고 자동 게시하지 않는다 ----------

def sync_bizinfo_policies(conn, keyword: str = "폐업") -> dict:
    """수집 -> 중복 제거까지만 수행한다. review_status='검토 필요'로 저장되며,
    지원정책 화면은 review_status='검토완료'만 보여주므로 사람이 검토·게시하기
    전에는 추천에 노출되지 않는다(팀플.md 9장 등록 흐름, 검수기준 '미확인 조건을
    신청 가능으로 확정하지 않는다' 준수). 필드명이 아직 검증되지 않아 원문을
    그대로 보존하고 조건(eligibility_rules) 추출·policies 등록은 사람이 한다."""
    result = fetch_bizinfo_policies(keyword)
    if not result["configured"] or result["message"]:
        return result

    now = datetime.utcnow().isoformat()
    added = 0
    for raw in result["items"]:
        raw_json = json.dumps(raw, ensure_ascii=False)
        existing = conn.execute("SELECT id FROM sources WHERE title=?", (raw_json,)).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO sources (agency, title, url, retrieved_at, reviewed_at, version, review_status)
            VALUES (?, ?, ?, ?, NULL, 'v1', '검토 필요')
            """,
            ("기업마당(자동수집, 필드 미확인)", raw_json, "", now),
        )
        added += 1
    conn.commit()
    return {
        "configured": True,
        "items": result["items"],
        "message": f"{added}건 수집해 검토 대기로 등록했습니다. (화면에는 아직 표시되지 않음)",
    }


def log_research_run(conn, source_count: int):
    """백그라운드 정책리서치 1회 실행 기록을 남긴다(설계문서 ERD의 research_runs)."""
    conn.execute(
        "INSERT INTO research_runs (run_at, source_count) VALUES (?, ?)",
        (datetime.utcnow().isoformat(), source_count),
    )
    conn.commit()


def get_reviewed_policies(conn):
    """사람이 검토를 마친(review_status='검토완료') 정책만 가져온다."""
    rows = conn.execute(
        """
        SELECT p.* FROM policies p
        JOIN sources s ON p.source_id = s.id
        WHERE s.review_status = '검토완료'
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_equipment(conn, user_id):
    rows = conn.execute("SELECT * FROM equipment WHERE user_id=?", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def record_equipment_sale(conn, equipment_id, price_text: str) -> bool:
    """실제 판매가를 equipment.final_price에 기록하고, 같은 데이터를
    market_price_samples에도 추가해 다음 pricing_model.predict_price(KNN)가
    실거래가로 더 정교해지게 한다(심사평 '데이터분석 요소' 대응). 카테고리/상태가
    비어있거나 가격을 해석할 수 없으면 학습 데이터에는 넣지 않는다(잘못된 근거를
    만들지 않기 위함)."""
    price = pricing_model.parse_price(price_text)
    if price is None:
        return False

    item = conn.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
    if not item:
        return False
    item = dict(item)

    conn.execute("UPDATE equipment SET final_price=? WHERE id=?", (price, equipment_id))

    if item.get("category") and item.get("condition") in pricing_model.CONDITION_ORDER:
        months = pricing_model.parse_used_period_months(item.get("used_period"))
        conn.execute(
            "INSERT INTO market_price_samples (category, used_period_months, condition, price) VALUES (?, ?, ?, ?)",
            (item["category"], months, item["condition"], price),
        )
    conn.commit()
    return True


def find_new_policy_matches(conn, user_id, profile: dict):
    """사람이 검토를 마친(review_status='검토완료') 정책 중, 이 사용자에게 아직
    한 번도 매칭 알림을 보낸 적 없고, 조건(evaluate_eligibility)과 진로가 맞는
    것만 추린다. DB에 쓰지는 않는 순수 판정 함수."""
    policies = get_reviewed_policies(conn)

    already_matched = {
        row["policy_id"]
        for row in conn.execute(
            "SELECT policy_id FROM policy_matches WHERE user_id=?", (user_id,)
        ).fetchall()
    }
    candidates = [p for p in policies if p["id"] not in already_matched]
    candidates = filter_policies_by_career(candidates, profile.get("career_path"))

    return [
        p for p in candidates
        if evaluate_eligibility(profile, p["eligibility_rules"]) == "입력 조건 부합"
    ]


def record_policy_match_and_notify(conn, user_id, policy: dict):
    """정책 매칭 1건을 기록하고(policy_matches) 사용자에게 알림을 만든다(notifications)."""
    now = datetime.utcnow().isoformat()
    conn.execute(
        """
        INSERT INTO policy_matches (user_id, policy_id, match_reason, matched_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, policy["id"], "조건/진로 일치", now),
    )
    conn.execute(
        """
        INSERT INTO notifications (user_id, policy_id, message, sent_at, read_at)
        VALUES (?, ?, ?, ?, NULL)
        """,
        (user_id, policy["id"], f"'{policy['title']}' 조건에 맞는 지원사업이 새로 등록됐어요.", now),
    )
    conn.commit()


def get_unread_notifications(conn, user_id):
    rows = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? AND read_at IS NULL ORDER BY sent_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_notification_read(conn, notification_id):
    conn.execute(
        "UPDATE notifications SET read_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), notification_id),
    )
    conn.commit()


# ---------- 규칙 엔진 (LLM 미사용, 순수 코드) ----------

# 체크리스트 완료시 자동 이동할 다음 단계. 마지막 단계('후')는 다음이 없다.
NEXT_STAGE = {"준비": "진행", "진행": "후", "후": None}


def compute_progress(tasks):
    """적용 확정 업무 중 사용자 완료 수 / 적용 확정 업무 수. 분모가 0이면 (0, 0)을 반환."""
    total = len(tasks)
    completed = sum(1 for t in tasks if t["status"] == "사용자 완료")
    return completed, total


def check_stage_complete(stage_tasks) -> bool:
    """해당 단계에 업무가 하나 이상 있고 전부 '사용자 완료'인지 확인한다."""
    completed, total = compute_progress(stage_tasks)
    return total > 0 and completed == total


def should_suggest_trade(conn, user_id) -> bool:
    """아직 처분하지 않은 집기가 있으면 중고거래 메뉴 이동을 제안할 만하다고 본다."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM equipment WHERE user_id=? AND status != '처분 완료'",
        (user_id,),
    ).fetchone()
    return row["c"] > 0


def priority_sort(tasks):
    """확인된 기한 초과 -> 임박 업무 -> 나머지 순으로 정렬."""
    today = date.today().isoformat()

    def sort_key(t):
        due = t.get("due_date")
        is_overdue = 0 if (due and due < today) else 1
        has_due = 0 if due else 1
        return (is_overdue, has_due, due or "9999-99-99")

    return sorted(tasks, key=sort_key)


def filter_policies_by_career(policies, career_path):
    """진로를 아직 안 골랐으면('모름'/None) 전체를 보여준다. 골랐으면 그 진로 전용이거나
    '공통'이거나 아직 태그가 안 된(NULL) 정책만 남긴다(미태그 정책을 실수로 숨기지 않기 위함)."""
    if not career_path or career_path == "모름":
        return list(policies)
    return [
        p for p in policies
        if p.get("target_career") in (career_path, "공통", None)
    ]


def sort_policies_by_deadline(policies):
    """마감일 임박 순으로 정렬한다. 마감일이 없는(상시 등) 정책은 맨 뒤로 보낸다."""
    def sort_key(p):
        deadline = p.get("application_deadline")
        return deadline or "9999-99-99"

    return sorted(policies, key=sort_key)


def evaluate_eligibility(profile: dict, eligibility_rules_json: str) -> str:
    """정책 조건과 프로필을 비교해 4가지 상태 라벨 중 하나를 반환한다. LLM을 호출하지 않는다."""
    rules = parse_eligibility_rules(eligibility_rules_json)
    if not rules:
        return "모집 상태 확인 필요"

    unmet = []
    unknown = []
    for field, expected in rules.items():
        actual = profile.get(field)
        if actual is None or actual == "모름":
            unknown.append(field)
        elif actual != expected:
            unmet.append(field)

    if unmet:
        return "현재 조건 불일치"
    if unknown:
        return "추가 정보 필요"
    return "입력 조건 부합"


# ---------- 설비 매칭(창업자용 마켓) — 판매 중인 물품을 사용자 구분 없이 조회 ----------

def get_marketplace_listings(conn, category: str = None, region: str = None):
    """'판매 중'으로 표시된 모든 사용자의 집기를 조회한다(심사평 "창업자-폐업자
    설비 매칭" 대응). 카테고리/지역은 선택 필터."""
    query = "SELECT * FROM equipment WHERE status = '판매 중'"
    params = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if region:
        query += " AND region = ?"
        params.append(region)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def record_marketplace_interest(conn, buyer_user_id, equipment_id):
    """같은 구매자가 같은 물품에 여러 번 눌러도 한 건으로만 기록한다(중복 클릭이
    판매자에게 보이는 관심 인원수를 부풀리지 않도록)."""
    existing = conn.execute(
        "SELECT id FROM marketplace_interests WHERE buyer_user_id=? AND equipment_id=?",
        (buyer_user_id, equipment_id),
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO marketplace_interests (buyer_user_id, equipment_id, created_at) VALUES (?, ?, ?)",
        (buyer_user_id, equipment_id, datetime.utcnow().isoformat()),
    )
    conn.commit()


def get_equipment_interests(conn, equipment_id):
    """판매자가 '중고품 관리' 화면에서 자기 물품에 관심 표시한 사람을 볼 수 있게
    한다 — 지금까지는 marketplace_interests에 기록만 되고 아무 화면에도 노출되지
    않는 반쪽짜리 기능이었다."""
    rows = conn.execute(
        "SELECT buyer_user_id, created_at FROM marketplace_interests WHERE equipment_id=? ORDER BY created_at",
        (equipment_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_interest_counts(conn, equipment_ids):
    """equipment_ids 각각의 관심 표시 개수를 한 번의 쿼리로 가져온다. 물품 목록
    화면에서 물품마다 get_equipment_interests()를 따로 부르면 N+1 쿼리가 되므로,
    개수만 필요한 목록 뷰에서는 이걸 대신 쓴다."""
    if not equipment_ids:
        return {}
    placeholders = ",".join("?" * len(equipment_ids))
    rows = conn.execute(
        f"SELECT equipment_id, COUNT(*) as cnt FROM marketplace_interests "
        f"WHERE equipment_id IN ({placeholders}) GROUP BY equipment_id",
        equipment_ids,
    ).fetchall()
    return {r["equipment_id"]: r["cnt"] for r in rows}


# ---------- 집기 판매 글 생성 (입력된 사실만 사용, 미입력 항목은 "확인 필요"로 표기) ----------

def generate_listing(item: dict, style: str = "short") -> str:
    name = item.get("name") or "물품명 미입력"

    def field(key, label, fallback="확인 필요"):
        value = item.get(key)
        if value in (None, "", "모름"):
            return f"{label}: {fallback}"
        return f"{label}: {value}"

    if style == "short":
        lines = [
            f"[{name}] 판매합니다.",
            field("model", "모델"),
            field("used_period", "사용 기간"),
            field("condition", "상태"),
            field("defects", "하자"),
            field("asking_price", "희망 가격"),
            field("pickup_terms", "수거 조건"),
            "* 위 내용은 판매자가 입력한 정보이며, 실제 상태는 직접 확인해주세요.",
        ]
        return "\n".join(lines)

    # 블로그 스타일: 더 긴 서술형이지만, 입력된 사실 외의 성능·무하자·가격 근거를 만들어내지 않는다.
    intro = f"안녕하세요, 폐업을 준비하며 사용하던 {name}을(를) 내놓습니다."
    body_lines = [
        field("model", "모델"),
        field("used_period", "사용 기간"),
        field("condition", "상태"),
        field("defects", "하자 여부"),
        field("asking_price", "희망 가격", fallback="협의 가능(확인 필요)"),
        field("pickup_terms", "수거 조건"),
    ]
    body = "\n".join(f"- {line}" for line in body_lines)
    closing = "직접 사용하던 물건이라 상태 관련 문의는 편하게 남겨주세요. 사진과 추가 설명은 요청 시 제공합니다."
    disclaimer = "* 본 글은 판매자가 입력한 정보로 자동 작성되었으며, 표기되지 않은 성능이나 무하자 여부를 보증하지 않습니다."
    return "\n\n".join([intro, body, closing, disclaimer])
