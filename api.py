"""
FastAPI 백엔드 — app.py(Streamlit)와 동급의 "전달 계층". ARCHITECTURE.md 참고:
여기서는 fastapi/starlette를 직접 써도 되지만, services.py/analytics.py/
supervisor_graph.py 등 로직 계층 함수는 그대로 호출만 하고 시그니처를 바꾸지
않는다 — 이 파일이 통째로 없어져도 로직 계층은 영향받지 않아야 한다.

지금은 "본 공사" 1단계로, 인증 + 대시보드/업무 + 멀티에이전트 챗봇 + 정책 목록 +
중고품(설비) CRUD + 설비 매칭 + 사진인식까지 구현했다. app.py 화면 기준으로
남은 건 없고, 이후는 프론트엔드/실제 인증/Postgres 같은 별개 공정이다.

실행: uvicorn api:app --reload --port 8000
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import analytics
import db
import llm_client
import pricing_model
import trade_graph
import seed
import services
from config import API_SESSION_SECRET
from llm_client import classify_product_image
from supervisor_graph import run_supervisor


# ---------- DB 연결: Streamlit의 st.cache_resource와 동일한 절충 — 앱 시작 시
# 1회 생성해 요청마다 재사용한다. SQLite 동시쓰기 한계는 별도 작업(Postgres
# 마이그레이션)에서 다룬다. ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.get_connection()
    db.init_db(conn)
    app.state.conn = conn
    yield


app = FastAPI(title="다음걸음 API", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=API_SESSION_SECRET)
# 프론트엔드(web/)를 별도 정적 서버(다른 포트)로 띄워 쓰므로 CORS를 열어준다.
# 쿠키 기반 세션을 쓰니 allow_credentials=True + 구체적인 origin이 필요하다
# (와일드카드 "*"는 credentials와 함께 쓸 수 없음).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500", "http://127.0.0.1:5500",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db(request: Request):
    return request.app.state.conn


# ---------- LLM 쿼터: 요청별 사용자를 ContextVar로 식별해 llm_client.py에 주입.
# Streamlit은 세션마다 별도 스레드라 모듈 전역변수를 못 썼지만(과거 버그 참고),
# FastAPI는 요청마다 asyncio 컨텍스트가 올바르게 격리되므로 ContextVar로 충분하다.
# ----------

_current_user_var: ContextVar[str | None] = ContextVar("current_user", default=None)
_quota_by_user: dict[str, int] = {}


def _get_quota_count() -> int:
    user = _current_user_var.get()
    return _quota_by_user.get(user, 0) if user else 0


def _increment_quota() -> None:
    user = _current_user_var.get()
    if user:
        _quota_by_user[user] = _quota_by_user.get(user, 0) + 1


llm_client.set_quota_backend(_get_quota_count, _increment_quota)


# ---------- 인증: 이름만 입력하는 세션 쿠키(Streamlit 로그인과 동일한 보안 수준,
# 비밀번호 없음). ----------

def get_current_user(request: Request) -> str:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    _current_user_var.set(user_id)
    return user_id


class LoginRequest(BaseModel):
    name: str


class UserOut(BaseModel):
    user_id: str


@app.post("/auth/login", response_model=UserOut)
def login(body: LoginRequest, request: Request, conn=Depends(get_db)):
    cleaned = body.name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="이름을 입력해주세요.")
    seed.seed_if_empty(conn, cleaned)
    request.session["user_id"] = cleaned
    return UserOut(user_id=cleaned)


@app.get("/auth/me", response_model=UserOut)
def me(user_id: str = Depends(get_current_user)):
    return UserOut(user_id=user_id)


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


# ---------- 대시보드/업무: app.py의 get_profile()/get_tasks()와 동일한 쿼리를
# 그대로 옮겼다(둘 다 원래 app.py 전용 헬퍼였을 뿐, services.py의 공용 함수는
# 아니었으므로 여기서 다시 정의하는 게 맞다). ----------

def _get_profile(conn, user_id: str) -> dict:
    row = conn.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else {}


def _get_tasks(conn, user_id: str, stage: str | None = None) -> list[dict]:
    query = """
        SELECT ut.*, tt.title AS task_title, tt.stage AS stage,
               tt.deadline_rule AS deadline_rule, tt.applicability_rules AS applicability_rules,
               tt.source_id AS source_id
        FROM user_tasks ut
        JOIN task_templates tt ON ut.template_id = tt.id
        WHERE ut.user_id = ?
    """
    params = [user_id]
    if stage:
        query += " AND tt.stage = ?"
        params.append(stage)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


class ProfileOut(BaseModel):
    region: str | None = None
    planned_close_date: str | None = None
    reported_closed: str | None = None
    employee_status: str | None = None
    rent_status: str | None = None
    demolition_status: str | None = None
    career_path: str | None = None


class DashboardOut(BaseModel):
    profile: ProfileOut
    completed: int
    total: int


@app.get("/dashboard", response_model=DashboardOut)
def dashboard(user_id: str = Depends(get_current_user), conn=Depends(get_db)):
    profile = _get_profile(conn, user_id)
    tasks = _get_tasks(conn, user_id)
    completed, total = services.compute_progress(tasks)
    profile_fields = {k: profile.get(k) for k in ProfileOut.model_fields}
    return DashboardOut(profile=ProfileOut(**profile_fields), completed=completed, total=total)


class TaskOut(BaseModel):
    id: int
    task_title: str
    stage: str
    status: str
    due_date: str | None = None


@app.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    stage: str | None = None, user_id: str = Depends(get_current_user), conn=Depends(get_db)
):
    tasks = _get_tasks(conn, user_id, stage)
    return [TaskOut(**t) for t in tasks]


class TaskUpdate(BaseModel):
    status: str


@app.patch("/tasks/{task_id}")
def update_task(
    task_id: int, body: TaskUpdate, user_id: str = Depends(get_current_user), conn=Depends(get_db)
):
    services.update_task_status(conn, task_id, body.status, user_id)
    return {"ok": True}


# ---------- 챗봇(멀티에이전트) — 이번 슬라이스의 핵심. supervisor_graph가 API로도
# 그대로 동작함을 증명한다. ----------

class ChatRequest(BaseModel):
    stage_key: str
    question: str


class ChatResponse(BaseModel):
    answer: str
    agent_name: str
    source_type: str


@app.post("/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest, user_id: str = Depends(get_current_user), conn=Depends(get_db)
):
    tasks = _get_tasks(conn, user_id, body.stage_key)
    if not tasks:
        raise HTTPException(status_code=404, detail="해당 단계에 등록된 업무가 없습니다.")
    profile = _get_profile(conn, user_id)
    result = run_supervisor(conn, tasks, body.question, user_id, profile)
    return ChatResponse(
        answer=result["answer"],
        agent_name=result.get("agent_name", ""),
        source_type=result.get("source_type", "none"),
    )


# ---------- 정책: app.py screen_policies()와 동일한 조회+판정 로직. ----------

class PolicyOut(BaseModel):
    id: int
    title: str
    agency: str | None = None
    application_deadline: str | None = None
    label: str
    fit_score: int


@app.get("/policies", response_model=list[PolicyOut])
def list_policies(user_id: str = Depends(get_current_user), conn=Depends(get_db)):
    profile = _get_profile(conn, user_id)
    rows = conn.execute(
        """
        SELECT p.*, s.agency FROM policies p
        JOIN sources s ON p.source_id = s.id
        WHERE s.review_status = '검토완료'
        """
    ).fetchall()
    policies = [dict(r) for r in rows]
    policies = services.sort_policies_by_deadline(
        services.filter_policies_by_career(policies, profile.get("career_path"))
    )

    out = []
    for p in policies:
        label = services.evaluate_eligibility(profile, p["eligibility_rules"])
        score = analytics.score_policy_fit(profile, p["eligibility_rules"])
        out.append(
            PolicyOut(
                id=p["id"], title=p["title"], agency=p.get("agency"),
                application_deadline=p.get("application_deadline"), label=label, fit_score=score,
            )
        )
    return out


# ---------- 중고품(설비): app.py screen_equipment()와 동일한 CRUD + AI 판매 글
# 생성(trade_graph) + 실판매가 기록(services.record_equipment_sale). 소유자
# 본인 것만 조회/수정 가능하도록 매 쿼리에서 user_id를 함께 확인한다(Streamlit
# 버전은 애초에 본인 목록만 SELECT했지만, API는 equipment_id를 직접 받으므로
# 이 확인이 없으면 남의 물품을 조작할 수 있다). ----------

EQUIPMENT_CATEGORIES = ["주방/조리기기", "냉장/냉동", "카페/음료기기", "집기/가구", "전자기기", "기타"]
CONDITION_OPTIONS = ["상", "중", "하"]


class EquipmentCreate(BaseModel):
    name: str
    category: str
    model: str | None = None
    used_period: str | None = None
    condition: str
    defects: str | None = None
    ownership_status: str = "본인 소유"
    asking_price: str | None = None
    pickup_terms: str | None = None


class EquipmentOut(BaseModel):
    id: int
    name: str
    category: str | None = None
    region: str | None = None
    model: str | None = None
    used_period: str | None = None
    condition: str | None = None
    defects: str | None = None
    asking_price: str | None = None
    pickup_terms: str | None = None
    draft: str | None = None
    status: str
    payment_status: str
    final_price: int | None = None
    interest_count: int = 0


def _get_own_equipment(conn, equipment_id: int, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM equipment WHERE id=? AND user_id=?", (equipment_id, user_id)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="물품을 찾을 수 없습니다.")
    return dict(row)


def _equipment_out(conn, row: dict) -> EquipmentOut:
    interests = services.get_equipment_interests(conn, row["id"])
    return EquipmentOut(
        id=row["id"], name=row["name"], category=row.get("category"), region=row.get("region"),
        model=row.get("model"), used_period=row.get("used_period"), condition=row.get("condition"),
        defects=row.get("defects"), asking_price=row.get("asking_price"), pickup_terms=row.get("pickup_terms"),
        draft=row.get("draft"), status=row["status"], payment_status=row["payment_status"],
        final_price=row.get("final_price"), interest_count=len(interests),
    )


@app.get("/equipment", response_model=list[EquipmentOut])
def list_equipment(user_id: str = Depends(get_current_user), conn=Depends(get_db)):
    rows = services.get_user_equipment(conn, user_id)
    return [_equipment_out(conn, r) for r in rows]


@app.post("/equipment", response_model=EquipmentOut, status_code=201)
def create_equipment(
    body: EquipmentCreate, user_id: str = Depends(get_current_user), conn=Depends(get_db)
):
    if body.category not in EQUIPMENT_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category는 {EQUIPMENT_CATEGORIES} 중 하나여야 합니다.")
    if body.condition not in CONDITION_OPTIONS:
        raise HTTPException(status_code=422, detail=f"condition은 {CONDITION_OPTIONS} 중 하나여야 합니다.")

    region = _get_profile(conn, user_id).get("region")
    cur = conn.execute(
        """
        INSERT INTO equipment
            (user_id, name, model, used_period, condition, defects,
             ownership_status, asking_price, pickup_terms, draft, status, payment_status,
             category, region)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, body.name, body.model, body.used_period, body.condition, body.defects,
            body.ownership_status, body.asking_price, body.pickup_terms, "", "보관 중", "미입금",
            body.category, region,
        ),
    )
    conn.commit()
    row = _get_own_equipment(conn, cur.lastrowid, user_id)
    return _equipment_out(conn, row)


