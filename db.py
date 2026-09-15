import threading

import psycopg2
import psycopg2.extras
import psycopg2.pool

from config import DATABASE_URL

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    region TEXT,
    planned_close_date TEXT,
    reported_closed TEXT,
    employee_status TEXT,
    rent_status TEXT,
    demolition_status TEXT,
    career_path TEXT,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    agency TEXT,
    title TEXT,
    url TEXT,
    retrieved_at TEXT,
    reviewed_at TEXT,
    version TEXT,
    review_status TEXT,
    extracted_draft TEXT
);

CREATE TABLE IF NOT EXISTS policies (
    id SERIAL PRIMARY KEY,
    source_id INTEGER,
    title TEXT,
    period TEXT,
    eligibility_rules TEXT,
    application_link_id TEXT,
    availability_status TEXT,
    target_career TEXT,
    application_deadline TEXT,
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS task_templates (
    id SERIAL PRIMARY KEY,
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
    id SERIAL PRIMARY KEY,
    source_id INTEGER,
    name TEXT,
    issuance_link_id TEXT,
    instructions TEXT,
    submission_link_id TEXT,
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS user_tasks (
    id SERIAL PRIMARY KEY,
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
    id SERIAL PRIMARY KEY,
    user_task_id INTEGER,
    document_guide_id INTEGER,
    user_checked INTEGER DEFAULT 0,
    checked_at TEXT,
    FOREIGN KEY(user_task_id) REFERENCES user_tasks(id),
    FOREIGN KEY(document_guide_id) REFERENCES document_guides(id)
);

CREATE TABLE IF NOT EXISTS equipment (
    id SERIAL PRIMARY KEY,
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
    payment_status TEXT DEFAULT '미입금',
    category TEXT,
    region TEXT,
    final_price INTEGER
);

CREATE TABLE IF NOT EXISTS change_logs (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    before_value TEXT,
    after_value TEXT,
    user_confirmed INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS policy_applications (
    id SERIAL PRIMARY KEY,
    user_task_id INTEGER NOT NULL UNIQUE,
    applied_at TEXT,
    supplement_note TEXT,
    supplement_due TEXT,
    decision_status TEXT DEFAULT '결과 대기',
    payment_status TEXT DEFAULT '미입금',
    updated_at TEXT,
    FOREIGN KEY(user_task_id) REFERENCES user_tasks(id)
);

CREATE TABLE IF NOT EXISTS research_runs (
    id SERIAL PRIMARY KEY,
    run_at TEXT,
    source_count INTEGER
);

CREATE TABLE IF NOT EXISTS policy_matches (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    policy_id INTEGER NOT NULL,
    match_reason TEXT,
    matched_at TEXT,
    FOREIGN KEY(policy_id) REFERENCES policies(id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    policy_id INTEGER,
    message TEXT,
    sent_at TEXT,
    read_at TEXT,
    FOREIGN KEY(policy_id) REFERENCES policies(id)
);

CREATE TABLE IF NOT EXISTS market_price_samples (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    used_period_months INTEGER,
    condition TEXT,
    price INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_outcome_history (
    id SERIAL PRIMARY KEY,
    policy_title TEXT,
    target_career TEXT,
    region TEXT,
    decision_status TEXT,
    decided_at TEXT,
    user_task_id INTEGER
);

CREATE TABLE IF NOT EXISTS marketplace_interests (
    id SERIAL PRIMARY KEY,
    buyer_user_id TEXT NOT NULL,
    equipment_id INTEGER NOT NULL,
    created_at TEXT,
    FOREIGN KEY(equipment_id) REFERENCES equipment(id)
);
"""

# 이전에 CREATE TABLE IF NOT EXISTS만으로 만들어진 기존 로컬 DB에는 새 컬럼이
# 추가되지 않으므로, 필요한 컬럼을 여기서 보수적으로 추가한다. Postgres 9.6+가
# 지원하는 IF NOT EXISTS 덕분에 SQLite 때처럼 컬럼 존재 여부를 먼저 조회할
# 필요 없이 매번 그대로 실행해도 안전하다(멱등).
MIGRATIONS = """
ALTER TABLE task_templates ADD COLUMN IF NOT EXISTS offset_days INTEGER;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS career_path TEXT;
ALTER TABLE policies ADD COLUMN IF NOT EXISTS target_career TEXT;
ALTER TABLE policies ADD COLUMN IF NOT EXISTS application_deadline TEXT;
ALTER TABLE equipment ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE equipment ADD COLUMN IF NOT EXISTS region TEXT;
ALTER TABLE equipment ADD COLUMN IF NOT EXISTS final_price INTEGER;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS extracted_draft TEXT;
ALTER TABLE policy_outcome_history ADD COLUMN IF NOT EXISTS user_task_id INTEGER;
"""

# 자주 필터링/조인되는 컬럼에 인덱스를 추가한다(DB 리뷰에서 지적됨 — SQLite 때는
# 데이터가 적어 체감되지 않았지만 Postgres에서 seq scan이 쌓이면 느려진다).
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_equipment_user_id ON equipment(user_id);
CREATE INDEX IF NOT EXISTS idx_equipment_status ON equipment(status);
CREATE INDEX IF NOT EXISTS idx_user_tasks_user_id ON user_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_user_tasks_template_id ON user_tasks(template_id);
CREATE INDEX IF NOT EXISTS idx_user_tasks_policy_id ON user_tasks(policy_id);
CREATE INDEX IF NOT EXISTS idx_change_logs_user_id ON change_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_policy_matches_user_id ON policy_matches(user_id);
CREATE INDEX IF NOT EXISTS idx_policy_matches_policy_id ON policy_matches(policy_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_document_checks_user_task_id ON document_checks(user_task_id);
CREATE INDEX IF NOT EXISTS idx_policies_source_id ON policies(source_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_interests_equipment_id ON marketplace_interests(equipment_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_interests_buyer_user_id ON marketplace_interests(buyer_user_id);
CREATE INDEX IF NOT EXISTS idx_market_price_samples_category ON market_price_samples(category);
"""


class _CompatCursor:
    """psycopg2 커서를 sqlite3.Cursor와 같은 모양으로 감싼다 — services.py 등
    나머지 모든 파일은 이 파일이 바뀐 걸 몰라도 되게 하기 위함(ARCHITECTURE.md
    참고: db.py만 프레임워크/드라이버에 의존하고, 로직 계층은 conn을 인자로만
    받는다)."""

    def __init__(self, cur):
        self._cur = cur
        self._lastrowid = None

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        if self._lastrowid is None:
            row = self._cur.fetchone()
            self._lastrowid = row["id"] if row else None
        return self._lastrowid


class _CompatConnection:
    """psycopg2 커넥션을 sqlite3.Connection과 같은 인터페이스(execute/executemany/
    commit/close)로 감싼다. 쿼리 문자열의 `?` 자리표시자는 SQL 리터럴 안에
    바인드 자리표시자 외 용도로 쓰인 곳이 없음을 확인했으므로 `%s`로 그대로
    치환한다. 모든 테이블의 PK 컬럼명이 `id`로 통일돼 있어, INSERT문에
    `RETURNING id`를 자동으로 붙여 sqlite3의 `cursor.lastrowid`를 재현한다."""

    def __init__(self, pg_conn):
        self._conn = pg_conn
        # app.py는 Streamlit 프로세스 전체가 이 커넥션 하나를 공유한다(여러
        # 세션이 각자 스레드에서 동시에 실행됨). psycopg2 커넥션 하나를 여러
        # 스레드가 동시에 쓰면 "another command is already in progress" 같은
        # 에러나 응답 뒤섞임이 날 수 있어, 모든 연산을 이 락으로 직렬화한다.
        # api.py는 요청마다 별도 커넥션을 풀에서 꺼내 쓰므로 경합이 없어
        # 이 락은 사실상 오버헤드가 없다.
        self._lock = threading.RLock()

    def execute(self, sql, params=None):
        with self._lock:
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            sql = sql.replace("?", "%s")
            stripped = sql.lstrip().upper()
            if stripped.startswith("INSERT") and "RETURNING" not in stripped:
                sql += " RETURNING id"
            try:
                # params가 빈 값(None/())이면 인자 없이 execute해야 한다 — psycopg2는
                # params를 넘기는 순간 확장 프로토콜(단일 statement만 허용)을 쓰므로,
                # SCHEMA/MIGRATIONS처럼 세미콜론으로 이어진 여러 statement를 한 번에
                # 실행하는 호출(db.py 내부에서만 씀)이 깨진다.
                if params:
                    cur.execute(sql, params)
                else:
                    cur.execute(sql)
            except Exception:
                # Postgres는 SQLite와 달리 statement 하나가 실패하면 커넥션 전체가
                # "실패한 트랜잭션" 상태로 잠기고, rollback() 전까지 이후 모든 쿼리가
                # 에러난다. app.py는 프로세스 전체가 커넥션 하나를 공유하므로, 이걸
                # 안 하면 한 사용자의 실패한 요청이 재시작 전까지 전체 서비스를
                # 마비시킨다(실제로 재현 가능한 회귀였음).
                self._conn.rollback()
                raise
            return _CompatCursor(cur)

    def executemany(self, sql, seq_of_params):
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.executemany(sql.replace("?", "%s"), seq_of_params)
            except Exception:
                self._conn.rollback()
                raise

    def commit(self):
        with self._lock:
            self._conn.commit()

    def rollback(self):
        with self._lock:
            self._conn.rollback()

    def close(self):
        # 실제로 TCP 연결을 끊지 않고 풀에 반납한다(app.py/api.py는 여전히
        # get_connection()/.close()만 알면 되고, 풀 존재 자체를 몰라도 된다).
        # 반납 전에 rollback으로 커넥션을 깨끗한 상태로 되돌린다 — 호출부가
        # commit()을 깜빡했더라도 다음에 이 커넥션을 받는 쪽이 이전 요청의
        # 미완료 트랜잭션을 물려받지 않게 하기 위함.
        with self._lock:
            try:
                self._conn.rollback()
            except Exception:
                pass
        _get_pool().putconn(self._conn)


_pool = None
_pool_init_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_init_lock:
            if _pool is None:
                # api.py는 요청마다 커넥션 하나를 빌렸다 반납하고, app.py는
                # 하나를 영구히 들고 있는다 — 20이면 둘 다 충분한 여유.
                _pool = psycopg2.pool.ThreadedConnectionPool(1, 20, DATABASE_URL)
    return _pool


def get_connection():
    pg_conn = _get_pool().getconn()
    return _CompatConnection(pg_conn)


def init_db(conn):
    conn.execute(SCHEMA)
    conn.commit()
    conn.execute(MIGRATIONS)
    conn.commit()
    conn.execute(INDEXES)
    conn.commit()
