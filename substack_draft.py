#!/usr/bin/env python3
"""substack_draft.py: last-mile CLI that turns a saved draft .md into a Substack DRAFT.

airdate saves the essay to drafts/<slug>.md and asks the paired Obsidian
connector to send it. The connector runs this script with its pinned Python,
on that one file, and hands it the Substack session in SUBSTACK_COOKIES for
the length of this one process. airdate itself never holds the session.

DRAFT ONLY, by design. This script creates a Substack draft and STOPS. It never
calls prepublish, publish or schedule, so nothing is ever emailed to
subscribers. You review the draft in Substack and press publish there
yourself. tests/test_draft_only.py fails if that ever changes.

Inputs:
    SUBSTACK_COOKIES  the session Cookie header, supplied by the connector
    frontmatter `publication`  the publication URL, e.g. https://yourname.substack.com

Output: a JSON object on stdout: {ok, draft_id, edit_url, title, message} on
success, {ok: false, error_kind, message} on failure. Exit 0 on success,
non-zero on failure.

error_kind is the typed failure class airdate reads instead of matching the
message text. This CLI knows two of them: `auth` when there is no session or
Substack refuses the one it was given, `transport` for everything else.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import obsidian_markdown  # local, stdlib-only — shared with server.py

AUDIENCE_ALLOWED = {"everyone", "only_paid", "founding", "only_free"}
COMMENT_ALLOWED = {"none", "only_paid", "everyone"}

# Frontmatter audience values the editor modal might emit, mapped to the
# library's vocabulary. Anything unrecognized falls back to "everyone".
AUDIENCE_ALIASES = {
    "free": "only_free",
    "only_free": "only_free",
    "paid": "only_paid",
    "only_paid": "only_paid",
    "founding": "founding",
    "everyone": "everyone",
    "public": "everyone",
}
COMMENT_ALIASES = {
    "paid": "only_paid",
    "only_paid": "only_paid",
    "subscribers": "everyone",
    "subscriber": "everyone",
    "everyone": "everyone",
    "public": "everyone",
    "none": "none",
    "off": "none",
}


def fail(message: str, error_kind: str = "transport", **extra) -> "None":
    """Print a JSON error to stdout and exit non-zero."""
    payload = {"ok": False, "error_kind": error_kind, "message": message}
    payload.update(extra)
    print(json.dumps(payload))
    sys.exit(1)


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a `---`-delimited YAML frontmatter block from the markdown body."""
    import yaml  # provided by python-substack's deps

    if not text.startswith("---"):
        return {}, text
    # Match the first fenced block: --- ... ---
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    front_raw, body = m.group(1), m.group(2)
    try:
        front = yaml.safe_load(front_raw) or {}
    except yaml.YAMLError:
        front = {}
    if not isinstance(front, dict):
        front = {}
    return front, body


def strip_working_notes(body: str) -> str:
    """Remove HTML comment blocks (the modal's `notes` working text) from the body."""
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()


def normalize_audience(value: str) -> str:
    v = (value or "").strip().lower()
    if v in AUDIENCE_ALLOWED:
        return v
    return AUDIENCE_ALIASES.get(v, "everyone")


def normalize_comments(value: str) -> str:
    v = (value or "").strip().lower()
    if v in COMMENT_ALLOWED:
        return v
    return COMMENT_ALIASES.get(v, "everyone")


def clean_list(value) -> list[str]:
    if isinstance(value, list):
        values = value
    else:
        values = re.split(r",|\n", str(value or ""))
    return [str(item).strip() for item in values if str(item).strip()]


def upload_image(api, image: str) -> str:
    image = str(image or "").strip()
    if not image:
        return ""
    if image.startswith(("http://", "https://")):
        return image
    if not Path(image).exists():
        fail(f"Hero image not found: {image}")
    uploaded = api.get_image(image)
    url = uploaded.get("url") if isinstance(uploaded, dict) else ""
    if not url:
        fail(f"Hero image upload did not return a Substack URL: {image}", upload_result=uploaded)
    return url


def build_list_node(items: list[dict], ordered: bool, parse_inline, tokens_to_text_nodes) -> dict:
    """A ProseMirror bullet_list/ordered_list node from an obsidian_markdown item tree.

    obsidian_markdown.parse_list_tree() does the text-only parsing (stdlib, no
    substack import); this is the one place that turns it into real nodes, since
    parse_inline/tokens_to_text_nodes only exist under .venv-substack. Nesting is
    already clamped to one level by parse_list_tree, so this never recurses past
    a list_item's own direct children.
    """
    list_items = []
    for item in items:
        text_nodes = tokens_to_text_nodes(parse_inline(item["text"]))
        content = [{"type": "paragraph", "content": text_nodes}] if text_nodes else [{"type": "paragraph"}]
        if item["children"]:
            child_ordered = item["children"][0]["ordered"]
            content.append(build_list_node(item["children"], child_ordered, parse_inline, tokens_to_text_nodes))
        list_items.append({"type": "list_item", "content": content})
    return {"type": "ordered_list" if ordered else "bullet_list", "content": list_items}


