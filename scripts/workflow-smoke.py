#!/usr/bin/env python3
"""Workflow smoke checks for airdate.

Starts server.py on an isolated random port with a throwaway vault and runtime
dir, walks first run, then checks the core pages, essay index quality, static
assets, API guardrails and the send path through a fake Obsidian connector.
No network, no Substack account.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable or "python3"


class SmokeFailure(AssertionError):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ANN001
        return None


def request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
):
    req = urllib.request.Request(base + path, data=data, headers=headers or {}, method=method)
    opener = urllib.request.build_opener() if follow_redirects else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=10) as response:
            body = response.read()
            return response.status, dict(response.headers), body
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def json_request(base: str, path: str, payload: dict | None = None, headers: dict[str, str] | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    status, response_headers, body = request(base, path, method="POST" if payload is not None else "GET", data=data, headers=hdrs)
    try:
        parsed = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{path} did not return JSON: {exc}") from exc
    return status, response_headers, parsed


def assert_true(condition: bool, message: str):
    if not condition:
        raise SmokeFailure(message)


def wait_for_index(base: str) -> list[dict]:
    last_error = None
    for _ in range(40):
        try:
            status, _, payload = json_request(base, "/api/essays")
            if status == 200 and payload.get("essays"):
                return payload["essays"]
        except Exception as exc:  # noqa: BLE001 - printed on timeout
            last_error = exc
        time.sleep(0.5)
    raise SmokeFailure(f"essay index did not warm; last error: {last_error}")


def content_type(headers: dict[str, str]) -> str:
    return str(headers.get("content-type") or headers.get("Content-Type") or "")


def header_value(headers: dict[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return str(value)
    return ""


def write_fixture_essays(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "Smoke").mkdir(parents=True, exist_ok=True)
    (root / "Sources").mkdir(parents=True, exist_ok=True)
    (root / "_assets" / "substack").mkdir(parents=True, exist_ok=True)
    (root / "Smoke" / "Short Essay.md").write_text(
        """---
title: "Short Smoke Essay"
subtitle: "A small publishable fixture."
summary: "A short fixture for Obsidian sync."
date: 2026-06-20
tags: ["essay", "smoke"]
status: developing
totem: circle
---

This is a short publishable essay body.

It has enough text to preview, save, and send as a fake Substack draft.
""",
        encoding="utf-8",
    )
    long_body = " ".join(f"sourceword{i}" for i in range(10050))
    (root / "Sources" / "RAW_Long Source.md").write_text(
        f"""---
title: "RAW Long Smoke Source"
summary: "A long source conversation fixture."
date: 2026-06-20
tags: ["essay", "source"]
status: developing
totem: square
---

{long_body}
""",
        encoding="utf-8",
    )
    (root / "Sources" / "Existing Linked Draft.md").write_text(
        """---
title: "Existing Linked Draft"
summary: "A linked draft fixture."
date: 2026-06-20
tags: ["essay", "source"]
status: developing
totem: square
source_role: draft
draft_of: "Sources/RAW_Long Source.md"
---

