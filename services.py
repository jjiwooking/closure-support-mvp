import json
from datetime import date, datetime, timedelta

from bizinfo_client import fetch_bizinfo_policies


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
    row = conn.execute("SELECT status FROM user_tasks WHERE id=?", (task_id,)).fetchone()
    before = row["status"] if row else None
    conn.execute(
        "UPDATE user_tasks SET status=?, updated_at=? WHERE id=?",
        (new_status, datetime.utcnow().isoformat(), task_id),
    )
    conn.commit()
    log_change(conn, user_id, "user_task", task_id, before, new_status)


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


def record_application_date(conn, user_task_id, applied_at, user_id):
    app = get_or_create_application(conn, user_task_id)
    conn.execute(
        "UPDATE policy_applications SET applied_at=?, updated_at=? WHERE user_task_id=?",
        (applied_at, datetime.utcnow().isoformat(), user_task_id),
    )
    conn.commit()
    log_change(conn, user_id, "policy_application", app["id"], app.get("applied_at"), applied_at)


def record_supplement(conn, user_task_id, note, due_date, user_id):
    app = get_or_create_application(conn, user_task_id)
    conn.execute(
        "UPDATE policy_applications SET supplement_note=?, supplement_due=?, updated_at=? WHERE user_task_id=?",
        (note, due_date, datetime.utcnow().isoformat(), user_task_id),
    )
    conn.commit()
    log_change(conn, user_id, "policy_application", app["id"], app.get("supplement_note"), note)


def update_decision_status(conn, user_task_id, status, user_id):
    app = get_or_create_application(conn, user_task_id)
    conn.execute(
        "UPDATE policy_applications SET decision_status=?, updated_at=? WHERE user_task_id=?",
        (status, datetime.utcnow().isoformat(), user_task_id),
    )
    conn.commit()
    log_change(conn, user_id, "policy_application", app["id"], app.get("decision_status"), status)


def update_application_payment(conn, user_task_id, status, user_id):
    app = get_or_create_application(conn, user_task_id)
    conn.execute(
        "UPDATE policy_applications SET payment_status=?, updated_at=? WHERE user_task_id=?",
        (status, datetime.utcnow().isoformat(), user_task_id),
    )
    conn.commit()
    log_change(conn, user_id, "policy_application", app["id"], app.get("payment_status"), status)


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
    try:
        rules = json.loads(eligibility_rules_json) if eligibility_rules_json else {}
    except (json.JSONDecodeError, TypeError):
        return "모집 상태 확인 필요"

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
