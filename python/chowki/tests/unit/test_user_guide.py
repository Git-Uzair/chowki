import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
USER_GUIDE_DIR = REPO_ROOT / "docs" / "user-guide"

EXPECTED_PAGES = [
    "index.md",
    "concepts.md",
    "warm-resume.md",
    "guardrails.md",
    "hitl.md",
    "configuration.md",
    "limits.md",
    "resuming-in-production.md",
]


def test_user_guide_pages_exist() -> None:
    for page in EXPECTED_PAGES:
        page_path = USER_GUIDE_DIR / page
        assert page_path.exists(), f"User guide page missing: {page_path}"


def test_python_code_blocks_parse() -> None:
    md_files = list(USER_GUIDE_DIR.glob("**/*.md"))
    assert len(md_files) > 0, "No markdown files found in user guide directory"

    python_block_re = re.compile(r"```python\s*(.*?)\s*```", re.DOTALL)

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        blocks = python_block_re.findall(content)
        for i, code in enumerate(blocks):
            try:
                ast.parse(code)
            except SyntaxError as err:
                pytest.fail(
                    f"Syntax error in {md_file.relative_to(REPO_ROOT)} python block {i + 1}:\n"
                    f"{err}\nCode:\n{code}"
                )


def test_intra_repo_links_resolve() -> None:
    md_files = list(USER_GUIDE_DIR.glob("**/*.md"))
    assert len(md_files) > 0, "No markdown files found in user guide directory"

    # Match markdown links: [text](link)
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        links = link_re.findall(content)
        for text, target in links:
            target = target.strip()

            # Skip external URLs or mailto or non-file links
            if target.startswith(("http://", "https://", "mailto:", "ftp://")):
                continue

            # Strip anchor if present
            target_path_str = target.split("#", 1)[0] if "#" in target else target

            if not target_path_str:
                continue  # link to anchor in same file

            if target_path_str.startswith("/"):
                resolved = REPO_ROOT / target_path_str.lstrip("/")
            else:
                resolved = (md_file.parent / target_path_str).resolve()

            assert resolved.exists(), (
                f"Broken link in {md_file.relative_to(REPO_ROOT)}: "
                f"[{text}]({target}) -> resolved path {resolved} does not exist"
            )