This is already a linked draft body.
""",
        encoding="utf-8",
    )


def start_fake_connector(port: int, expected_token: str):
    captures: list[dict] = []
    # `mode["draft"]` selects what the fake connector answers on /draft. The
    # failure shapes mirror what obsidian-airdate-connector/main.js returns
    # after classifyDraftResult (auth, timeout) or from its no-session refusal.
    mode: dict[str, str] = {"draft": "ok"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def reply(self, status: int, payload: dict):
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self) -> bool:
            return self.headers.get("X-Airdate-Token") == expected_token

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            if not self.authorized():
                self.reply(401, {"ok": False, "error": "Unauthorized"})
            elif self.path == "/status":
                self.reply(200, {"ok": True, "connected": True, "connected_at": "2026-07-31T00:00:00Z"})
            else:
                self.reply(404, {"ok": False, "error": "Not found"})

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length)
            if not self.authorized():
                self.reply(401, {"ok": False, "error": "Unauthorized"})
                return
            if self.path == "/connect":
                self.reply(202, {"ok": True, "pending": True})
                return
            if self.path != "/draft":
                self.reply(404, {"ok": False, "error": "Not found"})
                return
            payload = json.loads(raw.decode("utf-8"))
            # The real connector reads nothing but the one draft file; capture
            # what substack_draft.py would have read from it.
            markdown = Path(payload.get("file", "")).read_text(encoding="utf-8") if payload.get("file") else ""
            stored_draft_id = ""
            for line in markdown.splitlines():
                if line.startswith("substack_draft_id:"):
                    stored_draft_id = line.split(":", 1)[1].strip().strip('"')
                    break
            captures.append({**payload, "markdown": markdown, "stored_draft_id": stored_draft_id})
            if mode["draft"] == "auth":
                auth_stdout = json.dumps({
                    "ok": False,
                    "error_kind": "auth",
                    "message": "Substack draft creation failed: 401 Unauthorized (Substack refused the session; it may have expired. Run \"Connect Substack for airdate\" in Obsidian and send again.)",
                })
                self.reply(200, {
                    "ok": False,
                    "error_kind": "auth",
                    "message": json.loads(auth_stdout)["message"],
                    "stdout": auth_stdout,
                    "stderr": "",
                    "returncode": 1,
                })
                return
            if mode["draft"] == "timeout":
                self.reply(200, {
                    "ok": False,
                    "error_kind": "timeout",
                    "message": "Substack draft command did not finish within 120s. Check Substack for the draft before sending again.",
                    "stdout": "",
                    "stderr": "",
                    "returncode": None,
                })
                return
            if mode["draft"] == "no_session":
                message = "Connect Substack in Obsidian before sending from airdate."
                self.reply(400, {"ok": False, "error_kind": "auth", "error": message, "message": message})
                return
            result = {
                "ok": True,
                "draft_id": "connector-draft-1",
                "edit_url": "https://example.substack.com/p/connector-draft-1",
                "updated": bool(stored_draft_id),
                "warnings": ["Section was not applied: fake connector has no sections"],
                "message": "Fake connector draft created.",
            }
            self.reply(200, {**result, "stdout": json.dumps(result), "stderr": "", "returncode": 0})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, captures, mode


# Runs obsidian-airdate-connector/main.js's classifyDraftResult under node with
# a stub `obsidian` module, so the kill → timeout classification is checked at
# the layer that owns it rather than only through the fake connector's replies.
CONNECTOR_CLASSIFY_TEST = r"""
const Module = require("module");
const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request === "obsidian") return "obsidian";
  return origResolve.call(this, request, ...rest);
};
require.cache["obsidian"] = { id: "obsidian", filename: "obsidian", loaded: true, exports: { Plugin: class {}, Notice: class {} } };
const { classifyDraftResult } = require(process.argv[2]);
const killed = Object.assign(new Error("Command failed"), { killed: true, signal: "SIGTERM", code: null });
const authStdout = JSON.stringify({ ok: false, error_kind: "auth", message: "Substack draft creation failed: 403 Forbidden" });
const okStdout = JSON.stringify({ ok: true, draft_id: "d1", edit_url: "https://example.substack.com/publish/post/d1", warnings: ["Tags were not applied: nope"] });
const out = {
  killed: classifyDraftResult(killed, "", ""),
  auth: classifyDraftResult(Object.assign(new Error("exit 1"), { code: 1 }), authStdout, ""),
  crash: classifyDraftResult(Object.assign(new Error("exit 2"), { code: 2 }), "", "Traceback: boom"),
  spawn: classifyDraftResult(Object.assign(new Error("spawn python3 ENOENT"), { code: "ENOENT" }), "", ""),
  ok: classifyDraftResult(null, okStdout, ""),
};
process.stdout.write(JSON.stringify(out));
"""


def check_connector_classification(checks: list[str]) -> None:
    node = shutil.which("node")
    if not node:
        checks.append("connector classifyDraftResult: skipped (node not on PATH)")
        return
    test_path = Path(tempfile.mkdtemp(prefix="airdate-connector-test-")) / "classify.js"
    test_path.write_text(CONNECTOR_CLASSIFY_TEST, encoding="utf-8")
    main_js = (ROOT / "obsidian-airdate-connector" / "main.js").resolve()
    completed = subprocess.run([node, str(test_path), str(main_js)], capture_output=True, text=True, timeout=30)
    assert_true(completed.returncode == 0, f"connector classification test crashed: {completed.stderr}")
    out = json.loads(completed.stdout)
    killed = out["killed"]
    assert_true(killed.get("ok") is False and killed.get("error_kind") == "timeout" and killed.get("returncode") is None,
                f"execFile kill was not classified as a timeout: {killed}")
    assert_true("Check Substack for the draft" in killed.get("message", ""), f"timeout message lost its warning: {killed}")
    auth = out["auth"]
    assert_true(auth.get("ok") is False and auth.get("error_kind") == "auth" and auth.get("returncode") == 1,
                f"auth stdout was not lifted: {auth}")
    crash = out["crash"]
    assert_true(crash.get("ok") is False and crash.get("error_kind") == "transport" and crash.get("returncode") == 2 and crash.get("message"),
                f"plain crash was not classified as transport with a message: {crash}")
    spawn = out["spawn"]
    assert_true(spawn.get("ok") is False and spawn.get("error_kind") == "transport" and spawn.get("returncode") is None,
                f"spawn failure still reported a numeric returncode: {spawn}")
    ok = out["ok"]
    assert_true(ok.get("ok") is True and "error_kind" not in ok and ok.get("warnings") == ["Tags were not applied: nope"],
                f"success result was mangled: {ok}")
    checks.append("connector classifyDraftResult: kill → timeout, exit → auth/transport, success keeps warnings")


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    temp_dir = Path(tempfile.mkdtemp(prefix="airdate-smoke-"))
    log_path = temp_dir / "server.log"
    drafts_dir = temp_dir / "drafts"
    essays_dir = temp_dir / "Essays"
    write_fixture_essays(essays_dir)
    vault_dir = essays_dir.parent
    (vault_dir / ".obsidian").mkdir(parents=True, exist_ok=True)
    # Drafts always go to <airdate>/drafts (the connector only runs files from
    # there); remember what was already present so the smoke leaves no trace.
    drafts_root = ROOT / "drafts"
    drafts_before = {p for p in drafts_root.rglob("*") if p.is_file()}
    runtime_dir = temp_dir / "runtime"
    connector_port = free_port()
    connector_token = "smoke-connector-token"
    connector_server, connector_captures, connector_mode = start_fake_connector(connector_port, connector_token)
    env = os.environ.copy()
    for key in ("OBSIDIAN_ESSAYS_DIR", "AIRDATE_VAULT_DIR", "AIRDATE_ESSAYS_FOLDER", "OBSIDIAN_VAULT_NAME",
                "SUBSTACK_PUB", "AIRDATE_CONNECTOR_PORT", "SUBSTACK_COOKIES"):
        env.pop(key, None)
    env.update({
        "PORT": str(port),
        "AIR_DATE_DATA_DIR": str(runtime_dir),
        "AIRDATE_CONNECTOR_PORT": str(connector_port),
    })

    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [PYTHON, "server.py"],
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )

    checks: list[str] = []
    try:
        # First run: no config.json yet, so essay APIs refuse and settings
        # carries the setup state until a vault is chosen.
        for _ in range(100):
            try:
                status, _, first_status = json_request(base, "/api/app/status")
                if status == 200:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        assert_true(first_status.get("setup", {}).get("required") is True, f"fresh install was not in setup: {first_status.get('setup')}")
        blocked_status, _, blocked_payload = json_request(base, "/api/essays")
        assert_true(blocked_status == 409 and blocked_payload.get("error_kind") == "setup_required",
                    f"essay API answered before setup: {blocked_status} {blocked_payload}")
        # The wizard checks each step without saving, and may create the essays folder.
        assert_true(first_status["setup"].get("wizard") is True, f"fresh install did not offer the wizard: {first_status['setup']}")
        check_status, _, checked = json_request(base, "/api/settings/check", {"vault_path": str(vault_dir), "essays_folder": "Wizard Essays", "category_mode": "off"})
        assert_true(check_status == 200 and checked.get("vault_ok") is True and checked.get("essays_ok") is False and not checked.get("errors"),
                    f"wizard check misjudged a vault without its essays folder: {checked}")
        created_status, _, _ = json_request(base, "/api/settings/create-essays-folder", {"vault_path": str(vault_dir), "essays_folder": "Wizard Essays"})
        assert_true(created_status == 200 and (vault_dir / "Wizard Essays").is_dir(), "wizard did not create the essays folder")
        (vault_dir / "Wizard Essays").rmdir()
        assert_true(not (runtime_dir / "config.json").exists(), "a wizard step wrote config.json before finish")
        checks.append("wizard: steps checked without saving, essays folder created on request")

        not_vault_status, _, not_vault = json_request(base, "/api/settings", {"vault_path": str(essays_dir / "Smoke"), "essays_folder": "Essays"})
        assert_true(not_vault_status == 200 and not_vault.get("setup", {}).get("required") is True
                    and "not an Obsidian vault" in not_vault.get("setup", {}).get("vault_message", ""),
                    f"a folder without .obsidian was accepted as a vault: {not_vault}")
        bad_day_status, _, bad_day = json_request(base, "/api/settings", {"vault_path": str(vault_dir), "publish_day": "someday"})
        assert_true(bad_day_status == 400 and bad_day.get("errors"), f"invalid publish day was accepted: {bad_day}")
        setup_status, _, setup_result = json_request(base, "/api/settings", {
            "vault_path": str(vault_dir),
            "essays_folder": "Essays",
            "publish_day": "monday",
            "publication_name": "Smoke Weekly",
        })
        assert_true(setup_status == 200 and setup_result.get("ok") is True and setup_result.get("setup", {}).get("required") is False,
                    f"first-run settings did not complete setup: {setup_result}")
        config_file = runtime_dir / "config.json"
        assert_true(config_file.exists() and oct(config_file.stat().st_mode & 0o777) == "0o600", "config.json missing or not private")
        saved_config = json.loads(config_file.read_text(encoding="utf-8"))
        assert_true(saved_config["vault"]["name"] == vault_dir.name and saved_config["calendar"]["publish_day"] == "monday",
                    f"config.json did not record the settings: {saved_config}")
        totem_slots = setup_result.get("config", {}).get("totems", {}).get("items", [])
        assert_true([t["key"] for t in totem_slots] == ["circle", "triangle", "square", "diamond", "star"],
                    f"placeholder totems missing: {totem_slots}")
        assert_true(all(t["image"].startswith("/static/totems/placeholder-") for t in totem_slots), "placeholder totem icons missing")
        checks.append("first run: setup gate, vault validation, config.json written, placeholder totems on")

        # Tag presets save through the settings form and come back in the UI config.
        dup_status, _, dup = json_request(base, "/api/settings", {"tag_presets": [{"name": "Craft", "tags": "a"}, {"name": "craft", "tags": "b"}]})
        assert_true(dup_status == 400 and any("used twice" in e for e in dup.get("errors", [])), f"duplicate preset names were accepted: {dup}")
        preset_status, _, preset_result = json_request(base, "/api/settings", {"tag_presets": [{"name": "Craft", "color": "#112233", "tags": "writing, editing"}]})
        assert_true(preset_status == 200 and preset_result.get("config", {}).get("tag_presets") == [{"name": "Craft", "color": "#112233", "tags": ["writing", "editing"]}],
                    f"tag preset did not save: {preset_result}")
        cleared_status, _, cleared = json_request(base, "/api/settings", {"tag_presets": []})
        assert_true(cleared_status == 200 and cleared.get("config", {}).get("tag_presets") == [], f"tag presets did not clear: {cleared}")
        checks.append("tag presets: duplicate names refused, save and clear through settings")

        essays = wait_for_index(base)
        checks.append(f"essay index warmed: {len(essays)} essays")

        # The card's totem picker: read the file state, then save through the normal path.
        assert_true(all("totem_raw" in e for e in essays), "essay cards do not carry the note's own totem value")
        pick = essays[0]
        _, _, pick_detail = json_request(base, f"/api/essays/{pick['id']}")
        pick_status, _, _ = json_request(base, f"/api/essays/{pick['id']}/save", {
            "updates": {"totem": "star"}, "expected_mtime": pick_detail["mtime"], "expected_content_hash": pick_detail["content_hash"]})
        _, _, picked = json_request(base, f"/api/essays/{pick['id']}")
        assert_true(pick_status == 200 and picked["frontmatter"].get("totem") == "star", f"totem pick did not save: {picked.get('frontmatter')}")
        stale_status, _, _ = json_request(base, f"/api/essays/{pick['id']}/save", {
            "updates": {"totem": "circle"}, "expected_mtime": pick_detail["mtime"], "expected_content_hash": pick_detail["content_hash"]})
        assert_true(stale_status == 409, f"a totem pick with a stale file state was accepted: {stale_status}")
        none_status, _, _ = json_request(base, f"/api/essays/{pick['id']}/save", {
            "updates": {"totem": "none"}, "expected_mtime": picked["mtime"], "expected_content_hash": picked["content_hash"]})
        _, _, cleared_pick = json_request(base, f"/api/essays/{pick['id']}")
        assert_true(none_status == 200 and "totem" not in cleared_pick["frontmatter"], f"picking none did not clear the totem: {cleared_pick.get('frontmatter')}")
        if pick.get("totem_raw"):
            # Later checks read this fixture's own totem; put it back.
            restore_status, _, _ = json_request(base, f"/api/essays/{pick['id']}/save", {"updates": {"totem": pick["totem_raw"]}})
            assert_true(restore_status == 200, "could not restore the fixture's totem")
        checks.append("totem picker: assign via save, stale state refused, none clears")

        status, headers, body = request(base, "/airdate")
        assert_true(status == 200 and "text/html" in content_type(headers), "/airdate did not serve HTML")
        assert_true(b"<title>airdate" in body.lower(), "/airdate HTML missing the airdate title")
        assert_true(b'data-view="settings"' in body and b'id="settings-view"' in body, "/airdate HTML missing settings view")
        assert_true(b'data-view="all-ideas"' in body and b'id="all-ideas-view"' in body, "/airdate HTML missing essay catalog view")
        assert_true(b'id="month-grid"' in body and b'id="month-rail"' in body, "/airdate HTML missing the essays-page month rail")
        assert_true(b'id="calendar-view"' not in body, "/airdate HTML still has the retired standalone calendar view")
        assert_true(b'id="all-ideas-cards"' in body and b'id="ai-search"' in body, "/airdate HTML missing catalog grid/search")
        assert_true(b'name="body"' in body and b'id="editor-create-draft"' in body, "/airdate HTML missing Obsidian body/draft editor controls")
        checks.append("/airdate served")

        status, headers, body = request(base, "/", follow_redirects=False)
        assert_true(status == 302 and header_value(headers, "Location") == "/airdate", "/ did not redirect to /airdate")
        status, headers, body = request(base, "/")
        assert_true(status == 200 and b"<title>airdate" in body.lower(), "/ redirect did not land on the catalog")
        checks.append("/ redirects to /airdate")

        for retired in ("/calendar", "/hub", "/airdate-styleboard", "/assets/anything.webp"):
            status, _, _ = request(base, retired, follow_redirects=False)
            assert_true(status == 404, f"{retired} should be gone, got {status}")
        for retired_post in ("/api/render", "/api/save", "/api/send-draft", "/api/import-sheet", "/api/schedule"):
            status, _, _ = json_request(base, retired_post, {})
            assert_true(status == 404, f"{retired_post} should be gone, got {status}")
        checks.append("retired routes and old-form APIs return 404")

        status, _, body = request(base, "/favicon.ico")
        assert_true(status == 204 and body == b"", "/favicon.ico should return a quiet 204")
        checks.append("favicon request stays quiet")

        status, _, app_status = json_request(base, "/api/app/status")
        assert_true(status == 200 and app_status.get("ok") is True, "/api/app/status failed")
        assert_true(app_status.get("port") == port, "/api/app/status returned the wrong port")
        assert_true(app_status.get("app_url") == f"{base}/airdate", "/api/app/status returned the wrong app URL")
        assert_true("legacy_app_url" not in app_status and "desk_url" not in app_status, "status still carries desk or legacy URLs")
        assert_true(app_status.get("essay_count") == len(essays), "/api/app/status returned the wrong essay count")
        assert_true(app_status.get("config", {}).get("publish_day") == "monday", "status config lost the publish day")
        substack_keys = set((app_status.get("substack") or {}).keys())
        assert_true({"connected", "connector", "publication_configured"}.issubset(substack_keys), "settings Substack status changed")
        assert_true("can_generate_images" not in substack_keys, "settings Substack status still advertises an image generator")
        refresh_status, _, refresh_payload = json_request(base, "/api/app/refresh-essays", {})
        assert_true(refresh_status == 200 and refresh_payload.get("count", 0) > 0, "/api/app/refresh-essays failed")
        checks.append("settings status + refresh APIs passed")

        active = [essay for essay in essays if essay.get("status") in {"Inbox", "Writers Room", "Ready for Air"}]
        assert_true(len(active) > 0, "no active working-status essays found")
        assert_true(all(essay.get("search_blob") for essay in essays), "some essays are missing search_blob")
        standalone = next((essay for essay in essays if essay.get("source_role") == "standalone"), None)
        source = next((essay for essay in essays if essay.get("source_role") == "source"), None)
        linked = next((essay for essay in essays if essay.get("source_role") == "draft"), None)
        assert_true(standalone is not None, "standalone essay fixture was not indexed")
        assert_true(source is not None and source.get("is_long_source") is True, "long source fixture was not classified")
        assert_true(linked is not None and linked.get("draft_of") == source.get("relative_path"), "linked draft fixture was not indexed")
        assert_true(source.get("linked_draft_count", 0) >= 1, "source card did not report linked drafts")
        bad_paths = [
            essay.get("relative_path") or essay.get("path") or ""
            for essay in essays
            if "/archive/" in f"/{essay.get('relative_path', '').lower()}/"
            or f"/{essay.get('relative_path', '').lower()}".endswith("/moc.md")
        ]
        assert_true(not bad_paths, f"MOC/archive essays leaked into index: {bad_paths[:5]}")
        checks.append("essay index quality + source/draft roles passed")

        assets = {
            "/static/airdate.js": "javascript",
            "/static/air-date.css": "text/css",
            "/static/tokens.css": "text/css",
            "/static/totems/placeholder-1.svg": "image/svg+xml",
            "/static/totems/placeholder-5.svg": "image/svg+xml",
        }
        for path, expected in assets.items():
            status, headers, body = request(base, path)
            assert_true(status == 200, f"{path} returned {status}")
            assert_true(expected in content_type(headers), f"{path} content-type was {content_type(headers)!r}")
            assert_true(len(body) > 0, f"{path} was empty")
        checks.append("static assets served with expected MIME")

        missing_status, _, missing_payload = json_request(base, "/api/essays/not-real")
        assert_true(missing_status == 404 and missing_payload.get("error"), "unknown essay did not return JSON 404")

        wrong_type_status, _, wrong_type_body = request(
            base,
            f"/api/essays/{essays[0]['id']}/preview",
            method="POST",
            data=b"{}",
            headers={"content-type": "text/plain"},
        )
        assert_true(wrong_type_status == 415 and b"error" in wrong_type_body, "wrong content type did not return JSON 415")

        cross_status, _, cross_payload = json_request(
            base,
            f"/api/essays/{essays[0]['id']}/preview",
            {},
            headers={"origin": "https://example.invalid"},
        )
        assert_true(cross_status == 403 and cross_payload.get("error"), "cross-origin POST did not return JSON 403")

        invalid_status, _, invalid_body = request(
            base,
            f"/api/essays/{essays[0]['id']}/preview",
            method="POST",
            data=b"{",
            headers={"content-type": "application/json"},
        )
        assert_true(invalid_status == 400 and b"error" in invalid_body, "invalid JSON did not return JSON 400")

        traversal_status, _, traversal_body = request(base, "/static/../server.py")
        assert_true(traversal_status == 404 and b"error" in traversal_body, "static traversal did not return 404")
        checks.append("API guardrails passed")

        status, _, substack_status = json_request(base, "/api/substack/status")
        assert_true(status == 200, "/api/substack/status failed")
        assert_true(substack_status.get("connected") is False and substack_status.get("connector", {}).get("paired") is False,
                    f"an unpaired install reported a connection: {substack_status}")
        assert_true(substack_status.get("publication_configured") is False, "smoke env should not have a real publication configured")
        # Pair: the connector writes {port, token} into airdate's secrets.
        pairing = runtime_dir / "secrets" / "connector.json"
        pairing.parent.mkdir(parents=True, exist_ok=True)
        pairing.write_text(json.dumps({"port": connector_port, "token": connector_token}), encoding="utf-8")
        status, _, substack_status = json_request(base, "/api/substack/status")
        assert_true(substack_status.get("connected") is True and substack_status.get("connector", {}).get("paired") is True,
                    f"paired fake connector was not detected: {substack_status}")
        assert_true(substack_status.get("session_source") == "obsidian_connector", "session source is not the connector")
        assert_true("cookie" not in json.dumps(substack_status).lower(), "connector status exposed session material")
        assert_true("can_generate_images" not in substack_status and "image_model" not in substack_status,
                    "status payload still advertises an image generator")
        status_check_keys = {item.get("key") for item in substack_status.get("setup_checks", [])}
        assert_true({"connector_paired", "substack_command", "substack_session", "publication"} == status_check_keys, f"status setup checks changed: {status_check_keys}")

        selected = standalone
        selected_id = selected["id"]
        detail_status, _, detail = json_request(base, f"/api/essays/{selected_id}")
        assert_true(detail_status == 200 and detail.get("body"), "essay detail did not include editable body")
        assert_true(detail.get("content_hash") and detail.get("mtime"), "essay detail did not include file state")

        edited_body = detail["body"].rstrip() + "\n\nAdded from the airdate smoke."
        preview_status, _, preview = json_request(
            base,
            f"/api/essays/{selected_id}/preview",
            {"updates": {"summary": "Updated smoke summary."}, "body": edited_body},
        )
        assert_true(preview_status == 200 and "Added from the airdate smoke." in preview.get("preview_markdown", ""), "body preview did not include editor text")

        save_status, _, saved = json_request(
            base,
            f"/api/essays/{selected_id}/save",
            {
                "updates": {"summary": "Updated smoke summary."},
                "body": edited_body,
                "expected_content_hash": detail["content_hash"],
                "expected_mtime": detail["mtime"],
            },
        )
        assert_true(save_status == 200 and saved.get("body_saved") is True, f"save to Obsidian failed: {saved}")
        selected_path = essays_dir / selected["relative_path"]
        saved_markdown = selected_path.read_text(encoding="utf-8")
        assert_true("summary: \"Updated smoke summary.\"" in saved_markdown, "saved Markdown missing updated frontmatter")
        assert_true("Added from the airdate smoke." in saved_markdown, "saved Markdown missing updated body")

        conflict_hash = saved["content_hash"]
        selected_path.write_text(saved_markdown + "\nExternal Obsidian edit.\n", encoding="utf-8")
        conflict_status, _, conflict = json_request(
            base,
            f"/api/essays/{selected_id}/save",
            {"updates": {"summary": "Should conflict."}, "expected_content_hash": conflict_hash},
        )
        assert_true(conflict_status == 409 and conflict.get("reason") == "file_changed", f"conflict response changed: {conflict}")

        detail_status, _, detail = json_request(base, f"/api/essays/{selected_id}")
        assert_true(detail_status == 200 and "External Obsidian edit." in detail.get("body", ""), "detail did not reload current disk body")
        checks.append("Obsidian body save + conflict detection passed")

        source_status, _, source_preflight = json_request(
            base,
            f"/api/essays/{source['id']}/preflight",
            {"publish": {"title": source["title"], "subtitle": "Source subtitle", "publication": "https://example.substack.com"}},
        )
        assert_true(source_status == 200 and source_preflight.get("ready") is False, f"source preflight unexpectedly passed: {source_status} {source_preflight}")
        assert_true("source_role" in {item.get("key") for item in source_preflight.get("blockers", [])}, "source preflight did not block direct sending")

        create_status, _, created = json_request(
            base,
            f"/api/essays/{source['id']}/create-draft",
            {"selected_text": "A publishable seed from the long source."},
        )
        assert_true(create_status == 200 and created.get("draft", {}).get("source_role") == "draft", f"linked draft creation failed: {created}")
        assert_true(created.get("draft", {}).get("draft_of") == source.get("relative_path"), "created draft did not link back to source")
        checks.append("source guard + linked draft creation passed")

        blocked_status, _, blocked = json_request(
            base,
            f"/api/essays/{selected_id}/preflight",
            {"publish": {"title": selected["title"], "subtitle": "Smoke subtitle"}},
        )
        assert_true(blocked_status == 200 and blocked.get("ready") is False, "preflight without thumbnail unexpectedly passed")
        blocker_keys = {item.get("key") for item in blocked.get("blockers", [])}
        assert_true({"publication", "hero"}.issubset(blocker_keys), f"preflight blockers missing publication/hero: {blocker_keys}")

        # The ChatGPT bridge: the server prepares the styled prompt and nothing
        # else. No key, no generator, no lifecycle gate.
        prompt_status, _, prompt_payload = json_request(
            base,
            f"/api/essays/{selected_id}/thumbnail-prompt",
            {"publish": {"title": selected["title"], "subtitle": "Smoke subtitle"}},
        )
        assert_true(prompt_status == 200 and prompt_payload.get("ok") is True, f"thumbnail prompt endpoint failed: {prompt_payload}")
        prompt_text = prompt_payload.get("prompt", "")
        assert_true("text-free Substack essay thumbnail for Smoke Weekly" in prompt_text and selected["title"] in prompt_text,
                    f"thumbnail prompt lost its style spine, publication name or the essay title: {prompt_text!r}")
        assert_true("Totem lens: Circle" in prompt_text, f"thumbnail prompt lost the totem label: {prompt_text!r}")
        gone_status, _, _ = json_request(base, f"/api/essays/{selected_id}/thumbnail", {"publish": {}})
        assert_true(gone_status == 404, f"old thumbnail generator route still answers: {gone_status}")
        source_prompt_status, _, source_prompt = json_request(base, f"/api/essays/{source['id']}/thumbnail-prompt", {"publish": {}})
        assert_true(source_prompt_status == 400 and "linked draft" in source_prompt.get("error", ""),
                    f"source note was not refused a thumbnail prompt: {source_prompt_status} {source_prompt}")
        assert_true("openai_key" not in {item.get("key") for item in blocked.get("warnings", [])},
                    "preflight still warns about an image-generator key")

        tiny_png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        attach_status, _, attached = json_request(
            base,
            f"/api/essays/{selected_id}/attach-hero",
            {
                "filename": "smoke-card.png",
                "dataUrl": tiny_png,
                "expected_content_hash": detail["content_hash"],
                "expected_mtime": detail["mtime"],
            },
        )
        assert_true(attach_status == 200 and attached.get("ok") is True, f"hero attach failed: {attached}")
        attached_rel = attached.get("relativePath", "")
        assert_true(attached_rel.startswith("_assets/substack/"), f"attached hero did not use Obsidian assets: {attached_rel}")
        assert_true((essays_dir / attached_rel).exists(), "attached hero file was not written to Obsidian assets")
        attached_frontmatter = attached.get("essay", {}).get("frontmatter", {})
        assert_true(attached_frontmatter.get("hero_image") == attached_rel, "attached hero was not saved to frontmatter")
        assert_true(attached_frontmatter.get("social_image") == attached_rel, "attached hero did not mirror social image")

        attached_ready_status, _, attached_ready = json_request(
            base,
            f"/api/essays/{selected_id}/preflight",
            {"publish": {"title": selected["title"], "subtitle": "Smoke subtitle", "publication": "https://example.substack.com"}},
        )
        assert_true(attached_ready_status == 200 and attached_ready.get("ready") is False, f"unsaved metadata unexpectedly passed preflight: {attached_ready}")
        assert_true("persisted_publication" in {item.get("key") for item in attached_ready.get("blockers", [])},
                    "attached hero preflight did not require persisted publication")
        checks.append("Manual hero attach + saved-metadata gate passed")

        analyze_status, _, _ = json_request(base, f"/api/essays/{selected_id}/analyze", {})
        assert_true(analyze_status == 404, f"ai fill route still answers: {analyze_status}")

        hero_path = essays_dir / "_assets" / "substack" / "smoke-thumbnail.png"
        hero_path.parent.mkdir(parents=True, exist_ok=True)
        hero_path.write_bytes(b"fake image bytes")
        publish_payload = {
            "title": selected["title"],
            "subtitle": "Smoke subtitle",
            "summary": "Smoke summary for frontmatter transfer.",
            "publication": "https://example.substack.com",
            "audience": "everyone",
            "comment_permissions": "everyone",
            "email_subject": "Smoke email subject",
            "email_preview_text": "Smoke email preview text.",
            "hero_image": "_assets/substack/smoke-thumbnail.png",
            "social_image": "_assets/substack/smoke-thumbnail.png",
            "thumbnail_alt": "Smoke thumbnail",
            "thumbnail_prompt": "Smoke symbolic thumbnail prompt",
            "slug": "smoke-substack-draft",
            "seo_title": "Smoke SEO title",
            "seo_description": "Smoke SEO description",
            "social_title": "Smoke social title",
            "social_description": "Smoke social description",
            "tags": "smoke, automation",
        }
        unsaved_status, _, unsaved = json_request(
            base,
            f"/api/essays/{selected_id}/preflight",
            {"publish": publish_payload},
        )
        assert_true(
            unsaved_status == 200 and unsaved.get("ready") is False,
            f"form-only metadata unexpectedly satisfied send readiness: {unsaved}",
        )
        unsaved_fields = {item.get("field") for item in unsaved.get("blockers", [])}
        assert_true(
            {"seo_title", "seo_description", "social_title", "social_description", "thumbnail_prompt"}.issubset(unsaved_fields),
            f"preflight did not require saved metadata: {unsaved_fields}",
        )

        persisted_status, _, persisted = json_request(
            base,
            f"/api/essays/{selected_id}/send",
            {"updates": publish_payload, "publish": publish_payload},
        )
        assert_true(
            persisted_status == 200 and persisted.get("ok") is True,
            f"send did not first persist publishing frontmatter: {persisted}",
        )
        ready_status, _, ready = json_request(
            base,
            f"/api/essays/{selected_id}/preflight",
            {"publish": publish_payload},
        )
        assert_true(ready_status == 200 and ready.get("ready") is True, f"ready preflight failed: {ready}")
        assert_true(ready.get("metadata_complete") is True, f"metadata completeness failed: {ready}")

        # Stage 4a: blocker-severity body damage must STOP the send, not warn.
        # Same fully-valid publish payload as the ready case above — only the body
        # differs — so a False here can only come from the fidelity gate.
        # `![[...]]` is what Obsidian writes when you paste an image; the vendored
        # parser discards that whole block, prose included, and reports success.
        for label, damaged_body, expect_kind in (
            ("embed/block-swallow", "![[Pasted image 20260725.png]]\nProse glued to the image.\n", "block_swallowed"),
            ("local image", "![alt](_assets/local-only.png)\n", "local_image"),
            ("table", "| A | B |\n|---|---|\n| 1 | 2 |\n", "table"),
        ):
            damaged_status, _, damaged = json_request(
                base,
                f"/api/essays/{selected_id}/preflight",
                {"publish": publish_payload, "body": damaged_body},
            )
            assert_true(
                damaged_status == 200 and damaged.get("ready") is False,
                f"{label}: blocker-severity body damage did not stop the send: {damaged}",
            )
            blocker_text = " ".join(item.get("key", "") for item in damaged.get("blockers", []))
            assert_true(
                expect_kind in blocker_text,
                f"{label}: expected a {expect_kind} blocker, got {blocker_text!r}",
            )
            assert_true(
                damaged.get("body_fidelity", {}).get("by_severity", {}).get("blocker", 0) >= 1,
                f"{label}: fidelity summary lost the blocker count: {damaged.get('body_fidelity')}",
            )
            # The editor's one-line refusal must name the damage and the line,
            # not an internal finding key.
            body_blockers = [b for b in damaged.get("blockers", []) if b.get("key", "").startswith("body_")]
            assert_true(
                all(b.get("label") and "line " in b["label"] for b in body_blockers),
                f"{label}: body blockers missing a human label: {body_blockers}",
            )

        # And the control: clean prose with the same payload still sends.
        clean_status, _, clean = json_request(
            base,
            f"/api/essays/{selected_id}/preflight",
            {"publish": publish_payload, "body": "Just ordinary prose.\n\nA second paragraph.\n"},
        )
        assert_true(
            clean_status == 200 and clean.get("ready") is True,
            f"clean body should still preflight ready: {clean}",
        )
        checks.append("Blocker-severity body damage stops the send (embed/local image/table)")
        metadata_keys = {item.get("key") for item in ready.get("metadata_checks", [])}
        assert_true(
            {"summary", "tags", "seo_title", "seo_description", "social_title", "social_description", "thumbnail_prompt", "thumbnail_alt"}.issubset(metadata_keys),
            f"metadata checks changed: {metadata_keys}",
        )

        sends_before = len(connector_captures)
        send_status, _, send_result = json_request(
            base,
            f"/api/essays/{selected_id}/send",
            {"updates": {}, "publish": publish_payload},
        )
        assert_true(send_status == 200 and send_result.get("ok") is True, f"fake send failed: {send_result}")
        assert_true(send_result.get("preflight", {}).get("ready") is True, "send result did not include ready preflight")
        assert_true("error_kind" not in send_result, f"successful send carried an error_kind: {send_result}")
        assert_true(send_result.get("warnings") == ["Section was not applied: fake connector has no sections"],
                    f"transport warnings were dropped from a successful send: {send_result.get('warnings')}")
        assert_true(send_result.get("draft_id") == "connector-draft-1" and send_result.get("edit_url") == "https://example.substack.com/p/connector-draft-1",
                    f"draft identity was not lifted onto the send result: {send_result}")
        assert_true(send_result.get("transport") == "obsidian_connector", f"send did not use the connector: {send_result}")
        sent_markdown = selected_path.read_text(encoding="utf-8")
        assert_true('substack_draft_id: "connector-draft-1"' in sent_markdown,
                    "successful send did not persist the Substack draft ID")
        assert_true('substack_draft_url: "https://example.substack.com/p/connector-draft-1"' in sent_markdown,
                    "successful send did not persist the Substack draft URL")
        assert_true('status: "Live"' in sent_markdown,
                    "successful send did not persist Live status")
        assert_true(len(connector_captures) == sends_before + 1, f"connector saw {len(connector_captures) - sends_before} sends, expected 1")
        capture = connector_captures[-1]
        assert_true(set(capture) == {"file", "markdown", "stored_draft_id"},
                    f"airdate sent the connector more than the draft file: {sorted(capture)}")
        assert_true(Path(capture["file"]).resolve().parent == (ROOT / "drafts").resolve(), f"draft file outside drafts/: {capture['file']}")
        markdown = capture.get("markdown", "")
        assert_true(f'title: "{selected["title"]}"' in markdown, "draft markdown missing title")
        assert_true('subtitle: "Smoke subtitle"' in markdown, "draft markdown missing subtitle")
        assert_true('summary: "Smoke summary for frontmatter transfer."' in markdown, "draft markdown missing summary")
        assert_true('publication: "https://example.substack.com"' in markdown, "draft markdown missing publication")
        assert_true(f'hero: "{hero_path.resolve()}"' in markdown, "draft markdown missing hero path")
        assert_true('seo_title: "Smoke SEO title"' in markdown, "draft markdown missing SEO title")
        assert_true('seo_description: "Smoke SEO description"' in markdown, "draft markdown missing SEO description")
        assert_true('social_title: "Smoke social title"' in markdown, "draft markdown missing social title")
        assert_true('social_description: "Smoke social description"' in markdown, "draft markdown missing social description")
        assert_true('thumbnail_prompt: "Smoke symbolic thumbnail prompt"' in markdown, "draft markdown missing thumbnail prompt")
        assert_true('thumbnail_alt: "Smoke thumbnail"' in markdown, "draft markdown missing thumbnail alt")
        assert_true('email_subject: "Smoke email subject"' in markdown, "draft markdown missing email subject")
        assert_true('email_preview_text: "Smoke email preview text."' in markdown, "draft markdown missing email preview text")

        second_send_status, _, second_send = json_request(
            base,
            f"/api/essays/{selected_id}/send",
            {"updates": {}, "publish": publish_payload},
        )
        assert_true(second_send_status == 200 and second_send.get("ok") is True,
                    f"second fake send failed: {second_send}")
        second_capture = connector_captures[-1]
        assert_true(second_capture.get("stored_draft_id") == "connector-draft-1",
                    "second send did not carry stored Substack draft identity")
        transport_result = json.loads(second_send.get("stdout") or "{}")
        assert_true(transport_result.get("updated") is True,
                    "second send did not report an existing-draft update")
        checks.append("Substack preflight + send through the paired connector passed")

        # Send failures tell the truth (AD-008). Every failed send carries a
        # typed error_kind set by the layer that knows it, and only `auth`
        # licenses the editor to open the Obsidian sign-in and retry.
        blocked_send_status, _, blocked_send = json_request(
            base,
            f"/api/essays/{source['id']}/send",
            {"updates": {}, "publish": {"title": source["title"], "subtitle": "Source subtitle", "publication": "https://example.substack.com"}},
        )
        assert_true(blocked_send_status == 200 and blocked_send.get("ok") is False,
                    f"source-note send was not refused: {blocked_send}")
        assert_true(blocked_send.get("error_kind") == "blocked",
                    f"refused send was not classified as blocked: {blocked_send.get('error_kind')!r}")
        assert_true("source_role" in {item.get("key") for item in blocked_send.get("preflight", {}).get("blockers", [])},
                    f"blocked send lost its blocker list: {blocked_send.get('preflight')}")
        assert_true("substack_session" in json.dumps(blocked_send),
                    "fixture drift: the embedded preflight no longer carries the substack_session check this guards against")
        assert_true(blocked_send.get("warnings") == [], f"blocked send invented transport warnings: {blocked_send}")

        checks.append("Send failure truth: blocked at readiness")

        # AD-014: a failed send that saved edits must hand back the file state
        # it created, and a clean send carrying that state must not 409. The
        # first send is refused by the fidelity gate (a table), but its body
        # save still rewrote the note; the stale pre-send state proves the
        # trap, the returned state proves the fix.
        pre_status, _, pre_detail = json_request(base, f"/api/essays/{selected_id}")
        assert_true(pre_status == 200 and pre_detail.get("mtime"), f"detail before the blocked send failed: {pre_detail}")
        stale_state = {"expected_mtime": pre_detail["mtime"], "expected_content_hash": pre_detail["content_hash"]}
        table_body = "| A | B |\n|---|---|\n| 1 | 2 |\n"
        blocked_save_status, _, blocked_save = json_request(
            base,
            f"/api/essays/{selected_id}/send",
            {"updates": {}, "body": table_body, "publish": publish_payload, **stale_state},
        )
        assert_true(blocked_save_status == 200 and blocked_save.get("ok") is False and blocked_save.get("error_kind") == "blocked",
                    f"table body did not block the send: {blocked_save}")
        blocked_state = blocked_save.get("saved_state") or {}
        assert_true(blocked_state.get("mtime") and blocked_state.get("content_hash") and blocked_state.get("old_id") and blocked_state.get("new_id"),
                    f"blocked send that saved the body did not return its file state: {blocked_save}")
        assert_true(table_body.strip() in selected_path.read_text(encoding="utf-8"),
                    "blocked send did not persist the body it saved")
        stale_status, _, stale_send = json_request(
            base,
            f"/api/essays/{selected_id}/send",
            {"updates": {}, "body": "Just ordinary prose.\n", "publish": publish_payload, **stale_state},
        )
        assert_true(stale_status == 409 and stale_send.get("reason") == "file_changed",
                    f"pre-save state should still be refused as a conflict: {stale_status} {stale_send}")
        clean_status, _, clean_send = json_request(
            base,
            f"/api/essays/{selected_id}/send",
            {
                "updates": {}, "body": "Just ordinary prose.\n", "publish": publish_payload,
                "expected_mtime": blocked_state["mtime"], "expected_content_hash": blocked_state["content_hash"],
            },
        )
        assert_true(clean_status == 200 and clean_send.get("ok") is True,
                    f"clean send after a blocked send 409d or failed: {clean_status} {clean_send}")
        # And a transport failure that saved edits carries the same state.
        connector_mode["draft"] = "auth"
        try:
            auth_saved_status, _, auth_saved = json_request(
                base,
                f"/api/essays/{selected_id}/send",
                {
                    "updates": {"summary": "Smoke summary after a refused transport."}, "publish": publish_payload,
                    "expected_mtime": clean_send["mtime"], "expected_content_hash": clean_send["content_hash"],
                },
            )
        finally:
            connector_mode["draft"] = "ok"
        assert_true(auth_saved_status == 200 and auth_saved.get("error_kind") == "auth",
                    f"transport-failing send with edits was not refused as auth: {auth_saved}")
        auth_state = auth_saved.get("saved_state") or {}
        assert_true(auth_state.get("mtime") and auth_state.get("content_hash"),
                    f"transport failure that saved edits did not return its file state: {auth_saved}")
        recover_status, _, recover_send = json_request(
            base,
            f"/api/essays/{selected_id}/send",
            {
                "updates": {}, "publish": publish_payload,
                "expected_mtime": auth_state["mtime"], "expected_content_hash": auth_state["content_hash"],
            },
        )
        assert_true(recover_status == 200 and recover_send.get("ok") is True,
                    f"send after a transport failure 409d or failed: {recover_status} {recover_send}")
        checks.append("Failed send hands back its saved file state; the next send does not 409 (AD-014)")

        # Connector-side failure classes reach the editor untouched, and a
        # failed send never rewrites the note's remote identity.
        for connector_case, expect_kind, expect_returncode, expect_fragment in (
            ("auth", "auth", 1, "401"),
            ("timeout", "timeout", None, "Check Substack for the draft"),
            ("no_session", "auth", None, "Connect Substack in Obsidian"),
        ):
            connector_mode["draft"] = connector_case
            try:
                case_status, _, case_send = json_request(
                    base,
                    f"/api/essays/{selected_id}/send",
                    {"updates": {}, "publish": publish_payload},
                )
            finally:
                connector_mode["draft"] = "ok"
            assert_true(case_status == 200 and case_send.get("ok") is False,
                        f"connector {connector_case}: failed send reported success: {case_send}")
            assert_true(case_send.get("error_kind") == expect_kind,
                        f"connector {connector_case}: expected error_kind {expect_kind!r}, got {case_send.get('error_kind')!r}")
            assert_true(case_send.get("returncode", None) == expect_returncode,
                        f"connector {connector_case}: expected returncode {expect_returncode!r}, got {case_send.get('returncode')!r}")
            assert_true(expect_fragment in case_send.get("message", ""),
                        f"connector {connector_case}: message lost its sentence: {case_send.get('message')!r}")
            assert_true(case_send.get("transport") == "obsidian_connector",
                        f"connector {connector_case}: transport label missing: {case_send}")
            assert_true('substack_draft_id: "connector-draft-1"' in selected_path.read_text(encoding="utf-8"),
                        f"connector {connector_case}: failed send rewrote the remote draft identity")
        checks.append("Send failure truth: connector auth/timeout/no-session reach the editor typed")
        check_connector_classification(checks)

        # --- Status lifecycle: set-status → ready-for-air → unschedule → archive ---
        (essays_dir / "Smoke" / "Lifecycle Fixture.md").write_text(
            """---
