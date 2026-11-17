"""Shared helpers: build small vaults in a temporary folder, copy committed fixtures."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLE = ROOT / "vault.example"


def write_files(base: Path, files: dict[str, str]) -> Path:
    """Write {relative path: content}; content is dedented so tests can indent it."""
    for rel, content in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return base


def page(
    title: str = "A page",
    type: str = "note",
    description: str = "Says one thing clearly.",
    body: str = "",
    **extra,
) -> str:
    """A page with a complete common frontmatter (PRD 11.1); `extra` adds or overrides fields."""
    fields = {
        "title": title,
        "type": type,
        "description": description,
        "tags": [],
        "language": "en",
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "sources": [],
        "sensitivity": "public",
    }
    fields.update(extra)
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, list):
            value = "[" + ", ".join(str(v) for v in value) + "]"
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + textwrap.dedent(body).strip("\n") + "\n"


@pytest.fixture
def make_vault(tmp_path):
    def _make(files: dict[str, str] | None = None, name: str = "vault") -> Path:
        base = tmp_path / name
        base.mkdir(parents=True, exist_ok=True)
        return write_files(base, files or {})

    return _make


@pytest.fixture
def example_vault(tmp_path):
    """A writable copy of vault.example/."""
    target = tmp_path / "example"
    shutil.copytree(EXAMPLE, target, symlinks=True)
    (target / ".harness" / "protected.json").unlink(missing_ok=True)  # local state, not committed
    return target


@pytest.fixture
def fixture_vault(tmp_path):
    """A writable copy of tests/fixtures/<name>/."""

    def _copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copytree(FIXTURES / name, target, symlinks=True)
        return target

    return _copy


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    """Tests run on a fixed date so date-based checks are reproducible (docs/decisions.md D008)."""
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-11-16")
    monkeypatch.delenv("ANYBRAIN_VAULT", raising=False)
    monkeypatch.delenv("ANYBRAIN_PROFILE", raising=False)
