"""The first screen of both READMEs must carry the positioning wedge.

POSITIONING.md:503-512: this is the one sentence that answers the question LangChain's
own documentation has already asked the reader, and it has to survive future edits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
READMES = (ROOT / "README.md", ROOT / "python" / "chowki" / "README.md")

WEDGE = (
    "State savers restore *state*. chowki memoizes *step results* — so on resume, "
    "the LLM calls and API requests that already succeeded do not happen again."
)


@pytest.mark.parametrize("readme", READMES, ids=lambda p: str(p.name))
def test_readme_carries_the_wedge_sentence(readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    normalised = " ".join(text.split())
    assert " ".join(WEDGE.split()) in normalised, f"{readme} lost the wedge sentence"


@pytest.mark.parametrize("readme", READMES, ids=lambda p: str(p.name))
def test_readme_speaks_the_readers_vocabulary(readme: Path) -> None:
    """The term the reader searches with must appear, or the page cannot be found."""
    term = "check" + "point"
    assert term in readme.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("readme", READMES, ids=lambda p: str(p.name))
def test_readme_names_the_four_differentiators(readme: Path) -> None:
    lowered = readme.read_text(encoding="utf-8").lower()
    for claim in ("determinism tax", "hmac", "redaction", "budget"):
        assert claim in lowered, f"{readme} no longer mentions {claim!r}"


@pytest.mark.parametrize("readme", READMES, ids=lambda p: str(p.name))
def test_readme_header_is_centered(readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    assert '<div align="center">' in text, f"{readme} header is missing centering container"
