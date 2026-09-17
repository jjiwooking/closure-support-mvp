import json
from datetime import date

import psycopg2
import streamlit as st

from analytics import BUSINESS_TYPE_CATEGORIES, policy_approval_stats, score_equipment_match, score_policy_fit
from db import get_connection, init_db
from llm_client import classify_product_image, set_quota_backend
from pricing_model import fetch_samples, parse_price, predict_price
from research_graph import run_research
from seed import seed_if_empty
from supervisor_graph import run_supervisor
from services import (
    NEXT_STAGE,
    check_stage_complete,
    compute_progress,
    evaluate_eligibility,
    filter_policies_by_career,
    generate_listing,
    get_equipment_interests,
    get_marketplace_listings,
    get_or_create_application,
    get_unread_notifications,
    log_change,
    mark_notification_read,
    priority_sort,
    promote_source_draft,
    record_application_date,
    record_equipment_sale,
    record_marketplace_interest,
    record_supplement,
    register_policy_interest,
    should_suggest_trade,
    sort_policies_by_deadline,
    toggle_document_check,
    update_application_payment,
    update_career_path,
    update_decision_status,
    update_profile,
    update_task_status,
)
from trade_graph import run_trade

EQUIPMENT_CATEGORIES = ["주방/조리기기", "냉장/냉동", "카페/음료기기", "집기/가구", "전자기기", "기타"]
CONDITION_OPTIONS = ["상", "중", "하"]

st.set_page_config(page_title="다음걸음", layout="wide")

