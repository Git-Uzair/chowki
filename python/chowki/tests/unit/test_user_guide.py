import ast
import importlib
import re
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any

import pytest

import chowki
from chowki.config import ChowkiConfig
from chowki.guardrails.config import GuardrailConfig

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


def test_pause_calls_in_docs_use_keyword_args() -> None:
    md_files = list(USER_GUIDE_DIR.glob("**/*.md"))
    pause_pos_re = re.compile(r"chowki\.pause\(\s*[\"']")
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        assert not pause_pos_re.search(content), (
            f"Found positional chowki.pause(...) call in {md_file.name}. "
            f"pause() parameters are keyword-only (use chowki.pause(reason=...))."
        )


def test_recover_runs_calls_in_docs_pass_engine() -> None:
    md_files = list(USER_GUIDE_DIR.glob("**/*.md"))
    recover_no_arg_re = re.compile(r"chowki\.recover_runs\(\s*\)")
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        assert not recover_no_arg_re.search(content), (
            f"Found no-arg chowki.recover_runs() call in {md_file.name}. "
            f"recover_runs requires engine parameter (chowki.recover_runs(engine))."
        )


def test_db_path_default_documented_as_cwd_relative() -> None:
    config_md = USER_GUIDE_DIR / "configuration.md"
    content = config_md.read_text(encoding="utf-8")
    assert "./.chowki/chowki.db" in content
    assert "~/.chowki/chowki.db" not in content


def _python_blocks() -> list[tuple[Path, str]]:
    block_re = re.compile(r"```python\s*(.*?)\s*```", re.DOTALL)
    return [
        (md_file, code)
        for md_file in sorted(USER_GUIDE_DIR.glob("**/*.md"))
        for code in block_re.findall(md_file.read_text(encoding="utf-8"))
    ]


def test_documented_chowki_api_names_exist() -> None:
    """Every ``chowki.<name>`` and ``from chowki... import <name>`` in the guide resolves.

    The guide's job is to be copy-pasteable, so a name the package does not export is a
    broken example (``chowki.get_engine()`` was: ``get_engine`` lives in ``chowki.config``).
    """
    blocks = _python_blocks()
    assert len(blocks) > 0, "No python blocks found in user guide"

    for md_file, code in blocks:
        for node in ast.walk(ast.parse(code)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "chowki"
            ):
                assert hasattr(chowki, node.attr), (
                    f"{md_file.name} documents chowki.{node.attr}, which the chowki "
                    f"package does not export -- import it from its own module instead."
                )
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("chowki"):
                module = importlib.import_module(node.module or "")
                for alias in node.names:
                    assert hasattr(module, alias.name), (
                        f"{md_file.name} imports {alias.name} from {node.module}, "
                        f"which does not define it."
                    )


def _is_workflow_decorator(dec: ast.expr) -> bool:
    """``@chowki.workflow`` or ``@chowki.workflow(...)``."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    return isinstance(target, ast.Attribute) and target.attr == "workflow"


def _required_params(args: ast.arguments) -> list[str]:
    positional = args.posonlyargs + args.args
    without_default = positional[: len(positional) - len(args.defaults)]
    kwonly = [
        arg.arg
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        if default is None
    ]
    return [arg.arg for arg in without_default] + kwonly


def test_documented_workflows_are_invocable_the_way_resume_invokes_them() -> None:
    """Every documented workflow can be called the way a warm resume calls it.

    ``resume()``/``rerun()`` re-invoke the workflow as ``workflow_fn(run_id=run_id)`` and
    pass nothing else, so a documented workflow with a parameter that has no default
    cannot actually be resumed -- the re-invocation dies with a ``TypeError``.
    """
    blocks = _python_blocks()
    assert len(blocks) > 0, "No python blocks found in user guide"

    for md_file, code in blocks:
        for node in ast.walk(ast.parse(code)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not any(_is_workflow_decorator(dec) for dec in node.decorator_list):
                continue
            required = _required_params(node.args)
            assert not required, (
                f"{md_file.name} documents workflow {node.name} with parameter(s) "
                f"{required} that have no default -- resume() re-invokes the workflow as "
                f"{node.name}(run_id=run_id), which would raise TypeError."
            )


#: Config defaults the guide is allowed to quote: literal ones, which a doc cell can be
#: compared against. ``db_path`` (a ``default_factory``) is covered by its own test above.
_LITERAL_DEFAULTS: dict[str, object] = {
    f.name: f.default
    for cls in (ChowkiConfig, GuardrailConfig)
    for f in fields(cls)
    if f.default is not MISSING and isinstance(f.default, bool | int | float | str | None)
}

_NOT_A_LITERAL = object()

#: ``| `field` | ... | `documented default` | ...`` reference-table rows.
_TABLE_ROW_RE = re.compile(r"^\|\s*`(\w+)`\s*\|(.+)\|", re.MULTILINE)
_CELL_RE = re.compile(r"`([^`]+)`")

#: Inline prose defaults, e.g. ``(defaults: `retry_base_seconds = 1.0`)``.
_INLINE_DEFAULT_RE = re.compile(r"`(\w+)\s*=\s*([^`]+)`")


def _literal(text: str) -> Any:
    try:
        return ast.literal_eval(text.strip())
    except (ValueError, SyntaxError, TypeError):
        return _NOT_A_LITERAL


def _matches(documented: Any, expected: object) -> bool:
    # type(): True must not satisfy a documented 1, nor 30 a documented 30.0.
    return documented == expected and type(documented) is type(expected)


def test_documented_defaults_match_config_dataclasses() -> None:
    """Default values quoted in the guide equal the dataclass defaults they describe."""
    for md_file in sorted(USER_GUIDE_DIR.glob("**/*.md")):
        content = md_file.read_text(encoding="utf-8")

        documented: list[tuple[str, Any]] = [
            (field_name, _literal(value))
            for field_name, value in _INLINE_DEFAULT_RE.findall(content)
        ]
        for field_name, cells in _TABLE_ROW_RE.findall(content):
            for cell in _CELL_RE.findall(cells):
                documented.append((field_name, _literal(cell)))

        for field_name, value in documented:
            if field_name not in _LITERAL_DEFAULTS or value is _NOT_A_LITERAL:
                continue
            expected = _LITERAL_DEFAULTS[field_name]
            # A row lists the type as well as the default, so any literal cell that
            # matches is enough; a row where none matches documents a wrong default.
            row_values = [v for name, v in documented if name == field_name]
            assert any(_matches(v, expected) for v in row_values), (
                f"{md_file.name} documents {field_name} as {value!r}, "
                f"but the default is {expected!r}."
            )
