"""Content (P1b, PRD 11.10 to 11.13, 15.4 `--content`, 15.10, 15.11, 19.4b): checks, snapshots, edit ratio."""
from __future__ import annotations

import json
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


def test_published_pieces_keep_only_the_safety_checks(make_vault):
    root = make_vault({
        "me/voice.md": page("Voice", "profile", body="## Banned\nleverage\n"),
        "content/channels/linkedin.md": page("LinkedIn", "channel"),
        "content/published/2026-11-10-x.md": page(
            "X", "published", channel="linkedin", url="https://example.com", published="2026-11-10",
            draft="content/drafts/2026-11-09-x.md", edit_ratio=0.2, body="We leverage it.\n"),
    })
    assert findings(root, "content/published/2026-11-10-x.md") == [], "the owner's final text: no style findings"
    (root / ".harness").mkdir(exist_ok=True)
    (root / ".harness" / "clients.txt").write_text("Acme Corp\n")
    path = root / "content" / "published" / "2026-11-10-x.md"
    path.write_text(path.read_text().replace("We leverage it.", "We helped Acme Corp."))
    assert any(f.startswith("error") and "names a client" in f for f in findings(root, "content/published/2026-11-10-x.md"))


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
    # "They are planned around" became "We plan them around": aligned word by word, then letter by letter
    # inside the changed passage, 11 of 93 characters changed
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



def test_edit_ratio_is_fast_on_an_article():
    import time
    words = " ".join(f"word{i % 97}" for i in range(3000))
    edited = words.replace("word5 ", "WORD5 ", 40)
    start = time.perf_counter()
    ratio = content.edit_ratio(words, edited)
    assert time.perf_counter() - start < 2.0 and 0 < ratio < 0.05


@pytest.mark.parametrize("comment", [
    "<!-- SRC: projects/acme-mission.md -->", "<!-- source: projects/acme-mission.md -->",
    "<!-- src: wiki/notes/source-note.md,\n     projects/acme-mission.md -->",
])
def test_src_comment_forms_are_read(fixture_vault, comment):
    rel = "content/drafts/2026-11-23-forms.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n{comment}\n"))
    assert any("acme-mission" in f and "confidential" in f for f in findings(fixture_vault, rel))


def test_sources_used_in_the_notes_and_links_in_the_body_are_checked(fixture_vault):
    rel = "content/drafts/2026-11-24-notes.md"
    body = "A claim, see [[acme-mission]].\n\n## Notes for the owner\n- Sources used: company/plan.md (your plan)\n"
    (fixture_vault / rel).write_text(ready_draft(body))
    found = findings(fixture_vault, rel)
    assert any("links to confidential page projects/acme-mission.md" in f for f in found)
    assert any("company/plan.md is confidential" in f for f in found)


@pytest.mark.parametrize("text", ["[YOUR STORY]", "[Your story: the launch]", "[ your number: churn ]"])
def test_placeholder_forms_block_ready(fixture_vault, text):
    rel = "content/drafts/2026-11-25-ph.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n{text}\n"))
    assert any("placeholder" in f for f in findings(fixture_vault, rel))


def test_banned_list_forms_and_problems(make_vault):
    root = make_vault({"me/voice.md": page("Voice", "profile", body="""
        ## Banned
        1. `leverage`
        + "synergy"
        - `regex:\\bdelve\\w*`
        - regex:a*
        - regex:(unclosed
        """)})
    patterns, problems = lint.banned_patterns(root)
    assert [label for label, _ in patterns] == ["leverage", "synergy", "regex:\\bdelve\\w*"]
    assert any("matches an empty text" in p for p in problems) and any("not a valid regex" in p for p in problems)
    missing = make_vault({}, name="nothing")
    assert lint.banned_patterns(missing)[1] == ["me/voice.md not found: no banned pattern is checked (11.13)"]


def test_content_ideas_are_checked_for_clients_and_local_sources(fixture_vault):
    (fixture_vault / "content" / "ideas.md").write_text(
        "# Content ideas\n- 2026-11-20 · what Acme Corp taught me · wiki/notes/source-note.md\n"
        "- 2026-11-21 · a mission lesson · projects/acme-mission.md\n- 2026-11-22 · fine · wiki/notes/source-note.md\n")
    found = [str(f) for f in lint.check_content(lint.Context(fixture_vault)) if "ideas.md" in str(f)]
    assert any("ideas.md:2: names a client" in f for f in found)
    assert any("ideas.md:3: names a local-only source" in f for f in found)
    assert not any("ideas.md:4" in f for f in found)