class EquipmentStatusUpdate(BaseModel):
    status: str | None = None
    payment_status: str | None = None


@app.patch("/equipment/{equipment_id}", response_model=EquipmentOut)
def update_equipment(
    equipment_id: int, body: EquipmentStatusUpdate,
    user_id: str = Depends(get_current_user), conn=Depends(get_db),
):
    row = _get_own_equipment(conn, equipment_id, user_id)
    if body.status is not None:
        services.log_change(conn, user_id, "equipment", equipment_id, row["status"], body.status)
        conn.execute("UPDATE equipment SET status=? WHERE id=?", (body.status, equipment_id))
        conn.commit()
    if body.payment_status is not None:
        conn.execute("UPDATE equipment SET payment_status=? WHERE id=?", (body.payment_status, equipment_id))
        conn.commit()
    return _equipment_out(conn, _get_own_equipment(conn, equipment_id, user_id))


class ListingRequest(BaseModel):
    style: Literal["short", "blog"] = "short"


class ListingResponse(BaseModel):
    draft: str
    draft_source: str
    channel_tip: str
    predicted_price: int | None = None
    price_range: tuple[int, int] | None = None


@app.post("/equipment/{equipment_id}/listing", response_model=ListingResponse)
def generate_equipment_listing(
    equipment_id: int, body: ListingRequest,
    user_id: str = Depends(get_current_user), conn=Depends(get_db),
):
    item = _get_own_equipment(conn, equipment_id, user_id)
    result = trade_graph.run_trade(conn, item, style=body.style)
    conn.execute("UPDATE equipment SET draft=? WHERE id=?", (result["draft"], equipment_id))
    conn.commit()

    predicted = result["predicted_price"]
    return ListingResponse(
        draft=result["draft"], draft_source=result["draft_source"], channel_tip=result["channel_tip"],
        predicted_price=predicted.get("predicted_price") if predicted.get("ok") else None,
        price_range=tuple(predicted["price_range"]) if predicted.get("ok") else None,
    )


