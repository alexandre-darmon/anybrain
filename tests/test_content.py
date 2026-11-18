"""Content (P1b, PRD 11.10 to 11.13, 15.4 `--content`, 15.10, 15.11, 19.4b): checks, snapshots, edit ratio."""
from __future__ import annotations

import shutil

import pytest

from anybrain import cli, content, install, lint, stats
from anybrain import vault as v
from conftest import EXAMPLE, FIXTURES, page

CONTENT = FIXTURES / "content"


@pytest.fixture
def fixture_vault(tmp_path):
    root = tmp_path / "content"
    shutil.copytree(CONTENT, root)
    return root


def findings(root, rel):
    ctx = lint.Context(root)
    return [str(f) for f in lint.content_findings(ctx, v.load_page(root, root / rel))]


# ---------------------------------------------------------------- lint --content (the fixtures of step 15)

def test_banned_pattern_is_an_error_at_ready_and_a_warning_before(fixture_vault):
    ready = findings(fixture_vault, "content/drafts/2026-11-01-banned-ready.md")
    assert any(f.startswith("error") and "banned pattern 'leverage'" in f for f in ready)
    drafting = findings(fixture_vault, "content/drafts/2026-11-02-banned-drafting.md")
    assert any(f.startswith("warning") and "synergy" in f for f in drafting), "a regex pattern from me/voice.md"


def test_confidential_and_mission_sources_are_errors(fixture_vault):
    found = findings(fixture_vault, "content/drafts/2026-11-03-confidential-source.md")
    assert any("source projects/acme-mission.md is a confidential or mission page" in f for f in found)
    assert any("source company/plan.md is confidential" in f for f in found)


def test_placeholder_left_at_ready_is_an_error(fixture_vault):
    found = findings(fixture_vault, "content/drafts/2026-11-04-placeholder-ready.md")
    assert any(f.startswith("error") and "1 placeholder(s)" in f for f in found)


def test_a_clean_draft_passes_and_notes_for_the_owner_are_not_checked(fixture_vault):
    assert findings(fixture_vault, "content/drafts/2026-11-05-clean-ready.md") == []


def test_no_sources_no_guide_and_a_client_name_are_errors(fixture_vault):
    found = findings(fixture_vault, "content/drafts/2026-11-06-no-guide.md")
    assert any("no `sources`" in f for f in found)
    assert any("no channel guide content/channels/newsletter.md" in f for f in found)
    assert any("names a client from .harness/clients.txt" in f for f in found)


def test_content_check_exits_1_only_on_errors(fixture_vault, capsys):
    assert cli.main(["content", str(fixture_vault), "check", "content/drafts/2026-11-05-clean-ready.md"]) == 0
    assert cli.main(["content", str(fixture_vault), "check", "content/drafts/2026-11-02-banned-drafting.md"]) == 0
    assert cli.main(["content", str(fixture_vault), "check", "content/drafts/2026-11-01-banned-ready.md"]) == 1
    assert "content: 1 error(s)" in capsys.readouterr().out
    assert cli.main(["content", str(fixture_vault), "check", "wiki/notes/source-note.md"]) == 2


def test_lint_runs_content_on_every_draft(fixture_vault):
    found = [str(f) for f in lint.check_content(lint.Context(fixture_vault))]
    assert sum(f.startswith("error") for f in found) >= 6
    assert not any("2026-11-05-clean-ready" in f for f in found)


def test_published_pieces_get_warnings_not_errors_for_the_owners_own_text(make_vault):
    root = make_vault({
        "me/voice.md": page("Voice", "profile", body="## Banned\nleverage\n"),
        "content/channels/linkedin.md": page("LinkedIn", "channel"),
        "content/published/2026-11-10-x.md": page(
            "X", "published", channel="linkedin", url="https://example.com", published="2026-11-10",
            draft="content/drafts/2026-11-09-x.md", edit_ratio=0.2, body="We leverage it.\n"),
    })
    found = findings(root, "content/published/2026-11-10-x.md")
    assert found and all(f.startswith("warning") for f in found)


# ---------------------------------------------------------------- schema of the new types