st.markdown(
    """
    <style>
    /* 한글 가독성이 좋은 웹폰트(Pretendard) 적용 — 시스템 기본 폰트가 한글에서
    깨지거나 어색하게 보이는 문제 방지 */
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css");

    html, body, [class*="css"] {
        font-family: "Pretendard", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
    }

    /* 버튼 터치 영역 확대 (접근성: 충분한 버튼 크기). 반경은 과하게 둥글리지
    않고 각을 살짝만 죽이는 정도로 절제 */
    div.stButton > button, a[data-testid="stBaseButton-secondary"],
    div[data-testid="stFormSubmitButton"] > button {
        padding: 0.55rem 1.1rem;
        font-size: 1.02rem;
        border-radius: 6px;
    }
    /* 카드형 컨테이너를 더 뚜렷하게 */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 8px;
    }
    /* 채팅 말풍선 여백 확대 */
    div[data-testid="stChatMessage"] {
        padding: 0.6rem 0.9rem;
        border-radius: 8px;
    }
    /* 채팅 입력창을 화면 하단에 고정 — 페이지가 길어져도 스크롤 없이 항상 보이게 */
    div[data-testid="stChatInput"] {
        position: sticky;
        bottom: 0;
        z-index: 999;
        background-color: #FFFFFF;
        border: 2px solid #15803D;
        border-radius: 8px;
        padding: 0.4rem 0.6rem;
        margin-top: 0.5rem;
        box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.08);
    }
    /* 사이드바 메뉴 글자 크게 */
    section[data-testid="stSidebar"] label p {
        font-size: 1.05rem;
    }
    /* 사이드바 네비게이션 버튼: 왼쪽 정렬 풀폭 메뉴처럼 */
    section[data-testid="stSidebar"] div.stButton > button {
        justify-content: flex-start;
        text-align: left;
        border: none;
        font-weight: 500;
    }
    section[data-testid="stSidebar"] div.stButton > button p {
        text-align: left;
    }
    section[data-testid="stSidebar"] div.stButton {
        margin-bottom: 0.15rem;
    }
    section[data-testid="stSidebar"] h3 {
        font-size: 0.85rem;
        color: #6B7280;
        margin-top: 0.6rem;
        margin-bottom: 0.2rem;
        text-transform: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

@st.cache_resource
def _get_app_connection():
    """Streamlit은 버튼 클릭 등 모든 상호작용마다 스크립트 전체를 재실행하므로,
    캐싱하지 않으면 DB 연결·스키마 마이그레이션이 매번 반복된다. st.cache_resource로
    감싸 프로세스당 한 번만 실행되게 한다. 특정 사용자 데모 시드(seed_if_empty)는
    로그인 전엔 어떤 이름으로 로그인할지 알 수 없으므로 여기서 하지 않고,
    로그인 직후 별도로 호출한다."""
    try:
        conn = get_connection()
    except psycopg2.OperationalError:
        st.error(
            "데이터베이스에 연결할 수 없습니다. `DATABASE_URL`이 설정되지 않았거나 "
            "잘못된 값입니다 (기본값은 로컬 docker-compose용 localhost 주소라 "
            "배포 환경에서는 동작하지 않습니다). Streamlit Cloud라면 앱 Settings → "
            "Secrets에 실제 Postgres 접속 문자열을 등록하세요."
        )
        st.stop()
    init_db(conn)
    return conn


conn = _get_app_connection()


def _get_session_llm_count() -> int:
    return st.session_state.get("_llm_call_count", 0)


def _increment_session_llm_count() -> None:
    st.session_state["_llm_call_count"] = st.session_state.get("_llm_call_count", 0) + 1


# llm_client.py는 이 앱이 Streamlit이라는 걸 몰라야 하므로(나중에 다른 프레임워크로
# 바꿀 때 로직 계층을 그대로 재사용하기 위함), 세션별 카운터를 여기서 주입한다.
set_quota_backend(_get_session_llm_count, _increment_session_llm_count)


def _login_screen():
    """비밀번호 없는 이름 기반 '로그인'. 심사평 이후 발견된 위험(모든 접속자가
    데이터를 공유하는 문제)을 없애기 위한 최소 구현 — 해커톤 데모 범위에 맞춰
    이름만으로 구분하고, 같은 이름으로 다시 오면 이전 기록을 그대로 이어서
    보여준다."""
    st.title("다음걸음")
    st.write(
        "상호명 또는 닉네임을 입력하고 시작하세요. 비밀번호는 없으며, "
        "같은 이름으로 다시 오면 이전 기록이 이어집니다."
    )
    with st.form("login_form"):
        name_input = st.text_input("상호명 / 닉네임")
        submitted = st.form_submit_button("시작하기")
    if submitted:
        cleaned = name_input.strip()
        if not cleaned:
            st.warning("이름을 입력해주세요.")
        else:
            st.session_state["user_id"] = cleaned
            st.rerun()


if "user_id" not in st.session_state:
    _login_screen()
    st.stop()


def current_user_id():
    """st.session_state에서 매번 새로 읽는다. Streamlit은 세션마다 별도
    프로세스가 아니라 같은 프로세스 안에서 스크립트를 재실행하므로, 이 값을
    한 번 읽어 모듈 전역변수에 담아두면(예전 방식처럼) 동시 접속 시
    한 세션의 재실행이 그 전역변수를 바꾸는 순간 다른 세션이 실행 중이던
    코드가 그 값을 읽어가는 경쟁 상태가 생긴다. st.session_state는 내부적으로
    현재 실행 중인 세션에 자동으로 바인딩되므로, 매 호출마다 여기서 새로
    읽어야 세션 간 데이터가 섞이지 않는다."""
    return st.session_state["user_id"]


seed_if_empty(conn, current_user_id())


def get_profile():
    row = conn.execute("SELECT * FROM profiles WHERE user_id=?", (current_user_id(),)).fetchone()
    return dict(row) if row else {}


def get_tasks(stage=None):
    query = """
        SELECT ut.*, tt.title AS task_title, tt.stage AS stage,
               tt.deadline_rule AS deadline_rule, tt.applicability_rules AS applicability_rules,
               tt.source_id AS source_id
        FROM user_tasks ut
        JOIN task_templates tt ON ut.template_id = tt.id
        WHERE ut.user_id = ?
    """
    params = [current_user_id()]
    if stage:
        query += " AND tt.stage = ?"
        params.append(stage)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _selectbox_index(options, value, fallback_label):
    if value in options:
        return options.index(value)
    return options.index(fallback_label)


def render_profile_editor():
    profile = get_profile()

    with st.expander("내 상황 정보 수정", expanded=False):
        with st.form("edit_profile"):
            region = st.text_input(
                "소재지 (자치구, 모르면 '모름'이라고 입력)", value=profile.get("region") or ""
            )
            planned_default = (
                date.fromisoformat(profile["planned_close_date"])
                if profile.get("planned_close_date")
                else date.today()
            )
            planned = st.date_input("폐업 예정일", value=planned_default)

            reported_options = ["미신고", "신고완료", "모름"]
            reported = st.selectbox(
                "폐업신고 여부", reported_options,
                index=_selectbox_index(reported_options, profile.get("reported_closed"), "모름"),
            )
            employee_options = ["있음", "없음", "모름"]
            employee = st.selectbox(
                "직원 유무", employee_options,
                index=_selectbox_index(employee_options, profile.get("employee_status"), "모름"),
            )
            rent_options = ["임차", "자가", "모름"]
            rent = st.selectbox(
                "점포 임차 여부", rent_options,
                index=_selectbox_index(rent_options, profile.get("rent_status"), "모름"),
            )
            demolition_options = ["미정", "예정", "진행", "완료"]
            demolition = st.selectbox(
                "철거 상태", demolition_options,
                index=_selectbox_index(demolition_options, profile.get("demolition_status"), "미정"),
            )
            if st.form_submit_button("변경 내용 확인"):
                st.session_state["pending_profile_change"] = {
                    "region": region or "모름",
                    "planned_close_date": planned.isoformat(),
                    "reported_closed": reported,
                    "employee_status": employee,
                    "rent_status": rent,
                    "demolition_status": demolition,
                }

        pending = st.session_state.get("pending_profile_change")
        if pending:
            labels = {
                "region": "소재지", "planned_close_date": "폐업 예정일",
                "reported_closed": "폐업신고 여부", "employee_status": "직원 유무",
                "rent_status": "점포 임차 여부", "demolition_status": "철거 상태",
            }
            diffs = [
                f"- {labels[k]}: {profile.get(k) or '미입력'} → {v}"
                for k, v in pending.items() if str(profile.get(k)) != str(v)
            ]
            if not diffs:
                st.caption("변경된 내용이 없습니다.")
                del st.session_state["pending_profile_change"]
            else:
                st.info("**변경 내용 확인** (저장 전 확인이 필요합니다)\n" + "\n".join(diffs))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("변경 확정", key="confirm_profile_change"):
                        update_profile(conn, current_user_id(), pending)
                        del st.session_state["pending_profile_change"]
                        st.success("반영되었습니다. 사용자 예정일 기준 업무의 기한이 재계산되었습니다.")
                        st.rerun()
                with c2:
                    if st.button("취소", key="cancel_profile_change"):
                        del st.session_state["pending_profile_change"]
                        st.rerun()


STAGE_LABELS = {"준비": "폐업 준비", "진행": "폐업 진행", "후": "폐업 후"}


def render_stage_checklist(all_tasks):
    """단계별로 접이식 체크리스트를 보여준다. 체크하면 '사용자 완료'로,
    체크 해제하면 '진행 중'으로 기록한다. '확인 필요' 상태는 체크박스 옆에
    별도로 표시해 완료 여부와 헷갈리지 않게 한다."""
    for stage_key in ["준비", "진행", "후"]:
        stage_tasks = [t for t in all_tasks if t["stage"] == stage_key]
        stage_label = STAGE_LABELS[stage_key]
        stage_done = sum(1 for t in stage_tasks if t["status"] == "사용자 완료")

        with st.expander(
            f"{stage_label} ({stage_done}/{len(stage_tasks)} 완료)",
            expanded=(stage_key == "준비"),
        ):
            if not stage_tasks:
                st.caption("등록된 업무가 없습니다.")
                continue

            for t in priority_sort(stage_tasks):
                checked = t["status"] == "사용자 완료"
                c1, c2 = st.columns([4, 2])
                with c1:
                    new_checked = st.checkbox(
                        t["task_title"], value=checked, key=f"chk_{t['id']}"
                    )
                with c2:
                    if t["status"] == "확인 필요":
                        st.markdown(":orange[확인 필요]")
                    else:
                        st.caption(f"기한: {t.get('due_date') or '미정'}")
                if new_checked != checked:
                    new_status = "사용자 완료" if new_checked else "진행 중"
                    update_task_status(conn, t["id"], new_status, current_user_id())
                    if new_status == "사용자 완료":
                        updated_stage_tasks = [
                            {**x, "status": new_status} if x["id"] == t["id"] else x
                            for x in stage_tasks
                        ]
                        if check_stage_complete(updated_stage_tasks):
                            next_stage = NEXT_STAGE.get(stage_key)
                            st.session_state["stage_transition"] = {
                                "from": stage_key, "to": next_stage
                            }
                            if next_stage:
                                st.session_state["current_page"] = STAGE_LABELS[next_stage]
                    st.rerun()


def render_stage_transition_banner():
    """체크리스트 완료로 자동 단계전환이 막 일어났다면 한 번만 배너로 알린다.
    '준비' 단계를 막 끝냈고 아직 처분 안 된 집기가 있으면 중고거래 이동도 제안한다."""
    transition = st.session_state.pop("stage_transition", None)
    if not transition:
        return

    from_key, to_key = transition["from"], transition["to"]
    if to_key:
        st.success(
            f"'{STAGE_LABELS[from_key]}' 단계를 모두 완료했어요! '{STAGE_LABELS[to_key]}' 단계로 이동했습니다."
        )
    else:
        st.success(f"'{STAGE_LABELS[from_key]}' 단계를 모두 완료했어요! 모든 단계를 마쳤습니다.")

    if from_key == "준비" and should_suggest_trade(conn, current_user_id()):
        st.info("정리할 집기가 있다면 지금 중고거래를 준비해보세요.")
        if st.button("중고품 관리로 이동", key="goto_equipment_from_banner"):
            st.session_state["current_page"] = "중고품 관리"
            st.rerun()


def render_notifications():
    """백그라운드 정책리서치가 찾아낸, 아직 읽지 않은 맞춤 알림을 보여준다."""
    for n in get_unread_notifications(conn, current_user_id()):
        c1, c2 = st.columns([5, 1])
        with c1:
            st.info(n["message"])
        with c2:
            if st.button("읽음으로 표시", key=f"notif_read_{n['id']}"):
                mark_notification_read(conn, n["id"])
                st.rerun()


def screen_dashboard():
    render_stage_transition_banner()
    render_notifications()
    st.header("폐업 진행 상황")
    st.write(
        "폐업은 **폐업 준비 → 폐업 진행 → 폐업 후** 3단계로 진행돼요. "
        "아래 체크리스트에서 바로 완료 처리하거나, 왼쪽 '단계별 코칭' 메뉴에서 AI 코칭을 받을 수 있습니다."
    )
    with st.expander("AI 에이전트 구조 보기", expanded=False):
        st.markdown(
            "**단계별 코칭 챗봇** 질문 하나에도 여러 에이전트가 역할을 나눠 협업합니다.\n\n"
            "- **라우팅 에이전트**: 질문을 아래 세 전문 에이전트 중 하나로 연결\n"
            "- **가이드 에이전트**: 절차·서류 안내 (RAG 검색 + LLM)\n"
            "- **정책상담 에이전트**: 적합도 점수 기반 지원사업 추천 (스코어링 모델 + LLM)\n"
            "- **거래상담 에이전트**: 등록 물품의 AI 추천가 안내 (KNN 회귀 모델)\n\n"
            "백그라운드/화면별로는 이런 에이전트들도 따로 동작해요.\n"
            "- **정책수집 → 정책구조화 → 매칭** ('지원정책' 화면 관리자용 리서치 버튼)\n"
            "- **가격추정 → 글초안 → 채널안내** (중고품 등록 후 '판매 글 생성')"
        )
    render_profile_editor()
    profile = get_profile()
    all_tasks = get_tasks()
    completed, total = compute_progress(all_tasks)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("폐업 예정일", profile.get("planned_close_date") or "미정")
    with col2:
        if total == 0:
            st.metric("진행률", "상황 입력 필요")
        else:
            st.metric("진행률", f"{completed}/{total} 완료")
            st.progress(completed / total)
    with col3:
        unresolved = [t for t in all_tasks if t["status"] == "확인 필요"]
        st.metric("확인 필요 항목", f"{len(unresolved)}개")

    st.subheader("할 일 체크리스트")
    render_stage_checklist(all_tasks)


def _answer_stage_context(stage_key, tasks, question_text):
    """라우팅 에이전트(supervisor_graph)가 질문 성격에 따라 가이드/정책상담/거래상담
    중 알맞은 전문 에이전트에게 위임하고, 그 결과를 대화 형식으로 표시할 텍스트로
    바꾼다. source_type에 따라 검증된 등록 자료 답변과 미검토 실시간 검색 답변을
    화면에서 구조적으로 구분해 보여준다.

    stage_key별 chat_focus_*에 직전에 다룬 물품을 기억해두었다가 넘겨서,
    '그거 얼마야?' 같은 대명사 후속 질문도 거래상담 에이전트가 이어받을 수 있게 한다."""
    focus_key = f"chat_focus_{stage_key}"
    focus = st.session_state.get(focus_key) or {}
    response = run_supervisor(conn, tasks, question_text, current_user_id(), get_profile(), focus=focus)
    st.session_state[focus_key] = response.get("focus") or {}
    text = response["answer"]
    source_type = response.get("source_type", "none")
    agent_name = response.get("agent_name")

    if source_type == "local":
        if response["source_ids"]:
            source = conn.execute(
                "SELECT * FROM sources WHERE id=?", (response["source_ids"][0],)
            ).fetchone()
            if source:
                text += (
                    f"\n\n*출처: {source['agency']} · {source['title']} · "
                    f"확인일: {source['reviewed_at'] or '확인 필요'}*"
                )
        if response["actions"]:
            links = "\n".join(
                f"- [관련 공식 링크 열기](https://example-official-site.invalid/{a['link_id']})"
                for a in response["actions"]
            )
            text += "\n\n" + links
    elif source_type == "web_search":
        text = (
            "**[미검토 · 실시간 검색 결과]**\n\n"
            f"{text}\n\n"
            "*이 답변은 사람이 검토한 등록 자료가 아니라 실시간 검색 결과입니다. "
            "반드시 공식 사이트에서 직접 확인하세요.*"
        )
    # source_type == "none"이면 response["answer"] 자체가 이미 확인 필요 안내다.

    if agent_name:
        text = f"*{agent_name}*\n\n{text}"
    return text


def render_stage_chat(stage_key, stage_label, tasks):
    """단계 하나당 챗봇 하나. 업무를 먼저 고를 필요 없이 이 단계에 속한 모든
    업무의 근거를 한꺼번에 참고해서 답한다."""
    history_key = f"chat_stage_{stage_key}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []

    for role, content in st.session_state[history_key]:
        with st.chat_message(role):
            st.markdown(content)

    user_q = st.chat_input(f"{stage_label} 챗봇에게 물어보세요")
    if user_q:
        st.session_state[history_key].append(("user", user_q))
        answer = _answer_stage_context(stage_key, tasks, user_q)
        st.session_state[history_key].append(("assistant", answer))
        st.rerun()


def render_task_core(task):
    st.write(f"**내게 적용되는 이유**: {task.get('applicability_rules') or '확인 필요'}")
    st.write(
        f"**기한**: {task.get('due_date') or '미정'} · 근거: {task.get('deadline_rule') or '확인 필요'}"
    )

    docs = conn.execute(
        """
        SELECT dc.*, dg.name AS doc_name, dg.issuance_link_id, dg.submission_link_id, dg.instructions
        FROM document_checks dc
        JOIN document_guides dg ON dc.document_guide_id = dg.id
        WHERE dc.user_task_id = ?
        """,
        (task["id"],),
    ).fetchall()

    if docs:
        st.write("**준비물**")
        for d in docs:
            d = dict(d)
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.write(f"- {d['doc_name']}: {d['instructions']}")
            with c2:
                st.link_button(
                    "공식 발급 사이트로 이동",
                    f"https://example-official-site.invalid/{d['issuance_link_id']}",
                    key=f"issue_link_{d['id']}",
                )
            with c3:
                checked = st.checkbox(
                    "준비 완료", value=bool(d["user_checked"]), key=f"doc_{d['id']}"
                )
                if checked != bool(d["user_checked"]):
                    toggle_document_check(conn, d["id"], checked, current_user_id())
                    st.rerun()

    st.link_button(
        "공식 신청 사이트로 이동",
        "https://example-official-site.invalid/apply",
        key=f"apply_link_{task['id']}",
    )
    st.caption("링크 이동은 참고용이며, 실제 신청·제출 상태는 자동으로 반영되지 않습니다.")

    st.write(f"**현재 상태**: {task['status']}")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("신청 완료로 기록", key=f"done_{task['id']}"):
            update_task_status(conn, task["id"], "사용자 완료", current_user_id())
            st.rerun()
    with c2:
        if st.button("완료 취소", key=f"undo_{task['id']}"):
            update_task_status(conn, task["id"], "진행 중", current_user_id())
            st.rerun()
    with c3:
        if st.button("확인 필요로 표시", key=f"flag_{task['id']}"):
            update_task_status(conn, task["id"], "확인 필요", current_user_id())
            st.rerun()

    if task.get("policy_id"):
        st.divider()
        st.write("**지원사업 신청 기록**")
        policy_row = conn.execute(
            "SELECT * FROM policies WHERE id=?", (task["policy_id"],)
        ).fetchone()
        if policy_row:
            st.caption(f"연결된 지원사업: {policy_row['title']}")
        application = get_or_create_application(conn, task["id"])

        c1, c2 = st.columns(2)
        with c1:
            applied_default = (
                date.fromisoformat(application["applied_at"])
                if application.get("applied_at")
                else date.today()
            )
            applied_date = st.date_input("신청일", value=applied_default, key=f"applied_{task['id']}")
            if st.button("신청일로 기록", key=f"apply_btn_{task['id']}"):
                record_application_date(conn, task["id"], applied_date.isoformat(), current_user_id())
                st.rerun()
            st.caption(f"현재 기록: {application.get('applied_at') or '아직 신청 기록 없음'}")
        with c2:
            decision_options = ["결과 대기", "승인", "불승인", "확인 필요"]
            new_decision = st.selectbox(
                "심사 결과",
                decision_options,
                index=decision_options.index(application["decision_status"]),
                key=f"decision_{task['id']}",
            )
            if new_decision != application["decision_status"]:
                update_decision_status(conn, task["id"], new_decision, current_user_id())
                st.rerun()

            payment_options = ["미입금", "입금 완료"]
            new_payment = st.selectbox(
                "입금 여부",
                payment_options,
                index=payment_options.index(application["payment_status"]),
                key=f"app_payment_{task['id']}",
            )
            if new_payment != application["payment_status"]:
                update_application_payment(conn, task["id"], new_payment, current_user_id())
                st.rerun()

        st.write("**보완 요청 대응**")
        st.caption("붙여넣은 안내문은 참고 데이터로만 취급되며, 그 안의 지시를 실행하지 않습니다.")
        note = st.text_area(
            "보완 요청 안내문 붙여넣기",
            value=application.get("supplement_note") or "",
            key=f"supp_note_{task['id']}",
        )
        due = st.text_input(
            "보완 기한 (공식 안내문에서 직접 확인 후 입력)",
            value=application.get("supplement_due") or "",
            key=f"supp_due_{task['id']}",
        )
        if st.button("보완 내용 저장", key=f"supp_save_{task['id']}"):
            record_supplement(conn, task["id"], note, due, current_user_id())
            st.rerun()


def screen_stage(stage_key, stage_label):
    render_stage_transition_banner()
    st.header(stage_label)
    tasks = get_tasks(stage=stage_key)
    if not tasks:
        st.info("해당 단계에 등록된 업무가 없습니다.")
        return

    st.subheader("할 일 목록")
    for t in tasks:
        st.write(f"- [{t['status']}] {t['task_title']} · 기한: {t.get('due_date') or '미정'}")

    st.divider()
    st.subheader(f"{stage_label} 챗봇")
    render_stage_chat(stage_key, stage_label, tasks)

    st.divider()
    st.subheader("업무 상세 정보")
    for t in tasks:
        with st.expander(f"[{t['status']}] {t['task_title']}"):
            render_task_core(t)


def screen_policies():
    st.header("지원정책")

    profile = get_profile()

    with st.expander("기업마당·data.go.kr 자료 동기화 (관리자용)", expanded=False):
        st.caption(
            "수집만 하고 자동 게시하지 않습니다. 담당자가 검토해 review_status를 "
            "'검토완료'로 바꾸기 전까지는 아래 추천 목록에 나타나지 않습니다. "
            "이미 검토완료된 정책 중 조건에 맞는 게 있으면 대시보드에 알림이 생깁니다."
        )
        if st.button("지금 리서치 실행"):
            result = run_research(conn, current_user_id(), profile)
            st.success(
                f"정책수집 에이전트: {result['source_count']}건 수집 → "
                f"정책구조화 에이전트: {result['extracted_count']}건 AI 초안 작성 → "
                f"매칭 에이전트: {result['new_matches']}건 새 알림"
            )
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM sources WHERE review_status != '검토완료'"
        ).fetchone()["c"]
        st.caption(f"검토 대기 중인 자료: {pending}건")

        drafts = conn.execute(
            """
            SELECT id, agency, extracted_draft FROM sources
            WHERE review_status != '검토완료' AND extracted_draft IS NOT NULL
            """
        ).fetchall()
        if drafts:
            st.markdown("**AI 구조화 초안 검토** (정책구조화 에이전트 결과)")
            st.caption("추정 조건은 AI가 원문에서 읽은 것이며, 등록 후에도 필요하면 직접 보완할 수 있습니다.")
        for d in drafts:
            try:
                draft = json.loads(d["extracted_draft"])
            except (json.JSONDecodeError, TypeError):
                continue
            with st.container(border=True):
                st.write(f"**{draft.get('title') or '(제목 확인 필요)'}** · {d['agency']}")
                st.caption(draft.get("eligibility_summary") or "요약 없음")
                st.caption(
                    f"진로 추정: {draft.get('target_career_guess') or '확인 필요'} · "
                    f"마감일 추정: {draft.get('application_deadline_guess') or '상시/미정'}"
                )
                rules_guess = {
                    k: v for k, v in (draft.get("eligibility_rules_guess") or {}).items() if v
                }
                rules_preview = ", ".join(f"{k}={v}" for k, v in rules_guess.items())
                st.caption(f"추정 조건: {rules_preview or '없음(등록 후 직접 보완 필요)'}")
                if st.button("이 초안으로 정책 등록", key=f"promote_{d['id']}"):
                    promote_source_draft(conn, d["id"], draft)
                    st.success("등록되었습니다. 세부 조건은 필요 시 직접 보완하세요.")
                    st.rerun()

    career_options = ["모름", "재창업", "취업"]
    current_career = profile.get("career_path") or "모름"
    new_career = st.selectbox(
        "진로 (재창업 또는 취업을 고르면 관련 정책만 골라 보여드려요)",
        career_options,
        index=career_options.index(current_career) if current_career in career_options else 0,
    )
    if new_career != current_career:
        update_career_path(conn, current_user_id(), new_career)
        st.rerun()

    approval = policy_approval_stats(conn, current_career)
    if approval["decided"]:
        st.caption(
            f"유사 지원사업 과거 승인율: {approval['rate'] * 100:.0f}% "
            f"(과거 심사결과 {approval['decided']}건 기준)"
        )

    rows = conn.execute(
        """
        SELECT p.*, s.agency, s.title AS source_title, s.url AS source_url, s.reviewed_at
        FROM policies p
        JOIN sources s ON p.source_id = s.id
        WHERE s.review_status = '검토완료'
        """
    ).fetchall()
    rows = [dict(r) for r in rows]
    rows = sort_policies_by_deadline(filter_policies_by_career(rows, current_career))

    if not rows:
        st.info("현재 진로 기준으로 등록된 지원사업이 없습니다.")
        return

    registered_policy_ids = {
        row["policy_id"]
        for row in conn.execute(
            "SELECT policy_id FROM user_tasks WHERE user_id=? AND policy_id IS NOT NULL", (current_user_id(),)
        ).fetchall()
    }

    for r in rows:
        label = evaluate_eligibility(profile, r["eligibility_rules"])
        fit_score = score_policy_fit(profile, r["eligibility_rules"])
        with st.container(border=True):
            st.subheader(r["title"])
            st.write(f"기관: {r['agency']} · 신청기간: {r.get('period') or '확인 필요'}")
            st.write(f"마감일: {r.get('application_deadline') or '상시/미정'}")
            st.write(f"공식 공고: {r['source_url']}")
            badge_color = {
                "입력 조건 부합": "green",
                "추가 정보 필요": "orange",
                "현재 조건 불일치": "red",
                "모집 상태 확인 필요": "gray",
            }.get(label, "gray")
            st.markdown(f":{badge_color}[● {label}]")
            st.progress(fit_score / 100, text=f"AI 적합도 점수 {fit_score}/100")
            st.caption(f"자료 검토일: {r.get('reviewed_at') or '확인 필요'}")

            if r["id"] in registered_policy_ids:
                st.caption("관심 사업으로 등록됨 — '폐업 준비'/'폐업 진행' 화면에서 진행 상황을 기록하세요.")
            elif st.button("관심 사업으로 등록", key=f"interest_{r['id']}"):
                _, created = register_policy_interest(conn, r["id"], current_user_id())
                if created:
                    st.success("등록되었습니다. '폐업 진행' 화면에서 진행 상황을 기록하세요.")
                    st.rerun()
                else:
                    st.warning("등록에 필요한 업무 템플릿을 찾을 수 없습니다. (확인 필요)")


def screen_equipment():
    st.header("중고품 관리")

    with st.expander("사진으로 물품 인식 (선택, 등록 전 참고용)", expanded=False):
        st.caption(
            "사진을 올리고 분석하면 품목·브랜드·상태를 AI가 추정해 알려드려요. "
            "확정된 정보가 아니니 실제 값은 직접 확인한 뒤 아래 '새 물품 등록' 폼에 입력하세요."
        )
        photo = st.file_uploader("물품 사진", type=["jpg", "jpeg", "png"], key="equipment_photo")
        if photo and st.button("AI로 분석하기", key="analyze_equipment_photo"):
            result = classify_product_image(photo.getvalue(), photo.type or "image/jpeg")
            if result["ok"]:
                st.session_state["equipment_photo_analysis"] = result["text"]
            else:
                st.warning(f"사진 분석에 실패했습니다: {result['error']}")
        if st.session_state.get("equipment_photo_analysis"):
            st.info(
                f"AI 추정: {st.session_state['equipment_photo_analysis']}\n\n"
                "*참고용 설명이며, 실제 값은 아래 등록 폼에 직접 입력해주세요.*"
            )

    with st.expander("새 물품 등록", expanded=False):
        with st.form("new_equipment"):
            name = st.text_input("물품명")
            category = st.selectbox("카테고리", EQUIPMENT_CATEGORIES)
            model = st.text_input("모델 (모르면 비워두세요)")
            used_period = st.text_input("사용 기간 (예: 2년, 6개월)")
            condition = st.selectbox("상태", CONDITION_OPTIONS, index=1)
            defects = st.text_input("하자")
            ownership_status = st.selectbox("본인 소유 확인", ["본인 소유", "확인 필요"])
            asking_price = st.text_input("희망 가격")
            pickup_terms = st.text_input("수거 조건")
            submitted = st.form_submit_button("등록")
            if submitted:
                if not name:
                    st.warning("물품명은 필수입니다.")
                else:
                    region = get_profile().get("region")
                    conn.execute(
                        """
                        INSERT INTO equipment
                            (user_id, name, model, used_period, condition, defects,
                             ownership_status, asking_price, pickup_terms, draft, status, payment_status,
                             category, region)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            current_user_id(), name, model, used_period, condition, defects,
                            ownership_status, asking_price, pickup_terms, "", "보관 중", "미입금",
                            category, region,
                        ),
                    )
                    conn.commit()
                    st.session_state.pop("equipment_photo_analysis", None)
                    st.success("등록되었습니다.")
                    st.rerun()

    rows = conn.execute("SELECT * FROM equipment WHERE user_id=?", (current_user_id(),)).fetchall()
    if not rows:
        st.info("등록된 물품이 없습니다.")
        return

    status_options = ["보관 중", "판매 중", "예약", "처분 완료"]
    payment_options = ["미입금", "입금 완료"]

    for r in rows:
        r = dict(r)
        with st.container(border=True):
            st.subheader(r["name"])
            st.write(
                f"카테고리: {r.get('category') or '미지정'} · "
                f"모델: {r['model'] or '확인 필요'} · 상태: {r['condition'] or '확인 필요'} · "
                f"하자: {r['defects'] or '확인 필요'}"
            )
            st.write(
                f"희망 가격: {r['asking_price'] or '확인 필요'} · 수거 조건: {r['pickup_terms'] or '확인 필요'}"
            )

            interests = get_equipment_interests(conn, r["id"])
            if interests:
                buyers = ", ".join(i["buyer_user_id"] for i in interests)
                st.info(f"관심 표시 {len(interests)}명: {buyers}")

            style = st.radio(
                "판매 글 스타일", ["짧은 글", "블로그 스타일"], horizontal=True, key=f"style_{r['id']}"
            )
            if st.button("판매 글 생성 (AI)", key=f"gen_{r['id']}"):
                trade_result = run_trade(conn, r, style="short" if style == "짧은 글" else "blog")
                conn.execute("UPDATE equipment SET draft=? WHERE id=?", (trade_result["draft"], r["id"]))
                conn.commit()
                st.session_state[f"trade_result_{r['id']}"] = trade_result
                st.rerun()

            trade_result = st.session_state.get(f"trade_result_{r['id']}")
            if trade_result:
                predicted = trade_result["predicted_price"]
                if predicted.get("ok"):
                    low, high = predicted["price_range"]
                    st.info(
                        f"AI 추천가: {predicted['predicted_price']:,}원 "
                        f"(유사 사례 {low:,}~{high:,}원, {predicted['sample_size']}건) · {predicted['message']}"
                    )
                elif predicted.get("message"):
                    st.caption(predicted["message"])

            if r["draft"]:
                source_label = ""
                if trade_result:
                    source_label = " · AI 생성" if trade_result["draft_source"] == "llm" else " · 기본 템플릿"
                st.text_area(
                    f"판매 글{source_label} (복사해서 사용하세요)",
                    value=r["draft"], height=220, key=f"draft_{r['id']}",
                )
                if trade_result and trade_result.get("channel_tip"):
                    st.markdown(trade_result["channel_tip"])

            c1, c2 = st.columns(2)
            with c1:
                new_status = st.selectbox(
                    "처분 상태",
                    status_options,
                    index=status_options.index(r["status"]),
                    key=f"status_{r['id']}",
                )
                if new_status != r["status"]:
                    log_change(conn, current_user_id(), "equipment", r["id"], r["status"], new_status)
                    conn.execute("UPDATE equipment SET status=? WHERE id=?", (new_status, r["id"]))
                    conn.commit()
                    st.rerun()
            with c2:
                new_payment = st.selectbox(
                    "입금 여부",
                    payment_options,
                    index=payment_options.index(r["payment_status"]),
                    key=f"pay_{r['id']}",
                )
                if new_payment != r["payment_status"]:
                    conn.execute(
                        "UPDATE equipment SET payment_status=? WHERE id=?", (new_payment, r["id"])
                    )
                    conn.commit()
                    st.rerun()

            if r["status"] == "처분 완료":
                if r.get("final_price"):
                    st.caption(f"실제 판매가 기록됨: {r['final_price']:,}원 (AI 시세 학습에 반영됨)")
                else:
                    final_price_input = st.text_input(
                        "실제 판매가 (원, 다음 AI 추천가 정확도를 높이는 데 쓰여요)",
                        key=f"final_price_{r['id']}",
                    )
                    if st.button("판매가 기록", key=f"record_price_{r['id']}"):
                        if record_equipment_sale(conn, r["id"], final_price_input):
                            st.success("기록했습니다. 다음 AI 추천가부터 이 데이터가 반영돼요.")
                            st.rerun()
                        else:
                            st.warning("판매가를 해석할 수 없어요. 숫자로 입력해주세요(예: 120만원).")


