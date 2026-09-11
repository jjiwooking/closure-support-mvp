"""
실제 API 키가 준비되면 이 파일이 아니라 `.env` 파일(또는 시스템 환경변수)에
값을 채워 넣는다. 키를 코드나 대화, 저장소에 직접 적지 않는다.

.env 예시는 .env.example 참고. .env는 git에 커밋하지 않는다.
"""
import os
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

# 기업마당 지원사업정보 API 서비스키 (crtfcKey)
BIZINFO_API_KEY = os.environ.get("BIZINFO_API_KEY")

# LLM 공급자. 예: "anthropic", "gemini", "openai"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
# 모델명은 하드코딩하지 않고 환경변수로 둔다 — 공급자 쪽 모델 목록이 바뀌면
# 코드 수정 없이 .env의 이 값만 바꾸면 된다. 실제 사용 가능한 모델명은
# 발급받은 콘솔(Google AI Studio 등)에서 확인해 채운다.
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-3.6-flash")
# 가이드 RAG 검색에 쓰는 임베딩 모델. 생성용 LLM_MODEL과 별도로 관리한다.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")


def bizinfo_configured() -> bool:
    return bool(BIZINFO_API_KEY)


def llm_configured() -> bool:
    return bool(LLM_PROVIDER and LLM_API_KEY)