def test_the_folder_decides_the_type_and_holds_no_subfolder(make_vault):
    root = make_vault({
        "content/drafts/2026-11-01-note.md": page("Note in drafts", "note"),
        "content/drafts/old/2026-11-01-x.md": page("X", "draft", channel="linkedin", audience="A.", angle="B.",
                                                    status="idea", sources=["wiki/notes/a.md"]),
    })
    found = " ".join(str(f) for f in lint.check_schema(lint.Context(root)))
    assert "must have `type: draft`" in found and "holds no subfolder" in found


def test_a_nan_edit_ratio_is_a_schema_error_not_a_crash(make_vault, capsys):
    root = make_vault({"content/published/2026-11-10-x.md": page(
        "X", "published", channel="linkedin", url="https://example.com", published="2026-11-10",
        draft="content/drafts/x.md", edit_ratio=".nan")})
    assert any("`edit_ratio`" in str(f) for f in lint.check_schema(lint.Context(root)))
    assert stats.run(root) == 0


def test_content_check_reports_invalid_yaml_and_hides_a_local_only_draft(fixture_vault, monkeypatch, capsys):
    rel = "content/drafts/2026-11-26-broken.md"
    (fixture_vault / rel).write_text("---\ntitle: a: b\n---\nText.\n")
    assert cli.main(["content", str(fixture_vault), "check", rel]) == 1
    assert "invalid YAML" in capsys.readouterr().out
    secret = "content/drafts/2026-11-27-secret.md"
    (fixture_vault / secret).write_text(ready_draft("We helped Acme Corp.").replace("sensitivity: public", "sensitivity: confidential"))
    monkeypatch.setenv("ANYBRAIN_PROFILE", "frontier")
    cli.main(["content", str(fixture_vault), "check", secret])
    out = capsys.readouterr().out
    assert "details hidden" in out and "Acme" not in out


def test_stats_says_a_flat_ratio_under_target_without_advising_a_review(make_vault, capsys, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_TODAY", "2026-12-31")
    root = make_vault({f"content/published/2026-11-{i:02d}-p{i}.md": page(
        f"P{i}", "published", channel="linkedin", url="https://example.com", published=f"2026-11-{i:02d}",
        draft=f"content/drafts/p{i}.md", edit_ratio=0.1) for i in range(1, 9)})
    stats.run(root)
    out = capsys.readouterr().out
    assert "met (10%)" in out and "did not go down over the last 8 pieces: it stays under the 30% target" in out
    assert "review me/voice.md" not in out  # 16.5 says it; the advice is for a ratio above the target



# ---------------------------------------------------------------- final review before 0.1.0 (D047)

def test_a_client_name_with_a_star_is_still_caught(fixture_vault):
    (fixture_vault / ".harness" / "clients.txt").write_text("E*Trade\n")
    rel = "content/drafts/2026-11-28-star.md"
    (fixture_vault / rel).write_text(ready_draft("We helped E*Trade rebuild onboarding."))
    assert any("names a client" in f for f in findings(fixture_vault, rel))


def test_client_names_in_tags_audience_and_file_name(fixture_vault):
    rel = "content/drafts/2026-11-29-acme-corp-story.md"
    (fixture_vault / rel).write_text(ready_draft("A clean body."))
    assert any("names a client" in f for f in findings(fixture_vault, rel)), "the file name names the client"


@pytest.mark.parametrize("notes, bad", [
    ("- Sources used: wiki/notes/source-note.md and wiki/notes/source-note.md", None),
    ("- Sources used: wiki/notes/source-note.md; one learning from a page you named, not cited.", None),
    ("- Sources used: [Source note](wiki/notes/source-note.md)", None),
    ("- **Sources used**: projects/acme-mission.md", "acme-mission"),
    ("- Sources used:\n  - projects/acme-mission.md", "acme-mission"),
])
def test_the_notes_sources_line_reads_references_not_prose(fixture_vault, notes, bad):
    rel = "content/drafts/2026-11-30-notes.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n<!-- src: wiki/notes/source-note.md -->\n\n## Notes for the owner\n{notes}\n"))
    found = findings(fixture_vault, rel)
    if bad:
        assert any(bad in f and f.startswith("error") for f in found), found
    else:
        assert found == [], found


