"""anybrain.graph: link syntaxes, resolution, graph.json, neighbors (PRD 13.1 to 13.5)."""
from __future__ import annotations

import json

import pytest

from anybrain import graph as g
from anybrain import vault as v
from conftest import EXAMPLE, page


def build(root):
    return g.Graph(root)


def out_of(graph, rel):
    return [e.other for e in graph.nodes[rel].out]


# ---------------------------------------------------------------- syntaxes (13.1)

def test_every_link_syntax_resolves(make_vault):
    root = make_vault({
        "wiki/concepts/target.md": page("Target", "concept", confidence="low"),
        "wiki/notes/source.md": page("Source", body="""
            ## Related
            - plain [[target]] link
            - display text [[target|the target]]
            - heading [[target#Summary]]
            - folder form [[concepts/target]] and full path [[wiki/concepts/target]]
            - with extension [[wiki/concepts/target.md]]
            - markdown link to [the target](../concepts/target.md)
        """),
    })
    graph = build(root)
    node = graph.nodes["wiki/notes/source.md"]
    assert node.broken == []
    assert len(node.links) == 7
    assert out_of(graph, "wiki/notes/source.md") == ["wiki/concepts/target.md"]  # deduplicated


def test_links_in_comments_code_and_urls_are_ignored(make_vault):
    root = make_vault({"wiki/notes/a.md": page("A", body="""
        <!-- guidance: [[not-a-link]] -->
        Inline `[[code-link]]` stays code.
        ```
        [[fenced-link]]
        ```
        A web link [site](https://example.com/page.md) and an anchor [up](#top).
    """)})
    assert build(root).nodes["wiki/notes/a.md"].links == []


def test_heading_only_link_points_inside_the_page(make_vault):
    root = make_vault({"wiki/notes/a.md": page("A", body="See [[#Summary]].\n## Summary\n")})
    node = build(root).nodes["wiki/notes/a.md"]
    assert node.links == [] and node.broken == []


def test_links_inside_raw_are_not_parsed_and_raw_targets_become_sources(make_vault):
    root = make_vault({
        "raw/highlights/book.md": "Highlight that mentions [[missing-page]].\n",
        "raw/docs/report.pdf": "binary",
        "library/book.md": page("Book", "book", sources=["raw/highlights/book.md"], body="""
            From the export [[raw/highlights/book]] and [the report](../raw/docs/report.pdf).
        """),
    })
    graph = build(root)
    node = graph.nodes["library/book.md"]
    assert node.out == [] and node.broken == []
    assert node.sources == ["raw/highlights/book.md", "raw/docs/report.pdf"]
    assert "raw/highlights/book.md" not in graph.nodes


def test_embeds_resolve_to_attachments(make_vault):
    root = make_vault({
        "raw/attachments/diagram.png": "png",
        "wiki/notes/a.md": page("A", body="![[diagram.png]] and ![[missing.png]]"),
    })
    node = build(root).nodes["wiki/notes/a.md"]
    assert node.sources == ["raw/attachments/diagram.png"]
    assert [link.target for link in node.broken] == ["missing.png"]


# ---------------------------------------------------------------- resolution order (13.2)

def test_resolution_order_path_stem_alias_title(make_vault):
    root = make_vault({
        "library/snow-leopard.md": page("The Snow Leopard", "book", aliases=["Snow Leopard"]),
        "wiki/notes/st-augustine.md": page("St. Augustine"),
        "wiki/notes/a.md": page("A", body="""
            - stem [[snow-leopard]]
            - alias, any case [[snow leopard]]
            - title [[The Snow Leopard]]
            - a title with a dot [[St. Augustine]]
            - unknown [[nothing-here]]
        """),
    })
    graph = build(root)
    node = graph.nodes["wiki/notes/a.md"]
    assert [link.target for link in node.broken] == ["nothing-here"]
    assert out_of(graph, "wiki/notes/a.md") == ["library/snow-leopard.md", "wiki/notes/st-augustine.md"]