@pytest.mark.parametrize("extra, problem", [
    ({"channel": "tiktok"}, "`channel` is 'tiktok'"),
    ({"status": "posted"}, "`status` is 'posted'"),
    ({"angle": None}, "missing `angle`"),
    ({"channel": "application"}, "missing `brief`"),
    ({"channel": "application", "brief": "raw/docs/job.md"}, "expected a file in raw/inbox/"),
    ({"target_length": "long"}, "`target_length`"),
])
def test_draft_schema(make_vault, extra, problem):
    fields = {"channel": "linkedin", "audience": "PMs.", "angle": "One claim.", "status": "drafting",
              "sources": ["wiki/notes/a.md"]}
    fields.update(extra)
    root = make_vault({"content/drafts/2026-11-01-x.md": page("X", "draft", **fields)})
    assert any(problem in str(f) for f in lint.check_schema(lint.Context(root)))


def test_published_schema(make_vault):
    root = make_vault({"content/published/2026-11-10-x.md": page(
        "X", "published", channel="linkedin", published="2026-11-10", draft="content/drafts/x.md",
        edit_ratio=1.5, impressions=-1)})
    found = " ".join(str(f) for f in lint.check_schema(lint.Context(root)))
    assert "missing `url`" in found and "`edit_ratio` is 1.5" in found and "`impressions` is -1" in found


# ---------------------------------------------------------------- snapshot, edit ratio, diff

@pytest.mark.parametrize("first, final, expected", [
    ("The claim is short.", "The claim is short.", 0.0),
    ("The claim is short.", "The claim is very short.", 5 / 24),  # "very " inserted, over the longer text
    ("abc", "xyz", 1.0),
    ("", "", 0.0),
])
def test_edit_ratio_on_known_pairs(first, final, expected):
    assert content.edit_ratio(first, final) == pytest.approx(expected)


def test_edit_ratio_on_the_fixture_pair():
    first = (CONTENT / "edit-pair" / "first.md").read_text().strip()
    final = (CONTENT / "edit-pair" / "final.md").read_text().strip()
    # "They are planned around" became "We plan them around": 11 of 93 characters changed
    assert content.edit_ratio(first, final) == pytest.approx(11 / 93)


def test_reader_text_drops_frontmatter_comments_and_notes():
    text = page("X", "draft", body="One   claim\nhere.\n<!-- src: a.md -->\n\n## Notes for the owner\n- private\n")
    assert content.reader_text(text, is_draft=True) == "One claim here."


def test_snapshot_keeps_the_first_version(fixture_vault, capsys):
    rel = "content/drafts/2026-11-05-clean-ready.md"
    assert cli.main(["content", str(fixture_vault), "snapshot", rel]) == 0
    saved = fixture_vault / ".harness" / "drafts" / "2026-11-05-clean-ready.md"
    first = saved.read_text()
    (fixture_vault / rel).write_text(first.replace("plain words", "the owner's words"))
    assert cli.main(["content", str(fixture_vault), "snapshot", rel]) == 0
    assert saved.read_text() == first and "kept" in capsys.readouterr().out
    assert cli.main(["content", str(fixture_vault), "snapshot", "wiki/notes/source-note.md"]) == 2