def test_sources_with_spaces_angle_brackets_and_relative_links(fixture_vault):
    (fixture_vault / "raw" / "highlights").mkdir(parents=True)
    (fixture_vault / "raw" / "highlights" / "The Snow Leopard.md").write_text("highlights\n")
    rel = "content/drafts/2026-12-01-forms.md"
    body = ("A claim.\n<!-- src: raw/highlights/The Snow Leopard.md -->\n"
            "Another.\n<!-- src: [note](<../../wiki/notes/source-note.md>), [web](https://example.com) -->\n")
    (fixture_vault / rel).write_text(ready_draft(body))
    assert findings(fixture_vault, rel) == []


@pytest.mark.parametrize("text, count", [
    ("[YOUR STORY: x]", 1), ("[YOUR NUMBERS: churn]", 1), ("see [your story](https://example.com)", 0),
])
def test_placeholders_not_links(fixture_vault, text, count):
    rel = "content/drafts/2026-12-02-ph.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim. {text}\n"))
    assert sum("placeholder" in f for f in findings(fixture_vault, rel)) == count


def test_banned_entries_keep_their_inner_spaces_and_quotes(make_vault):
    root = make_vault({"me/voice.md": page("Voice", "profile", body=r"""
        ## Banned
        - " to be honest "
        - regex:can't\b
        """)})
    patterns, _ = lint.banned_patterns(root)
    assert patterns[0][0] == " to be honest " and patterns[1][1].search("I can't do it")


def test_a_one_letter_fix_in_a_long_passage_counts_one_letter():
    long = "https://example.com/" + "a" * 1200
    assert content.edit_ratio(long + " end.", long.replace("aaaa", "aaab", 1) + " end.") < 0.01
    first = " ".join(f"word{i}" for i in range(400))
    assert content.edit_ratio(first, first.replace("word200", "WORD200")) < 0.01


def test_a_moved_source_on_a_published_piece_is_a_warning(make_vault):
    root = make_vault({
        "content/channels/linkedin.md": page("LinkedIn", "channel"),
        "content/published/2026-11-10-x.md": page(
            "X", "published", channel="linkedin", url="https://example.com", published="2026-11-10",
            draft="content/drafts/x.md", edit_ratio=0.2, sources=["wiki/notes/gone.md"], body="Text.\n"),
    })
    found = findings(root, "content/published/2026-11-10-x.md")
    assert found and all(f.startswith("warning") for f in found)


def test_ideas_catch_a_wrapped_name_and_a_local_raw_document(fixture_vault):
    (fixture_vault / "raw" / "docs").mkdir(parents=True)
    (fixture_vault / "raw" / "docs" / "contract.md").write_text("private\n")
    (fixture_vault / "content" / "ideas.md").write_text(
        "# Content ideas\n- 2026-11-20 · what Acme\n  Corp taught me · wiki/notes/source-note.md\n"
        "- 2026-11-21 · a contract lesson · raw/docs/contract.md\n")
    found = [str(f) for f in lint.check_content(lint.Context(fixture_vault)) if "ideas.md" in str(f)]
    assert any("names a client" in f for f in found) and any("ideas.md:4: names a local-only source" in f for f in found)


# ---------------------------------------------------------------- independent check of the D047 fixes (D050)

def published(body, sources=("wiki/notes/source-note.md",)):
    return page("Published", "published", channel="linkedin", published="2026-11-10", url="https://example.com/p",
                draft="", edit_ratio=0.1, sources=list(sources), body=body)


@pytest.mark.parametrize("comment", [
    "https://example.com/post projects/acme-mission.md",
    "https://example.com/post and projects/acme-mission.md",
    "https://example.com/post (via projects/acme-mission.md)",
    "<https://example.com/post> company/plan.md",
])
def test_a_url_in_a_src_comment_does_not_hide_the_path_after_it(fixture_vault, comment):
    rel = "content/drafts/2026-12-04-u.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim from a mission.\n<!-- src: {comment} -->\n"))
    found = findings(fixture_vault, rel)
    assert any(f.startswith("error") and "confidential" in f for f in found), (comment, found)


@pytest.mark.parametrize("comment", ["projects/acme-mission.md (Decisions)",
                                     "wiki/notes/source-note.md projects/acme-mission.md"])