title: "Lifecycle Smoke Fixture"
summary: "A fixture for the status lifecycle."
category: "Smoke"
status: developing
totem: circle
---

Lifecycle fixture body — must survive every move byte-for-byte.
""",
            encoding="utf-8",
        )
        json_request(base, "/api/app/refresh-essays", {})
        _, _, listing = json_request(base, "/api/essays?scope=all")
        life = next((e for e in listing.get("essays", []) if e.get("title") == "Lifecycle Smoke Fixture"), None)
        assert_true(life is not None, "lifecycle fixture was not indexed")
        life_id = life["id"]

        st, _, res = json_request(base, f"/api/essays/{life_id}/set-status", {"status": "Writers Likey"})
        assert_true(st == 200 and res.get("status") == "Writers Likey", f"set-status → Writers Likey failed: {res}")
        life_id = str(res.get("new_id") or life_id)

        st, _, res = json_request(base, f"/api/essays/{life_id}/ready-for-air", {"scheduled_at": "2026-08-03"})
        assert_true(st == 200 and res.get("status") == "Ready for Air", f"ready-for-air failed: {res}")
        life_id = str(res.get("new_id") or life_id)
        _, _, detail = json_request(base, f"/api/essays/{life_id}")
        assert_true(detail.get("frontmatter", {}).get("scheduled_at") == "2026-08-03", f"scheduled_at not written: {detail.get('frontmatter')}")

        st, _, res = json_request(base, f"/api/essays/{life_id}/unschedule", {})
        assert_true(st == 200 and res.get("status") == "Writers Likey", f"unschedule failed: {res}")
        life_id = str(res.get("new_id") or life_id)
        _, _, detail = json_request(base, f"/api/essays/{life_id}")
        assert_true(not detail.get("frontmatter", {}).get("scheduled_at"), f"unschedule left scheduled_at: {detail.get('frontmatter')}")
        assert_true("Lifecycle fixture body" in (detail.get("body") or ""), "lifecycle body was lost across edits")

        st, _, res = json_request(base, f"/api/essays/{life_id}/archive", {})
        assert_true(st == 200 and res.get("status") == "Archived", f"archive failed: {res}")
        assert_true(res.get("moved") is True and "Archive/" in res.get("relative_path", ""),
                    f"archive did not move the file into Archive/: {res}")
        checks.append("Status lifecycle (set-status → ready-for-air → unschedule → archive + folder move) passed")

        # --- Durable uid: stamped once, stable, resolvable by either token ---
        _, _, listing = json_request(base, "/api/essays?scope=all")
        stamped = next((e for e in listing.get("essays", []) if e.get("title") == "Short Smoke Essay"), None)
        assert_true(stamped is not None, "uid fixture essay missing from index")
        uid = stamped.get("uid") or ""
        legacy = stamped.get("legacy_path_id") or ""
        assert_true(len(uid) == 32 and all(c in "0123456789abcdef" for c in uid), f"uid is not a 32-hex token: {uid!r}")
        assert_true(stamped.get("id") == uid, f"canonical id should be the uid: {stamped.get('id')!r} vs {uid!r}")
        assert_true(legacy and legacy != uid, f"legacy_path_id missing/equal: {legacy!r}")

        # Both tokens resolve to the same file (the alias is what keeps old
        # references working after the id flips).
        by_uid_status, _, by_uid = json_request(base, f"/api/essays/{uid}")
        by_hash_status, _, by_hash = json_request(base, f"/api/essays/{legacy}")
        assert_true(by_uid_status == 200 and by_hash_status == 200, "uid/legacy-hash did not both resolve")
        assert_true(by_uid.get("relative_path") == by_hash.get("relative_path"),
                    f"tokens resolved to different files: {by_uid.get('relative_path')} vs {by_hash.get('relative_path')}")
        assert_true(by_hash.get("id") == uid, "detail via legacy hash should report the canonical uid")

        # Stamping is write-once: another write must not re-mint.
        json_request(base, f"/api/essays/{uid}/save", {"updates": {"summary": "uid stability probe"}})
        _, _, listing2 = json_request(base, "/api/essays?scope=all")
        again = next((e for e in listing2.get("essays", []) if e.get("title") == "Short Smoke Essay"), None)
        assert_true(again and again.get("uid") == uid, f"uid changed across writes: {uid} -> {again and again.get('uid')}")
        checks.append("Durable uid: stamped once, stable, resolvable by uid + legacy hash")

        # --- Regression: a FOREIGN `uid` frontmatter key must be ignored ---
        # `uid` is a common Obsidian key (Zettelkasten/UID plugins, Templater).
        # Adopting it as the essay id made those essays permanently unroutable
        # (a value with "/" breaks route parsing; digit-only values coerce to int).
        (essays_dir / "Smoke" / "Foreign Uid.md").write_text(
            """---
