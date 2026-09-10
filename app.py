from datetime import date

import streamlit as st

from coaching import build_stage_response
from db import get_connection, init_db
from seed import seed_if_empty
from services import (
    compute_progress,
    evaluate_eligibility,
    generate_listing,
    get_or_create_application,
    log_change,
    priority_sort,
    record_application_date,
    record_supplement,
    register_policy_interest,
    sync_bizinfo_policies,
    toggle_document_check,
    update_application_payment,
    update_decision_status,
    update_profile,
    update_task_status,
)

USER_ID = "demo_user"

st.set_page_config(page_title="다음걸음", layout="wide")

st.markdown(
    """
    <style>
    /* 버튼 터치 영역 확대 (접근성: 충분한 버튼 크기) */
    div.stButton > button, a[data-testid="stBaseButton-secondary"],
    div[data-testid="stFormSubmitButton"] > button {
        padding: 0.55rem 1.1rem;
        font-size: 1.02rem;
        border-radius: 10px;
    }
    /* 카드형 컨테이너를 더 뚜렷하게 */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 14px;
    }
    /* 채팅 말풍선 여백 확대 */
    div[data-testid="stChatMessage"] {
        padding: 0.6rem 0.9rem;
        border-radius: 14px;
    }
    /* 채팅 입력창을 화면 하단에 고정 — 페이지가 길어져도 스크롤 없이 항상 보이게 */
    div[data-testid="stChatInput"] {
        position: sticky;
        bottom: 0;
        z-index: 999;
        background-color: #FFFFFF;
        border: 2px solid #0F766E;
        border-radius: 12px;
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

conn = get_connection()
init_db(conn)
seed_if_empty(conn, USER_ID)


def get_profile():
    row = conn.execute("SELECT * FROM profiles WHERE user_id=?", (USER_ID,)).fetchone()
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
    params = [USER_ID]
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
                        update_profile(conn, USER_ID, pending)
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
                    update_task_status(
                        conn, t["id"], "사용자 완료" if new_checked else "진행 중", USER_ID
                    )
                    st.rerun()


def screen_dashboard():
    st.header("폐업 진행 상황")
    st.write(
        "폐업은 **폐업 준비 → 폐업 진행 → 폐업 후** 3단계로 진행돼요. "
        "아래 체크리스트에서 바로 완료 처리하거나, 왼쪽 '단계별 코칭' 메뉴에서 AI 코칭을 받을 수 있습니다."
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


def _answer_stage_context(tasks, question_text):
    """등록된 근거만 사용해 답변을 만들고 대화 형식으로 표시할 텍스트를 반환한다."""
    response = build_stage_response(conn, tasks, question_text)
    text = response["answer"]

    if response["source_ids"]:
        source = conn.execute(
            "SELECT * FROM sources WHERE id=?", (response["source_ids"][0],)
        ).fetchone()
        if source:
            text += (
                f"\n\n*출처: {source['agency']} · {source['title']} · "
                f"확인일: {source['reviewed_at'] or '확인 필요'}*"
            )
    else:
        text += "\n\n*검토된 공식 자료가 없어 지어내지 않고 확인 필요로 표시했습니다.*"

    if response["actions"]:
        links = "\n".join(
            f"- [관련 공식 링크 열기](https://example-official-site.invalid/{a['link_id']})"
            for a in response["actions"]
        )
        text += "\n\n" + links

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
        answer = _answer_stage_context(tasks, user_q)
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
                    toggle_document_check(conn, d["id"], checked, USER_ID)
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
            update_task_status(conn, task["id"], "사용자 완료", USER_ID)
            st.rerun()
    with c2:
        if st.button("완료 취소", key=f"undo_{task['id']}"):
            update_task_status(conn, task["id"], "진행 중", USER_ID)
            st.rerun()
    with c3:
        if st.button("확인 필요로 표시", key=f"flag_{task['id']}"):
            update_task_status(conn, task["id"], "확인 필요", USER_ID)
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
                record_application_date(conn, task["id"], applied_date.isoformat(), USER_ID)
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
                update_decision_status(conn, task["id"], new_decision, USER_ID)
                st.rerun()

            payment_options = ["미입금", "입금 완료"]
            new_payment = st.selectbox(
                "입금 여부",
                payment_options,
                index=payment_options.index(application["payment_status"]),
                key=f"app_payment_{task['id']}",
            )
            if new_payment != application["payment_status"]:
                update_application_payment(conn, task["id"], new_payment, USER_ID)
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
            record_supplement(conn, task["id"], note, due, USER_ID)
            st.rerun()


def screen_stage(stage_key, stage_label):
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

    with st.expander("기업마당 자료 동기화 (관리자용)", expanded=False):
        st.caption(
            "수집만 하고 자동 게시하지 않습니다. 담당자가 검토해 review_status를 "
            "'검토완료'로 바꾸기 전까지는 아래 추천 목록에 나타나지 않습니다."
        )
        if st.button("지금 동기화 시도"):
            result = sync_bizinfo_policies(conn)
            if not result["configured"]:
                st.warning(result["message"])
            elif result["message"] and "수집해" in result["message"]:
                st.success(result["message"])
            else:
                st.error(result["message"] or "알 수 없는 오류입니다.")
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM sources WHERE review_status != '검토완료'"
        ).fetchone()["c"]
        st.caption(f"검토 대기 중인 자료: {pending}건")

    profile = get_profile()
    rows = conn.execute(
        """
        SELECT p.*, s.agency, s.title AS source_title, s.url AS source_url, s.reviewed_at
        FROM policies p
        JOIN sources s ON p.source_id = s.id
        WHERE s.review_status = '검토완료'
        """
    ).fetchall()

    if not rows:
        st.info("등록된 지원사업이 없습니다.")
        return

    for r in rows:
        r = dict(r)
        label = evaluate_eligibility(profile, r["eligibility_rules"])
        with st.container(border=True):
            st.subheader(r["title"])
            st.write(f"기관: {r['agency']} · 신청기간: {r.get('period') or '확인 필요'}")
            st.write(f"공식 공고: {r['source_url']}")
            badge_color = {
                "입력 조건 부합": "green",
                "추가 정보 필요": "orange",
                "현재 조건 불일치": "red",
                "모집 상태 확인 필요": "gray",
            }.get(label, "gray")
            st.markdown(f":{badge_color}[● {label}]")
            st.caption(f"자료 검토일: {r.get('reviewed_at') or '확인 필요'}")

            existing_task = conn.execute(
                "SELECT id FROM user_tasks WHERE user_id=? AND policy_id=?", (USER_ID, r["id"])
            ).fetchone()
            if existing_task:
                st.caption("관심 사업으로 등록됨 — '폐업 준비'/'폐업 진행' 화면에서 진행 상황을 기록하세요.")
            elif st.button("관심 사업으로 등록", key=f"interest_{r['id']}"):
                _, created = register_policy_interest(conn, r["id"], USER_ID)
                if created:
                    st.success("등록되었습니다. '폐업 진행' 화면에서 진행 상황을 기록하세요.")
                    st.rerun()
                else:
                    st.warning("등록에 필요한 업무 템플릿을 찾을 수 없습니다. (확인 필요)")


def screen_equipment():
    st.header("중고품 관리")

    with st.expander("새 물품 등록", expanded=False):
        with st.form("new_equipment"):
            name = st.text_input("물품명")
            model = st.text_input("모델 (모르면 비워두세요)")
            used_period = st.text_input("사용 기간")
            condition = st.text_input("상태")
            defects = st.text_input("하자")
            ownership_status = st.selectbox("본인 소유 확인", ["본인 소유", "확인 필요"])
            asking_price = st.text_input("희망 가격")
            pickup_terms = st.text_input("수거 조건")
            submitted = st.form_submit_button("등록")
            if submitted:
                if not name:
                    st.warning("물품명은 필수입니다.")
                else:
                    conn.execute(
                        """
                        INSERT INTO equipment
                            (user_id, name, model, used_period, condition, defects,
                             ownership_status, asking_price, pickup_terms, draft, status, payment_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            USER_ID, name, model, used_period, condition, defects,
                            ownership_status, asking_price, pickup_terms, "", "보관 중", "미입금",
                        ),
                    )
                    conn.commit()
                    st.success("등록되었습니다.")
                    st.rerun()

    rows = conn.execute("SELECT * FROM equipment WHERE user_id=?", (USER_ID,)).fetchall()
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
                f"모델: {r['model'] or '확인 필요'} · 상태: {r['condition'] or '확인 필요'} · "
                f"하자: {r['defects'] or '확인 필요'}"
            )
            st.write(
                f"희망 가격: {r['asking_price'] or '확인 필요'} · 수거 조건: {r['pickup_terms'] or '확인 필요'}"
            )

            style = st.radio(
                "판매 글 스타일", ["짧은 글", "블로그 스타일"], horizontal=True, key=f"style_{r['id']}"
            )
            if st.button("판매 글 생성", key=f"gen_{r['id']}"):
                draft = generate_listing(r, style="short" if style == "짧은 글" else "blog")
                conn.execute("UPDATE equipment SET draft=? WHERE id=?", (draft, r["id"]))
                conn.commit()
                st.rerun()

            if r["draft"]:
                st.text_area(
                    "판매 글 (복사해서 사용하세요)", value=r["draft"], height=220, key=f"draft_{r['id']}"
                )

            c1, c2 = st.columns(2)
            with c1:
                new_status = st.selectbox(
                    "처분 상태",
                    status_options,
                    index=status_options.index(r["status"]),
                    key=f"status_{r['id']}",
                )
                if new_status != r["status"]:
                    log_change(conn, USER_ID, "equipment", r["id"], r["status"], new_status)
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


PAGES = {
    "폐업 진행 상황": screen_dashboard,
    "폐업 준비": lambda: screen_stage("준비", "폐업 준비"),
    "폐업 진행": lambda: screen_stage("진행", "폐업 진행"),
    "폐업 후": lambda: screen_stage("후", "폐업 후"),
    "지원정책": screen_policies,
    "중고품 관리": screen_equipment,
}

PAGE_DESCRIPTIONS = {
    "폐업 진행 상황": "전체 진행 상황 한눈에 보기 + 단계별 통계",
    "폐업 준비": "닫기 전 준비할 일과 코칭",
    "폐업 진행": "신청·신고 진행과 코칭",
    "폐업 후": "마무리 업무와 코칭",
    "지원정책": "받을 수 있는 지원사업 확인",
    "중고품 관리": "중고 집기 정리·판매 글",
}

NAV_GROUPS = [
    (None, ["폐업 진행 상황"]),
    ("단계별 코칭", ["폐업 준비", "폐업 진행", "폐업 후"]),
    ("기타", ["지원정책", "중고품 관리"]),
]

if "current_page" not in st.session_state:
    st.session_state["current_page"] = "폐업 진행 상황"

st.sidebar.title("다음걸음")

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