def test_a_confidential_source_with_a_note_is_named_at_every_status(fixture_vault, comment):
    rel = "content/drafts/2026-12-05-n.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n<!-- src: {comment} -->\n").replace("ready", "drafting"))
    assert any(f.startswith("error") and "confidential or mission page" in f for f in findings(fixture_vault, rel))
    pub = "content/published/2026-11-10-x.md"
    (fixture_vault / pub).parent.mkdir(parents=True, exist_ok=True)
    (fixture_vault / pub).write_text(published(f"A claim.\n<!-- src: {comment} -->\n"))
    found = findings(fixture_vault, pub)
    assert any(f.startswith("error") and "confidential or mission page" in f for f in found)
    assert not any("not found" in f for f in found)


@pytest.mark.parametrize("notes", [
    "- Sources used: source-note, acme-mission",
    "- Sources used:\n\t- projects/acme-mission.md",
    "**Sources used:**\n- projects/acme-mission.md",
    "- Sources used: wiki/notes/source-note.md (via projects/acme-mission.md)",
])
def test_the_notes_sources_line_reads_bare_names_tabs_and_lists(fixture_vault, notes):
    rel = "content/drafts/2026-12-06-notes.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n## Notes for the owner\n\n{notes}\n"))
    found = findings(fixture_vault, rel)
    assert any(f.startswith("error") and "confidential or mission page" in f for f in found), (notes, found)


@pytest.mark.parametrize("note", ["(the 80/20 cut)", "(sections My take/Timeline)", "and/or 2026/11/02",
                                  "hbr.org/2016/09/know-your-customers-jobs-to-be-done"])
def test_prose_with_a_slash_on_the_sources_line_does_not_block_ready(fixture_vault, note):
    rel = "content/drafts/2026-12-07-prose.md"
    (fixture_vault / rel).write_text(ready_draft(
        f"A claim.\n\n## Notes for the owner\n\n- Sources used: wiki/notes/source-note.md {note}\n"))
    assert findings(fixture_vault, rel) == []


@pytest.mark.parametrize("body", [
    "[YOUR STORY: an interview that misled you, with the\nyear and what you changed afterwards]",
    "[YOUR STORY: an interview that misled you",
    "[YOUR NUMBER](%)",
])
def test_a_wrapped_unclosed_or_odd_placeholder_still_blocks_ready(fixture_vault, body):
    rel = "content/drafts/2026-12-01-wrapped.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n{body}\n"))
    assert any("placeholder" in f for f in findings(fixture_vault, rel)), body


def test_a_link_whose_text_says_your_story_is_not_a_placeholder(fixture_vault):
    rel = "content/drafts/2026-12-02-link.md"
    (fixture_vault / rel).write_text(ready_draft(
        "Read [your story](https://example.com/story) and [YOUR NUMBERS](wiki/notes/source-note.md).\n"))
    assert not any("placeholder" in f for f in findings(fixture_vault, rel))


def test_an_escaped_star_does_not_hide_a_client_name(fixture_vault):
    (fixture_vault / ".harness" / "clients.txt").write_text("E*Trade\n")
    rel = "content/drafts/2026-12-03-star.md"
    (fixture_vault / rel).write_text(ready_draft("We helped E\\*Trade rebuild onboarding."))
    assert any("names a client" in f for f in findings(fixture_vault, rel))


@pytest.mark.parametrize("line", [
    "- 2026-11-21 · a contract lesson, drawn from raw/docs/contract.md.",
    "- 2026-11-22 · what the plan says about pricing, see company/plan.md.",
    "- 2026-11-23 · a mission lesson from acme-mission.md",
    "- 2026-11-24 · the board view, raw/docs/Board minutes Q3.md",
    "- 2026-11-25 · the board view, [minutes](<raw/docs/Board minutes Q3.md>)",
])
def test_ideas_lines_naming_a_local_only_source_are_caught(fixture_vault, line):
    (fixture_vault / "content" / "ideas.md").write_text(f"# Content ideas\n\n{line}\n")
    found = [str(f) for f in lint.ideas_findings(lint.Context(fixture_vault), [])]
    assert any("names a local-only source" in f for f in found), (line, found)


def test_edit_ratio_does_not_loop_when_a_long_passage_shares_nothing_with_a_word():
    para = ("Long paragraph about indexes and links, with many words that go on and on. " * 16).strip()
    first = "Opening line.\n\n" + para + "\n\n" + para + "\n\nSo the vault stays plain text."
    final = "Opening line.\n\nHence the vault stays plain text."
    assert 0.9 < content.edit_ratio(first, final) <= 1.0
    assert content.edit_ratio("x" * 3000 + " " + "y" * 3000, "z") == 1.0


