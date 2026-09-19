#!/usr/bin/env python3
"""Obsidian → Substack fidelity tests.

Three tiers, all offline:
  1. TEXT  — obsidian_markdown.scan() findings. Runs under system python3.
  2. NODE  — what the vendored parser actually emits. Needs .venv-substack;
             skipped with a notice under system python3. api=None means no network.
  3. GUARD — the anti-swallow invariant: no source block may vanish. This is a
             property test, so it catches "a block disappeared" regressions that
             nobody wrote a fixture for.

Stage 0 locks in TODAY's behavior, including the bugs, so each later stage shows
up as a deliberate, visible diff. Cases that document a defect are marked XFAIL:
they assert the broken output on purpose and must be flipped when fixed.

Run:  python3 scripts/obsidian-fidelity-test.py
      .venv-substack/bin/python scripts/obsidian-fidelity-test.py   # adds tier 2
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import obsidian_markdown as om  # noqa: E402

failures: list[str] = []
xfails: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok  {name}")
    else:
        failures.append(name)
        print(f"FAIL  {name}\n  got : {got!r}\n  want: {want!r}")


def check_xfail(name: str, got, want_broken) -> None:
    """Assert a known-broken behavior so the fix shows as a visible diff."""
    if got == want_broken:
        xfails.append(name)
        print(f"  XFAIL  {name}  (documents current damage)")
    else:
        failures.append(name)
        print(f"FAIL  {name} — behavior changed; update this case\n  got : {got!r}\n  was : {want_broken!r}")


def kinds(body: str) -> list[str]:
    return sorted({f["kind"] for f in om.scan(body)})


print("── tier 1: scan() detection ─────────────────────────────────")

# Heading welds every following line of its block into the heading text.
check("heading weld detected",
      kinds("### Week 1\nThat piece is personal.\n"), ["heading_weld"])
check("heading with a blank line after is clean",
      kinds("### Week 1\n\nThat piece is personal.\n"), [])

# Obsidian template footer.
check("hashtag-only line detected", kinds("Prose.\n\n#avss #content #writing\n"), ["hashtag_line"])
check("trailing tags on prose detected",
      kinds("Some prose here #avss #content\n"), ["hashtag_trailing"])
check("hashtag mid-sentence is left alone (never delete prose)",
      kinds("The #metoo movement and item #1 matter.\n"), [])

# Tables have no Substack node at all.
check("table detected",
      kinds("| A | B |\n|---|---|\n| 1 | 2 |\n"), ["table"])

# List defects — Stage 3 builds every block with a list marker line as real
# nodes directly (lead prose split off, continuation lines joined onto their
# item), so scan() no longer flags any of them.
check("nested list in a pure-list block is no longer flagged (Stage 3 handles it)",
      kinds("- Parent\n  - Child\n"), [])
check("`1)` numbering in a pure-list block is no longer flagged (Stage 3 handles it)",
      kinds("1) First\n2) Second\n"), [])
check("`+` bullet in a pure-list block is no longer flagged (Stage 3 handles it)",
      kinds("+ item\n+ other\n"), [])
check("single-item list is no longer flagged (Stage 3 handles it)",
      kinds("- lone bullet\n"), [])
check("flat `-` list is clean", kinds("- one\n- two\n"), [])
check("nested list mixed with plain continuation text is no longer flagged (AD-007)",
      kinds("- Parent\nsome wrapped prose, no marker\n  - Child\n"), [])
check("prose lead before a nested list is no longer flagged (AD-007 split)",
      kinds("Three traps:\n1) **CO2**\n2) **Scope**\n  - detail\n"), [])
check("tab-indented ordered items with wrapped prose are no longer flagged (AD-007)",
      kinds("Intro.\n\t1.\t**One.**\nWrapped line.\n\t2.\t**Two.**\n"), [])

# The parser pieces themselves: continuation attaches, lead prose is loud.
check("split_list_block separates the lead from the list",
      om.split_list_block(["Intro:", "- a", "wrapped", "- b"]), (["Intro:"], ["- a", "wrapped", "- b"]))
check("split_list_block on a block with no marker keeps everything as lead",
      om.split_list_block(["Just prose.", "More prose."]), (["Just prose.", "More prose."], []))
check("continuation line joins onto the item above it (never dropped)",
      [i["text"] for i in om.parse_list_tree(["- Parent", "wrapped prose", "- Next"])],
      ["Parent wrapped prose", "Next"])
check("continuation under a nested child joins onto the child",
      om.parse_list_tree(["- P", "  - C", "child wrap"])[0]["children"][0]["text"], "C child wrap")
try:
    om.parse_list_tree(["lead prose", "- a"])
    check("parse_list_tree refuses a lead line instead of deleting it", "no error", "ValueError")
except ValueError:
    check("parse_list_tree refuses a lead line instead of deleting it", "ValueError", "ValueError")

# The catastrophic one: the whole block is discarded.
check("embed block-swallow detected",
      kinds("![[diagram.png]]\nThis sentence follows.\n"),
      ["block_swallowed", "embed"])
check("wikilink + embed in one block also swallows",
      kinds("[[Note]] then ![[img.png]] later.\n"),
      ["block_swallowed", "embed", "wikilink"])
check("a real markdown image is not flagged as swallowed",
      kinds("![alt](https://example.com/a.png)\n"), [])
check("local image path flagged",
      kinds("![alt](_assets/a.png)\n"), ["local_image"])

# Cheap degradations.
check("callout detected", kinds("> [!note] Heads up\n> body\n"), ["callout"])
check("footnotes detected", kinds("A claim.[^1]\n\n[^1]: The source.\n"), ["footnote"])
check("wikilink detected", kinds("See [[Weapon & Dance]] for more.\n"), ["wikilink"])

# Fenced code must be immune to every rule above.
check("fenced code is untouched",
      kinds("```\n# not a heading\n- [[not a wikilink]]\n| a | b |\n|---|---|\n```\n"), [])

# Severity wiring.
sev = {f["kind"]: f["severity"] for f in om.scan("| A | B |\n|---|---|\n| 1 | 2 |\n")}
check("tables are blocker severity", sev.get("table"), om.BLOCKER)
sev = {f["kind"]: f["severity"] for f in om.scan("![[x.png]]\nprose\n")}
check("swallowed blocks are blocker severity", sev.get("block_swallowed"), om.BLOCKER)

summary = om.summarize(om.scan("### H\nwelded\n\n#tag1 #tag2\n"))
check("summarize counts by kind",
      (summary["total"], sorted(summary["by_kind"])), (2, ["hashtag_line", "heading_weld"]))

print("\n── tier 1b: preprocess() repairs (Stage 1) ──────────────────")


def pre(body: str) -> str:
    return om.preprocess(body)[0]


def pre_kinds(body: str) -> list[str]:
    return sorted({f["kind"] for f in om.preprocess(body)[1]})

check("heading separated from the prose beneath it",
      pre("### Week 1\nThat piece is personal.\n"),
      "### Week 1\n\nThat piece is personal.\n")
check("heading separated from the prose above it",
      pre("Prose above.\n## Title\n\nAfter.\n"),
      "Prose above.\n\n## Title\n\nAfter.\n")
check("already-separated heading is untouched",
      pre("### Week 1\n\nThat piece is personal.\n"),
      "### Week 1\n\nThat piece is personal.\n")
check("heading repair is reported", pre_kinds("### W\nprose\n"), ["heading_weld"])

check("tag-only line removed", pre("Prose.\n\n#avss #content\n"), "Prose.\n")
check("trailing tags stripped, prose kept",
      pre("Real prose here #avss #content\n"), "Real prose here\n")
check("hashtag mid-sentence survives untouched",
      pre("The #metoo movement and item #1 matter.\n"),
      "The #metoo movement and item #1 matter.\n")
check("tag removal is reported",
      pre_kinds("Prose.\n\n#avss #content\n"), ["hashtag_line"])

check("fenced code is never rewritten",
      pre("```\n## not a heading\n#nottag #alsonot\n```\n"),
      "```\n## not a heading\n#nottag #alsonot\n```\n")

# The repair must actually clear what scan() was reporting.
messy = "### Week 1\nThat piece is personal.\n\nMore prose #avss #content\n\n#tag1 #tag2\n"
check("preprocess clears the defects scan reported",
      [f["kind"] for f in om.scan(pre(messy))], [])

print("\n── tier 2 + 3: emitted nodes and the anti-swallow invariant ──")

try:
    from substack.post import Post  # type: ignore
except ImportError:
    print("  skip  vendored `substack` not importable under this interpreter")
    print("        (re-run with .venv-substack/bin/python to exercise tiers 2 and 3)")
    Post = None  # type: ignore


def emit(body: str) -> list[dict]:
    post = Post("T", "S", 1)
    post.from_markdown(body, api=None)
    return post.draft_body["content"]


def node_types(body: str) -> list[str]:
    return [n.get("type") for n in emit(body)]


if Post is not None:
    # Tier 2 — document exactly what the parser does today.
    # Stage 1 FIXED these — assert the repaired pipeline, not the raw parser.
    def emit_repaired(body: str) -> list[dict]:
        post = Post("T", "S", 1)
        post.from_markdown(om.preprocess(body)[0], api=None)
        return post.draft_body["content"]

    check("FIXED heading no longer welds the prose beneath it",
          [n.get("type") for n in emit_repaired("### Week 1\nThat piece is personal.\n")],
          ["heading", "paragraph"])
    check("FIXED heading text no longer contains the welded prose",
          emit_repaired("### Week 1\nThat piece is personal.\n")[0]["content"][0]["text"],
          "Week 1")
    check("FIXED tag line no longer becomes a heading",
          [n.get("type") for n in emit_repaired("Prose.\n\n#avss #content #writing\n")],
          ["paragraph"])
    # Raw-parser damage, still documented so the contrast is explicit.
    check_xfail("XFAIL raw parser still welds (proves the repair is what fixes it)",
                len(emit("### Week 1\nThat piece is personal.\n")), 1)
    check_xfail("XFAIL raw parser still makes a tag line a heading",
                node_types("#avss #content #writing\n"), ["heading"])
    check_xfail("XFAIL table rows become one paragraph each",
                node_types("| A | B |\n|---|---|\n| 1 | 2 |\n"),
                ["paragraph", "paragraph", "paragraph"])
    check_xfail("XFAIL nested list flattens to one level",
                len(emit("- Parent\n  - Child\n  - Second\n")[0]["content"]), 3)
    check_xfail("XFAIL `1)` list is not a list",
                node_types("1) First\n2) Second\n"), ["paragraph", "paragraph"])
    check_xfail("XFAIL single-item list is literal text",
                node_types("- lone bullet\n"), ["paragraph"])
    check_xfail("XFAIL embed block is discarded entirely",
                emit("![[diagram.png]]\nThis sentence follows.\n"), [])

    # Things the parser already gets right — these must never regress.
    check("flat bullet list emits a bullet_list", node_types("- one\n- two\n"), ["bullet_list"])
    check("paragraph passes through", node_types("Just prose.\n"), ["paragraph"])
    check("heading alone is a heading", node_types("## Title\n"), ["heading"])
    # Substack's schema uses camelCase extensions alongside stock node names.
    check("fenced code emits a code block", node_types("```\nx = 1\n```\n"), ["codeBlock"])

    # Tier 3: the Stage 3 send-path fix, exercised through substack_draft.py's
    # real build_list_node — bypasses from_markdown() for pure-list blocks only.
    import substack_draft as sd  # local, .venv-substack only (imports substack.post)
    from substack.post import parse_inline, tokens_to_text_nodes

    def emit_sent(body: str) -> list[dict]:
        post = Post("T", "S", 1)
        repaired, _ = om.preprocess(body)
        for _start, block_lines in om.iter_blocks(repaired):
            lead_lines, list_lines = om.split_list_block(block_lines)
            if any(ln.strip() for ln in lead_lines):
                post.from_markdown("\n".join(lead_lines), api=None)
            if list_lines:
                for run in om.group_list_runs(om.parse_list_tree(list_lines)):
                    node = sd.build_list_node(run, run[0]["ordered"], parse_inline, tokens_to_text_nodes)
                    post.draft_body["content"] = post.draft_body.get("content", []) + [node]
        return post.draft_body["content"]

    check("FIXED nested list actually nests, one level deep",
          emit_sent("- Parent\n  - Child\n  - Second\n"),
          [{
              "type": "bullet_list",
              "content": [
                  {"type": "list_item", "content": [
                      {"type": "paragraph", "content": [{"type": "text", "text": "Parent"}]},
                      {"type": "bullet_list", "content": [
                          {"type": "list_item", "content": [
                              {"type": "paragraph", "content": [{"type": "text", "text": "Child"}]}]},
                          {"type": "list_item", "content": [
                              {"type": "paragraph", "content": [{"type": "text", "text": "Second"}]}]},
                      ]},
                  ]},
              ],
          }])
    # A -> B -> C (two levels of nesting) must clamp to A -> [B, C] flat at depth
    # 1, not stay nested two deep and not drop C.
    clamped = emit_sent("- A\n  - B\n    - C\n")[0]["content"][0]["content"][1]
    check("FIXED nesting past one level is clamped flat, not dropped",
          [li["content"][0]["content"][0]["text"] for li in clamped["content"]],
          ["B", "C"])
    check("FIXED `1)` numbering emits a real ordered_list",
          [n["type"] for n in emit_sent("1) First\n2) Second\n")], ["ordered_list"])
    check("FIXED single-item list emits a real list, not literal text",
          [n["type"] for n in emit_sent("- lone bullet\n")], ["bullet_list"])
    check("FIXED single-item `1)` list emits a real ordered_list",
          [n["type"] for n in emit_sent("1) lone item\n")], ["ordered_list"])
    check("FIXED `+` bullet emits a real bullet_list",
          [n["type"] for n in emit_sent("+ item\n+ other\n")], ["bullet_list"])
    check("mixed top-level bullet/ordered runs emit separate list nodes",
          [n["type"] for n in emit_sent("- a\n- b\n1. c\n2. d\n")], ["bullet_list", "ordered_list"])
    mixed = emit_sent("- Parent\nplain continuation, no marker\n  - Child\n")
    check("FIXED a mixed marker/prose block emits one nested bullet_list (AD-007)",
          [n["type"] for n in mixed], ["bullet_list"])
    check("FIXED the continuation line is joined onto its item, not dropped",
          mixed[0]["content"][0]["content"][0]["content"][0]["text"], "Parent plain continuation, no marker")
    check("FIXED the nested child survives under the wrapped parent",
          mixed[0]["content"][0]["content"][1]["content"][0]["content"][0]["content"][0]["text"], "Child")
    check("FIXED a prose lead splits into a paragraph followed by the list",
          [n["type"] for n in emit_sent("Three traps:\n1) **CO2**\n2) **Scope**\n")],
          ["paragraph", "ordered_list"])
    check("FIXED a heading lead is separated by preprocess, then the list is built",
          [n["type"] for n in emit_sent("## Why\nTraps:\n- a\n- b\n")],
          ["heading", "paragraph", "bullet_list"])

    # Tier 3 — the invariant. Every non-empty source block must leave a trace.
    # Markers (#, -, >, 1.) are consumed by the parser, so probe on the text that
    # survives them — otherwise the invariant reports false losses.
    import re as _re
    MARKER_PREFIX = _re.compile(r"^\s*(?:[#>]+\s*|[-*+]\s+|\d+[.)]\s+)+")

    def probe(line: str) -> str:
        return MARKER_PREFIX.sub("", line).strip()[:20]

    def invariant(name: str, body: str, expect_pass: bool) -> None:
        blocks = om.iter_blocks(body)
        emitted = emit(body)
        serialized = str(emitted)
        missing = [
            probe(lines[0]) for _, lines in blocks
            if probe(lines[0]) and probe(lines[0]) not in serialized
        ]
        held = len(emitted) >= len(blocks) and not missing
        if held == expect_pass:
            verdict = "ok  " if expect_pass else "XFAIL"
            if not expect_pass:
                xfails.append(name)
            print(f"  {verdict}  {name}")
        else:
            failures.append(name)
            print(f"FAIL  {name}: invariant held={held}, expected={expect_pass} (missing={missing})")

    invariant("invariant holds for ordinary prose", "One.\n\nTwo.\n\nThree.\n", True)
    invariant("invariant holds for mixed content", "# T\n\n- a\n- b\n\nProse.\n", True)

    # The send-path invariant (AD-007): emit() probes the raw parser only. The
    # list node-builder is the send path, so every non-blank LINE of a mixed
    # list block — continuation prose included — must survive emit_sent().
    def probe_sent(line: str) -> str:
        # Inline emphasis becomes marks on text nodes, so probe the bare words.
        return probe(line.replace("*", "").replace("_", ""))

    def invariant_sent(name: str, body: str) -> None:
        serialized = str(emit_sent(body))
        missing = [
            probe_sent(line) for _, lines in om.iter_blocks(body) for line in lines
            if probe_sent(line) and probe_sent(line) not in serialized
        ]
        if not missing:
            print(f"  ok    {name}")
        else:
            failures.append(name)
            print(f"FAIL  {name}: send path swallowed {missing}")

    invariant_sent("send-path invariant holds for a mixed list block",
                   "Intro sentence:\n- Parent item\nwrapped continuation prose\n  - Child item\nchild wrap\n- Sibling\n")
    invariant_sent("send-path invariant holds for tab-indented ordered items with prose",
                   "Structure:\n\t1.\t**Wake nobody.**\nRecognition is voluntary.\n\t2.\t**Costume matters.**\nPortal, not obstacle.\n")
    invariant("XFAIL invariant broken by embed swallow",
              "![[diagram.png]]\nThis sentence follows.\n\nLater prose.\n", False)

# Tier 4: corpus canary. Opt-in so the suite stays hermetic; point
# AIRDATE_FIDELITY_CORPUS at your essays folder to scan your own vault.
if os.environ.get("AIRDATE_FIDELITY_CORPUS"):
    import collections
    import pathlib

    root = pathlib.Path(os.environ["AIRDATE_FIDELITY_CORPUS"]).expanduser()
    print("\n── tier 4: corpus canary ────────────────────────────────────")
    if not root.exists():
        print(f"  skip  vault not found at {root}")
    else:
        counts: collections.Counter = collections.Counter()
        scanned = 0
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(root).as_posix()
            if rel.startswith("Archive/"):
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="ignore")
            body = text.split("\n---\n", 1)[1] if text.startswith("---\n") and "\n---\n" in text else text
            for finding in om.scan(body):
                counts[finding["severity"]] += 1
        print(f"  scanned {scanned} publishable essays: {dict(counts)}")
        # Stage 0 records the baseline rather than asserting zero — each transform
        # stage should drive these down, and this is where that shows up.
        print("  (baseline recorded; later stages must reduce lossy/blocker counts)")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print(f"all obsidian fidelity tests passed ({len(xfails)} xfail documenting current damage)")