def test_pages_win_over_raw_files_with_the_same_stem(make_vault):
    root = make_vault({
        "raw/highlights/snow-leopard.md": "export",
        "raw/articles/only-raw.md": "article",
        "library/snow-leopard.md": page("Snow", "book"),
        "wiki/notes/a.md": page("A", body="- [[snow-leopard]] and [[only-raw]]"),
    })
    graph = build(root)
    assert out_of(graph, "wiki/notes/a.md") == ["library/snow-leopard.md"]
    assert graph.nodes["wiki/notes/a.md"].sources == ["raw/articles/only-raw.md"]


def test_duplicate_stems_are_ambiguous(make_vault):
    root = make_vault({
        "library/same.md": page("Same one", "book"),
        "wiki/notes/same.md": page("Same two"),
        "wiki/notes/a.md": page("A", body="- links to [[same]]"),
    })
    graph = build(root)
    assert [link.target for link in graph.nodes["wiki/notes/a.md"].broken] == ["same"]
    assert graph.resolver.duplicate_stems() == {"same": ["library/same.md", "wiki/notes/same.md"]}


def test_markdown_links_resolve_relative_then_from_root(make_vault):
    root = make_vault({
        "library/b.md": page("B", "book"),
        "wiki/notes/a.md": page("A", body="[rel](../../library/b.md) [root](library/b.md) [bad](../../../x.md) [%20](no%20file.md)"),
    })
    node = build(root).nodes["wiki/notes/a.md"]
    assert [e.other for e in node.out] == ["library/b.md"]
    assert [link.target for link in node.broken] == ["../../../x.md", "no file.md"]


# ---------------------------------------------------------------- edges, context, stats (13.4)

def test_in_edges_context_and_line_numbers(make_vault):
    root = make_vault({
        "wiki/concepts/c.md": page("C", "concept", confidence="low"),
        "library/b.md": page("B", "book", body="""
            ## Key ideas
            - **Bold** idea that *builds on* [[c]], see [the page](../wiki/concepts/c.md).
        """),
    })
    graph = build(root)
    (edge,) = graph.nodes["library/b.md"].out
    assert edge.context == "Bold idea that builds on [[c]], see the page."
    assert edge.line == 14  # 11 frontmatter lines, a blank line, the heading, then the bullet
    (back,) = graph.nodes["wiki/concepts/c.md"].inbound
    assert back.other == "library/b.md"


def test_context_is_truncated_to_160_characters():
    text = g.clean_context("- " + "word " * 60 + "[[x]]")
    assert len(text) == 160


def test_self_links_are_ignored(make_vault):
    root = make_vault({"wiki/notes/a.md": page("A", body="Back to [[a]].")})
    node = build(root).nodes["wiki/notes/a.md"]
    assert node.out == [] and node.broken == []


def test_orphans_are_compiled_pages_without_incoming_links(make_vault):
    root = make_vault({
        "library/alone.md": page("Alone", "book"),
        "journal/2026-01-01.md": page("Brief", "brief", body="Opened [[linked]]."),
        "projects/linked.md": page("Linked", "project"),
        "me/profile.md": page("Profile", "profile"),
    })
    graph = build(root)
    assert graph.orphans() == ["library/alone.md"]
    assert graph.stats() == {"pages": 4, "edges": 1, "orphans": 1, "broken": 0}


def test_graph_json_schema_on_example():
    data = json.loads((EXAMPLE / ".harness" / "graph.json").read_text(encoding="utf-8"))
    assert set(data) == {"generated", "pages", "stats"}
    assert set(data["stats"]) == {"pages", "edges", "orphans", "broken"}
    node = data["pages"]["library/snow-leopard.md"]
    assert set(node) == {"title", "type", "description", "sensitivity", "out", "in", "sources", "broken"}
    assert {"to": "wiki/concepts/category-design.md",
            "context": node["out"][0]["context"]} == node["out"][0]
    assert all(set(e) == {"from", "context"} for e in node["in"])