def resolve_cookie() -> str:
    return os.environ.get("SUBSTACK_COOKIES", "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Substack DRAFT from a saved draft .md file.")
    parser.add_argument("--file", required=True, help="Path to the drafts/<slug>.md file to upload.")
    parser.add_argument("--publish", action="store_true",
                        help="DISABLED. Draft-only by design. Present only to fail loudly if wired by mistake.")
    args = parser.parse_args()

    if args.publish:
        fail("Refusing to publish: this CLI is draft-only by design. Publish from Substack yourself.")

    path = Path(args.file)
    if not path.exists():
        fail(f"Draft file not found: {path}")

    cookies_string = resolve_cookie()
    if not cookies_string:
        fail("No Substack session. Connect Substack through the airdate connector in Obsidian.",
             error_kind="auth")

    text = path.read_text(encoding="utf-8", errors="ignore")
    front, raw_body = split_frontmatter(text)
    body = strip_working_notes(raw_body)

    title = str(front.get("title") or "").strip()
    if not title:
        fail("Draft has no title; cannot create a Substack draft.")
    if not body:
        fail("Draft body is empty; nothing to send.")

    publication_url = str(front.get("publication") or "").strip()

    try:
        from substack import Api
        from substack.post import Post, parse_inline, tokens_to_text_nodes
    except ImportError as exc:
        fail(f"python-substack not importable by this interpreter ({sys.executable}). "
             f"Install it into the venv this command points at. ({exc})")

    try:
        api = Api(cookies_string=cookies_string, publication_url=publication_url or None)
        user_id = api.get_user_id()

        post = Post(
            title=title,
            subtitle=str(front.get("subtitle") or "").strip(),
            user_id=user_id,
            audience=normalize_audience(str(front.get("audience") or "everyone")),
            write_comment_permissions=normalize_comments(str(front.get("comment_permissions") or "everyone")),
        )
        # These values are part of airdate's draft-ready frontmatter contract.
        # python-substack serializes Post attributes verbatim in get_draft().
        post.email_subject = str(front.get("email_subject") or title).strip()
        post.email_preview_text = str(front.get("email_preview_text") or front.get("summary") or "").strip()
        hero = str(front.get("hero") or front.get("hero_image") or "").strip()
        if hero:
            hero_url = upload_image(api, hero)
            post.add({
                "type": "captionedImage",
                "src": hero_url,
                "alt": str(front.get("thumbnail_alt") or title).strip(),
                "title": str(front.get("thumbnail_alt") or title).strip(),
                "imageSize": "wide",
                "belowTheFold": False,
            })

        # Repair Obsidian-isms the converter mangles, then convert. Only the sent
        # body is rewritten — drafts/<slug>.md stays a faithful mirror of the vault.
        body, fidelity_findings = obsidian_markdown.preprocess(body)

        # Convert the markdown body into Substack's structured body. Tier-3a
        # Stage 3: from_markdown() strips every line's indentation before it ever
        # checks for a list marker (post.py:765), so real nesting can't come from
        # markdown text alone. Any block with a list marker line is split at its
        # first marker (see split_list_block): the lead (a heading, an intro
        # sentence) still goes through from_markdown(), and the list part is
        # built as ProseMirror nodes directly, one nesting level deep, with
        # soft-wrapped continuation lines joined onto their item. Blocks with no
        # marker go through from_markdown() unchanged, block by block.
        for _start, block_lines in obsidian_markdown.iter_blocks(body):
            lead_lines, list_lines = obsidian_markdown.split_list_block(block_lines)
            if any(ln.strip() for ln in lead_lines):
                post.from_markdown("\n".join(lead_lines), api=api)
            if list_lines:
                # post.add() is a DSL helper (rebuilds content via
                # add_complex_text) — not a raw node append. A pre-built list
                # node has to go straight onto draft_body, the same way the
                # vendored parser's own flush_bullets()/flush_ordered() do it.
                tree = obsidian_markdown.parse_list_tree(list_lines)
                for run in obsidian_markdown.group_list_runs(tree):
                    node = build_list_node(run, run[0]["ordered"], parse_inline, tokens_to_text_nodes)
                    post.draft_body["content"] = post.draft_body.get("content", []) + [node]

        # One canonical Markdown note corresponds to one remote draft. Once a
        # successful send has written the remote id into frontmatter, later
        # sends replace that same draft rather than creating duplicates.
        stored_draft_id = str(front.get("substack_draft_id") or "").strip()
        updated = bool(stored_draft_id)
        if stored_draft_id:
            try:
                draft = api.put_draft(stored_draft_id, **post.get_draft())
            except Exception as exc:  # noqa: BLE001
                if "404" not in str(exc) and "not found" not in str(exc).lower():
                    raise
                # Never fall back to creating a new draft here: if the old one
                # was published, a new draft would duplicate a live post.
                fail(
                    f"Substack has no draft {stored_draft_id} for the account signed in"
                    f"{' on ' + publication_url if publication_url else ''}. It may have been"
                    " deleted or published, or it belongs to another account. To send this"
                    " note as a new draft, delete substack_draft_id and substack_draft_url"
                    " from its frontmatter, then send again.",
                    error_kind="transport",
                    stale_draft_id=stored_draft_id,
                )
            draft_id = stored_draft_id
        else:
            draft = api.post_draft(post.get_draft())
            draft_id = draft.get("id") or draft.get("draft_id")
        if not draft_id:
            fail("Substack did not return a draft ID.")

        # Optional metadata pass (slug + SEO) — only fields that are present.
        warnings = []
        repaired: dict[str, int] = {}
        for finding in fidelity_findings:
            repaired[finding["kind"]] = repaired.get(finding["kind"], 0) + 1
        for kind, count in sorted(repaired.items()):
            warnings.append(f"Body repaired before sending: {kind} x{count}")
        for finding in obsidian_markdown.scan(body):
            if finding["severity"] in (obsidian_markdown.BLOCKER, obsidian_markdown.LOSSY):
                warnings.append(f"Body line {finding['line']}: {finding['detail']}")
        put_kwargs = {}
        if str(front.get("slug") or "").strip():
            put_kwargs["slug"] = str(front.get("slug")).strip()
        if str(front.get("seo_title") or "").strip():
            put_kwargs["search_engine_title"] = str(front.get("seo_title")).strip()
        if str(front.get("seo_description") or "").strip():
            put_kwargs["search_engine_description"] = str(front.get("seo_description")).strip()
        if str(front.get("section") or "").strip():
            try:
                post.set_section(str(front.get("section")).strip(), api.get_sections())
                put_kwargs["draft_section_id"] = post.draft_section_id
            except Exception as exc:
                warnings.append(f"Section was not applied: {exc}")
        if draft_id and put_kwargs:
            try:
                api.put_draft(draft_id, **put_kwargs)
            except Exception as exc:
                warnings.append(f"Metadata polish was not applied: {exc}")

        tags = clean_list(front.get("tags"))
        if draft_id and tags:
            try:
                api.add_tags_to_post(draft_id, tags)
            except Exception as exc:
                warnings.append(f"Tags were not applied: {exc}")

        if str(front.get("social_image") or "").strip():
            warnings.append("Social image is saved in frontmatter; this CLI does not know Substack's social-image field.")
        if str(front.get("scheduled_at") or "").strip():
            warnings.append("Scheduled time is saved in frontmatter; this draft-only CLI did not schedule publication.")
        if front.get("send_email") is True:
            warnings.append("Email send is saved in frontmatter; this draft-only CLI did not email subscribers.")

        edit_url = None
        if publication_url and draft_id:
            edit_url = f"{publication_url.rstrip('/')}/publish/post/{draft_id}"

        print(json.dumps({
            "ok": True,
            "draft_id": draft_id,
            "updated": updated,
            "edit_url": edit_url,
            "title": title,
            "hero_uploaded": bool(hero),
            "tags": tags,
            "warnings": warnings,
            "message": "Draft updated in Substack. Review and publish there." if updated else "Draft created in Substack. Review and publish there.",
        }))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any library/network/auth error as JSON
        msg = str(exc)
        hint = ""
        error_kind = "transport"
        if any(k in msg.lower() for k in ("401", "403", "unauthor", "forbidden", "login", "cookie")):
            hint = " (Substack refused the session; it may have expired. Run \"Connect Substack for airdate\" in Obsidian and send again.)"
            error_kind = "auth"
        fail(f"Substack draft creation failed: {msg}{hint}", error_kind=error_kind)


if __name__ == "__main__":
    main()
