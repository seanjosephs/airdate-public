#!/usr/bin/env python3
"""Round-trip tests for the surgical frontmatter editor (AD-003).

Mirrors production: split_frontmatter -> sanitize_updates -> apply_updates ->
apply_frontmatter_edits. Asserts that only changed keys move and everything else
(comments, key order, block-list format, quote style, unknown keys, body) is
preserved byte-for-byte.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

failures = []


def edit(text, updates):
    fm, _ = server.split_frontmatter(text)
    sanitized = server.sanitize_updates(updates)
    _, changes = server.apply_updates(fm, sanitized)
    return server.apply_frontmatter_edits(text, changes)


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        failures.append(name)
        print(f"FAIL  {name}")
        print("  --- got ---")
        print(got)
        print("  --- want ---")
        print(want)


# 1. Scalar change touches only its line; body preserved verbatim.
src = '---\nstatus: "Inbox"\ntotem: "circle"\ncategory: "Politics"\n---\n\nBody line one.\nBody line two.\n'
want = '---\nstatus: "Ready for Air"\ntotem: "circle"\ncategory: "Politics"\n---\n\nBody line one.\nBody line two.\n'
check("scalar change, body preserved", edit(src, {"status": "Ready for Air"}), want)

# 2. New key inserted at canonical position (scheduled_at comes after tags/section
#    but after category among present keys), others intact.
src = '---\nstatus: "Ready for Air"\ntotem: "circle"\ncategory: "Politics"\n---\n\nBody.\n'
want = '---\nstatus: "Ready for Air"\ntotem: "circle"\ncategory: "Politics"\nscheduled_at: "2026-07-27"\n---\n\nBody.\n'
check("new key inserted canonically", edit(src, {"scheduled_at": "2026-07-27"}), want)

# 3. Deleting a key (empty value) removes only that line.
src = '---\nstatus: "Ready for Air"\nscheduled_at: "2026-07-27"\ntotem: "circle"\n---\n\nBody.\n'
want = '---\nstatus: "Ready for Air"\ntotem: "circle"\n---\n\nBody.\n'
check("delete key via empty value", edit(src, {"scheduled_at": ""}), want)

# 4. Obsidian block-list tags survive an unrelated scalar change.
src = ('---\nstatus: "Inbox"\ntags:\n  - politics\n  - ai\ncategory: "Politics"\n---\n\nBody.\n')
want = ('---\nstatus: "Writers Likey"\ntags:\n  - politics\n  - ai\ncategory: "Politics"\n---\n\nBody.\n')
check("block-list tags untouched by scalar change", edit(src, {"status": "Writers Likey"}), want)

# 5. Comments and blank lines inside frontmatter are preserved.
src = '---\n# hand note: keep me\nstatus: "Inbox"\ntotem: "circle"\n---\n\nBody.\n'
want = '---\n# hand note: keep me\nstatus: "Ready for Air"\ntotem: "circle"\n---\n\nBody.\n'
check("comment preserved", edit(src, {"status": "Ready for Air"}), want)

# 6. Unknown (hand-authored) key is left byte-for-byte.
src = '---\nstatus: "Inbox"\nmy_custom: keep this exactly\ntotem: "circle"\n---\n\nBody.\n'
want = '---\nstatus: "Ready for Air"\nmy_custom: keep this exactly\ntotem: "circle"\n---\n\nBody.\n'
check("unknown key preserved", edit(src, {"status": "Ready for Air"}), want)

# 7. Changing a block-list value keeps block style (changed items take the app's
#    canonical quoted form; only the changed key is rewritten).
src = '---\nstatus: "Writers Room"\ntags:\n  - old\n---\n\nBody.\n'
want = '---\nstatus: "Writers Room"\ntags:\n  - "one"\n  - "two"\n---\n\nBody.\n'
check("changed list keeps block style", edit(src, {"tags": ["one", "two"]}), want)

# 8. Single-quoted value on an untouched key stays single-quoted.
src = "---\nstatus: \"Inbox\"\ntitle: 'Hand Quoted'\ntotem: \"circle\"\n---\n\nBody.\n"
want = "---\nstatus: \"Ready for Air\"\ntitle: 'Hand Quoted'\ntotem: \"circle\"\n---\n\nBody.\n"
check("untouched quote style preserved", edit(src, {"status": "Ready for Air"}), want)

# 9. A set-to-current value (already normalized) is a no-op => byte-identical.
src = '---\nstatus: "Ready for Air"\n---\n\nBody.\n'
check("no-op change is byte-identical", edit(src, {"status": "Ready for Air"}), src)

# 10. Dead-alias status IS migrated when status is explicitly set (Inbox → Writers
#     Room), touching only the status line.
src = '---\nstatus: "Inbox"\ntotem: "circle"\n---\n\nBody.\n'
want = '---\nstatus: "Writers Room"\ntotem: "circle"\n---\n\nBody.\n'
check("dead alias migrates on explicit set", edit(src, {"status": "Inbox"}), want)

# 11. Unschedule composition: the /unschedule action is set_essay_status(id,
#     "Writers Likey", {"scheduled_at": ""}) — status flips, scheduled_at is
#     deleted, body byte-identical, and it's idempotent.
src = '---\nstatus: "Ready for Air"\nscheduled_at: "2026-07-27"\ntotem: "circle"\n---\n\nEssay body.\n'
want = '---\nstatus: "Writers Likey"\ntotem: "circle"\n---\n\nEssay body.\n'
unscheduled = edit(src, {"status": "Writers Likey", "scheduled_at": ""})
check("unschedule clears scheduled_at + body intact", unscheduled, want)
check("unschedule is idempotent", edit(unscheduled, {"status": "Writers Likey", "scheduled_at": ""}), want)

# 12. CRLF file: frontmatter is parsed (no field loss) and the body is preserved
#     byte-for-byte on a status edit (the old code lost all fields + corrupted).
crlf = '---\r\nstatus: "Inbox"\r\ntotem: "circle"\r\n---\r\n\r\nCRLF body line.\r\n'
fm_crlf, body_crlf = server.split_frontmatter(crlf)
if fm_crlf.get("status") == "Inbox" and fm_crlf.get("totem") == "circle":
    print("  ok  CRLF frontmatter parses (no field loss)")
else:
    failures.append("CRLF frontmatter parses")
    print(f"FAIL  CRLF frontmatter parses -> {fm_crlf}")
crlf_edited = edit(crlf, {"status": "Ready for Air"})
if "CRLF body line.\r\n" in crlf_edited and 'status: "Ready for Air"' in crlf_edited:
    print("  ok  CRLF edit keeps body + updates status")
else:
    failures.append("CRLF edit keeps body")
    print(f"FAIL  CRLF edit keeps body -> {crlf_edited!r}")

# 13. UTF-8 BOM file: same guarantees; the BOM is preserved.
bom = '﻿---\nstatus: "Inbox"\ntotem: "circle"\n---\n\nBOM body.\n'
fm_bom, _ = server.split_frontmatter(bom)
bom_edited = edit(bom, {"status": "Ready for Air"})
want_bom = '﻿---\nstatus: "Ready for Air"\ntotem: "circle"\n---\n\nBOM body.\n'
if fm_bom.get("status") == "Inbox":
    print("  ok  BOM frontmatter parses (no field loss)")
else:
    failures.append("BOM frontmatter parses")
    print(f"FAIL  BOM frontmatter parses -> {fm_bom}")
check("BOM edit preserves BOM + body", bom_edited, want_bom)

if failures:
    print(f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("\nall frontmatter round-trip tests passed")