title: "Foreign Uid Fixture"
category: "Smoke"
status: "Writers Room"
totem: circle
uid: 2026/01/07-1200
---

A note carrying someone else's uid convention.
""",
            encoding="utf-8",
        )
        json_request(base, "/api/app/refresh-essays", {})
        _, _, listing = json_request(base, "/api/essays?scope=all")
        foreign = next((e for e in listing.get("essays", []) if e.get("title") == "Foreign Uid Fixture"), None)
        assert_true(foreign is not None, "foreign-uid fixture not indexed")
        assert_true(foreign.get("uid") == "", f"foreign uid must not be adopted: {foreign.get('uid')!r}")
        assert_true(foreign.get("id") == foreign.get("legacy_path_id"),
                    f"foreign-uid essay must keep its path-hash id: {foreign.get('id')!r}")
        routable_status, _, _ = json_request(base, f"/api/essays/{foreign['id']}")
        assert_true(routable_status == 200, "foreign-uid essay must stay routable")
        checks.append("Foreign `uid` frontmatter is ignored; essay stays routable")

        # --- Regression: duplicate id tie-break is OLDEST-wins, not scan order ---
        # An Obsidian copy is named "Foo 1.md"/"Foo copy.md", and space/dot
        # ordering sorts the COPY first — scan-order first-wins handed the
        # identity (and all later writes) to the copy.
        shared = "abcdef0123456789abcdef0123456789"
        original = essays_dir / "Smoke" / "Twin.md"
        copy = essays_dir / "Smoke" / "Twin 1.md"   # sorts BEFORE "Twin.md"
        for target in (original, copy):
            target.write_text(
                f"""---