def test_graph_json_redacts_confidential_pages(make_vault):
    root = make_vault({
        "company/plan.md": page("Plan", sensitivity="confidential", sources=["raw/docs/deal.pdf"],
                                body="Linked to [[public-note]] because of the secret deal."),
        "wiki/notes/public-note.md": page("Public note"),
    })
    data = build(root).to_json_dict("2026-11-16T00:00:00Z")
    secret = data["pages"]["company/plan.md"]
    assert secret["title"] == "Plan" and secret["description"] == ""
    assert secret["out"] == [{"to": "wiki/notes/public-note.md", "context": ""}]
    assert secret["sources"] == []
    assert data["pages"]["wiki/notes/public-note.md"]["in"] == [{"from": "company/plan.md", "context": ""}]
    assert "secret deal" not in json.dumps(data)


def test_graph_json_comparison_ignores_the_timestamp(example_vault):
    graph = build(example_vault)
    assert g.graph_json_changes(example_vault, graph) is None


# ---------------------------------------------------------------- neighbors (13.5)

def test_neighbors_text_format(capsys):
    assert g.run_neighbors(EXAMPLE, "library/snow-leopard.md") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "The Snow Leopard (book, public)"
    assert lines[1].startswith("  → wiki/concepts/category-design.md   Win by creating")
    assert lines[2].startswith('      "Being different is being a snow leopard')
    arrows = [line.strip()[0] for line in lines[1:] if line.startswith("  ") and not line.startswith("      ")]
    assert arrows == ["→"] * 3 + ["←"] * 3


def test_neighbors_accepts_a_slug_or_title_and_rejects_unknown_pages(capsys):
    assert g.run_neighbors(EXAMPLE, "The Snow Leopard") == 0
    with pytest.raises(v.VaultError, match="page not found"):
        g.run_neighbors(EXAMPLE, "no-such-page")


def test_neighbors_depth_two_is_deduplicated_and_marked():
    graph = build(EXAMPLE)
    entries = g.neighbor_entries(graph, "library/snow-leopard.md", depth=2, profile="frontier")
    second = [e for e in entries if e["distance"] == 2]
    paths = [e["path"] for e in second]
    assert "projects/paused-idea.md" in paths
    assert len(paths) == len(set(paths))
    first_paths = {e["path"] for e in entries if e["distance"] == 1}
    assert not first_paths & set(paths)
    assert "library/snow-leopard.md" not in paths
    assert all(e["via"] for e in second)


@pytest.fixture
def mixed(make_vault):
    return make_vault({
        "projects/tool.md": page("Tool", "project", body="Paid through [[company-admin]] and read [[a-note]]."),
        "company/company-admin.md": page("Company admin", sensitivity="confidential", body="Secret about [[tool]]."),
        "wiki/notes/a-note.md": page("A note"),
    })


def test_confidential_neighbours_are_masked_on_frontier(mixed, capsys, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_PROFILE", "frontier")
    assert g.run_neighbors(mixed, "projects/tool.md") == 0
    out = capsys.readouterr().out
    assert "Company admin (confidential: local profile only)" in out
    assert "company/company-admin.md" not in out
    assert "Secret about" not in out


def test_confidential_neighbours_are_shown_on_local(mixed, capsys, monkeypatch):
    monkeypatch.setenv("ANYBRAIN_PROFILE", "local")
    assert g.run_neighbors(mixed, "projects/tool.md") == 0
    out = capsys.readouterr().out
    assert "company/company-admin.md" in out
    assert "Secret about [[tool]]." in out


def test_confidential_page_itself_is_refused_on_frontier(mixed, capsys):
    assert g.run_neighbors(mixed, "company/company-admin.md") == 1
    assert "local profile only" in capsys.readouterr().err


def test_depth_two_never_expands_through_a_masked_page(mixed):
    graph = build(mixed)
    entries = g.neighbor_entries(graph, "wiki/notes/a-note.md", depth=2, profile="frontier")
    assert all(not e["masked"] or e["distance"] == 2 for e in entries)
    assert not any(e["via"] == "company/company-admin.md" for e in entries)


def test_neighbors_json(capsys):
    assert g.run_neighbors(EXAMPLE, "languaging", as_json=True) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["page"] == "wiki/concepts/languaging.md"
    assert {e["direction"] for e in data["neighbors"]} == {"out", "in"}
    assert all(e["distance"] == 1 for e in data["neighbors"])
