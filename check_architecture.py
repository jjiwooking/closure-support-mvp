"""
아키텍처 경계 검사 — ARCHITECTURE.md 참고.

app.py는 Streamlit UI 계층이라 streamlit을 써도 되지만, 나머지 로직 계층
파일들은 나중에 앱/웹페이지로 그대로 옮겨 쓸 수 있어야 하므로 streamlit을
직접 import하면 안 된다. 이 스크립트는 그 규칙이 깨졌는지 바로 확인한다.

사용법: python check_architecture.py
"""
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).parent
ALLOWED_STREAMLIT_FILES = {"app.py", "check_architecture.py"}
STREAMLIT_IMPORT_RE = re.compile(r"^\s*(import\s+streamlit|from\s+streamlit)", re.MULTILINE)


def find_violations():
    violations = []
    for path in sorted(PROJECT_ROOT.glob("*.py")):
        if path.name in ALLOWED_STREAMLIT_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if STREAMLIT_IMPORT_RE.search(text):
            violations.append(path.name)
    return violations


if __name__ == "__main__":
    violations = find_violations()
    if violations:
        print("아키텍처 위반: 다음 파일이 streamlit을 직접 import합니다 (app.py만 허용):")
        for name in violations:
            print(f"  - {name}")
        sys.exit(1)

    print("OK: streamlit은 app.py에만 있습니다. 로직 계층은 여전히 프레임워크 독립적입니다.")
    sys.exit(0)