def screen_marketplace():
    st.header("설비 매칭")
    st.caption(
        "폐업을 준비 중인 사장님들이 '판매 중'으로 등록한 설비를 예비 창업자가 "
        "카테고리·지역으로 찾아볼 수 있는 화면입니다."
    )

    all_listings = get_marketplace_listings(conn)
    categories = ["전체"] + sorted({l["category"] for l in all_listings if l.get("category")})
    regions = ["전체"] + sorted({l["region"] for l in all_listings if l.get("region")})
    business_options = ["선택 안 함"] + list(BUSINESS_TYPE_CATEGORIES.keys())

    business_type = st.selectbox(
        "준비 중인 업종 (고르면 관련도 높은 매물을 위로 추천해드려요)", business_options
    )
    c1, c2 = st.columns(2)
    with c1:
        category_filter = st.selectbox("카테고리", categories)
    with c2:
        region_filter = st.selectbox("지역", regions)

    listings = get_marketplace_listings(
        conn,
        category=None if category_filter == "전체" else category_filter,
        region=None if region_filter == "전체" else region_filter,
    )

    if not listings:
        st.info("조건에 맞는 판매 중인 설비가 없습니다.")
        return

    business = None if business_type == "선택 안 함" else business_type
    if business:
        listings = sorted(
            listings, key=lambda it: score_equipment_match(it, business), reverse=True
        )

    # 같은 카테고리 매물이 여러 건이면 시세 샘플을 매물마다 다시 조회하지 않고
    # 카테고리당 한 번만 가져와 재사용한다(N+1 쿼리 방지).
    samples_by_category = {}
    for item in listings:
        with st.container(border=True):
            st.subheader(item["name"])
            st.write(
                f"카테고리: {item.get('category') or '확인 필요'} · "
                f"지역: {item.get('region') or '확인 필요'} · "
                f"상태: {item.get('condition') or '확인 필요'}"
            )
            st.write(
                f"희망 가격: {item.get('asking_price') or '확인 필요'} · "
                f"수거 조건: {item.get('pickup_terms') or '확인 필요'}"
            )

            if business:
                match_score = score_equipment_match(item, business)
                st.progress(match_score / 100, text=f"{business} 창업 추천도 {match_score}/100")

            item_category = item.get("category")
            if item_category and item_category not in samples_by_category:
                samples_by_category[item_category] = fetch_samples(conn, item_category)
            predicted = predict_price(
                conn, item_category, item.get("used_period"), item.get("condition"),
                samples=samples_by_category.get(item_category),
            )
            if predicted.get("ok") and predicted.get("predicted_price") is not None:
                asking_num = parse_price(item.get("asking_price"))
                if asking_num is not None:
                    diff_pct = round(
                        (predicted["predicted_price"] - asking_num) / predicted["predicted_price"] * 100
                    )
                    if diff_pct > 0:
                        st.markdown(f":green[AI 추정 시세 대비 약 {diff_pct}% 저렴]")
                    elif diff_pct < 0:
                        st.markdown(f":orange[AI 추정 시세 대비 약 {-diff_pct}% 비쌈]")

            if item.get("draft"):
                with st.expander("판매 글 보기"):
                    st.write(item["draft"])

            if st.button("관심 표시", key=f"interest_{item['id']}"):
                record_marketplace_interest(conn, current_user_id(), item["id"])
                st.success("관심을 표시했습니다. 판매자에게 직접 연락해보세요.")