title: "{target.stem}"
category: "Smoke"
status: "Writers Room"
totem: circle
airdate_uid: "{shared}"
---

Body of {target.stem}.
""",
                encoding="utf-8",
            )
        os.utime(original, (1_600_000_000, 1_600_000_000))  # original is older
        os.utime(copy, (1_700_000_000, 1_700_000_000))      # copy is newer
        json_request(base, "/api/app/refresh-essays", {})
        _, _, listing = json_request(base, "/api/essays?scope=all")
        twins = {e["title"]: e for e in listing.get("essays", []) if e.get("title") in ("Twin", "Twin 1")}
        assert_true(len(twins) == 2, f"twin fixtures not both indexed: {list(twins)}")
        assert_true(twins["Twin"].get("id") == shared,
                    f"the OLDER file must keep the shared id, got {twins['Twin'].get('id')!r}")
        assert_true(twins["Twin 1"].get("uid") == "" and twins["Twin 1"]["id"] == twins["Twin 1"]["legacy_path_id"],
                    "the newer copy must be demoted to its path id")
        _, _, shared_detail = json_request(base, f"/api/essays/{shared}")
        assert_true(shared_detail.get("relative_path", "").endswith("Twin.md"),
                    f"shared id must resolve to the original, got {shared_detail.get('relative_path')!r}")
        checks.append("Duplicate id tie-break: oldest file keeps the id, copy demoted")

        # Parse-failure surfacing on /api/app/status — the demoted duplicate above
        # is the one expected entry (a silent demotion would be the real bug).
        _, _, app_status_final = json_request(base, "/api/app/status")
        assert_true("parse_failure_files" in app_status_final, "parse-failure surface missing")
        failure_files = app_status_final.get("parse_failure_files") or []
        assert_true(app_status_final.get("parse_failures") == 1 and any("Twin 1" in f for f in failure_files),
                    f"expected the demoted duplicate to be surfaced, got {failure_files}")
        checks.append("Parse-failure surface reports the demoted duplicate")

        print("workflow smoke passed")
        for check in checks:
            print(f"- {check}")
        return 0
    except Exception as exc:  # noqa: BLE001 - terminal command should show context
        print(f"workflow smoke failed: {exc}", file=sys.stderr)
        if log_path.exists():
            print("\nserver log tail:", file=sys.stderr)
            print("\n".join(log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:]), file=sys.stderr)
        return 1
    finally:
        connector_server.shutdown()
        connector_server.server_close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        shutil.rmtree(temp_dir, ignore_errors=True)
        for leftover in {p for p in drafts_root.rglob("*") if p.is_file()} - drafts_before:
            leftover.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
