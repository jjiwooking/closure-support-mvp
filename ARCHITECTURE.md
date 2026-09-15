# 아키텍처: UI 계층과 로직 계층 분리

이 프로젝트는 지금 Streamlit으로 빠르게 만들고 확인하는 단계지만, **로직 계층은
Streamlit과 완전히 무관하게 짜여 있어서 나중에 앱이나 웹페이지로 그대로
옮길 수 있다.**

## 구조

```
UI 계층 (Streamlit 전용 — 나중에 교체 대상)
└─ app.py

로직 계층 (프레임워크 무관 — 그대로 재사용 가능)
├─ db.py               DB 연결/스키마/마이그레이션
├─ services.py          비즈니스 로직 (프로필, 업무, 정책 신청, 중고거래 등)
├─ analytics.py         정책 적합도 스코어링, 업종-설비 매칭
├─ pricing_model.py     중고 설비 가격 예측(KNN)
├─ seed.py               데모/참고 데이터 시드
├─ config.py             환경변수 로딩
├─ llm_client.py         Gemini API 호출 (쿼터 카운터는 set_quota_backend로 주입받음)
├─ bizinfo_client.py, datago_client.py, safetykorea_client.py   외부 API 클라이언트
├─ guide_graph.py, research_graph.py, supervisor_graph.py,
│  trade_graph.py, rag_store.py, coaching.py                    LangGraph 에이전트
```

## 지켜야 할 규칙

**`app.py`를 제외한 어떤 파일도 `streamlit`을 import하면 안 된다.**

로직 계층의 모든 함수는 필요한 값(예: `user_id`, `conn`)을 인자로 명시적으로
받는다 — `st.session_state`나 그 밖의 Streamlit 전역 상태에 암묵적으로
의존하지 않는다. 유일하게 예외였던 `llm_client.py`의 LLM 호출 쿼터 카운터도
`set_quota_backend(get_count_fn, increment_fn)`로 주입받는 방식으로 바꿔서,
"카운터를 어디에 저장할지"는 호출부(app.py)가 결정하고 `llm_client.py` 자체는
그걸 모르게 했다.

이 규칙이 깨졌는지는 저장소 루트에서 다음으로 바로 확인할 수 있다:

```
python check_architecture.py
```

## 나중에 앱/웹페이지로 옮길 때 할 일

1. **`app.py`만 새로 만들면 된다.** 나머지 파일은 그대로 가져다 쓴다.
   - REST API가 필요하면 FastAPI로 로직 계층 함수들을 엔드포인트로 감싸면 됨
   - 프론트엔드(React/Next.js 등)가 그 API를 호출하는 구조
2. **세션/로그인**: 지금은 `st.session_state` 기반 이름 로그인(비밀번호 없음).
   실제 서비스로 가려면 진짜 인증(세션 쿠키/JWT)으로 교체 필요.
3. **DB**: ~~SQLite~~ → **완료(Postgres로 이전함)**. `db.py`가 psycopg2로 Postgres에
   접속하고, sqlite3.Connection과 같은 인터페이스(`execute`/`executemany`/`commit`)로
   감싼 얇은 호환 래퍼를 제공하므로 `db.py` 외 로직 계층 파일은 전혀 수정하지 않았다.
   로컬 개발은 `docker-compose.yml`(`docker compose up -d`)로 띄운다. 날짜/불린/JSON은
   기존처럼 TEXT 컬럼 + Python 파싱 방식을 그대로 유지했다(엔진 교체만 하고 타입
   개선은 하지 않음 — 필요하면 별도 작업으로 진행).
4. **LLM 쿼터**: `llm_client.set_quota_backend()`에 새 환경에 맞는
   get/increment 함수만 넣어주면 됨(예: Redis, DB, 요청 컨텍스트 등).