PAGES = {
    "폐업 진행 상황": screen_dashboard,
    "폐업 준비": lambda: screen_stage("준비", "폐업 준비"),
    "폐업 진행": lambda: screen_stage("진행", "폐업 진행"),
    "폐업 후": lambda: screen_stage("후", "폐업 후"),
    "지원정책": screen_policies,
    "중고품 관리": screen_equipment,
    "설비 매칭": screen_marketplace,
}

PAGE_DESCRIPTIONS = {
    "폐업 진행 상황": "전체 진행 상황 한눈에 보기 + 단계별 통계",
    "폐업 준비": "닫기 전 준비할 일과 코칭",
    "폐업 진행": "신청·신고 진행과 코칭",
    "폐업 후": "마무리 업무와 코칭",
    "지원정책": "받을 수 있는 지원사업 확인",
    "중고품 관리": "중고 집기 정리·판매 글",
    "설비 매칭": "예비 창업자를 위한 폐업 설비 찾기",
}

NAV_GROUPS = [
    (None, ["폐업 진행 상황"]),
    ("단계별 코칭", ["폐업 준비", "폐업 진행", "폐업 후"]),
    ("기타", ["지원정책", "중고품 관리", "설비 매칭"]),
]

if "current_page" not in st.session_state:
    st.session_state["current_page"] = "폐업 진행 상황"

st.sidebar.title("다음걸음")
st.sidebar.caption(current_user_id())
if st.sidebar.button("다른 이름으로 시작", key="logout", use_container_width=True):
    st.session_state.clear()
    st.rerun()
st.sidebar.divider()

for group_label, page_names in NAV_GROUPS:
    if group_label:
        st.sidebar.markdown(f"### {group_label}")
    for name in page_names:
        is_active = st.session_state["current_page"] == name
        if st.sidebar.button(
            name,
            key=f"nav_{name}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            st.session_state["current_page"] = name
            st.rerun()

choice = st.session_state["current_page"]
st.sidebar.divider()
st.sidebar.caption(PAGE_DESCRIPTIONS.get(choice, ""))
PAGES[choice]()
