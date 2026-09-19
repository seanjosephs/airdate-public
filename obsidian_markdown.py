"""Obsidian → Substack markdown fidelity (AD-003 Tier-3a).

Stdlib only, and deliberately free of any `substack` import: server.py runs under
the system interpreter (no venv packages) while substack_draft.py runs under
.venv-substack. Both need the same analysis, so it lives in one shared module —
that way the preflight warning and the eventual transform can never drift.

Stage 0 scope is `scan()` only: pure detection, no rewriting. It models what the
vendored parser (substack/post.py) actually does to a body so the damage can be
surfaced BEFORE a send. The rewrite layers land in later stages.

Reference points in the vendored parser this file models:
  post.py:546  from_markdown  — splits the body on blank lines into blocks
  post.py:657  heading branch — takes the block's first line, welds the rest in
  post.py:665  image branch   — any block starting with "!" enters; the regexes
                                below require "](url)", and there is NO else, so
                                a non-matching block is silently DISCARDED
  post.py:765  list branch    — line.strip() before marker matching, so all
                                indentation (and therefore nesting) is lost
  post.py:781  ordered lists  — only `\\d+\\.` matches; `1)` falls through
  post.py:794  bullets        — only `*` and `-`; `+` falls through
  post.py:818  single-line    — a one-line block has no list case at all
"""
from __future__ import annotations

import re
from typing import Any

# Severity classes.
#   blocker — would ship wrong; the send should stop (promoted in later stages)
#   lossy   — content or structure is silently destroyed
#   info    — cosmetic / recoverable
BLOCKER = "blocker"
LOSSY = "lossy"
INFO = "info"

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+\S")
HASHTAG_TOKEN_RE = re.compile(r"^#[A-Za-z0-9_][A-Za-z0-9_/\-]*$")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")
LIST_MARKER_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
ORDERED_PAREN_RE = re.compile(r"^\s*\d+\)\s+")
PLUS_BULLET_RE = re.compile(r"^\s*\+\s+")
EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
FOOTNOTE_REF_RE = re.compile(r"\[\^[^\]]+\]")
FOOTNOTE_DEF_RE = re.compile(r"^\s*\[\^[^\]]+\]:\s")
CALLOUT_RE = re.compile(r"^\s*>\s*\[!")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# The two shapes post.py's image branch will actually accept.
LINKED_IMAGE_RE = re.compile(r"\[!\[.*?\]\(.*?\)\]\(.*?\)")
PLAIN_IMAGE_RE = re.compile(r"!\[.*?\]\((.*?)\)")


def _finding(kind: str, severity: str, line: int, detail: str) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "line": line, "detail": detail}