class SaleRequest(BaseModel):
    price_text: str


@app.post("/equipment/{equipment_id}/sale")
def record_sale(
    equipment_id: int, body: SaleRequest,
    user_id: str = Depends(get_current_user), conn=Depends(get_db),
):
    _get_own_equipment(conn, equipment_id, user_id)  # 소유권 확인(없으면 404)
    ok = services.record_equipment_sale(conn, equipment_id, body.price_text)
    if not ok:
        raise HTTPException(status_code=422, detail="판매가를 해석할 수 없습니다. 숫자로 입력해주세요(예: 120만원).")
    return {"ok": True}


# ---------- 설비 매칭: app.py screen_marketplace()와 동일한 필터+업종 추천 랭킹+
# 시세 비교. 카테고리별 시세 샘플은 목록 전체에서 한 번만 조회해 재사용한다
# (screen_marketplace에서 고친 N+1 쿼리 방지 패턴을 그대로 따름). ----------

class MarketplaceListingOut(BaseModel):
    id: int
    name: str
    category: str | None = None
    region: str | None = None
    condition: str | None = None
    asking_price: str | None = None
    pickup_terms: str | None = None
    draft: str | None = None
    match_score: int | None = None
    predicted_price: int | None = None
    price_diff_pct: int | None = None  # 양수 = 시세보다 저렴, 음수 = 시세보다 비쌈


