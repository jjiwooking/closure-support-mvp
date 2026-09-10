import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "closure_support.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    region TEXT,
    planned_close_date TEXT,
    reported_closed TEXT,
    employee_status TEXT,
    rent_status TEXT,
    demolition_status TEXT,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agency TEXT,
    title TEXT,
    url TEXT,
    retrieved_at TEXT,
    reviewed_at TEXT,
    version TEXT,
    review_status TEXT
);

CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    title TEXT,
    period TEXT,
    eligibility_rules TEXT,
    application_link_id TEXT,
    availability_status TEXT,
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS task_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT,
    title TEXT,
    applicability_rules TEXT,
    dependency_ids TEXT,
    deadline_rule TEXT,
    offset_days INTEGER,
    source_id INTEGER,
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS document_guides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    name TEXT,
    issuance_link_id TEXT,
    instructions TEXT,
    submission_link_id TEXT,
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS user_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    template_id INTEGER,
    policy_id INTEGER,
    status TEXT DEFAULT '시작 전',
    due_date TEXT,
    due_type TEXT,
    rule_version TEXT,
    updated_at TEXT,
    FOREIGN KEY(template_id) REFERENCES task_templates(id),
    FOREIGN KEY(policy_id) REFERENCES policies(id)
);

CREATE TABLE IF NOT EXISTS document_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_task_id INTEGER,
    document_guide_id INTEGER,
    user_checked INTEGER DEFAULT 0,
    checked_at TEXT,
    FOREIGN KEY(user_task_id) REFERENCES user_tasks(id),
    FOREIGN KEY(document_guide_id) REFERENCES document_guides(id)
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT,
    model TEXT,
    used_period TEXT,
    condition TEXT,
    defects TEXT,
    ownership_status TEXT,
    asking_price TEXT,
    pickup_terms TEXT,
    draft TEXT,
    status TEXT DEFAULT '보관 중',
    payment_status TEXT DEFAULT '미입금'
);

CREATE TABLE IF NOT EXISTS change_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    before_value TEXT,
    after_value TEXT,
    user_confirmed INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS policy_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_task_id INTEGER NOT NULL UNIQUE,
    applied_at TEXT,
    supplement_note TEXT,
    supplement_due TEXT,
    decision_status TEXT DEFAULT '결과 대기',
    payment_status TEXT DEFAULT '미입금',
    updated_at TEXT,
    FOREIGN KEY(user_task_id) REFERENCES user_tasks(id)
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)


def _migrate(conn):
    """CREATE TABLE IF NOT EXISTS는 기존 테이블에 새 컬럼을 추가해주지 않으므로,
    이미 생성된 로컬 DB에 대해 필요한 컬럼만 보수적으로 추가한다."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(task_templates)")]
    if "offset_days" not in cols:
        conn.execute("ALTER TABLE task_templates ADD COLUMN offset_days INTEGER")
        conn.commit()
