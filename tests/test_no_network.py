import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["app", "core"]
FORBIDDEN_PATTERN = re.compile(r"^\s*(import|from)\s+(requests|httpx|urllib\b)", re.MULTILINE)

# The optional Phase 7 Ollama integration (talking to localhost only) is the
# one permitted exception per the spec's privacy requirements.
EXEMPT_NAME_FRAGMENT = "ollama"


def test_no_forbidden_network_libraries_outside_ollama_module():
    offenders = []
    for scan_dir in SCAN_DIRS:
        for path in (PROJECT_ROOT / scan_dir).rglob("*.py"):
            if EXEMPT_NAME_FRAGMENT in path.name.lower():
                continue
            text = path.read_text(encoding="utf-8")
            if FORBIDDEN_PATTERN.search(text):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert not offenders, f"Found forbidden network imports in: {offenders}"