@app.get("/marketplace", response_model=list[MarketplaceListingOut])
def list_marketplace(
    category: str | None = None,
    region: str | None = None,
    business_type: str | None = None,
    user_id: str = Depends(get_current_user), conn=Depends(get_db),
):
    listings = services.get_marketplace_listings(conn, category=category, region=region)
    if business_type:
        listings = sorted(
            listings, key=lambda it: analytics.score_equipment_match(it, business_type), reverse=True
        )

    samples_by_category: dict[str, list] = {}
    out = []
    for item in listings:
        match_score = analytics.score_equipment_match(item, business_type) if business_type else None

        item_category = item.get("category")
        if item_category and item_category not in samples_by_category:
            samples_by_category[item_category] = pricing_model.fetch_samples(conn, item_category)
        predicted = pricing_model.predict_price(
            conn, item_category, item.get("used_period"), item.get("condition"),
            samples=samples_by_category.get(item_category),
        )

        predicted_price = None
        diff_pct = None
        if predicted.get("ok") and predicted.get("predicted_price") is not None:
            predicted_price = predicted["predicted_price"]
            asking_num = pricing_model.parse_price(item.get("asking_price"))
            if asking_num is not None:
                diff_pct = round((predicted_price - asking_num) / predicted_price * 100)

        out.append(
            MarketplaceListingOut(
                id=item["id"], name=item["name"], category=item.get("category"), region=item.get("region"),
                condition=item.get("condition"), asking_price=item.get("asking_price"),
                pickup_terms=item.get("pickup_terms"), draft=item.get("draft"),
                match_score=match_score, predicted_price=predicted_price, price_diff_pct=diff_pct,
            )
        )
    return out


@app.post("/marketplace/{equipment_id}/interest")
def express_interest(
    equipment_id: int, user_id: str = Depends(get_current_user), conn=Depends(get_db)
):
    row = conn.execute("SELECT id FROM equipment WHERE id=?", (equipment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="물품을 찾을 수 없습니다.")
    services.record_marketplace_interest(conn, user_id, equipment_id)
    return {"ok": True}


# ---------- 사진인식: app.py screen_equipment()의 "사진으로 물품 인식"과 동일하게,
# 결과는 참고용 설명 텍스트만 반환한다 — 폼 필드를 자동으로 채우지 않는다는
# 원칙(사람이 확인 후 직접 입력)은 API에서도 그대로 유지한다. 등록된 물품에
# 매어두지 않는 독립 엔드포인트다(app.py도 물품 등록 전 참고용으로만 쓴다). ----------

ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png"}


class PhotoAnalysisOut(BaseModel):
    text: str


@app.post("/equipment/photo-analysis", response_model=PhotoAnalysisOut)
async def analyze_equipment_photo(
    photo: UploadFile = File(...), user_id: str = Depends(get_current_user)
):
    mime_type = photo.content_type or "image/jpeg"
    if mime_type not in ALLOWED_PHOTO_TYPES:
        raise HTTPException(status_code=422, detail="JPG 또는 PNG 이미지만 지원합니다.")
    image_bytes = await photo.read()
    result = classify_product_image(image_bytes, mime_type)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=f"사진 분석에 실패했습니다: {result['error']}")
    return PhotoAnalysisOut(text=result["text"])