# ---------------------------------------------------------------- independent review of the observability (D051)

@pytest.mark.parametrize("comment", ["company/Q3 numbers.csv wiki/notes/source-note.md",
                                     "wiki/notes/source-note.md company/Q3 numbers.csv",
                                     "company/Q3 numbers.csv"])
def test_a_confidential_path_with_a_space_never_reaches_ready(fixture_vault, comment):
    (fixture_vault / "company" / "Q3 numbers.csv").write_text("quarter,revenue\n")
    rel = "content/drafts/2026-12-08-csv.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n<!-- src: {comment} -->\n"))
    found = findings(fixture_vault, rel)
    assert any(f.startswith("error") and "confidential" in f for f in found), (comment, found)


def test_a_path_word_that_does_not_resolve_still_blocks_ready(fixture_vault):
    rel = "content/drafts/2026-12-09-missing.md"
    (fixture_vault / rel).write_text(ready_draft("A claim.\n<!-- src: wiki/notes/source-note.md wiki/notes/gone.md -->\n"))
    assert any(f.startswith("error") and "wiki/notes/gone.md" in f and "not found" in f for f in findings(fixture_vault, rel))


@pytest.mark.parametrize("body", [
    "[YOUR STORY: a product judged in the wrong category\n\nLater, see [my newsletter](https://example.com/news).",
    "[YOUR STORY: a teardown, as in [this one](https://example.com/teardown)]",
])
def test_a_placeholder_never_borrows_a_later_link(fixture_vault, body):
    rel = "content/drafts/2026-12-10-ph.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n{body}\n"))
    assert any("placeholder" in f for f in findings(fixture_vault, rel)), body


@pytest.mark.parametrize("words", ["(the part on naming decisions)", "plus my admin notes", "the plan, and decisions"])
def test_plain_words_on_the_sources_line_never_name_a_confidential_page(fixture_vault, words):
    for name in ("decisions", "admin"):
        (fixture_vault / "company" / f"{name}.md").write_text(page(name.title(), "note", sensitivity="confidential"))
    rel = "content/drafts/2026-12-11-prose.md"
    (fixture_vault / rel).write_text(ready_draft(
        f"A claim.\n\n## Notes for the owner\n\n- Sources used: wiki/notes/source-note.md {words}\n"))
    assert findings(fixture_vault, rel) == []


def test_a_page_name_in_backticks_on_the_sources_line_is_checked(fixture_vault):
    rel = "content/drafts/2026-12-12-tick.md"
    (fixture_vault / rel).write_text(ready_draft(
        "A claim.\n\n## Notes for the owner\n\n- Sources used: wiki/notes/source-note.md and `plan`\n"))
    assert any(f.startswith("error") and "confidential" in f for f in findings(fixture_vault, rel))


@pytest.mark.parametrize("body", ["We helped AT\\&T rename a category.", "We helped O\\'Brien Partners rename it."])
def test_any_markdown_escape_does_not_hide_a_client_name(fixture_vault, body):
    (fixture_vault / ".harness" / "clients.txt").write_text("AT&T\nO'Brien Partners\n")
    rel = "content/drafts/2026-12-13-esc.md"
    (fixture_vault / rel).write_text(ready_draft(body))
    assert any("names a client" in f for f in findings(fixture_vault, rel)), body


# ---------------------------------------------------------------- check of the D051 fixes (D052)

@pytest.mark.parametrize("where, text", [
    ("src", "./company/Q3 numbers.csv wiki/notes/source-note.md"),
    ("src", "/company/Q3 numbers.csv wiki/notes/source-note.md"),
    ("src", "Company/Q3 numbers.csv wiki/notes/source-note.md"),
    ("src", "Q3 numbers.csv wiki/notes/source-note.md"),
    ("notes", "./company/Q3 numbers.csv, wiki/notes/source-note.md"),
    ("sources", "[[Acme Plan]] wiki/notes/source-note.md"),
    ("sources", "[Q3](<../../company/Q3 numbers.csv>) wiki/notes/source-note.md"),
])
def test_a_confidential_source_is_caught_whatever_its_written_form(fixture_vault, where, text):
    (fixture_vault / "company" / "Q3 numbers.csv").write_text("quarter,revenue\n")
    (fixture_vault / "company" / "Acme Plan.md").write_text(page("Acme Plan", "note"))
    rel = "content/drafts/2026-12-14-forms.md"
    if where == "src":
        draft = ready_draft(f"A claim.\n<!-- src: {text} -->\n")
    elif where == "notes":
        draft = ready_draft(f"A claim.\n\n## Notes for the owner\n\n- Sources used: {text}\n")
    else:
        draft = ready_draft("A claim.").replace("sources: [wiki/notes/source-note.md]",
                                                f'sources: ["wiki/notes/source-note.md", {json.dumps(text)}]')
    (fixture_vault / rel).write_text(draft)
    found = findings(fixture_vault, rel)
    assert any(f.startswith("error") and "confidential" in f for f in found), (text, found)