def _is_fence(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def iter_blocks(body: str) -> list[tuple[int, list[str]]]:
    """Split into blank-line-delimited blocks the way from_markdown does.

    Returns (1-based start line, lines). A fenced code block is atomic even when
    it contains blank lines, so rules never reach inside sample code.
    """
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 1
    in_fence = False
    for index, raw in enumerate(body.split("\n"), start=1):
        if _is_fence(raw):
            if not current:
                start = index
            current.append(raw)
            in_fence = not in_fence
            if not in_fence:  # fence just closed — the block ends with it
                blocks.append((start, current))
                current = []
            continue
        if in_fence:
            current.append(raw)
            continue
        if raw.strip():
            if not current:
                start = index
            current.append(raw)
        elif current:
            blocks.append((start, current))
            current = []
    if current:
        blocks.append((start, current))
    return blocks


def is_hashtag_line(line: str) -> bool:
    """A line that is nothing but Obsidian tags (the template footer)."""
    tokens = line.split()
    return bool(tokens) and all(HASHTAG_TOKEN_RE.match(token) for token in tokens)


def trailing_hashtag_run(line: str) -> str:
    """The trailing run of tags on an otherwise-prose line, or ""."""
    tokens = line.split()
    keep = len(tokens)
    while keep and HASHTAG_TOKEN_RE.match(tokens[keep - 1]):
        keep -= 1
    if keep in (0, len(tokens)):
        return ""  # all tags (handled by is_hashtag_line) or none
    return " ".join(tokens[keep:])


def block_is_swallowed(lines: list[str]) -> bool:
    """True when post.py's image branch eats this block and emits nothing.

    post.py:665 admits any block starting with "!" (and the linked-image form
    starting with "[" that contains "!["). Both regexes require "](url)". There
    is no else, so a block that enters and matches neither is discarded whole —
    taking every following prose line in the block with it.
    """
    text = "\n".join(lines).strip()
    if not text:
        return False
    enters = text.startswith("!") or (text.startswith("[") and "![" in text)
    if not enters:
        return False
    return not (LINKED_IMAGE_RE.search(text) or PLAIN_IMAGE_RE.search(text))


def split_list_block(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split a block into (lead, list_lines) at its first list marker line.

    `lead` is whatever precedes the first marker (a heading, an intro sentence,
    nothing); `list_lines` runs from that marker to the end of the block and may
    mix marker lines with plain soft-wrapped continuation lines, which
    parse_list_tree attaches to the item above them. A block with no marker
    line at all returns (lines, []). This is the boundary for the Stage 3
    node-builder: the lead goes through the vendored parser as before, the list
    part is built as real nodes.
    """
    for index, line in enumerate(lines):
        if LIST_MARKER_RE.match(line):
            return lines[:index], lines[index:]
    return list(lines), []


def is_list_block(lines: list[str]) -> bool:
    """True when the block's first non-blank line is a list marker line.

    Continuation lines (soft-wrapped prose under an item) no longer disqualify a
    block; see split_list_block for the lead-prose case.
    """
    lead, list_lines = split_list_block(lines)
    return bool(list_lines) and not any(ln.strip() for ln in lead)


def _flatten_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull every descendant up to be a direct sibling, depth-first, in order."""
    out: list[dict[str, Any]] = []
    for item in items:
        out.append({**item, "children": []})
        out.extend(_flatten_items(item["children"]))
    return out


def parse_list_tree(lines: list[str]) -> list[dict[str, Any]]:
    """Parse the list part of a block (see split_list_block) into an item tree.

    Each item is {"ordered": bool, "text": str, "children": [...]}. A plain
    (non-marker) line is a soft-wrapped continuation of the item above it and is
    joined onto that item's text; it is never dropped. A plain line before any
    item is a caller error — split the lead off with split_list_block first —
    and raises rather than silently deleting prose. Nesting is clamped to one
    level deep — Substack's own editor doesn't support more than that, so
    anything past the first nested level is pulled up flat into it rather than
    silently vanishing or (worse) shipping malformed.
    """
    stack: list[tuple[int, dict[str, Any]]] = []
    roots: list[dict[str, Any]] = []
    for raw in lines:
        if not raw.strip():
            continue
        match = LIST_MARKER_RE.match(raw)
        if not match:
            if not stack:
                raise ValueError(
                    "parse_list_tree got a non-marker line before any list item; "
                    f"split the lead off with split_list_block first: {raw.strip()[:60]!r}"
                )
            current = stack[-1][1]
            current["text"] = f"{current['text']} {raw.strip()}".strip()
            continue
        indent = len(match.group(1).expandtabs(4))
        marker = match.group(2)
        item: dict[str, Any] = {
            "ordered": marker[-1] in ".)",
            "text": raw[match.end():].strip(),
            "children": [],
        }
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1]["children"] if stack else roots).append(item)
        stack.append((indent, item))

    # Clamp to one level: every root keeps its direct children as depth-1 items,
    # but anything past that (grandchildren and deeper) collapses flat into that
    # same depth-1 sibling group rather than staying nested under its parent.
    for root in roots:
        root["children"] = _flatten_items(root["children"])
    return roots


