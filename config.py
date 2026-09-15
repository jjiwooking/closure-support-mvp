"""
실제 API 키가 준비되면 이 파일이 아니라 `.env` 파일(또는 시스템 환경변수)에
값을 채워 넣는다. 키를 코드나 대화, 저장소에 직접 적지 않는다.

.env 예시는 .env.example 참고. .env는 git에 커밋하지 않는다.
"""
import os
import secrets
from pathlib import Path


def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# Postgres 연결 문자열. 기본값은 docker-compose.yml의 로컬 컨테이너 자격증명과
# 일치한다 — `docker compose up -d`만으로 별도 설정 없이 동작한다.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://closure:closure@localhost:5432/closure_support"
)

# 기업마당 지원사업정보 API 서비스키 (crtfcKey)
BIZINFO_API_KEY = os.environ.get("BIZINFO_API_KEY")

# 공공데이터포털(data.go.kr) 지원정책 API 서비스키. 실제 엔드포인트는
# datago_client.py에 아직 채워지지 않았으니 키만 넣어서는 동작하지 않는다.
DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY")

# 제품안전정보센터(safetykorea.kr) 인증정보 조회 API 서비스키. 사진 인식이 아니라
# 모델명/인증번호 기반 조회용이며, 실제 엔드포인트는 safetykorea_client.py에
# 아직 채워지지 않았다.
SAFETYKOREA_API_KEY = os.environ.get("SAFETYKOREA_API_KEY")

# LLM 공급자. 예: "anthropic", "gemini", "openai"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
# 모델명은 하드코딩하지 않고 환경변수로 둔다 — 공급자 쪽 모델 목록이 바뀌면
# 코드 수정 없이 .env의 이 값만 바꾸면 된다. 실제 사용 가능한 모델명은
# 발급받은 콘솔(Google AI Studio 등)에서 확인해 채운다.
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-3.6-flash")
# 가이드 RAG 검색에 쓰는 임베딩 모델. 생성용 LLM_MODEL과 별도로 관리한다.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")

# api.py(FastAPI)의 세션 쿠키 서명키. 설정 안 하면 프로세스 시작마다 무작위로
# 새로 생성한다 — 하드코딩된 기본값을 두면 그 자체가 보안 구멍이 되므로,
# 대신 "서버 재시작 시 기존 세션이 전부 무효화됨(다시 로그인 필요)"을
# 감수한다. 여러 인스턴스로 띄우거나 재시작해도 세션을 유지하려면 .env에
# 직접 값을 채워 넣는다.
API_SESSION_SECRET = os.environ.get("API_SESSION_SECRET") or secrets.token_hex(32)

# "production"이면 세션 쿠키에 Secure 속성을 붙여 HTTPS로만 전송한다(로컬 개발은
# 평문 HTTP라 기본값은 개발 모드로 둔다 — https_only=True면 로컬에서 로그인 자체가
# 안 됨).
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

# api.py CORS 허용 origin. 콤마로 구분한 목록을 .env에 넣으면 되고, 안 넣으면
# 로컬 개발 포트(정적 서버 5500/3000) 기본값을 그대로 쓴다.
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5500,http://127.0.0.1:5500,http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if o.strip()
]


def bizinfo_configured() -> bool:
    return bool(BIZINFO_API_KEY)


def datago_configured() -> bool:
    return bool(DATA_GO_KR_API_KEY)


def safetykorea_configured() -> bool:
    return bool(SAFETYKOREA_API_KEY)


def llm_configured() -> bool:
    return bool(LLM_PROVIDER and LLM_API_KEY)