def test_a_confidential_path_that_does_not_resolve_is_an_error_at_every_status(fixture_vault):
    rel = "content/drafts/2026-12-15-q9.md"
    (fixture_vault / rel).write_text(ready_draft("A claim.", sources=("figures from company/q9-plan.md",))
                                     .replace("status: ready", "status: drafting"))
    assert any(f.startswith("error") and "confidential" in f for f in findings(fixture_vault, rel))


def test_a_link_to_a_url_with_parentheses_is_an_external_source(fixture_vault):
    rel = "content/drafts/2026-12-16-wiki.md"
    (fixture_vault / rel).write_text(ready_draft(
        "A claim.\n<!-- src: [Category design](https://en.wikipedia.org/wiki/Category_(marketing)) -->\n",
        sources=("wiki/notes/source-note.md", "[Category](https://en.wikipedia.org/wiki/Category_(marketing))")))
    assert findings(fixture_vault, rel) == []


def test_a_placeholder_with_a_hint_in_parentheses_counts(fixture_vault):
    rel = "content/drafts/2026-12-17-hint.md"
    (fixture_vault / rel).write_text(ready_draft("Churn fell to [YOUR NUMBER](hint: churn after the rename) in a quarter."))
    assert any("placeholder" in f for f in findings(fixture_vault, rel))


@pytest.mark.parametrize("body", ["We worked with AT&amp;T last year.", "We worked with O&#39;Brien Partners.",
                                  "We worked with O&rsquo;Brien Partners."])
def test_html_entities_do_not_hide_a_client_name(fixture_vault, body):
    (fixture_vault / ".harness" / "clients.txt").write_text("AT&T\nO'Brien Partners\n")
    rel = "content/drafts/2026-12-18-entity.md"
    (fixture_vault / rel).write_text(ready_draft(body))
    assert any("names a client" in f for f in findings(fixture_vault, rel)), body


def test_an_unquoted_wikilink_in_sources_is_read_as_one(fixture_vault):
    (fixture_vault / "company" / "Acme Plan.md").write_text(page("Acme Plan", "note"))
    rel = "content/drafts/2026-12-19-yaml.md"
    (fixture_vault / rel).write_text(ready_draft("A claim.").replace(
        "sources: [wiki/notes/source-note.md]", "sources: [wiki/notes/source-note.md, [[Acme Plan]]]"))
    assert any(f.startswith("error") and "confidential" in f for f in findings(fixture_vault, rel))


# ---------------------------------------------------------------- check of the D052 fixes (D053)

@pytest.mark.parametrize("where", ["sources", "src", "link"])
def test_a_file_or_obsidian_url_to_a_confidential_file_is_caught(fixture_vault, where):
    url = f"file://{fixture_vault}/company/plan.md" if where != "link" else "obsidian://open?vault=b&file=company%2Fplan.md"
    rel = "content/drafts/2026-12-20-url.md"
    draft = ready_draft(f"A claim.\n<!-- src: {url} -->\n" if where == "src" else "A claim.")
    if where != "src":
        draft = draft.replace("sources: [wiki/notes/source-note.md]",
                              f'sources: ["wiki/notes/source-note.md", "[plan]({url})"]' if where == "link"
                              else f'sources: ["wiki/notes/source-note.md", "{url}"]')
    (fixture_vault / rel).write_text(draft)
    assert any(f.startswith("error") and "confidential" in f or "a path in company" in f
               for f in findings(fixture_vault, rel)), where


@pytest.mark.parametrize("line", ["wiki/notes/source-note.md, https://www.linkedin.com/company/acme-robotics",
                                  "wiki/notes/source-note.md and https://www.google.com/finance/quote/ACME:NASDAQ"])
