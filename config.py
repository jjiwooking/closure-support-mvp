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

# LLM 공급자 결정 후 채운다. 예: "anthropic", "gemini", "openai" (아직 미확정)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER")
LLM_API_KEY = os.environ.get("LLM_API_KEY")


def bizinfo_configured() -> bool:
    return bool(BIZINFO_API_KEY)


def llm_configured() -> bool:
    return bool(LLM_PROVIDER and LLM_API_KEY)
