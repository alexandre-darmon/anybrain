"""Migration from an Obsidian vault (PRD 21): inventory, owner-text detection, check (acceptance of 21.3)."""
from __future__ import annotations

import os
import stat

import pytest

from anybrain import cli, lint, migrate
from anybrain import vault as v
from conftest import FIXTURES, page

LEGACY = FIXTURES / "legacy-obsidian"
DAY = "2026-11-16"


@pytest.fixture
def vault(make_vault):
    return make_vault({"me/preferences.md": page("Preferences", "profile")})


def report(vault):
    return (vault / "journal" / "reports" / f"migration-{DAY}.md")


def test_inventory_snapshots_the_legacy_vault_read_only_without_hidden_folders(vault, capsys):
    assert cli.main(["migrate", str(vault), "inventory", str(LEGACY)]) == 0
    snapshot = vault / "raw" / "legacy" / DAY
    assert (snapshot / "Pièces jointes" / "schema.png").is_file()
    assert (snapshot / "MOC - Création de niche.md").is_file()
    assert not (snapshot / ".obsidian").exists()
    mode = stat.S_IMODE((snapshot / "Inbox" / "Idée rapide.md").stat().st_mode)
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    out = capsys.readouterr().out
    assert f"wrote raw/legacy/{DAY}/ (read-only copy)" in out and f"wrote journal/reports/migration-{DAY}.md" in out


def test_report_lists_every_file_with_a_suggested_target_and_counts(vault):
    cli.main(["migrate", str(vault), "inventory", str(LEGACY)])
    text = report(vault).read_text()
    files = [p for p in LEGACY.rglob("*") if p.is_file() and ".obsidian" not in p.parts]
    lines = [line for line in text.splitlines() if line.startswith("- `")]
    assert len(lines) == len(files) == 15
    assert all(line.endswith("status: pending") for line in lines)
    assert "- Files: 15 (14 Markdown, 1 attachments or other files)." in text
    assert "- Files with owner-written text: 10 (12 paragraphs)." in text
    assert "`.obsidian/`" in text and "`tags` (1)" in text
    assert "- `MOC - Création de niche.md` · owner text: 1 · suggested: wiki/concepts/creation-de-niche.md" in text
    assert "suggested: dropped: replaced by .harness/templates/" in text
    assert lint.check_schema(lint.Context(vault)) == [], "the report is a valid report page"