def test_a_web_url_with_company_or_finance_in_it_is_an_external_source(fixture_vault, line):
    rel = "content/drafts/2026-12-21-web.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n## Notes for the owner\n\n- Sources used: {line}\n"))
    assert findings(fixture_vault, rel) == []


@pytest.mark.parametrize("item", ["wiki/notes/source-note.md (e.g. the naming section)",
                                  "wiki/notes/source-note.md (chapter 3.2)", "wiki/notes/source-note.md on Node.js",
                                  "wiki/notes/source-note.md, the 80/20 cut",
                                  "[HBR on categories](https://hbr.org/2016/category) via wiki/notes/source-note.md"])
def test_ordinary_words_in_an_annotated_source_do_not_block_ready(fixture_vault, item):
    rel = "content/drafts/2026-12-22-words.md"
    (fixture_vault / rel).write_text(ready_draft("A claim.").replace(
        "sources: [wiki/notes/source-note.md]", f"sources: [{json.dumps(item)}]"))
    assert findings(fixture_vault, rel) == [], item


@pytest.mark.parametrize("source", ["index.md", "wiki/index.md", "library/index.md"])
def test_an_index_page_is_not_a_confidential_file(fixture_vault, source):
    (fixture_vault / "company" / "index.md").write_text("<!-- generated by anybrain index; do not edit -->\n")
    (fixture_vault / "wiki" / "index.md").write_text("# Wiki\n")
    rel = "content/drafts/2026-12-23-index.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n<!-- src: wiki/notes/source-note.md {source} -->\n"))
    assert not any("confidential" in f for f in findings(fixture_vault, rel)), source


def test_a_page_named_like_a_confidential_file_in_another_folder_is_not_confidential(fixture_vault):
    (fixture_vault / "wiki" / "notes" / "plan.md").write_text(page("Plan", "note"))
    rel = "content/drafts/2026-12-24-plan.md"
    (fixture_vault / rel).write_text(ready_draft("A claim.\n<!-- src: wiki/notes/plan.md -->\n"))
    assert findings(fixture_vault, rel) == []


def test_hidden_files_under_company_do_not_become_confidential_names(fixture_vault):
    (fixture_vault / "company" / ".git").mkdir()
    (fixture_vault / "company" / ".git" / "description").write_text("x")
    rel = "content/drafts/2026-12-25-git.md"
    (fixture_vault / rel).write_text(ready_draft(
        "A claim.\n\n## Notes for the owner\n\n- Sources used: wiki/notes/source-note.md (the main idea and its description)\n"))
    assert findings(fixture_vault, rel) == []


def test_many_confidential_files_keep_the_content_check_fast(fixture_vault):
    import time
    folder = fixture_vault / "company" / "clients"
    folder.mkdir()
    for i in range(1500):
        (folder / f"client-{i}.pdf").write_bytes(b"%PDF")
    rel = "content/drafts/2026-12-26-fast.md"
    (fixture_vault / rel).write_text(ready_draft("A claim.\n" + "<!-- src: wiki/notes/source-note.md -->\n" * 50))
    start = time.monotonic()
    findings(fixture_vault, rel)
    assert time.monotonic() - start < 5


@pytest.mark.parametrize("notes", ["- Sources used:\n  1. projects/acme-mission.md",
                                   "- Sources used: wiki/notes/source-note.md,\n  projects/acme-mission.md",
                                   "Sources used: wiki/notes/source-note.md\n\nAlso drew on company/plan.md for figures."])
def test_every_line_of_the_notes_counts(fixture_vault, notes):
    rel = "content/drafts/2026-12-27-notes.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n## Notes for the owner\n\n{notes}\n"))
    assert any(f.startswith("error") and "confidential" in f or "a path in company" in f
               for f in findings(fixture_vault, rel)), notes


def test_a_decoded_line_break_does_not_shift_a_client_line_number(make_vault):
    root = make_vault({".harness/clients.txt": "Acme Corp\n",
                       "wiki/notes/a.md": page("A", body="One&#10;two&#10;three\n\nWe helped Acme Corp.\n")})
    found = [str(f) for f in lint.check_secrets(lint.Context(root))]
    body_line = (root / "wiki/notes/a.md").read_text().split("\n").index("We helped Acme Corp.") + 1
    assert any(f"wiki/notes/a.md:{body_line}" in f for f in found), found


# ---------------------------------------------------------------- check of the D053 fixes (D054)