def group_list_runs(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split top-level items into consecutive bullet/ordered runs.

    Mirrors the vendored parser's own flush-on-marker-switch behavior (post.py's
    flush_bullets/flush_ordered), so a block that mixes `-` and `1.` items at the
    top level still emits one list node per run instead of one node per block.
    """
    runs: list[list[dict[str, Any]]] = []
    for item in items:
        if runs and runs[-1][-1]["ordered"] == item["ordered"]:
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


def scan(body: str) -> list[dict[str, Any]]:
    """Detect every construct the vendored parser mangles. Pure; no rewriting."""
    findings: list[dict[str, Any]] = []
    if not body or not body.strip():
        return findings

    for start, lines in iter_blocks(body):
        if _is_fence(lines[0]):
            continue  # fenced code is passed through verbatim

        # Whole block silently discarded (the worst failure mode).
        if block_is_swallowed(lines):
            findings.append(_finding(
                "block_swallowed", BLOCKER, start,
                f"This block would be silently dropped, including {len(lines) - 1} following line(s): "
                f"{lines[0].strip()[:80]!r}",
            ))

        # A heading welds every following line of its block into the heading.
        if HEADING_RE.match(lines[0]) and len(lines) > 1:
            findings.append(_finding(
                "heading_weld", LOSSY, start,
                f"Heading {lines[0].strip()[:60]!r} absorbs the {len(lines) - 1} line(s) under it "
                "(needs a blank line after the heading)",
            ))

        # Stage 3: any block with a list marker line is built as a real
        # ProseMirror node tree by the send path (see split_list_block and
        # parse_list_tree below) — nesting, `1)`/`+` markers, single-item lists,
        # a prose or heading lead, and soft-wrapped continuation lines all ship
        # correctly, so none of the per-line list findings below apply to it.
        _lead, list_lines = split_list_block(lines)
        list_block_handled = bool(list_lines)

        # A one-line list block has no list case in the parser at all.
        if len(lines) == 1 and LIST_MARKER_RE.match(lines[0]) and not list_block_handled:
            findings.append(_finding(
                "single_item_list", INFO, start,
                f"Single-item list ships as literal text: {lines[0].strip()[:60]!r}",
            ))

        # Tables: no table node exists in the schema at all.
        table_rows = [ln for ln in lines if TABLE_ROW_RE.match(ln)]
        if len(table_rows) >= 2 and any(TABLE_SEP_RE.match(ln) for ln in lines):
            findings.append(_finding(
                "table", BLOCKER, start,
                f"Table ({len(table_rows)} rows) has no Substack equivalent; it would ship as one "
                f"paragraph per row with a literal separator. Header: {table_rows[0].strip()[:70]!r}",
            ))

        for offset, line in enumerate(lines):
            line_no = start + offset

            if is_hashtag_line(line):
                findings.append(_finding(
                    "hashtag_line", LOSSY, line_no,
                    f"Tag line ships as a heading: {line.strip()[:60]!r}",
                ))
            elif trailing_hashtag_run(line):
                findings.append(_finding(
                    "hashtag_trailing", INFO, line_no,
                    f"Trailing tags on a prose line: {trailing_hashtag_run(line)[:60]!r}",
                ))

            marker = LIST_MARKER_RE.match(line)
            if marker and marker.group(1) and not list_block_handled:
                findings.append(_finding(
                    "nested_list", LOSSY, line_no,
                    f"Nested list item flattens to top level: {line.strip()[:60]!r}",
                ))
            if ORDERED_PAREN_RE.match(line) and not list_block_handled:
                findings.append(_finding(
                    "ordered_paren", LOSSY, line_no,
                    f"`1)` numbering is not recognized and ships as plain text: {line.strip()[:60]!r}",
                ))
            if PLUS_BULLET_RE.match(line) and not list_block_handled:
                findings.append(_finding(
                    "plus_bullet", LOSSY, line_no,
                    f"`+` bullet is not recognized and ships as plain text: {line.strip()[:60]!r}",
                ))
            if CALLOUT_RE.match(line):
                findings.append(_finding(
                    "callout", INFO, line_no,
                    f"Callout flattens to a plain quote with a literal marker: {line.strip()[:60]!r}",
                ))
            if FOOTNOTE_DEF_RE.match(line):
                findings.append(_finding(
                    "footnote", LOSSY, line_no,
                    f"Footnote definition ships as a stray paragraph: {line.strip()[:60]!r}",
                ))
            elif FOOTNOTE_REF_RE.search(line):
                findings.append(_finding(
                    "footnote", INFO, line_no,
                    "Footnote reference ships as literal text",
                ))

            for embed in EMBED_RE.findall(line):
                findings.append(_finding(
                    "embed", BLOCKER, line_no,
                    f"Obsidian embed ![[{embed}]] cannot be resolved and would be dropped",
                ))
            for target in WIKILINK_RE.findall(line):
                findings.append(_finding(
                    "wikilink", INFO, line_no,
                    f"Wikilink [[{target}]] ships as literal brackets",
                ))
            for src in MD_IMAGE_RE.findall(line):
                if not src.strip().lower().startswith(("http://", "https://")):
                    findings.append(_finding(
                        "local_image", BLOCKER, line_no,
                        f"Local image path {src.strip()[:60]!r} is corrupted before upload "
                        "(a leading slash is stripped) and fails silently",
                    ))

    findings.sort(key=lambda f: (f["line"], f["kind"]))
    return findings


def _strip_trailing_hashtags(line: str) -> str:
    tokens = line.split()
    keep = len(tokens)
    while keep and HASHTAG_TOKEN_RE.match(tokens[keep - 1]):
        keep -= 1
    indent = line[: len(line) - len(line.lstrip())]
    return (indent + " ".join(tokens[:keep])).rstrip()


def preprocess(body: str) -> tuple[str, list[dict[str, Any]]]:
    """Layer 1: text→text repairs that need no ProseMirror knowledge.

    Stage 1 scope:
      - Isolate headings. The parser only treats a line as a heading when it is
        the FIRST line of a blank-line-delimited block, and it welds the rest of
        that block into the heading text. So a heading needs a blank line on both
        sides: before it (or it is swallowed into the preceding paragraph as
        literal "## text") and after it (or it eats the prose beneath).
      - Drop Obsidian tag lines, which otherwise ship as an H1.

    Returns (rewritten_body, findings). Fenced code is never touched.
    """
    findings: list[dict[str, Any]] = []
    if not body:
        return body, findings

    # Pass 1 — hashtag removal.
    kept: list[str] = []
    in_fence = False
    for index, raw in enumerate(body.split("\n"), start=1):
        if _is_fence(raw):
            in_fence = not in_fence
            kept.append(raw)
            continue
        if in_fence:
            kept.append(raw)
            continue
        if is_hashtag_line(raw):
            findings.append(_finding(
                "hashtag_line", LOSSY, index,
                f"Removed tag line (would have shipped as a heading): {raw.strip()[:60]!r}",
            ))
            continue
        if trailing_hashtag_run(raw):
            removed = trailing_hashtag_run(raw)
            stripped = _strip_trailing_hashtags(raw)
            findings.append(_finding(
                "hashtag_trailing", INFO, index,
                f"Removed trailing tags: {removed[:60]!r}",
            ))
            kept.append(stripped)
            continue
        kept.append(raw)

    # Pass 2 — heading isolation.
    out: list[str] = []
    in_fence = False
    for position, line in enumerate(kept):
        if _is_fence(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and HEADING_RE.match(line):
            following = kept[position + 1] if position + 1 < len(kept) else ""
            if out and out[-1].strip():
                out.append("")
            out.append(line)
            if following.strip():
                out.append("")
                findings.append(_finding(
                    "heading_weld", LOSSY, position + 1,
                    f"Separated heading from the prose beneath it: {line.strip()[:60]!r}",
                ))
            continue
        out.append(line)

    # Collapse blank runs outside fences — the parser splits on any blank line,
    # so extra blanks are noise, and this keeps the inserted separators tidy.
    collapsed: list[str] = []
    in_fence = False
    for line in out:
        if _is_fence(line):
            in_fence = not in_fence
            collapsed.append(line)
            continue
        if not in_fence and not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)

    findings.sort(key=lambda f: (f["line"], f["kind"]))
    return "\n".join(collapsed), findings


def summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts by severity and kind, for a compact preflight message."""
    by_kind: dict[str, int] = {}
    by_severity: dict[str, int] = {BLOCKER: 0, LOSSY: 0, INFO: 0}
    for finding in findings:
        by_kind[finding["kind"]] = by_kind.get(finding["kind"], 0) + 1
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
    return {"total": len(findings), "by_kind": by_kind, "by_severity": by_severity}