def test_report_goes_to_journal_on_the_local_profile(vault, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    cli.main(["migrate", str(vault), "inventory", str(LEGACY)])
    assert (vault / "journal" / f"migration-{DAY}.md").is_file()


def test_inventory_is_idempotent_and_never_resets_a_started_migration(vault, capsys):
    cli.main(["migrate", str(vault), "inventory", str(LEGACY)])
    path = report(vault)
    path.write_text(path.read_text().replace("status: pending", "status: mapped to library/x.md", 1))
    before = path.read_text()
    capsys.readouterr()
    assert cli.main(["migrate", str(vault), "inventory", str(LEGACY)]) == 0
    out = capsys.readouterr().out
    assert "kept raw/legacy/" in out and "statuses are not reset" in out
    assert path.read_text() == before


def test_legacy_and_new_vault_must_be_separate(vault, capsys):
    assert cli.main(["migrate", str(vault), "inventory", str(vault / "me")]) == 2
    assert "separate folders" in capsys.readouterr().err


@pytest.mark.parametrize("rel, text, expected", [
    ("note.md", "# Title\n\nMy own long sentence here.\n", ["My own long sentence here."]),
    ("note.md", "> A quote from a book here.\n\nMine too, written by me.\n", ["Mine too, written by me."]),
    ("note.md", "{{title}} placeholder text here\n", []),
    ("note.md", "- [[a]]\n- [[b]] and [[c]]\n", []),
    ("note.md", "Too short\n", []),
    ("note.md", "```dataview\ntable from x where y\n```\n", []),
    ("Readwise/x.md", "- A highlight from the book.\n    - **Note:** my own note on it.\n", ["my own note on it."]),
    ("Modèles/x.md", "Template prose that is long enough.\n", []),
    ("note.md", "---\ntags: [x]\n---\n- first item of mine here\n- second item of mine here\n",
     ["first item of mine here", "second item of mine here"]),
])
def test_owner_paragraphs(rel, text, expected):
    assert migrate.owner_paragraphs(rel, text) == expected


def test_check_before_and_after_migration(vault, capsys):
    cli.main(["migrate", str(vault), "inventory", str(LEGACY)])
    capsys.readouterr()
    assert cli.main(["migrate", str(vault), "check"]) == 1
    out = capsys.readouterr().out
    assert "migrate: 0 of 12 owner paragraph(s) found in the vault" in out and "15 file(s) still pending" in out
    # the agent's work, simulated: owner text placed verbatim, every file given a status
    snapshot = vault / "raw" / "legacy" / DAY
    bodies = {}
    for path in snapshot.rglob("*.md"):
        rel = v.rel_path(snapshot, path)
        for paragraph in migrate.owner_paragraphs(rel, path.read_text()):
            if rel.startswith("Archive/"):
                continue  # the owner said: do not ingest the archive
            bodies.setdefault(v.slugify(path.stem), []).append(paragraph)
    for slug, paragraphs in bodies.items():
        (vault / "wiki" / "notes").mkdir(parents=True, exist_ok=True)
        (vault / "wiki" / "notes" / f"{slug}.md").write_text(page(slug, body="\n\n".join(paragraphs)))
    text = report(vault).read_text()
    text = text.replace("`Archive/Vieux projet.md` · owner text: 1 · suggested: raw/archive/ (not ingested unless the owner asks) · status: pending",
                        "`Archive/Vieux projet.md` · owner text: 1 · suggested: raw/archive/ (not ingested unless the owner asks) · status: skipped: the owner keeps the archive out")
    report(vault).write_text(text.replace("status: pending", "status: mapped to wiki/notes/"))
    assert cli.main(["migrate", str(vault), "check"]) == 0
    out = capsys.readouterr().out
    assert "migrate: 11 of 12 owner paragraph(s) found in the vault, 1 kept only in the snapshot, 0 missing; 0 file(s) still pending" in out
    assert "kept only in the snapshot (skipped by the owner): Archive/Vieux projet.md" in out


def test_owner_text_moved_to_raw_inbox_counts_as_preserved(vault, capsys):
    cli.main(["migrate", str(vault), "inventory", str(LEGACY)])
    (vault / "raw" / "inbox").mkdir(parents=True)
    (vault / "raw" / "inbox" / "idee-rapide.md").write_text((LEGACY / "Inbox" / "Idée rapide.md").read_text())
    capsys.readouterr()
    cli.main(["migrate", str(vault), "check"])
    assert "missing owner text from Inbox/" not in capsys.readouterr().out


def test_check_without_snapshot(vault, capsys):
    assert cli.main(["migrate", str(vault), "check"]) == 2
    assert "run anybrain migrate" in capsys.readouterr().err


# ---------------------------------------------------------------------------- review dates by code (D037)

def legacy_projects(tmp_path):
    root = tmp_path / "old"
    (root / "Projets").mkdir(parents=True)
    (root / "Projets" / "Alpha.md").write_text("---\nnext_review: 2027-01-15\n---\nNotes on alpha, written by me.\n")
    (root / "Projets" / "Beta.md").write_text("Beta project notes, written by me.\n\n- Échéance:: 2026-12-05\n")
    (root / "Projets" / "Gamma.md").write_text("Gamma project notes, written by me.\n")
    (root / "Projets" / "Delta.md").write_text("---\nreview: next spring\n---\nDelta project notes, written by me.\n")
    return root


def set_status(vault, mapping):
    path = report(vault)
    text = path.read_text()
    for rel, target in mapping.items():
        line = next(line for line in text.splitlines() if line.startswith(f"- `{rel}`"))
        text = text.replace(line, line.replace("status: pending", f"status: mapped to {target}"))
    path.write_text(text)


def project_page(vault, slug, review):
    fields = {"status": "idea", "kind": "personal"}
    if review:
        fields["next_review"] = review
    (vault / "projects").mkdir(exist_ok=True)
    (vault / "projects" / f"{slug}.md").write_text(page(slug.title(), "project", **fields) + "\n" + "\n".join(
        f"{name} project notes, written by me." if name != "Alpha" else "Notes on alpha, written by me."
        for name in [slug.title()]) + "\n")


def test_inventory_lists_the_review_date_already_on_a_legacy_note(vault, tmp_path):
    cli.main(["migrate", str(vault), "inventory", str(legacy_projects(tmp_path))])
    text = report(vault).read_text()
    assert "- `Projets/Alpha.md` · owner text: 1 · suggested: ask the owner · review date: 2027-01-15 (from `next_review`)" in text
    assert "review date: 2026-12-05 (from `Échéance::`)" in text
    assert "- `Projets/Gamma.md` · owner text: 1 · suggested: ask the owner · status: pending" in text
    assert 'review date: not a valid YYYY-MM-DD date (`review`: "next spring"), ask the owner' in text
    assert "with none, `next_review: 2026-12-16` (30 days after this migration)" in text


def test_check_verifies_project_review_dates(vault, tmp_path, capsys):
    cli.main(["migrate", str(vault), "inventory", str(legacy_projects(tmp_path))])
    set_status(vault, {"Projets/Alpha.md": "projects/alpha.md", "Projets/Beta.md": "projects/alpha.md",
                       "Projets/Gamma.md": "projects/gamma.md", "Projets/Delta.md": "projects/delta.md"})
    project_page(vault, "alpha", "2027-01-15")  # wrong: Beta's date is earlier
    project_page(vault, "gamma", "2026-12-17")  # wrong: the default is 2026-12-16
    project_page(vault, "delta", "2027-04-01")  # the owner chose it: not checked
    capsys.readouterr()
    assert cli.main(["migrate", str(vault), "check"]) == 1
    out = capsys.readouterr().out
    assert "next_review of projects/alpha.md: 2027-01-15, expected 2026-12-05 (from the legacy note)" in out
    assert "next_review of projects/gamma.md: 2026-12-17, expected 2026-12-16 (default, 30 days after the migration)" in out
    assert "delta" not in out.split("migrate:")[0]
    assert "2 project review date(s) to fix" in out
    project_page(vault, "alpha", "2026-12-05")
    project_page(vault, "gamma", "2026-12-16")
    capsys.readouterr()
    cli.main(["migrate", str(vault), "check"])
    assert "0 project review date(s) to fix" in capsys.readouterr().out


@pytest.mark.parametrize("text, expected", [
    ("---\ndeadline: 2026-12-01\nnext_review: 2027-02-01\n---\nBody.\n", ("2027-02-01", "`next_review`")),
    ("---\ndue: 2026-12-01\n---\nBody.\n\nRevue:: 2027-03-01\n", ("2027-03-01", "`Revue::`")),
    ("Body with an inline [Échéance:: 2026-12-24] in a sentence.\n", ("2026-12-24", "`Échéance::`")),
    ("Body (next_review:: 2027-01-02) in parentheses.\n", ("2027-01-02", "`next_review::`")),
    ("- **Prochaine revue**:: 2027-01-03\n", ("2027-01-03", "`Prochaine revue::`")),
    ("---\nreview: [2027-01-01, 2027-02-01]\n---\nBody.\n", ('["2027-01-01", "2027-02-01"]', "`review`")),
    ("No date here.\n", None),
])
def test_review_date_forms_and_ranking(text, expected):
    fm, body, _, _ = v.parse_frontmatter(text)
    assert migrate.review_date(fm, body) == expected


def test_an_impossible_date_goes_to_the_owner_and_is_not_checked(vault, tmp_path, capsys):
    root = tmp_path / "old"
    (root / "Projets").mkdir(parents=True)
    (root / "Projets" / "Epsilon.md").write_text("---\nnext_review: '2026-02-30'\n---\nEpsilon notes, written by me.\n")
    (root / "Projets" / "Zeta.md").write_text("Zeta notes, written by me.\n")
    cli.main(["migrate", str(vault), "inventory", str(root)])
    assert "review date: not a valid YYYY-MM-DD date (`next_review`: \"2026-02-30\"), ask the owner" in report(vault).read_text()
    # one page made from both notes: the owner gives its date, so check does not impose the default
    set_status(vault, {"Projets/Epsilon.md": "[[projects/epsilon]]", "Projets/Zeta.md": "projects/epsilon.md"})
    project_page(vault, "epsilon", "2027-05-01")
    capsys.readouterr()
    cli.main(["migrate", str(vault), "check"])
    assert "0 project review date(s) to fix" in capsys.readouterr().out


def test_check_reads_every_project_named_in_a_status(vault, tmp_path, capsys):
    cli.main(["migrate", str(vault), "inventory", str(legacy_projects(tmp_path))])
    set_status(vault, {"Projets/Alpha.md": "library/alpha.md and [[projects/alpha]]",
                       "Projets/Beta.md": "projects/beta.md", "Projets/Gamma.md": "projects/gamma.md",
                       "Projets/Delta.md": "projects/delta.md"})
    project_page(vault, "alpha", "2026-12-31")
    capsys.readouterr()
    cli.main(["migrate", str(vault), "check"])
    out = capsys.readouterr().out
    assert "next_review of projects/alpha.md: 2026-12-31, expected 2027-01-15 (from the legacy note)" in out


def test_a_review_date_survives_a_frontmatter_that_is_not_valid_yaml(vault, tmp_path):
    root = tmp_path / "old"
    (root / "Projets").mkdir(parents=True)
    (root / "Projets" / "Eta.md").write_text("---\ntitle: Eta: the sequel\nnext_review: \"2027-02-02\"\n---\nEta notes, written by me.\n")
    cli.main(["migrate", str(vault), "inventory", str(root)])
    assert "review date: 2027-02-02 (from `next_review`)" in report(vault).read_text()