@pytest.mark.parametrize("url", ["file:///Users/me/brain/projects/acme-mission.md",
                                 "obsidian://open?vault=brain&file=projects%2Facme-mission",
                                 "obsidian://vault/brain/company/plan", "obsidian:///Users/me/brain/company/plan.md",
                                 "vscode://file/Users/me/brain/projects/acme-mission.md"])
@pytest.mark.parametrize("where", ["sources", "src", "notes"])
def test_a_local_url_to_a_confidential_or_mission_page_is_caught(fixture_vault, url, where):
    rel = "content/drafts/2026-12-28-local.md"
    if where == "src":
        draft = ready_draft(f"A claim.\n<!-- src: {url} -->\n")
    elif where == "notes":
        draft = ready_draft(f"A claim.\n\n## Notes for the owner\n\n- Sources used: wiki/notes/source-note.md, {url}\n")
    else:
        draft = ready_draft("A claim.").replace("sources: [wiki/notes/source-note.md]",
                                                f'sources: ["wiki/notes/source-note.md", "{url}"]')
    (fixture_vault / rel).write_text(draft)
    found = findings(fixture_vault, rel)
    assert any(f.startswith("error") and ("confidential" in f or "a path in company" in f) for f in found), (url, found)


@pytest.mark.parametrize("text", ["obsidian://[vault] links", "file://[path]", "file://"])
def test_a_malformed_local_url_is_not_a_crash(fixture_vault, text):
    rel = "content/drafts/2026-12-29-ipv6.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n## Notes for the owner\n\n- Alternative angles: open {text}\n"))
    findings(fixture_vault, rel)


@pytest.mark.parametrize("item", ["wiki/notes/source-note.md and Projects/Acme-Mission",
                                  "wiki/notes/source-note.md and /Users/me/brain/projects/acme-mission",
                                  "wiki/notes/source-note.md and ~/brain/projects/acme-mission",
                                  "wiki/notes/source-note.md and ../../projects/acme-mission"])
def test_a_mission_path_written_any_way_is_caught(fixture_vault, item):
    rel = "content/drafts/2026-12-30-mission.md"
    (fixture_vault / rel).write_text(ready_draft("A claim.").replace(
        "sources: [wiki/notes/source-note.md]", f"sources: [{json.dumps(item)}]"))
    assert any(f.startswith("error") and "confidential" in f for f in findings(fixture_vault, rel)), item


@pytest.mark.parametrize("line", ["- Alternative angles: why finance/legal should sit in discovery calls",
                                  "- Unmatched needs: 5 years in corporate finance/M&A",
                                  "- Not read: company/ and finance/ stay local"])
def test_prose_elsewhere_in_the_notes_does_not_block_ready(fixture_vault, line):
    rel = "content/drafts/2026-12-31-prose.md"
    (fixture_vault / rel).write_text(ready_draft(
        f"A claim.\n\n## Notes for the owner\n\n- Sources used: wiki/notes/source-note.md\n{line}\n"))
    assert findings(fixture_vault, rel) == [], line


@pytest.mark.parametrize("notes", ["- Sources used: wiki/notes/source-note.md\n\nAlso drew on projects/acme-mission.md.",
                                   "- Sources used: wiki/notes/source-note.md\n- Also leaned on projects/acme-mission.md.",
                                   "- Sources used: wiki/notes/source-note.md, _company/plan.md_",
                                   "### Sources used\n\n- projects/acme-mission.md",
                                   "- **Sources used**\n- projects/acme-mission.md"])
def test_a_mission_page_named_anywhere_in_the_notes_is_caught(fixture_vault, notes):
    rel = "content/drafts/2027-01-01-anywhere.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n## Notes for the owner\n\n{notes}\n"))
    found = findings(fixture_vault, rel)
    assert any(f.startswith("error") and ("confidential" in f or "a path in company" in f) for f in found), (notes, found)


@pytest.mark.parametrize("text", ["[linkedin.com/company/acme](https://www.linkedin.com/company/acme)",
                                  "www.linkedin.com/company/acme-robotics", "linkedin.com/company/acme-robotics",
                                  "www.google.com/finance/quote/ACME:NASDAQ"])
def test_a_web_page_with_company_or_finance_in_its_address_is_external(fixture_vault, text):
    rel = "content/drafts/2027-01-02-web.md"
    (fixture_vault / rel).write_text(ready_draft(f"A claim.\n\n## Notes for the owner\n\n- Sources used: wiki/notes/source-note.md, {text}\n"))
    assert findings(fixture_vault, rel) == [], text