def test_diff_against_the_published_piece_on_the_example_vault(example_vault, capsys):
    assert cli.main(["content", str(example_vault), "diff", "content/drafts/2026-11-09-interviews-fail-early.md"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("edit ratio 0.15 (15%)") and "content/published/2026-11-10-interviews-fail-early.md" in out
    assert "~ changed: happened instead. -> happened." in out and "~ changed: makes the same point -> says it" in out


def test_diff_without_a_snapshot_says_what_to_do(fixture_vault, capsys):
    assert cli.main(["content", str(fixture_vault), "diff", "content/drafts/2026-11-05-clean-ready.md"]) == 2
    assert "run anybrain content snapshot" in capsys.readouterr().err


# ---------------------------------------------------------------- index, stats, init

def test_content_index_lists_drafts_by_status_and_published_pieces(example_vault):
    text = (example_vault / "content" / "index.md").read_text()
    assert "## drafts" in text and "## published" in text
    assert "(linkedin · drafting · 2 sources · 1 placeholder)" in text
    assert "(linkedin · 2026-11-10 · edit 15%)" in text
    assert "- [content](content/index.md): 2 drafts (0 ready), 1 published" in (example_vault / "index.md").read_text()


def test_stats_reports_edit_ratios_and_convergence(make_vault, capsys, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-12-31")
    files = {}
    for i, ratio in enumerate([0.5, 0.45, 0.4, 0.35, 0.5, 0.5, 0.5, 0.5], start=1):
        files[f"content/published/2026-11-{i:02d}-p{i}.md"] = page(
            f"P{i}", "published", channel="linkedin", url="https://example.com", published=f"2026-11-{i:02d}",
            draft=f"content/drafts/p{i}.md", edit_ratio=ratio, impressions=100)
    root = make_vault(files)
    assert stats.run(root) == 0
    out = capsys.readouterr().out
    assert "content: 8 published piece(s)" in out and "linkedin: 8 piece(s), median edit 50%, impressions 800" in out
    assert "edit ratio target (at most 30% by the 8th piece): not met (50%)" in out
    assert "did not go down over the last 8 pieces" in out


def test_init_creates_the_content_folders_voice_and_ideas(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    brain = tmp_path / "brain"
    assert install.init(str(brain), out=lambda line: None) == 0
    for rel in ("content/channels", "content/drafts", "content/published", ".harness/drafts"):
        assert (brain / rel).is_dir(), rel
    assert (brain / "content" / "ideas.md").is_file()
    patterns, problems = lint.banned_patterns(brain)
    assert problems == [] and len(patterns) == 9
    for name in ("draft", "published", "channel", "voice"):
        assert (brain / ".harness" / "templates" / f"{name}.md").is_file()


# ---------------------------------------------------------------- review of P1b (D046)

def ready_draft(body="The claim.", title="Ready draft", sources=("wiki/notes/source-note.md",)):
    return page(title, "draft", channel="linkedin", audience="PMs.", angle="One claim.", status="ready",
                sources=list(sources), body=body)


@pytest.mark.parametrize("body, title", [
    ("We helped Acme\nCorp rebuild.", "T"),
    ("We helped Acme" + chr(0xA0) + "Corp rebuild.", "T"),
    ("We helped Societe Generale.", "T"),
    ("We helped L" + chr(0x2019) + "Oréal.", "T"),
    ("We helped " + __import__("unicodedata").normalize("NFD", "Société Générale") + ".", "T"),
    ("We helped **Acme** Corp.", "T"),
    ("A clean body.", "What Acme Corp taught me"),
])
def test_client_names_are_caught_in_every_form(fixture_vault, body, title):
    (fixture_vault / ".harness" / "clients.txt").write_text("Acme Corp\nSociété Générale\nL'Oréal\n")
    rel = "content/drafts/2026-11-20-client.md"
    (fixture_vault / rel).write_text(ready_draft(body, title))
    assert any("names a client" in f for f in findings(fixture_vault, rel)), (body, title)


@pytest.mark.parametrize("source", [
    "[[acme-mission]]", "[[projects/acme-mission]]", "projects/acme-mission", "projects/acme-mission.md#Decisions",
    "[[projects/acme-mission.md|the mission]]", "[mission](projects/acme-mission.md)", '"projects/acme-mission.md"',
    "`projects/acme-mission.md`", "[[Acme mission]]", "wiki/../company/plan.md",
])
def test_a_mission_or_company_source_is_caught_in_every_form(fixture_vault, source):
    rel = "content/drafts/2026-11-21-source.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n<!-- src: {source} -->\n"))
    found = findings(fixture_vault, rel)
    assert any(f.startswith("error") and ("confidential" in f) for f in found), (source, found)


def test_an_unresolved_source_blocks_ready_and_warns_before(fixture_vault):
    rel = "content/drafts/2026-11-22-missing.md"
    (fixture_vault / rel).write_text(ready_draft("A claim.\n<!-- src: wiki/notes/nowhere.md -->\n"))
    assert any(f.startswith("error") and "not found" in f for f in findings(fixture_vault, rel))
    (fixture_vault / rel).write_text(ready_draft("A claim.\n<!-- src: ../outside.md -->\n").replace("status: ready", "status: drafting"))
    assert any(f.startswith("warning") and "outside the vault" in f for f in findings(fixture_vault, rel))
