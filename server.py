#!/usr/bin/env python3
from __future__ import annotations

import functools
import hashlib
import hmac
import ipaddress
import json
import os
import re
import sys
import tempfile
import uuid
import threading
import time
import urllib.error
import urllib.request
from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import airdate_config  # local, stdlib-only settings loader
import obsidian_markdown  # local, stdlib-only, shared with substack_draft.py


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

TRUE_VALUES = {"1", "true", "yes", "on"}

# The red pen: twenty lines for script pages past three months, shipped here
# rather than in config so an install always has them. The writer's own lines
# (config red_pen.lines) are added after these; red_pen.enabled turns all of it
# off. A line that names the coffee ring or the crayon is only ever written on
# the oldest paper, the one that has them.
RED_PEN_JABS = (
    "it's not the odyssey. publish it or throw it away.",
    "this draft is old enough to have opinions about you.",
    "the coffee ring is load bearing now.",
    "even tolstoy hit send eventually.",
    "you've rewritten the first line more than you've read the last one.",
    "perfect is a rumor. monday is real.",
    "this page has seen two moons. it would like to see a reader.",
    "it's an essay, not a will. nobody dies if it's wrong.",
    "somebody needs this one. they can't read your desk.",
    "dust is not an editing technique.",
    "you be a writer. writers publish.",
    "the umbrella is right there. rain check it or air it.",
    "this script has a better attendance record than most plans.",
    "nobody ever said, i wish they'd sat on that essay longer.",
    "still here. still good. still not published.",
    "the crayon is all that's left. it still writes.",
    "fear of the send button is not a genre.",
    "one more polish and it becomes a different essay.",
    "you already said the hard part. the rest is a button.",
    "direction, not distance. move it one slot.",
)


def env_first(*names: str, default: Any = "") -> Any:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip() != "":
            return value
    return default


def env_flag_first(*names: str, default: bool = False) -> bool:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and raw.strip() != "":
            return raw.strip().lower() in TRUE_VALUES
    return default


DATA_DIR = Path(env_first("AIR_DATE_DATA_DIR", default=ROOT / ".airdate-data")).expanduser().resolve()
# Drafts always live in the app folder: the paired Obsidian connector only
# runs substack_draft.py on files under <airdate>/drafts/.
DRAFTS = ROOT / "drafts"
SECRET_DIR = DATA_DIR / "secrets"
STATE_DIR = DATA_DIR / "state"

# Shared static-asset MIME map (used by the /static/ and /vault-asset/ handlers).
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
}
VAULT_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
UPLOADS = DRAFTS / "assets"
# When each essay first showed up in airdate, keyed by essay id. The five ages
# of paper on a script card count from here, not from the file's mtime, which
# moves every time the writer saves. Runtime only: nothing about arrival is
# ever written into the vault.
ARRIVALS_LOG = STATE_DIR / "arrivals.json"
# When the writer put the star on each essay's post-it, keyed by essay id. The
# writers likey stamp carries this date. Runtime only, like arrivals: the star
# is a status in the note, but its date is airdate's own bookkeeping.
STARRED_LOG = STATE_DIR / "starred.json"
# Written by the connector's "Pair with airdate" command: {port, token}. The
# token is the only capability airdate holds; the Substack session itself
# stays in Obsidian's secret storage.
CONNECTOR_PAIRING_FILE = SECRET_DIR / "connector.json"

PLACEHOLDER_PUBLICATION_MARKERS = {"yourname", "yourpublication"}


def is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().split(":", 1)[0].strip("[]").lower()
    if normalized in {"", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))
IS_LOOPBACK = is_loopback_host(HOST)
AUTH_USER = str(env_first("AIR_DATE_AUTH_USER", default="airdate")).strip() or "airdate"
AUTH_PASSWORD = str(env_first("AIR_DATE_AUTH_PASSWORD", default="")).strip()
AUTH_REQUIRED = env_flag_first("AIR_DATE_AUTH_REQUIRED", default=bool(AUTH_PASSWORD) or not IS_LOOPBACK)
EXPOSE_LOCAL_PATHS = env_flag_first("AIR_DATE_EXPOSE_LOCAL_PATHS", default=not AUTH_REQUIRED and IS_LOOPBACK)
MAX_REQUEST_BYTES = int(env_first("AIR_DATE_MAX_REQUEST_BYTES", default=str(16 * 1024 * 1024)))
MAX_UPLOAD_BYTES = int(env_first("AIR_DATE_MAX_UPLOAD_BYTES", default=str(10 * 1024 * 1024)))


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def atomic_write_text(path: Path, value: str) -> None:
    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(value)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, value: bytes) -> None:
    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(value)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def public_path(path: Path) -> str:
    if EXPOSE_LOCAL_PATHS:
        return str(path)
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


LONG_SOURCE_WORD_THRESHOLD = int(env_first("AIR_DATE_LONG_SOURCE_WORD_THRESHOLD", default="10000"))
STATUS_SET = {"Writers Room", "Writers Likey", "Ready for Air", "Live", "Archived"}
SOURCE_ROLE_SET = {"source", "draft", "standalone"}


# --- Settings -------------------------------------------------------------
# Everything about the writer (vault, publication, taxonomy, cadence) comes
# from <data dir>/config.json through apply_config(). The module-level names
# below are reassigned there, so functions always read the current settings.

CONFIG: dict[str, Any] = {}
CONFIG_FILE_EXISTS = False
CONFIG_LOAD_ERROR = ""
CONFIG_ERRORS: list[str] = []
VAULT_CHECK: dict[str, Any] = {}
SETUP_REQUIRED = True
CONFIG_FINGERPRINT = ""
VAULT_DIR = DATA_DIR / "no-vault-configured"
ESSAYS_FOLDER = "Essays"
OBSIDIAN_ESSAYS_DIR = VAULT_DIR / ESSAYS_FOLDER
OBSIDIAN_VAULT_NAME = ""
OBSIDIAN_SUBSTACK_ASSETS_DIR = OBSIDIAN_ESSAYS_DIR / "_assets" / "substack"
SUBSTACK_PUBLICATION = ""
PUBLICATION_NAME = ""
CONNECTOR_PORT = 17777
TOTEMS_ENABLED = False
TOTEM_ITEMS: dict[str, dict[str, Any]] = {}
TOTEM_DEFAULT = ""
CATEGORY_MODE = "folders"
CATEGORY_KEYWORDS: dict[str, dict[str, int]] = {}
HIDDEN_FILENAME_PREFIXES: list[str] = []
HIDDEN_TITLE_CONTAINS: list[str] = []
HIDDEN_TOPLEVEL_TITLE_CONTAINS: list[str] = []
THUMBNAIL_STYLE_PROMPT = airdate_config.DEFAULT_STYLE_PROMPT
PUBLISH_DAY: str | None = None
# The board: what a new essay's note looks like until it gets its own.
BOARD_DEFAULT_PAD = "sticky"
BOARD_DEFAULT_COLOR = "canary"
BOARD_WEEKS_SHOWN = 3
# The red pen on old script pages, and whether a star gives an essay fresh paper.
RED_PEN_ENABLED = True
RED_PEN_LINES: list[str] = []
PAPER_FRESH_ON_PROMOTION = False
TAG_PRESETS: list[dict[str, Any]] = []
LINKS: list[dict[str, str]] = []


def apply_config(config: dict[str, Any], file_exists: bool, load_error: str = "") -> None:
    """Install a config (already merged with defaults) as the running settings."""
    global BOARD_DEFAULT_PAD, BOARD_DEFAULT_COLOR, BOARD_WEEKS_SHOWN
    global RED_PEN_ENABLED, RED_PEN_LINES, PAPER_FRESH_ON_PROMOTION
    global CONFIG, CONFIG_FILE_EXISTS, CONFIG_LOAD_ERROR, CONFIG_ERRORS, VAULT_CHECK, SETUP_REQUIRED
    global CONFIG_FINGERPRINT, VAULT_DIR, ESSAYS_FOLDER, OBSIDIAN_ESSAYS_DIR, OBSIDIAN_VAULT_NAME
    global OBSIDIAN_SUBSTACK_ASSETS_DIR, SUBSTACK_PUBLICATION, PUBLICATION_NAME, CONNECTOR_PORT
    global TOTEMS_ENABLED, TOTEM_ITEMS, TOTEM_DEFAULT, CATEGORY_MODE, CATEGORY_KEYWORDS
    global HIDDEN_FILENAME_PREFIXES, HIDDEN_TITLE_CONTAINS, HIDDEN_TOPLEVEL_TITLE_CONTAINS
    global THUMBNAIL_STYLE_PROMPT, PUBLISH_DAY, TAG_PRESETS, LINKS

    effective = airdate_config.apply_env_overrides(config)
    CONFIG = effective
    CONFIG_FILE_EXISTS = file_exists
    CONFIG_LOAD_ERROR = load_error
    CONFIG_ERRORS = airdate_config.validate_config(effective)
    VAULT_CHECK = airdate_config.check_vault(effective)

    vault = effective["vault"]
    raw_vault = str(vault.get("path") or "").strip()
    VAULT_DIR = Path(raw_vault).expanduser() if raw_vault else DATA_DIR / "no-vault-configured"
    ESSAYS_FOLDER = str(vault.get("essays_folder") or "Essays").strip().strip("/") or "Essays"
    OBSIDIAN_ESSAYS_DIR = VAULT_DIR / ESSAYS_FOLDER
    OBSIDIAN_VAULT_NAME = str(vault.get("name") or "").strip() or VAULT_DIR.name
    OBSIDIAN_SUBSTACK_ASSETS_DIR = OBSIDIAN_ESSAYS_DIR / "_assets" / "substack"
    hidden = vault.get("hidden") or {}
    HIDDEN_FILENAME_PREFIXES = [str(v) for v in hidden.get("filename_prefixes") or [] if str(v)]
    HIDDEN_TITLE_CONTAINS = [str(v).lower() for v in hidden.get("title_contains") or [] if str(v)]
    HIDDEN_TOPLEVEL_TITLE_CONTAINS = [str(v).lower() for v in hidden.get("toplevel_title_contains") or [] if str(v)]

    SUBSTACK_PUBLICATION = airdate_config.publication_url(str(effective["substack"].get("publication") or ""))
    PUBLICATION_NAME = str(effective["substack"].get("publication_name") or "").strip()
    try:
        CONNECTOR_PORT = int(effective["connector"].get("port") or 17777)
    except (TypeError, ValueError):
        CONNECTOR_PORT = 17777

    totems = effective["totems"]
    items = [item for item in totems.get("items") or [] if isinstance(item, dict) and item.get("key")]
    TOTEM_ITEMS = {str(item["key"]): item for item in items}
    TOTEMS_ENABLED = bool(totems.get("enabled")) and bool(TOTEM_ITEMS)
    default_key = totems.get("default")
    TOTEM_DEFAULT = str(default_key) if default_key in TOTEM_ITEMS else ""

    categories = effective["categories"]
    CATEGORY_MODE = categories.get("mode") if categories.get("mode") in airdate_config.CATEGORY_MODES else "folders"
    CATEGORY_KEYWORDS = {
        str(item["name"]).strip(): {str(k).lower(): int(v) for k, v in (item.get("keywords") or {}).items()}
        for item in categories.get("items") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }

    THUMBNAIL_STYLE_PROMPT = str(effective["thumbnail"].get("style_prompt") or "").strip() or airdate_config.DEFAULT_STYLE_PROMPT
    day = effective["calendar"].get("publish_day")
    PUBLISH_DAY = day if day in airdate_config.WEEKDAYS else None

    board = effective.get("board") or {}
    BOARD_DEFAULT_PAD = str(board.get("default_pad") or "").strip() or "sticky"
    BOARD_DEFAULT_COLOR = str(board.get("default_color") or "").strip() or "canary"
    try:
        BOARD_WEEKS_SHOWN = max(1, min(12, int(board.get("weeks_shown") or 3)))
    except (TypeError, ValueError):
        BOARD_WEEKS_SHOWN = 3
    red_pen = effective.get("red_pen") or {}
    RED_PEN_ENABLED = red_pen.get("enabled") is not False
    RED_PEN_LINES = [
        line.strip() for line in red_pen.get("lines") or []
        if isinstance(line, str) and line.strip()
    ]
    paper = effective.get("paper") or {}
    PAPER_FRESH_ON_PROMOTION = paper.get("fresh_on_promotion") is True
    TAG_PRESETS = [p for p in effective.get("tag_presets") or [] if isinstance(p, dict) and p.get("name")]
    LINKS = [l for l in effective.get("links") or [] if isinstance(l, dict) and l.get("label") and l.get("url")]

    SETUP_REQUIRED = bool(
        not file_exists and not os.environ.get("OBSIDIAN_ESSAYS_DIR") and not os.environ.get("AIRDATE_VAULT_DIR")
    ) or bool(load_error) or bool(CONFIG_ERRORS) or not VAULT_CHECK.get("vault_ok") or not VAULT_CHECK.get("essays_ok")
    CONFIG_FINGERPRINT = hashlib.sha1(json.dumps(effective, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def load_and_apply_config() -> None:
    config, exists, error = airdate_config.load_config(DATA_DIR)
    apply_config(config, exists, error)


load_and_apply_config()


def connector_pairing() -> dict[str, Any]:
    """The pairing the connector wrote into airdate's secrets folder:
    {port, token}. Never the Substack session itself."""
    try:
        payload = json.loads(CONNECTOR_PAIRING_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def connector_token() -> str:
    return str(connector_pairing().get("token") or "").strip()


def connector_url() -> str:
    port = connector_pairing().get("port")
    if not isinstance(port, int) or isinstance(port, bool):
        port = CONNECTOR_PORT
    return f"http://127.0.0.1:{port}"


# Typed failure classes on a send result. Each is set by the one layer that
# knows it: `blocked` by the readiness gate here, `auth` by the transport's
# exception classifier (substack_draft.py) or the connector's no-session
# refusal, `timeout` where a kill or socket timeout is detected, `transport`
# for everything else. The editor reads this field; it never pattern-matches
# the result text.
SEND_ERROR_KINDS = ("blocked", "auth", "timeout", "transport")


def _connector_error(exc: BaseException) -> dict[str, Any]:
    """Classify a failed connector round-trip. A socket timeout is the one
    case this layer can name on its own; everything else is transport."""
    reason = getattr(exc, "reason", None)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in str(exc).lower():
        return {
            "ok": False,
            "connected": False,
            "error_kind": "timeout",
            "error": f"Obsidian connector did not answer in time: {exc}",
            "message": "The Obsidian connector did not answer in time. Check Substack for the draft before sending again.",
        }
    return {"ok": False, "connected": False, "error_kind": "transport", "error": f"Obsidian connector unavailable: {exc}"}


def connector_request(path: str, payload: dict[str, Any] | None = None, timeout: int = 5) -> dict[str, Any]:
    token = connector_token()
    if not token:
        return {
            "ok": False,
            "connected": False,
            "paired": False,
            "error_kind": "transport",
            "error": "The Obsidian connector is not paired. In Obsidian, run \"Pair with airdate\".",
        }
    request = urllib.request.Request(
        f"{connector_url()}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"X-Airdate-Token": token, "Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
            return decoded if isinstance(decoded, dict) else {"ok": False, "error_kind": "transport", "error": "Invalid connector response."}
    except urllib.error.HTTPError as exc:
        # The connector answers a refusal (no session, bad draft path) with a
        # JSON body on a 4xx. Keep that body: it carries the typed error_kind
        # and the sentence the editor should show.
        try:
            decoded = json.loads(exc.read().decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            decoded = None
        if isinstance(decoded, dict):
            decoded["ok"] = False
            return decoded
        return _connector_error(exc)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return _connector_error(exc)


def connector_status() -> dict[str, Any]:
    return connector_request("/status")


# A complete Substack draft is a property of the canonical Obsidian note, not
# of a temporarily filled browser form. These keys must therefore exist on disk
# before airdate allows a send. `section` is intentionally absent: publications
# without sections have no meaningful value to persist there.
PERSISTED_DRAFT_FIELDS = (
    "title",
    "subtitle",
    "summary",
    "publication",
    "audience",
    "comment_permissions",
    "email_subject",
    "email_preview_text",
    "hero_image",
    "thumbnail_alt",
    "seo_title",
    "seo_description",
    "social_title",
    "social_description",
    "thumbnail_prompt",
)


EDITOR_ALLOWED_FIELDS = {
    "publication",
    "title",
    "subtitle",
    "summary",
    "source_note",
    "post_type",
    "hero_image",
    "hero",
    "slug",
    "tags",
    "section",
    "audience",
    "publish_on_web",
    "send_email",
    "free_preview",
    "email_subject",
    "email_preview_text",
    "test_email_recipients",
    "comment_permissions",
    "scheduled_at",
    "free_unlock_at",
    "canonical_url",
    "seo_title",
    "seo_description",
    "social_title",
    "social_description",
    "social_image",
    "notes",
    "thumbnail_prompt",
    "thumbnail_alt",
    "status",
    "totem",
    "source_role",
    "draft_of",
    "category",
    "published_date",
    "substack_url",
    "substack_draft_id",
    "substack_draft_url",
    # The note this essay goes up on the board as, chosen in the editor's
    # right rail. Validated in sanitize_updates: only what the board draws.
    "note_pad",
    "note_color",
    # Rainy day (slice 6): stamped by park_essay, read back by back_to_room.
    # Not editor-settable through the form, but written through the same
    # save_essay_updates path set_essay_status already uses for every other
    # machine-stamped field (published_date, substack_url, ...).
    "previous_status",
    "archived_at",
}

NOTE_PADS = ("sticky", "paper", "index")
NOTE_COLORS = ("canary", "blue", "orange", "pink", "green")

ORDERED_FRONTMATTER_KEYS = [
    "title",
    "subtitle",
    "summary",
    "status",
    "totem",
    "category",
    "published_date",
    "substack_url",
    "substack_draft_id",
    "substack_draft_url",
    "source_role",
    "draft_of",
    "publication",
    "slug",
    "section",
    "tags",
    "post_type",
    "audience",
    "publish_on_web",
    "send_email",
    "free_preview",
    "email_subject",
    "email_preview_text",
    "test_email_recipients",
    "comment_permissions",
    "scheduled_at",
    "free_unlock_at",
    "canonical_url",
    "seo_title",
    "seo_description",
    "social_title",
    "social_description",
    "social_image",
    "hero",
    "hero_image",
    "source_note",
    "thumbnail_prompt",
    "thumbnail_alt",
    "notes",
    "note_pad",
    "note_color",
    "previous_status",
    "archived_at",
    # Machine-only durable identity — stamped once, never client-settable
    # (deliberately absent from EDITOR_ALLOWED_FIELDS). Last so it stays out of
    # the human-facing top of the Properties editor. Namespaced to avoid
    # colliding with Obsidian's own widely-used `uid` key.
    "airdate_uid",
]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "substack-draft"


def yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(str(item), ensure_ascii=False) for item in value if str(item).strip()) + "]"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def clean_tags(raw: str | list[str]) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        values = re.split(r",|\n", raw)
    return [tag.strip() for tag in values if tag.strip()]


def normalize_body(value: str) -> str:
    body = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return body.lstrip("\n")


def compose_markdown(frontmatter: dict[str, Any], body: str) -> str:
    normalized = normalize_body(body)
    return dump_frontmatter(frontmatter) + "\n\n" + normalized.rstrip() + "\n"


def build_markdown(payload: dict[str, Any]) -> str:
    title = payload.get("title", "").strip()
    slug = payload.get("slug", "").strip() or slugify(title)
    frontmatter = {
        "title": title,
        "subtitle": payload.get("subtitle", "").strip(),
        "summary": payload.get("summary", "").strip(),
        "publication": payload.get("publication", "").strip(),
        "slug": slug,
        "hero": payload.get("hero", "").strip(),
        "tags": clean_tags(payload.get("tags", "")),
        "section": payload.get("section", "").strip(),
        "audience": payload.get("audience", "everyone").strip(),
        "post_type": payload.get("postType", "text").strip(),
        "comment_permissions": payload.get("commentPermissions", "everyone").strip(),
        "free_preview": bool(payload.get("freePreview", False)),
        "publish_on_web": bool(payload.get("publishOnWeb", True)),
        "send_email": bool(payload.get("sendEmail", False)),
        "email_subject": payload.get("emailSubject", "").strip(),
        "email_preview_text": payload.get("emailPreviewText", "").strip(),
        "test_email_recipients": clean_tags(payload.get("testEmailRecipients", "")),
        "scheduled_at": payload.get("scheduledAt", "").strip(),
        "free_unlock_at": payload.get("freeUnlockAt", "").strip(),
        "canonical_url": payload.get("canonicalUrl", "").strip(),
        "seo_title": payload.get("seoTitle", "").strip(),
        "seo_description": payload.get("seoDescription", "").strip(),
        "social_title": payload.get("socialTitle", "").strip(),
        "social_description": payload.get("socialDescription", "").strip(),
        "social_image": payload.get("socialImage", "").strip(),
        "thumbnail_prompt": payload.get("thumbnailPrompt", "").strip(),
        "thumbnail_alt": payload.get("thumbnailAlt", "").strip(),
        "substack_draft_id": payload.get("substackDraftId", "").strip(),
        "substack_draft_url": payload.get("substackDraftUrl", "").strip(),
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        if value not in ("", [], None):
            lines.append(f"{key}: {yaml_value(value)}")
    lines.append("---")
    lines.append("")
    notes = payload.get("notes", "").strip()
    if notes:
        lines.extend(["<!--", notes, "-->", ""])
    lines.append(payload.get("body", "").strip())
    lines.append("")
    return "\n".join(lines)


def save_draft_file(payload: dict[str, Any]) -> tuple[Path, str]:
    ensure_private_dir(DRAFTS)
    title = payload.get("title", "").strip()
    slug = payload.get("slug", "").strip() or slugify(title)
    safe_slug = slugify(slug)
    path = DRAFTS / f"{safe_slug}.md"
    markdown = build_markdown(payload)
    atomic_write_text(path, markdown)
    return path, markdown


def save_image_upload(payload: dict[str, Any]) -> dict[str, str]:
    filename, suffix, raw = decode_image_upload(payload)
    stem = slugify(Path(filename).stem)
    path = UPLOADS / f"{stem}{suffix}"
    counter = 2
    while path.exists():
        path = UPLOADS / f"{stem}-{counter}{suffix}"
        counter += 1

    atomic_write_bytes(path, raw)
    return {"path": public_path(path), "relativePath": f"./drafts/assets/{path.name}"}


def decode_image_upload(payload: dict[str, Any]) -> tuple[str, str, bytes]:
    filename = Path(str(payload.get("filename") or "image")).name
    data_url = str(payload.get("dataUrl") or "")
    if "," not in data_url:
        raise ValueError("Upload did not include image data.")

    header, encoded = data_url.split(",", 1)
    if not header.startswith("data:image/"):
        raise ValueError("Only image uploads are supported.")

    suffix = Path(filename).suffix.lower()
    if suffix not in {".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        media_type = header.split(";", 1)[0].split("/", 1)[1]
        suffix = ".jpg" if media_type == "jpeg" else f".{media_type}"

    stem = slugify(Path(filename).stem)
    path = UPLOADS / f"{stem}{suffix}"
    counter = 2
    while path.exists():
        path = UPLOADS / f"{stem}-{counter}{suffix}"
        counter += 1

    try:
        raw = b64decode(encoded, validate=True)
    except BinasciiError as exc:
        raise ValueError("Upload included invalid image data.") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Image uploads are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return filename, suffix, raw


def unique_obsidian_asset_path(stem: str, suffix: str) -> Path:
    safe_stem = slugify(stem)
    path = OBSIDIAN_SUBSTACK_ASSETS_DIR / f"{safe_stem}{suffix}"
    counter = 2
    while path.exists():
        path = OBSIDIAN_SUBSTACK_ASSETS_DIR / f"{safe_stem}-{counter}{suffix}"
        counter += 1
    return path


def publication_looks_configured(value: str) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return False
    return not any(marker in normalized for marker in PLACEHOLDER_PUBLICATION_MARKERS)


def build_thumbnail_prompt(essay_detail: dict[str, Any], publish_frontmatter: dict[str, Any]) -> str:
    provided = _stringify(publish_frontmatter.get("thumbnail_prompt")).strip()
    title = _stringify(publish_frontmatter.get("title")) or essay_detail.get("title", "")
    subtitle = _stringify(publish_frontmatter.get("subtitle")) or essay_detail.get("summary", "")
    summary = _stringify(publish_frontmatter.get("summary")) or essay_detail.get("summary", "")
    totem = ensure_totem(publish_frontmatter.get("totem") or essay_detail.get("totem"))
    totem_label = str(TOTEM_ITEMS.get(totem, {}).get("label") or totem) if totem else ""
    tags = publish_frontmatter.get("tags") or []
    if not isinstance(tags, list):
        tags = clean_tags(_stringify(tags))
    tag_text = ", ".join(tags[:6])

    if provided:
        creative_brief = provided
    else:
        totem_clause = f"Totem lens: {totem_label}. " if totem_label else ""
        creative_brief = (
            f"Title: {title}. Subtitle/summary: {subtitle or summary}. "
            f"{totem_clause}Tags: {tag_text or 'essay, reflection'}."
        )

    publication_name = PUBLICATION_NAME or OBSIDIAN_VAULT_NAME or "this publication"
    style = THUMBNAIL_STYLE_PROMPT.replace("{publication_name}", publication_name).strip()
    return f"{style} {creative_brief}".strip()


def essay_thumbnail_prompt(essay_id: str, publish_updates: dict[str, Any]) -> dict[str, Any]:
    """The ChatGPT bridge: airdate prepares the styled prompt, the image is
    generated outside the app and dragged back through the attach paths."""
    detail = get_essay_detail(essay_id)
    if detail.get("source_role") == "source":
        raise ValueError("Create a linked draft before preparing a Substack thumbnail prompt for this source note.")
    publish_frontmatter = dict(detail.get("frontmatter") or {})
    for key, value in sanitize_updates(publish_updates).items():
        if value in (None, "", []):
            continue
        publish_frontmatter[key] = value
    return {
        "ok": True,
        "essay_id": essay_id,
        "prompt": build_thumbnail_prompt(detail, publish_frontmatter),
    }


def local_draft_path(value: str) -> Path | None:
    return local_content_path(value)


def local_content_path(value: str, source_path: Path | None = None) -> Path | None:
    raw = (value or "").strip()
    if not raw or raw.startswith(("http://", "https://")):
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()

    normalized = raw[2:] if raw.startswith("./") else raw
    candidates: list[Path] = []
    if normalized.startswith("drafts/"):
        candidates.append(ROOT / normalized)
    if normalized.startswith("_assets/"):
        candidates.append(OBSIDIAN_ESSAYS_DIR / normalized)
    if source_path is not None:
        candidates.append(source_path.parent / normalized)
    candidates.extend([OBSIDIAN_ESSAYS_DIR / normalized, ROOT / normalized])

    seen: set[str] = set()
    unique_candidates: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved)
        if key not in seen:
            unique_candidates.append(resolved)
            seen.add(key)
    for candidate in unique_candidates:
        if candidate.exists():
            return candidate
    return unique_candidates[0] if unique_candidates else None


def prepare_essay_publish_payload(
    essay_id: str,
    publish_updates: dict[str, Any] | None = None,
    body_override: str | None = None,
) -> tuple[dict[str, Any], Path, dict[str, Any], str]:
    path = resolve_essay_path(essay_id)
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = split_frontmatter(text)
    if body_override is not None:
        body = normalize_body(body_override)

    publish_frontmatter = dict(frontmatter)
    if publish_updates:
        for key, value in sanitize_updates(publish_updates).items():
            if value in (None, "", []):
                continue
            publish_frontmatter[key] = value

    tag_list = publish_frontmatter.get("tags", [])
    if isinstance(tag_list, str):
        tag_list = clean_tags(tag_list)
    elif not isinstance(tag_list, list):
        tag_list = []
    title_default = _stringify(publish_frontmatter.get("title")) or path.stem
    subtitle_default = _stringify(publish_frontmatter.get("subtitle"))
    summary_default = (
        _stringify(publish_frontmatter.get("summary") or publish_frontmatter.get("subtitle")).strip()
        or safe_excerpt(body, 170)
    )
    totem_default = infer_totem(
        publish_frontmatter.get("totem") or publish_frontmatter.get("element"),
        path.name,
        title_default,
        tag_list,
        body,
    )
    metadata_defaults = metadata_defaults_for_card(
        publish_frontmatter,
        body,
        title_default,
        subtitle_default,
        summary_default,
        tag_list,
        totem_default,
    )
    for key, value in metadata_defaults.items():
        if key == "tags":
            if not tag_list and value:
                publish_frontmatter[key] = value
            continue
        if not _stringify(publish_frontmatter.get(key)).strip() and value not in (None, "", []):
            publish_frontmatter[key] = value

    tags_value = publish_frontmatter.get("tags")
    if not isinstance(tags_value, list):
        tags_value = _stringify(tags_value)

    test_recipients = publish_frontmatter.get("test_email_recipients")
    if not isinstance(test_recipients, list):
        test_recipients = _stringify(test_recipients)

    hero_value = _stringify(publish_frontmatter.get("hero") or publish_frontmatter.get("hero_image"))
    hero_path = local_content_path(hero_value, path)
    hero_for_payload = str(hero_path) if hero_path is not None and hero_path.exists() else hero_value
    social_image_value = _stringify(publish_frontmatter.get("social_image"))
    social_image_path = local_content_path(social_image_value, path)
    social_image_for_payload = (
        str(social_image_path) if social_image_path is not None and social_image_path.exists() else social_image_value
    )

    publish_payload = {
        "title": _stringify(publish_frontmatter.get("title")) or path.stem,
        "subtitle": _stringify(publish_frontmatter.get("subtitle")),
        "summary": _stringify(publish_frontmatter.get("summary")),
        "publication": _stringify(publish_frontmatter.get("publication"))
            or SUBSTACK_PUBLICATION,
        "slug": _stringify(publish_frontmatter.get("slug")),
        "hero": hero_for_payload,
        "tags": tags_value,
        "section": _stringify(publish_frontmatter.get("section")),
        "audience": _stringify(publish_frontmatter.get("audience")) or "everyone",
        "postType": _stringify(publish_frontmatter.get("post_type")) or "text",
        "commentPermissions": _stringify(publish_frontmatter.get("comment_permissions")) or "everyone",
        "freePreview": bool(publish_frontmatter.get("free_preview")),
        "publishOnWeb": bool(publish_frontmatter.get("publish_on_web", True)),
        "sendEmail": bool(publish_frontmatter.get("send_email", False)),
        "emailSubject": _stringify(publish_frontmatter.get("email_subject")),
        "emailPreviewText": _stringify(publish_frontmatter.get("email_preview_text")),
        "testEmailRecipients": test_recipients,
        "scheduledAt": _stringify(publish_frontmatter.get("scheduled_at")),
        "freeUnlockAt": _stringify(publish_frontmatter.get("free_unlock_at")),
        "canonicalUrl": _stringify(publish_frontmatter.get("canonical_url")),
        "seoTitle": _stringify(publish_frontmatter.get("seo_title")),
        "seoDescription": _stringify(publish_frontmatter.get("seo_description")),
        "socialTitle": _stringify(publish_frontmatter.get("social_title")),
        "socialDescription": _stringify(publish_frontmatter.get("social_description")),
        "socialImage": social_image_for_payload,
        "thumbnailPrompt": _stringify(publish_frontmatter.get("thumbnail_prompt")),
        "thumbnailAlt": _stringify(publish_frontmatter.get("thumbnail_alt")),
        "substackDraftId": _stringify(publish_frontmatter.get("substack_draft_id")),
        "substackDraftUrl": _stringify(publish_frontmatter.get("substack_draft_url")),
        "notes": _stringify(publish_frontmatter.get("notes")),
        "body": body.strip(),
    }
    return publish_payload, path, publish_frontmatter, body


# Caps on how many body-fidelity findings are listed individually in a preflight
# response. The counts in `body_fidelity` always carry the full total; these only
# bound the rendered lists so one messy essay can't produce a wall of text.
BODY_BLOCKER_LIMIT = 10
BODY_WARNING_LIMIT = 20

# Short human phrases for the editor's one-line "not ready: ..." status. The full
# explanation still rides along in the blocker's `message`; this is only what
# the writer reads at the moment a send is refused, so it names the damage, not the
# internal finding kind.
BODY_BLOCKER_LABELS = {
    "block_swallowed": "text would be dropped",
    "embed": "unsupported embed",
    "local_image": "local image",
    "table": "table",
}


def preflight_publish_payload(publish_payload: dict[str, Any]) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    metadata_checks: list[dict[str, Any]] = []

    def add_check(key: str, label: str, ok: bool, field: str = "", message: str = "") -> None:
        checks.append({"key": key, "label": label, "ok": ok, "field": field, "message": "" if ok else message})
        if not ok:
            blockers.append({"key": key, "field": field, "message": message or f"{label} is required."})

    def add_metadata_check(key: str, label: str, ok: bool, field: str = "", message: str = "") -> None:
        metadata_checks.append({
            "key": key,
            "label": label,
            "ok": ok,
            "field": field,
            "message": "" if ok else message,
        })

    add_check("title", "Title", bool(_stringify(publish_payload.get("title")).strip()), "title")
    add_check("subtitle", "Subtitle", bool(_stringify(publish_payload.get("subtitle")).strip()), "subtitle")
    add_check(
        "publication",
        "Publication",
        publication_looks_configured(_stringify(publish_payload.get("publication"))),
        "publication",
        "Set a real Substack publication URL/domain.",
    )
    add_check("body", "Essay body", bool(_stringify(publish_payload.get("body")).strip()), "", "Essay file body is empty.")

    hero = _stringify(publish_payload.get("hero")).strip()
    add_check("hero", "Hero / generated thumbnail", bool(hero), "hero_image", "Generate or drop a thumbnail before sending.")
    hero_path = local_draft_path(hero)
    if hero_path is not None:
        add_check(
            "hero_path",
            "Local hero image path",
            hero_path.exists(),
            "hero_image",
            f"Local hero image was not found: {hero}",
        )

    connector = connector_status()
    add_check(
        "substack_command",
        "Obsidian connector",
        bool(connector.get("ok")),
        "",
        "Install the airdate connector in Obsidian and run \"Pair with airdate\".",
    )
    add_check("substack_session", "Substack session", bool(connector.get("connected")), "", "Connect Substack through Obsidian before sending.")

    social_image = _stringify(publish_payload.get("socialImage")).strip()
    if social_image:
        warnings.append({
            "key": "social_image",
            "message": "Social image is saved locally, but the current CLI does not know Substack's private social-image field.",
        })
    if _stringify(publish_payload.get("scheduledAt")).strip():
        warnings.append({
            "key": "scheduled_at",
            "message": "Scheduled time is saved locally; this draft-only path does not schedule publication.",
        })
    if publish_payload.get("sendEmail") is True:
        warnings.append({
            "key": "send_email",
            "message": "Send email is saved locally; this draft-only path will not email subscribers.",
        })

    # Body fidelity: constructs the Substack converter mangles or silently drops.
    # Surfaced here because preflight runs before every send — this is the last
    # point where the writer can see the damage before it ships. Stage 0 reports only;
    # the lossy/blocker classes get promoted to real blockers as each transform
    # stage lands and can actually fix them.
    # Scan the body as it will actually be SENT (after the Layer-1 repairs), so
    # preflight predicts the real outcome instead of flagging damage that the
    # send path already fixes.
    repaired_body, _, line_map = obsidian_markdown.preprocess_with_line_map(_stringify(publish_payload.get("body")))
    fidelity_findings = obsidian_markdown.scan(repaired_body)

    # A finding is made on the repaired text, whose lines have moved: airdate
    # dropped tag lines and put blank lines around headings. Name the line the
    # writer wrote instead, counted from the first non-blank line of the
    # script, and carry it as a number so the room can put the writer on it.
    def written_line(finding: dict[str, Any]) -> int:
        line = int(finding["line"])
        return line_map[line - 1] if 0 < line <= len(line_map) else line

    # Tier-3a Stage 2 + 4a: every BLOCKER-severity finding stops the send.
    #
    # Stage 2 promoted only tables. That left the genuinely dangerous kinds as
    # warnings the writer could scroll past: `block_swallowed` and `local_image` fail
    # SILENTLY — the draft is created, the send reports success, and the content
    # simply is not there. `![[Pasted image ...png]]` (Obsidian's default when
    # you paste an image) enters the vendored parser's image branch, matches
    # neither image regex, hits no else, and the whole block is discarded —
    # taking every prose line glued to it, since blocks split on blank lines.
    # Detection was already correct; only the gate was missing. Blocking is the
    # right call for all of them: none can be auto-repaired yet, so the honest
    # outcome is to stop and name the line rather than ship a lossy draft.
    blocking_findings = [f for f in fidelity_findings if f["severity"] == obsidian_markdown.BLOCKER]
    for finding in blocking_findings[:BODY_BLOCKER_LIMIT]:
        label = BODY_BLOCKER_LABELS.get(finding["kind"], finding["kind"].replace("_", " "))
        line = written_line(finding)
        blockers.append({
            "key": f"body_{finding['kind']}_{line}",
            "field": "",
            "line": line,
            "label": f"{label} (line {line})",
            "message": f"line {line}: {finding['detail']}",
        })
    if len(blocking_findings) > BODY_BLOCKER_LIMIT:
        extra = len(blocking_findings) - BODY_BLOCKER_LIMIT
        blockers.append({
            "key": "body_blockers_more",
            "field": "",
            "count": extra,
            "label": f"+{extra} more",
            "message": f"...and {extra} more blocking body-fidelity findings.",
        })

    other_findings = [f for f in fidelity_findings if f["severity"] != obsidian_markdown.BLOCKER]
    for finding in other_findings[:BODY_WARNING_LIMIT]:  # cap the noise; the summary carries the rest
        line = written_line(finding)
        warnings.append({
            "key": f"body_{finding['kind']}",
            "line": line,
            "message": f"line {line}: {finding['detail']}",
        })
    fidelity_summary = obsidian_markdown.summarize(fidelity_findings)
    if len(other_findings) > BODY_WARNING_LIMIT:
        warnings.append({
            "key": "body_fidelity_more",
            "message": f"...and {len(other_findings) - BODY_WARNING_LIMIT} more body-fidelity findings.",
        })

    summary = _stringify(publish_payload.get("summary")).strip()
    subtitle = _stringify(publish_payload.get("subtitle")).strip()
    title = _stringify(publish_payload.get("title")).strip()
    tags = clean_tags(publish_payload.get("tags", ""))
    thumbnail_alt = _stringify(publish_payload.get("thumbnailAlt")).strip()
    thumbnail_prompt = _stringify(publish_payload.get("thumbnailPrompt")).strip()
    seo_title = _stringify(publish_payload.get("seoTitle")).strip()
    seo_description = _stringify(publish_payload.get("seoDescription")).strip()
    social_title = _stringify(publish_payload.get("socialTitle")).strip()
    social_description = _stringify(publish_payload.get("socialDescription")).strip()

    add_metadata_check("summary", "Summary", bool(summary), "summary", "Add a summary for cards, SEO, and editorial review.")
    add_metadata_check("tags", "Tags", bool(tags), "tags", "Add at least one tag.")
    add_metadata_check("seo_title", "SEO title", bool(seo_title or title), "seo_title", "Add an SEO title or confirm the title can be reused.")
    add_metadata_check("seo_description", "SEO description", bool(seo_description or summary or subtitle), "seo_description", "Add SEO description or summary copy.")
    add_metadata_check("social_title", "Social title", bool(social_title or title), "social_title", "Add social title or confirm the title can be reused.")
    add_metadata_check("social_description", "Social description", bool(social_description or summary or subtitle), "social_description", "Add social description or summary copy.")
    add_metadata_check("thumbnail_prompt", "Thumbnail prompt", bool(thumbnail_prompt), "thumbnail_prompt", "Add or generate thumbnail prompt notes.")
    add_metadata_check("thumbnail_alt", "Thumbnail alt text", bool(thumbnail_alt or title), "thumbnail_alt", "Add thumbnail alt text.")

    metadata_complete = all(item["ok"] for item in metadata_checks)

    return {
        "ready": not blockers,
        "metadata_complete": metadata_complete,
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
        "metadata_checks": metadata_checks,
        "body_fidelity": fidelity_summary,
        "summary": {
            "title": _stringify(publish_payload.get("title")),
            "publication": _stringify(publish_payload.get("publication")),
            "hero": hero,
            "word_count": len(re.findall(r"\b\w+\b", _stringify(publish_payload.get("body")))),
            "has_command": bool(connector.get("ok")),
            "has_session": bool(connector.get("connected")),
            "metadata_complete": metadata_complete,
        },
    }


def preflight_essay_for_substack(
    essay_id: str,
    publish_updates: dict[str, Any] | None = None,
    body_override: str | None = None,
) -> dict[str, Any]:
    publish_payload, path, publish_frontmatter, body = prepare_essay_publish_payload(essay_id, publish_updates, body_override)
    word_count = len(re.findall(r"\b\w+\b", body))
    source_role, is_long_source = source_role_for(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), publish_frontmatter, word_count)
    preflight = preflight_publish_payload(publish_payload)

    # `publish_updates` is allowed to drive a preview, but it never proves the
    # metadata exists in the canonical note. Re-read the file here instead of
    # trusting the merged publish payload, which may contain unsaved form data.
    persisted_text = path.read_text(encoding="utf-8", errors="ignore")
    persisted_frontmatter, persisted_body = split_frontmatter(persisted_text)
    # Advisory only. A key written twice reads as its last copy, which is rarely
    # what the writer meant; airdate does not write duplicates, so say it is there.
    for key in duplicated_frontmatter_keys(persisted_text):
        preflight["warnings"].append({
            "key": f"duplicate_key_{key}",
            "message": f"This note has \"{key}\" more than once in its frontmatter; airdate reads the last one.",
        })
    missing_persisted: list[str] = []
    for field in PERSISTED_DRAFT_FIELDS:
        if not _stringify(persisted_frontmatter.get(field)).strip():
            missing_persisted.append(field)
    if not clean_tags(persisted_frontmatter.get("tags", [])):
        missing_persisted.append("tags")
    if not persisted_body.strip():
        missing_persisted.append("body")
    for field in missing_persisted:
        preflight["blockers"].append({
            "key": f"persisted_{field}",
            "field": field,
            "message": f"Save {field.replace('_', ' ')} to Obsidian before sending.",
        })
    if missing_persisted:
        preflight["ready"] = False

    if source_role == "source":
        preflight["ready"] = False
        preflight["blockers"].insert(0, {
            "key": "source_role",
            "field": "source_role",
            "message": "Create a linked draft before sending this source note to Substack.",
        })
    return {
        "ok": True,
        "essay_id": essay_id,
        "essay_path": public_path(path),
        "source_role": source_role,
        "is_long_source": is_long_source,
        **preflight,
    }


def metadata_defaults_for_card(
    frontmatter: dict[str, Any],
    body: str,
    title: str,
    subtitle: str,
    summary: str,
    tags: list[str],
    totem: str,
) -> dict[str, Any]:
    """Derived publish metadata used by cards/editor without mutating Obsidian."""
    body_excerpt = safe_excerpt(body, 180)
    summary_value = _stringify(frontmatter.get("summary")).strip() or summary or body_excerpt
    subtitle_value = _stringify(frontmatter.get("subtitle")).strip() or summary_value
    title_value = _stringify(frontmatter.get("title")).strip() or title
    publication_value = _stringify(frontmatter.get("publication")).strip() or SUBSTACK_PUBLICATION
    hero_value = _stringify(frontmatter.get("hero_image") or frontmatter.get("hero")).strip()
    social_image_value = _stringify(frontmatter.get("social_image")).strip() or hero_value
    seo_title = _stringify(frontmatter.get("seo_title")).strip() or title_value
    seo_description = _stringify(frontmatter.get("seo_description")).strip() or summary_value or subtitle_value
    social_title = _stringify(frontmatter.get("social_title")).strip() or title_value
    social_description = _stringify(frontmatter.get("social_description")).strip() or summary_value or subtitle_value
    thumbnail_alt = _stringify(frontmatter.get("thumbnail_alt")).strip() or title_value
    thumbnail_prompt = _stringify(frontmatter.get("thumbnail_prompt")).strip()
    if not thumbnail_prompt:
        totem_label = str(TOTEM_ITEMS.get(totem, {}).get("label") or totem) if totem else ""
        thumbnail_prompt = f"Editorial illustration for \u201c{title_value}\u201d" + (f", {totem_label} symbolism" if totem_label else "")
    email_subject = _stringify(frontmatter.get("email_subject")).strip() or title_value
    email_preview_text = _stringify(frontmatter.get("email_preview_text")).strip() or summary_value

    return {
        "title": title_value,
        "subtitle": subtitle_value,
        "summary": summary_value,
        "publication": publication_value,
        "hero_image": hero_value,
        "social_image": social_image_value,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "social_title": social_title,
        "social_description": social_description,
        "thumbnail_prompt": thumbnail_prompt,
        "thumbnail_alt": thumbnail_alt,
        "email_subject": email_subject,
        "email_preview_text": email_preview_text,
        "tags": tags,
    }


def publish_readiness_for_card(
    frontmatter: dict[str, Any],
    body: str,
    title: str,
    subtitle: str,
    summary: str,
    tags: list[str],
    totem: str,
    source_role: str,
) -> dict[str, Any]:
    defaults = metadata_defaults_for_card(frontmatter, body, title, subtitle, summary, tags, totem)
    missing_metadata: list[str] = []
    if not defaults["title"]:
        missing_metadata.append("title")
    if not defaults["subtitle"]:
        missing_metadata.append("subtitle")
    if not publication_looks_configured(defaults["publication"]):
        missing_metadata.append("publication")
    if not defaults["summary"]:
        missing_metadata.append("summary")
    if not clean_tags(defaults["tags"]):
        missing_metadata.append("tags")
    if not _stringify(body).strip():
        missing_metadata.append("body")
    if not defaults["seo_title"]:
        missing_metadata.append("seo title")
    if not defaults["seo_description"]:
        missing_metadata.append("seo description")
    if not defaults["social_title"]:
        missing_metadata.append("social title")
    if not defaults["social_description"]:
        missing_metadata.append("social description")
    if not defaults["thumbnail_alt"]:
        missing_metadata.append("thumbnail alt")

    missing_images: list[str] = []
    if not defaults["hero_image"]:
        missing_images.append("hero image")

    metadata_complete = not missing_metadata
    image_complete = not missing_images
    if source_role == "source":
        status = "source"
        label = "source note"
    elif not metadata_complete:
        status = "metadata"
        label = f"{len(missing_metadata)} metadata gap{'' if len(missing_metadata) == 1 else 's'}"
    elif not image_complete:
        status = "image"
        label = "needs hero"
    else:
        status = "ready"
        label = "ready to send"

    return {
        "status": status,
        "label": label,
        "metadata_complete": metadata_complete,
        "image_complete": image_complete,
        "ready_to_send": source_role != "source" and metadata_complete and image_complete,
        "ready_except_image": source_role != "source" and metadata_complete and not image_complete,
        "missing_metadata": missing_metadata,
        "missing_images": missing_images,
        "defaults": defaults,
    }


def transport_payload(result: dict[str, Any]) -> dict[str, Any]:
    """The JSON object substack_draft.py printed on stdout, or {} when the
    subprocess died before printing one."""
    raw = _stringify(result.get("stdout")).strip()
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def finish_transport_result(result: dict[str, Any]) -> dict[str, Any]:
    """Lift what the transport knows onto the send result: the draft identity,
    the typed error_kind and its sentence, and the warnings it collected on
    either outcome. A failure no layer classified is a transport failure."""
    payload = transport_payload(result)
    # stale_draft_id rides along so the editor can offer recovery instead of
    # asking the writer to hand-edit YAML. substack_draft.py sets it when
    # Substack 404s a draft id we stored.
    for key in ("error_kind", "message", "draft_id", "edit_url", "stale_draft_id"):
        if not _stringify(result.get(key)).strip() and _stringify(payload.get(key)).strip():
            result[key] = payload[key]
    raw_warnings = result.get("warnings")
    if not isinstance(raw_warnings, list):
        raw_warnings = payload.get("warnings")
    result["warnings"] = [str(w) for w in (raw_warnings if isinstance(raw_warnings, list) else []) if _stringify(w).strip()]
    if result.get("ok"):
        result.pop("error_kind", None)
        return result
    if result.get("error_kind") not in SEND_ERROR_KINDS:
        result["error_kind"] = "transport"
    if not _stringify(result.get("message")).strip():
        stderr_lines = [line for line in _stringify(result.get("stderr")).splitlines() if line.strip()]
        result["message"] = (
            _stringify(result.get("error")).strip()
            or (stderr_lines[-1].strip() if stderr_lines else "")
            or "Substack draft was not created."
        )
    return result


def publish_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """Save the draft file and hand it to the paired Obsidian connector, the
    only Substack transport. The connector runs its pinned substack_draft.py,
    which creates a draft and stops: nothing here can publish, schedule or
    email subscribers."""
    saved_path, _ = save_draft_file(payload)
    saved_public_path = public_path(saved_path)
    connector = connector_status()
    if not connector.get("connected"):
        return {
            "ok": False,
            "saved": saved_public_path,
            "error_kind": "auth" if connector.get("ok") else "transport",
            "message": "Saved locally. Connect Substack through the Obsidian airdate connector to create a draft.",
        }
    # 125s: the connector's own kill deadline is 120s, so its verdict arrives
    # before this socket gives up.
    result = connector_request("/draft", {"file": str(saved_path)}, timeout=125)
    result["saved"] = saved_public_path
    result["transport"] = "obsidian_connector"
    return finish_transport_result(result)


def _frontmatter_open(text: str) -> int | None:
    """Index just past the opening frontmatter fence (after '---' and its line
    ending), tolerating a leading UTF-8 BOM and a CRLF fence. None when the text
    does not open with a fence. Guards against the silent field-loss + save
    corruption a '---\\r\\n' or BOM opener would otherwise cause."""
    start = 1 if text.startswith("﻿") else 0
    if text.startswith("---\n", start):
        return start + 4
    if text.startswith("---\r\n", start):
        return start + 5
    return None


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    open_end = _frontmatter_open(text)
    if open_end is None:
        return {}, text
    for marker in ("\n---\n", "\n---\r\n"):
        end_idx = text.find(marker, open_end)
        if end_idx != -1:
            return parse_frontmatter(text[open_end:end_idx]), text[end_idx + len(marker):]
    return {}, text


def parse_scalar(value: str) -> Any:
    v = value.strip()
    if v == "":
        return ""
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        parts = re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", inner)
        out = []
        for part in parts:
            p = part.strip()
            if (p.startswith('"') and p.endswith('"')) or (p.startswith("'") and p.endswith("'")):
                out.append(p[1:-1])
            else:
                out.append(p)
        return out
    if v.startswith('"') and v.endswith('"'):
        # airdate's own serializer writes JSON-compatible quoted scalars.
        # Decode legacy \uXXXX escapes instead of showing them as literal text.
        try:
            decoded = json.loads(v)
            if isinstance(decoded, str):
                return decoded
        except json.JSONDecodeError:
            pass
        return v[1:-1]
    if v.startswith("'") and v.endswith("'"):
        return v[1:-1]
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def parse_frontmatter(block: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    pending_list_key: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if pending_list_key and line.lstrip().startswith("- "):
            # The key line stored "" as a placeholder; the first "- item" line
            # converts it to a list (str has no .append — this crashed and
            # silently dropped every block-list file from the scan).
            if not isinstance(parsed.get(pending_list_key), list):
                parsed[pending_list_key] = []
            parsed[pending_list_key].append(parse_scalar(line.lstrip()[2:]))
            continue
        pending_list_key = None
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value == "":
            parsed[key] = ""
            pending_list_key = key
        else:
            parsed[key] = parse_scalar(value)
    return parsed


def dump_frontmatter(frontmatter: dict[str, Any]) -> str:
    keys = [key for key in ORDERED_FRONTMATTER_KEYS if key in frontmatter]
    keys.extend(sorted([key for key in frontmatter if key not in keys]))
    lines = ["---"]
    for key in keys:
        value = frontmatter[key]
        if value in (None, "", []):
            continue
        lines.append(f"{key}: {yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines)


def frontmatter_bounds(text: str) -> tuple[int, int, str] | None:
    """(block_start, close_start, close_marker) for the opening/closing fences,
    or None if the text has no frontmatter. block = text[block_start:close_start];
    text[close_start:] is the closing marker followed by the body. Tolerates a
    leading BOM / CRLF opening fence so those files edit surgically too."""
    open_end = _frontmatter_open(text)
    if open_end is None:
        return None
    for marker in ("\n---\n", "\n---\r\n"):
        idx = text.find(marker, open_end)
        if idx != -1:
            return open_end, idx, marker
    return None


def _frontmatter_key_spans(lines: list[str]) -> list[tuple[str, int, int, str]]:
    """Every top-level key occurrence, in file order, as (key, start, end, style):
    the [start, end) line range it occupies (its key line plus any block-list /
    indented continuation lines) and its style ('block' | 'inline' | 'scalar').
    Comments and blank lines belong to no key. A key written twice yields two
    occurrences; a note like that reads as its last copy, so callers that edit a
    key have to deal with all of them."""
    spans: list[tuple[str, int, int, str]] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        starts_list = stripped.startswith("- ")
        if not stripped or stripped.startswith("#") or starts_list or ":" not in line \
                or line.startswith((" ", "\t")):
            i += 1
            continue
        key = line.split(":", 1)[0].strip()
        after_colon = line.split(":", 1)[1].strip()
        j = i + 1
        is_block = False
        while j < n:
            nxt = lines[j]
            if nxt.strip().startswith("- "):
                is_block = True
                j += 1
            elif nxt.startswith((" ", "\t")) and nxt.strip():
                j += 1
            else:
                break
        spans.append((key, i, j, "block" if is_block else ("inline" if after_colon.startswith("[") else "scalar")))
        i = j
    return spans


def duplicated_frontmatter_keys(text: str) -> list[str]:
    """Top-level frontmatter keys written more than once, in first-seen order."""
    bounds = frontmatter_bounds(text)
    if bounds is None:
        return []
    start, close_start, _marker = bounds
    counts: dict[str, int] = {}
    for key, _s, _e, _style in _frontmatter_key_spans(text[start:close_start].split("\n")):
        counts[key] = counts.get(key, 0) + 1
    return [key for key, count in counts.items() if count > 1]


def _block_indent(lines: list[str], start: int, end: int) -> str:
    for k in range(start, end):
        if lines[k].strip().startswith("- "):
            return lines[k][: len(lines[k]) - len(lines[k].lstrip())]
    return "  "


def _render_key_lines(key: str, value: Any, style: str, indent: str = "  ") -> list[str]:
    """Serialize one key. A list keeps block style if that is how it was written
    (so Obsidian's Properties editor output survives); everything else is a single
    inline line."""
    if isinstance(value, list) and style == "block":
        out = [f"{key}:"]
        out.extend(f"{indent}- {yaml_value(item)}" for item in value if str(item).strip())
        return out
    return [f"{key}: {yaml_value(value)}"]


def _canonical_insert_index(lines: list[str], key: str) -> int:
    """Where a brand-new key line should go so key order stays canonical. Unknown
    keys append to the end of the block."""
    if key not in ORDERED_FRONTMATTER_KEYS:
        return len(lines)
    target = ORDERED_FRONTMATTER_KEYS.index(key)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- ") \
                or ":" not in line or line.startswith((" ", "\t")):
            continue
        existing = line.split(":", 1)[0].strip()
        if existing in ORDERED_FRONTMATTER_KEYS and ORDERED_FRONTMATTER_KEYS.index(existing) > target:
            return i
    return len(lines)


def apply_frontmatter_edits(text: str, changes: dict[str, dict[str, Any]]) -> str:
    """Rewrite only the frontmatter keys in `changes` (from apply_updates: each
    {"before", "after"}, after=empty ⇒ delete), leaving every untouched line —
    comments, key order, block-list format, quote style, unknown keys — and the
    body byte-for-byte. Real round-trip: no full reserialization.

    Falls back to a fresh dump only when the file has no frontmatter at all."""
    if not changes:
        return text
    bounds = frontmatter_bounds(text)
    if bounds is None:
        fresh = {k: c["after"] for k, c in changes.items() if c.get("after") not in (None, "", [])}
        if not fresh:
            return text
        return dump_frontmatter(fresh) + "\n\n" + normalize_body(text).rstrip() + "\n"

    start, close_start, marker = bounds
    tail = text[close_start:]  # closing fence + body, preserved verbatim
    lines = text[start:close_start].split("\n")
    occurrences = {s_start: (key, s_end, style) for key, s_start, s_end, style in _frontmatter_key_spans(lines)}

    new_lines: list[str] = []
    handled: set[str] = set()
    i, n = 0, len(lines)
    while i < n:
        occurrence = occurrences.get(i)
        if occurrence is None:
            new_lines.append(lines[i])  # comment / blank / stray line
            i += 1
            continue
        key, s_end, style = occurrence
        if key in changes:
            # A changed key is written once, where it first appears. Any further
            # copies of it are dropped, so the note cannot go on reading as an
            # older value after the edit. Keys not in `changes` are never touched,
            # repeated or not.
            after = changes[key]["after"]
            if key not in handled and after not in (None, "", []):
                new_lines.extend(_render_key_lines(key, after, style, _block_indent(lines, i, s_end)))
            # else: deletion, or a later copy: emit nothing for this span
            handled.add(key)
        else:
            new_lines.extend(lines[i:s_end])  # unchanged: verbatim
        i = s_end

    for key, change in changes.items():
        if key in handled:
            continue
        after = change.get("after")
        if after in (None, "", []):
            continue  # nothing to add for a not-present key set empty
        at = _canonical_insert_index(new_lines, key)
        # New keys the app adds use its default inline style (block-list format is
        # only ever preserved, never introduced).
        new_lines[at:at] = _render_key_lines(key, after, "inline")

    return text[:start] + "\n".join(new_lines) + tail


def first_line_excerpt(body: str, limit: int = 180) -> str:
    """The essay's actual opening line, for the script page on a card.

    This is not `summary`. Summary prefers frontmatter and is what Substack
    sends as the email preview; the card wants the first thing the writer
    actually wrote. Headings, blockquote markers, list bullets and horizontal
    rules are skipped, because none of them is a sentence."""
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("#", ">", "---", "***", "|", "```")):
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+\.\s+", "", line)
        line = re.sub(r"[*_`]", "", line).strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "\u2026"
    return ""


def safe_excerpt(text: str, limit: int = 180) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def file_state(path: Path, text: str | None = None) -> dict[str, Any]:
    if text is None:
        text = path.read_text(encoding="utf-8", errors="ignore")
    stat = path.stat()
    return {
        "mtime": stat.st_mtime,
        "mtime_iso": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "content_hash": content_hash(text),
    }


def normalize_source_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    return role if role in SOURCE_ROLE_SET else ""


def source_role_for(relative_path: str, frontmatter: dict[str, Any], word_count: int) -> tuple[str, bool]:
    explicit = normalize_source_role(frontmatter.get("source_role"))
    draft_of = str(frontmatter.get("draft_of") or "").strip()
    rawish = Path(relative_path).name.lower().startswith("raw_") or "/raw_" in f"/{relative_path.lower()}"
    is_long_source = rawish or word_count >= LONG_SOURCE_WORD_THRESHOLD
    if explicit:
        return explicit, is_long_source
    if draft_of:
        return "draft", is_long_source
    if is_long_source:
        return "source", is_long_source
    return "standalone", is_long_source


def ensure_totem(value: Any) -> str:
    """A configured totem key, the configured default, or "" (totems off or
    no default)."""
    if not TOTEMS_ENABLED:
        return ""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in TOTEM_ITEMS else TOTEM_DEFAULT


def normalize_status(value: Any) -> str:
    # Lifecycle: Writers Room -> Writers Likey -> Ready for Air -> Live.
    # Writers Room = the default: any essay that exists is in the writers room,
    # so an absent/blank status means Writers Room (there is no "Inbox" status;
    # un-filed essays surface via the needs-intake flag, not a status).
    # Writers Likey = writing done, ready to be scheduled (no date yet) — the
    # pool you drag onto the calendar. Ready for Air = scheduled + on the
    # calendar (set by the Ready for Air button/drag); Live = published on
    # Substack. There is no "Published" status any more: live means published,
    # and the old word survives only as a folder on disk and as an alias here.
    # Archived sits outside the flow. "airdate" is the tool's name, never a
    # status: the old working status "Air Date" aliases to "Ready for Air".
    if not value:
        return "Writers Room"
    raw = str(value).strip()
    if raw in STATUS_SET:
        return raw
    lower_map = {
        "seed": "Writers Room",
        "dormant": "Writers Room",
        "inbox": "Writers Room",
        "developing": "Writers Room",
        "growing": "Writers Room",
        "draft": "Writers Room",
        "complete": "Writers Room",
        "completed": "Writers Room",
        "done": "Writers Room",
        "writers room": "Writers Room",
        "writers-room": "Writers Room",
        "writers likey": "Writers Likey",
        "writers-likey": "Writers Likey",
        "likey": "Writers Likey",
        "ready to schedule": "Writers Likey",
        "ready-to-schedule": "Writers Likey",
        "queued": "Ready for Air",
        "ready": "Ready for Air",
        "ready for air": "Ready for Air",
        "ready-for-air": "Ready for Air",
        "air date": "Ready for Air",
        "air-date": "Ready for Air",
        "airdate": "Ready for Air",
        "live": "Live",
        "released": "Live",
        "published": "Live",
        "archived": "Archived",
        "archive": "Archived",
    }
    return lower_map.get(raw.lower(), "Writers Room")


# The totem picker's "none". The editor sends "" for "unchanged", so clearing
# needs its own word.
CLEAR_TOTEM = "none"


def raw_totem(frontmatter: dict[str, Any]) -> str:
    """The note's own totem value, untouched by inference."""
    return str(frontmatter.get("totem") or frontmatter.get("element") or "").strip()


def infer_totem(
    explicit_value: Any,
    relative_path: str,
    title: str,
    tags: list[str],
    body_text: str,
) -> str:
    """An explicit configured totem wins; otherwise score the configured
    keyword tables; otherwise the configured default. "" when totems are off."""
    if not TOTEMS_ENABLED:
        return ""
    explicit = str(explicit_value or "").strip().lower()
    if explicit in TOTEM_ITEMS:
        return explicit

    hay = " ".join(
        [
            relative_path.lower(),
            title.lower(),
            " ".join(tag.lower() for tag in tags),
            safe_excerpt(body_text, 400).lower(),
        ]
    )
    scores: dict[str, int] = {}
    for key, item in TOTEM_ITEMS.items():
        keywords = item.get("keywords") or {}
        scores[key] = sum(int(weight) for marker, weight in keywords.items() if str(marker).lower() in hay)
    best = sorted(scores.items(), key=lambda entry: entry[1], reverse=True)
    if best and best[0][1] > 0:
        return best[0][0]
    return TOTEM_DEFAULT


def scheduling_refusal(status: Any) -> str | None:
    """Why this essay cannot go on the board, or None if it can.

    Writers room essays cannot be scheduled. The card dims its drag handle and
    placing mode declines, but those are hints — this is the rule, so a direct
    POST cannot put an unstarred essay on a Monday either. The sentence comes
    back in the refusal so the interface can show it without inventing wording."""
    if normalize_status(status) == "Writers Room":
        return "star it for writers likey before it can go on the board."
    return None


def has_live_post_link(frontmatter: dict[str, Any]) -> bool:
    """Proof that an essay is actually published: a real http(s) post link.

    A draft id is not proof — the draft exists on Substack but nobody can read
    it. This is the one piece of evidence that turns an essay live, whether it
    is pasted on the board or found in an old note."""
    url = str(frontmatter.get("substack_url") or "").strip()
    return bool(re.match(r"^https?://", url))


def effective_status(relative_path: str, frontmatter: dict[str, Any]) -> str:
    """The single source of truth for an essay's lifecycle status.

    Folder wins for the two terminal states: a file physically in Published/ IS
    live and a file in Archive/ IS Archived, no matter what the frontmatter
    says — that reconciliation is the whole point (it kills the bug where a
    published essay showed a working status with a live Publish button). The
    folder keeps its old name; only the word the app uses changed.

    Off the terminal folders the frontmatter status carries the working state,
    with one re-reading. Old notes wrote "Live" to mean "draft sent", which is
    not what live means now, so that one word is believed only when the note
    carries a real post link; a draft id alone, or nothing, reads as Ready for
    Air. An old "Published" gets no such treatment: it was the writer's own
    claim that the essay went out, and airdate takes them at their word.
    Nothing is rewritten in the vault; set_essay_status relocates and restamps
    on the next write."""
    rp = relative_path.lower()
    top = rp.split("/", 1)[0] if "/" in rp else ""
    if top == "published" or "/published/" in rp:
        return "Live"
    if top == "archive" or "/archive/" in rp:
        return "Archived"
    raw = frontmatter.get("status")
    if raw and str(raw).strip():
        status = normalize_status(raw)
        # Only the ambiguous old word is re-read. "Published" is believed.
        if (
            status == "Live"
            and str(raw).strip().lower() == "live"
            and not has_live_post_link(frontmatter)
        ):
            return "Ready for Air"
        return status
    if frontmatter.get("published") is True:
        return "Live"
    return "Writers Room"


def classify_essay(relative_path: str, title: str, frontmatter: dict[str, Any]) -> str:
    """Bucket an essay: 'hidden' (never listed), 'archived', 'shelf' (published),
    or 'active'. Reads effective_status so the card, detail view, and dropdown
    all agree on one source of truth.

    Hidden covers index/MOC/meta notes and research notes (`type: research` or a
    `research` tag): material kept inside its topic folder for future essays,
    never an essay itself, so never listed, scanned for sending, or re-hashed.
    A writer's own filename and title conventions add to that through
    `vault.hidden` in config.json."""
    title_l = title.lower()
    fm_type = str(frontmatter.get("type") or "").strip().lower()
    tags = [str(tag).strip().lower() for tag in frontmatter.get("tags", [])] if isinstance(frontmatter.get("tags"), list) else []

    filename = relative_path.rsplit("/", 1)[-1]
    if any(filename.startswith(prefix) for prefix in HIDDEN_FILENAME_PREFIXES):
        return "hidden"
    if any(marker in title_l for marker in HIDDEN_TITLE_CONTAINS):
        return "hidden"
    if "/" not in relative_path and any(marker in title_l for marker in HIDDEN_TOPLEVEL_TITLE_CONTAINS):
        return "hidden"
    if fm_type in {"index", "moc", "meta", "research"}:
        return "hidden"
    if any(tag in {"moc", "index", "meta", "research"} for tag in tags):
        return "hidden"

    status = effective_status(relative_path, frontmatter)
    if status == "Archived":
        return "archived"
    if status == "Live":
        return "shelf"
    return "active"


def days_since(dt: datetime) -> int:
    now = datetime.now(timezone.utc)
    delta = now - dt
    return max(0, int(delta.total_seconds() // 86400))


def obsidian_url_for(relative_from_essays: str) -> str:
    prefix = "" if ESSAYS_FOLDER == "." else f"{ESSAYS_FOLDER}/"
    vault_file = f"{prefix}{relative_from_essays}".replace("\\", "/")
    return (
        "obsidian://open?vault="
        + urllib_parse_quote(OBSIDIAN_VAULT_NAME)
        + "&file="
        + urllib_parse_quote(vault_file)
    )


def urllib_parse_quote(value: str) -> str:
    return urllib.request.pathname2url(value).replace("/", "%2F")


UID_KEY = "airdate_uid"
UID_RE = re.compile(r"[0-9a-f]{32}")


def frontmatter_uid(frontmatter: dict[str, Any]) -> str:
    """This tool's durable id, or "" if absent/foreign.

    Namespaced (`airdate_uid`, not `uid`) because `uid` is a common Obsidian
    frontmatter key — Zettelkasten/UID plugins, Templater, imported notes — and
    adopting a foreign value as the essay id made those essays permanently
    unroutable (a value containing "/" breaks route parsing; parse_scalar coerces
    digit-only values to int). Shape-gated to 32 hex so a malformed value is
    simply invisible: the essay keeps its path-hash id and still earns its own
    durable id on the next write.
    """
    value = frontmatter.get(UID_KEY)
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if UID_RE.fullmatch(value) else ""


def essay_id_for(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12]


# Files that failed to parse on the most recent scan — surfaced via
# /api/app/status so a latent parser bug is visible, not silently hiding files.
_LAST_SCAN_FAILURES: list[dict[str, str]] = []


def discover_essays() -> tuple[list[dict[str, Any]], dict[str, Path]]:
    global _LAST_SCAN_FAILURES
    essays: list[dict[str, Any]] = []
    id_map: dict[str, Path] = {}
    failures: list[dict[str, str]] = []
    # uid -> (essay dict already appended, its path, its mtime) so a later,
    # older file can reclaim the id from an earlier-sorted copy.
    seen_uids: dict[str, tuple[dict[str, Any], Path, float]] = {}

    if not OBSIDIAN_ESSAYS_DIR.exists():
        _LAST_SCAN_FAILURES = failures
        return essays, id_map

    for path in sorted(OBSIDIAN_ESSAYS_DIR.rglob("*.md")):
        # Dot folders (.obsidian, .trash, .git) are never essays, which matters
        # most when the essays folder is the vault root.
        if any(part.startswith(".") for part in path.relative_to(OBSIDIAN_ESSAYS_DIR).parts[:-1]):
            continue
        try:
            relative = path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
            legacy_path_id = essay_id_for(relative)
            text = path.read_text(encoding="utf-8", errors="ignore")
            frontmatter, body = split_frontmatter(text)
            stat = path.stat()
            touched = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            # Durable identity: the stamped airdate_uid is the canonical id when
            # present, so an Obsidian move/rename no longer churns the id. Files
            # not yet stamped keep the path hash until their next write.
            uid = frontmatter_uid(frontmatter)
            if uid:
                prior = seen_uids.get(uid)
                if prior is not None:
                    # Two files claim one id — an Obsidian duplicate. Tie-break on
                    # mtime (oldest wins), NOT scan order: a copy is named
                    # "Foo 1.md"/"Foo copy.md" and space/dot ordering puts it
                    # BEFORE "Foo.md", so first-in-sort-order would hand the
                    # identity (and every subsequent write) to the copy.
                    prior_essay, prior_path, prior_mtime = prior
                    if stat.st_mtime < prior_mtime:
                        loser_path = prior_path          # this file is older: it wins
                        prior_essay["uid"] = ""
                        prior_essay["id"] = prior_essay["legacy_path_id"]
                    else:
                        loser_path = path                # the earlier claimant is older
                        uid = ""
                    note = f"duplicate {UID_KEY}; newer copy falls back to its path id"
                    failures.append({"file": public_path(loser_path), "error": note})
                    sys.stderr.write(f"[airdate] {note}: {public_path(loser_path)}\n")
            essay_id = uid or legacy_path_id
            tags = frontmatter.get("tags", [])
            if isinstance(tags, str):
                tags = clean_tags(tags)
            elif not isinstance(tags, list):
                tags = []
            word_count = len(re.findall(r"\b\w+\b", body))
            title = (
                str(frontmatter.get("title") or "").strip()
                or path.stem.replace("-", " ").replace("_", " ").strip()
                or "Untitled Essay"
            )
            collection = classify_essay(relative, title, frontmatter)
            if collection == "hidden":
                continue
            summary = (
                str(frontmatter.get("summary") or frontmatter.get("subtitle") or "").strip()
                or safe_excerpt(body, 170)
            )
            touched_days = days_since(touched)
            totem = infer_totem(frontmatter.get("totem") or frontmatter.get("element"), relative, title, tags, body)
            totem_raw = raw_totem(frontmatter)
            status = effective_status(relative, frontmatter)

            subtitle = str(frontmatter.get("subtitle") or "").strip()
            themes = frontmatter.get("themes") or []
            if isinstance(themes, str):
                themes = clean_tags(themes)
            source_role, is_long_source = source_role_for(relative, frontmatter, word_count)
            draft_of = str(frontmatter.get("draft_of") or "").strip()
            category = str(frontmatter.get("category") or "").strip()
            if not category and "/" in relative:
                top = relative.split("/", 1)[0]
                if top.lower() not in {"published", "archive"} and not top.startswith("_"):
                    category = top
            published_date = str(frontmatter.get("published_date") or "").strip()
            substack_url = str(frontmatter.get("substack_url") or "").strip()
            has_frontmatter = bool(frontmatter)
            # Intake is now a "needs filing" triage, not a status: an active
            # essay wants intake when it has no frontmatter or no category.
            needs_intake = collection == "active" and (
                not has_frontmatter or (CATEGORY_MODE == "folders" and not category)
            )
            publish_readiness = publish_readiness_for_card(
                frontmatter,
                body,
                title,
                subtitle,
                summary,
                tags,
                totem,
                source_role,
            )

            # Flatten every frontmatter value + title/summary/path into one
            # lowercased blob so the client can search across all frontmatter.
            blob_parts: list[str] = [
                title,
                summary,
                subtitle,
                relative,
                source_role,
                draft_of,
                publish_readiness.get("label", ""),
                publish_readiness.get("status", ""),
            ]
            blob_parts.extend(str(t) for t in tags)
            blob_parts.extend(str(t) for t in themes)
            blob_parts.extend(str(item) for item in publish_readiness.get("missing_metadata", []))
            blob_parts.extend(str(item) for item in publish_readiness.get("missing_images", []))
            for value in frontmatter.values():
                if isinstance(value, (list, tuple)):
                    blob_parts.extend(str(item) for item in value)
                else:
                    blob_parts.append(str(value))
            search_blob = " ".join(part for part in blob_parts if part).lower()

            essays.append(
                {
                    "id": essay_id,
                    "uid": uid,
                    "legacy_path_id": legacy_path_id,
                    "path": public_path(path),
                    "relative_path": relative,
                    "title": title,
                    "subtitle": subtitle,
                    "summary": summary,
                    "themes": themes,
                    "totem": totem,
                    # What the note itself says, for the card's totem slot:
                    # "" is unassigned, and a value outside the five is kept visible.
                    "totem_raw": totem_raw,
                    "status": status,
                    "collection": collection,
                    "category": category,
                    "published_date": published_date,
                    "substack_url": substack_url,
                    "scheduled_at": str(frontmatter.get("scheduled_at") or "").strip(),
                    # The room's fields. arrived_at drives the five ages of
                    # paper; excerpt is the line on the script page and is
                    # deliberately not `summary`; note_pad/note_color are the
                    # essay's own board note, falling back to app settings.
                    "arrived_at": arrival_for(essay_id, touched.isoformat()),
                    # When the post-it got its star, for the writers likey
                    # stamp. "" when never starred, or starred before airdate
                    # kept the date.
                    "starred_at": load_starred().get(essay_id, ""),
                    # Rainy day (slice 6): the phase an essay had just before it
                    # was parked, and when. Both live in frontmatter (they move
                    # with the file), unlike arrived_at/starred_at, which never
                    # touch the vault. "" off the Archive folder.
                    "previous_status": str(frontmatter.get("previous_status") or "").strip(),
                    "archived_at": str(frontmatter.get("archived_at") or "").strip(),
                    "excerpt": first_line_excerpt(body),
                    "substack_draft_url": str(frontmatter.get("substack_draft_url") or "").strip(),
                    "hero_url": hero_asset_url(frontmatter.get("hero_image") or frontmatter.get("hero")),
                    "note_pad": str(frontmatter.get("note_pad") or "").strip() or BOARD_DEFAULT_PAD,
                    "note_color": str(frontmatter.get("note_color") or "").strip() or BOARD_DEFAULT_COLOR,
                    "needs_intake": needs_intake,
                    "tags": tags,
                    "word_count": word_count,
                    "last_touched": touched.isoformat(),
                    "last_touched_human": f"{touched_days}d ago",
                    "obsidian_url": obsidian_url_for(relative),
                    "search_blob": search_blob,
                    "source_role": source_role,
                    "draft_of": draft_of,
                    "linked_drafts": [],
                    "linked_draft_count": 0,
                    "is_long_source": is_long_source,
                    "publish_readiness": publish_readiness,
                }
            )
            # Register both tokens so a uid and a legacy path-hash reference
            # (old bookmark, stale client state) both resolve to the same file.
            # When an older file reclaimed a uid above, this overwrites the
            # demoted copy's mapping so the id points at the original.
            id_map[essay_id] = path
            id_map[legacy_path_id] = path
            if uid:
                seen_uids[uid] = (essays[-1], path, stat.st_mtime)
        except FileNotFoundError:
            # Moved or deleted between the directory walk and the read (an
            # airdate write, Obsidian sync). Not unparseable: it is simply not
            # here now, and the next scan finds it wherever it went.
            continue
        except Exception as exc:  # noqa: BLE001
            # A single malformed essay must not blank the whole catalog — skip it,
            # but record + log it so the failure is visible, not silent (the old
            # bare `continue` is exactly how a parser bug once hid files).
            failures.append({"file": public_path(path), "error": str(exc)})
            sys.stderr.write(f"[airdate] skipped unparseable essay {public_path(path)}: {exc}\n")
            continue

    _LAST_SCAN_FAILURES = failures
    by_relative = {essay["relative_path"]: essay for essay in essays}
    for essay in essays:
        draft_of = essay.get("draft_of")
        if not draft_of:
            continue
        source = by_relative.get(str(draft_of))
        if not source:
            continue
        source.setdefault("linked_drafts", []).append({
            "id": essay["id"],
            "title": essay["title"],
            "relative_path": essay["relative_path"],
        })
        source["linked_draft_count"] = len(source.get("linked_drafts", []))

    return essays, id_map


# --- Essay index cache -------------------------------------------------------
# discover_essays() reads every markdown file in the vault. On an iCloud cold
# start that first pass can take ~2 minutes while files materialize, which left
# /airdate stuck on "ideas are loading…". The last successful scan is persisted
# to STATE_DIR so the API can answer from it immediately while a background
# thread re-scans (stale-while-revalidate). Delete the file to force a rescan.
ESSAY_INDEX_FILE = STATE_DIR / "essay_index.json"
ESSAY_INDEX_VERSION = 6  # 6: rows carry starred_at
ESSAY_REFRESH_MIN_INTERVAL = 5.0  # seconds between background re-scans

_essay_cache_cond = threading.Condition()
_essay_cache: dict[str, Any] | None = None  # {"essays", "id_map", "scanned_at"}
_essay_scan_inflight = False
_essay_last_scan_started = 0.0


def _load_persisted_essay_index() -> dict[str, Any] | None:
    try:
        raw = json.loads(ESSAY_INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != ESSAY_INDEX_VERSION:
        return None
    if raw.get("obsidian_dir") != str(OBSIDIAN_ESSAYS_DIR):
        return None  # index was built against a different vault
    if raw.get("config_fingerprint") != CONFIG_FINGERPRINT:
        return None  # totems, categories or hidden rules changed since
    essays = raw.get("essays")
    if not isinstance(essays, list):
        return None
    id_map: dict[str, Path] = {}
    for essay in essays:
        if not isinstance(essay, dict) or not essay.get("relative_path"):
            continue
        target = OBSIDIAN_ESSAYS_DIR / essay["relative_path"]
        # Register every token the live scan would (id, uid, legacy path hash) —
        # rebuilding from "id" alone dropped the aliases, so a legacy-hash
        # reference 404'd until a background rescan repopulated the map.
        for token in (essay.get("id"), essay.get("uid"), essay.get("legacy_path_id")):
            if token:
                id_map[str(token)] = target
    return {"essays": essays, "id_map": id_map, "scanned_at": str(raw.get("scanned_at") or "")}


def refresh_essay_index() -> tuple[list[dict[str, Any]], dict[str, Path]]:
    """Scan the vault now (blocking), then update the in-memory and on-disk cache."""
    global _essay_cache
    essays, id_map = discover_essays()
    scanned_at = datetime.now(timezone.utc).isoformat()
    with _essay_cache_cond:
        _essay_cache = {"essays": essays, "id_map": id_map, "scanned_at": scanned_at}
        _essay_cache_cond.notify_all()
    try:
        atomic_write_text(
            ESSAY_INDEX_FILE,
            json.dumps(
                {
                    "version": ESSAY_INDEX_VERSION,
                    "scanned_at": scanned_at,
                    "obsidian_dir": str(OBSIDIAN_ESSAYS_DIR),
                    "config_fingerprint": CONFIG_FINGERPRINT,
                    "essays": essays,
                },
                ensure_ascii=False,
            ),
        )
    except OSError:
        pass
    return essays, id_map


def schedule_essay_refresh(force: bool = False) -> None:
    """Kick off a background vault re-scan unless one is running or just ran."""
    global _essay_scan_inflight, _essay_last_scan_started
    with _essay_cache_cond:
        if _essay_scan_inflight:
            return
        now = time.monotonic()
        if not force and now - _essay_last_scan_started < ESSAY_REFRESH_MIN_INTERVAL:
            return
        _essay_scan_inflight = True
        _essay_last_scan_started = now

    def worker() -> None:
        global _essay_scan_inflight
        try:
            refresh_essay_index()
        except Exception:
            pass
        finally:
            with _essay_cache_cond:
                _essay_scan_inflight = False
                _essay_cache_cond.notify_all()

    threading.Thread(target=worker, name="essay-index-refresh", daemon=True).start()


def get_essay_index() -> tuple[list[dict[str, Any]], dict[str, Path]]:
    """Cached essay list + id->path map. Serves the last scan instantly and
    refreshes in the background; blocks on a full vault scan only when no
    cache exists in memory or on disk."""
    global _essay_cache
    with _essay_cache_cond:
        cache = _essay_cache
    if cache is None:
        loaded = _load_persisted_essay_index()
        if loaded is not None:
            with _essay_cache_cond:
                if _essay_cache is None:
                    _essay_cache = loaded
                cache = _essay_cache
    if cache is not None:
        schedule_essay_refresh()
        return cache["essays"], cache["id_map"]
    # No cache anywhere. If a scan is already in flight (startup warm-up),
    # wait for it instead of scanning the vault twice in parallel.
    with _essay_cache_cond:
        while _essay_scan_inflight and _essay_cache is None:
            _essay_cache_cond.wait()
        if _essay_cache is not None:
            return _essay_cache["essays"], _essay_cache["id_map"]
    return refresh_essay_index()


def essay_index_scanned_at() -> str:
    with _essay_cache_cond:
        return _essay_cache["scanned_at"] if _essay_cache else ""


def substack_status_payload() -> dict[str, Any]:
    publication = SUBSTACK_PUBLICATION
    publication_configured = publication_looks_configured(publication)
    connector = connector_status()
    paired = bool(connector_token())
    available = bool(connector.get("ok"))
    connected = bool(connector.get("connected"))
    return {
        "connected": connected,
        "connector": {
            "paired": paired,
            "available": available,
            "connected": connected,
            "connected_at": _stringify(connector.get("connected_at")),
        },
        "session_source": "obsidian_connector" if connected else "",
        "publication": publication,
        "publication_configured": publication_configured,
        "setup_checks": [
            {
                "key": "connector_paired",
                "label": "Obsidian connector paired",
                "ok": paired,
                "message": "" if paired else "Install the airdate connector in Obsidian and run \"Pair with airdate\".",
            },
            {
                "key": "substack_command",
                "label": "Obsidian connector running",
                "ok": available,
                "message": "" if available else "Open Obsidian with the airdate connector enabled.",
            },
            {
                "key": "substack_session",
                "label": "Substack session",
                "ok": connected,
                "message": "" if connected else "Connect Substack through Obsidian.",
            },
            {
                "key": "publication",
                "label": "Publication",
                "ok": publication_configured,
                "message": "" if publication_configured else "Set your Substack publication in settings.",
            },
        ],
    }


def local_app_url(path: str = "") -> str:
    display_host = "127.0.0.1" if HOST in {"0.0.0.0", "::"} else HOST
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    suffix = path if path.startswith("/") else f"/{path}" if path else ""
    return f"http://{display_host}:{PORT}{suffix}"


def room_redirect_location(query: str) -> str:
    """Where /airdate/room now sends a browser: /airdate, with the same query
    string, so an old ?essay= link still opens that essay. A header cannot
    carry a line break or other control character, so any are dropped."""
    clean = "".join(ch for ch in str(query or "") if ch.isprintable() and ch not in "\r\n")
    return f"/airdate?{clean}" if clean else "/airdate"


def collection_counts(essays: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"active": 0, "shelf": 0, "archived": 0}
    for essay in essays:
        bucket = str(essay.get("collection") or "active")
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def setup_payload() -> dict[str, Any]:
    vault = CONFIG.get("vault", {})
    return {
        "required": SETUP_REQUIRED,
        "config_exists": CONFIG_FILE_EXISTS,
        "load_error": CONFIG_LOAD_ERROR,
        "errors": CONFIG_ERRORS,
        "vault_ok": bool(VAULT_CHECK.get("vault_ok")),
        "essays_ok": bool(VAULT_CHECK.get("essays_ok")),
        "vault_message": VAULT_CHECK.get("vault_message", ""),
        "essays_message": VAULT_CHECK.get("essays_message", ""),
        "form": {
            "vault_path": str(vault.get("path") or "") if EXPOSE_LOCAL_PATHS else "",
            "vault_name": OBSIDIAN_VAULT_NAME if vault.get("path") else "",
            "essays_folder": ESSAYS_FOLDER,
            "publication": SUBSTACK_PUBLICATION,
            "publication_name": PUBLICATION_NAME,
            "publish_day": PUBLISH_DAY or "",
            "category_mode": CATEGORY_MODE,
            "editor_mode": editor_settings()["mode"],
            "board_default_pad": BOARD_DEFAULT_PAD,
            "board_default_color": BOARD_DEFAULT_COLOR,
            "board_weeks_shown": BOARD_WEEKS_SHOWN,
            "red_pen_enabled": RED_PEN_ENABLED,
            # The writer's own lines only, newline-joined for a textarea; the
            # twenty airdate ships are never editable here.
            "red_pen_lines": "\n".join(RED_PEN_LINES),
            "paper_fresh_on_promotion": PAPER_FRESH_ON_PROMOTION,
        },
        "paths_editable": EXPOSE_LOCAL_PATHS,
        # The wizard is for a fresh install. A config.json that exists but
        # needs fixing opens settings with its errors instead.
        "wizard": bool(SETUP_REQUIRED and not CONFIG_FILE_EXISTS and not CONFIG_LOAD_ERROR),
    }


def totem_image_url(key: str, vault_path: str, index: int) -> str:
    """Where a totem's art comes from, in order of preference."""
    if vault_path and resolve_vault_asset(vault_path) is not None:
        return f"/vault-asset/{urllib_parse_quote_path(vault_path)}"
    shipped = STATIC / "totems" / f"{key}-512.webp"
    if shipped.is_file():
        return f"/static/totems/{key}-512.webp"
    return f"/static/totems/placeholder-{index % 5 + 1}.svg"


def ui_config_payload() -> dict[str, Any]:
    """The writer's taxonomy and cadence, for the browser. No filesystem paths."""
    totems = []
    for index, (key, item) in enumerate(TOTEM_ITEMS.items()):
        image = str(item.get("image") or "").strip()
        totems.append({
            "key": key,
            "label": str(item.get("label") or key),
            "color": str(item.get("color") or ""),
            "role": str(item.get("role") or ""),
            # The writer's own vault art wins. Failing that, the art airdate
            # ships, so a fresh clone looks right instead of showing five grey
            # placeholders. The placeholder is the last resort, for a totem
            # key with no shipped art at all.
            "image": totem_image_url(key, image, index),
            "image_path": image,
        })
    return {
        "publication": SUBSTACK_PUBLICATION,
        "publication_name": PUBLICATION_NAME,
        "vault_name": OBSIDIAN_VAULT_NAME,
        "publish_day": PUBLISH_DAY,
        "totems": {"enabled": TOTEMS_ENABLED, "default": TOTEM_DEFAULT, "items": totems if TOTEMS_ENABLED else [], "slots": totems},
        "category_mode": CATEGORY_MODE,
        "tag_presets": [
            {
                "name": str(p.get("name")),
                "color": str(p.get("color") or ""),
                "tags": [str(t) for t in p.get("tags") or []][:5],
            }
            for p in TAG_PRESETS
        ],
        "links": [{"label": str(l["label"]), "url": str(l["url"])} for l in LINKS],
        # The twenty that ship, then the writer's own. The card picks one per
        # essay; the client decides which fit which paper.
        "red_pen": {"enabled": RED_PEN_ENABLED, "lines": [*RED_PEN_JABS, *RED_PEN_LINES]},
        # How many Mondays the board shows from this week, and the note an
        # essay gets when it has not chosen one.
        "board": {
            "weeks_shown": BOARD_WEEKS_SHOWN,
            "default_pad": BOARD_DEFAULT_PAD,
            "default_color": BOARD_DEFAULT_COLOR,
        },
        # What the essay editor shows, and how tall the writer left the script.
        "editor": editor_settings(),
    }


def editor_settings() -> dict[str, Any]:
    """The running editor settings, shaped for the browser."""
    editor = CONFIG.get("editor") if isinstance(CONFIG.get("editor"), dict) else {}
    mode = editor.get("mode") if editor.get("mode") in airdate_config.EDITOR_MODES else "simplified"
    sections = editor.get("sections") if isinstance(editor.get("sections"), dict) else {}
    height = editor.get("script_height")
    if not isinstance(height, int) or isinstance(height, bool) or not (
        airdate_config.SCRIPT_HEIGHT_MIN <= height <= airdate_config.SCRIPT_HEIGHT_MAX
    ):
        height = None
    return {
        "mode": mode,
        "sections": {str(k): v for k, v in sections.items() if isinstance(v, bool)},
        "script_height": height,
        # Anything but a real true reads as not shown, so a hand-edited value
        # shows the tour once more rather than never.
        "tour_done": editor.get("tour_done") is True,
    }


EDITOR_SETTING_KEYS = ("mode", "sections", "script_height", "tour_done")


def save_editor_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Change only editor.* in config.json: the script height the writer
    dragged to, the gear sheet's mode and sections, and that the editor tour
    has been shown. The
    rest of the file is carried over untouched and is not re-validated, so a
    remembered height never trips over an unrelated setting."""
    global _essay_cache
    unknown = sorted(key for key in payload if key not in EDITOR_SETTING_KEYS)
    if unknown:
        return {"ok": False, "errors": [f"only the editor's mode, sections, script height and tour change here, not {', '.join(unknown)}."]}
    if not payload:
        return {"ok": False, "errors": ["nothing to change."]}
    current, _, load_error = airdate_config.load_config(DATA_DIR)
    if load_error:
        raise ValueError(f"{load_error} Fix or remove the file, then try again.")
    editor = dict(current.get("editor") or {})
    for key in EDITOR_SETTING_KEYS:
        if key in payload:
            editor[key] = payload[key]
    errors = airdate_config.validate_editor(editor, known_sections_only=True)
    if errors:
        return {"ok": False, "errors": errors}
    current["editor"] = editor
    airdate_config.save_config(DATA_DIR, current)
    load_and_apply_config()
    return {"ok": True, "editor": editor_settings()}


def urllib_parse_quote_path(value: str) -> str:
    return "/".join(urllib.request.pathname2url(part) for part in value.split("/"))


def app_status_payload() -> dict[str, Any]:
    essays, _ = get_essay_index() if not SETUP_REQUIRED else ([], {})
    counts = collection_counts(essays)
    return {
        "ok": True,
        "app_name": "airdate",
        "host": HOST,
        "port": PORT,
        "base_url": local_app_url(),
        "app_url": local_app_url("/airdate"),
        "auth_required": AUTH_REQUIRED,
        "local_paths_visible": EXPOSE_LOCAL_PATHS,
        "obsidian_dir": public_path(OBSIDIAN_ESSAYS_DIR),
        "data_dir": public_path(DATA_DIR),
        "drafts_dir": public_path(DRAFTS),
        "essay_count": counts["active"],
        "collection_counts": counts,
        "scanned_at": essay_index_scanned_at(),
        "parse_failures": len(_LAST_SCAN_FAILURES),
        "parse_failure_files": [f["file"] for f in _LAST_SCAN_FAILURES],
        "setup": setup_payload(),
        "config": ui_config_payload(),
        "substack": substack_status_payload(),
    }


def save_settings(form: dict[str, Any]) -> dict[str, Any]:
    """The settings form (first run and after). Writes config.json, reloads the
    running settings and the essay index. Never writes to the vault."""
    global _essay_cache
    current, _, load_error = airdate_config.load_config(DATA_DIR)
    if load_error:
        raise ValueError(f"{load_error} Fix or remove the file, then try again.")
    if not EXPOSE_LOCAL_PATHS:
        form = {key: value for key, value in form.items() if key != "vault_path"}
    errors = airdate_config.form_errors(form)
    if errors:
        return {"ok": False, "errors": errors, "setup": setup_payload()}
    updated = airdate_config.settings_from_form(current, form)
    errors = airdate_config.validate_config(updated)
    if errors:
        return {"ok": False, "errors": errors, "setup": setup_payload()}
    airdate_config.save_config(DATA_DIR, updated)
    load_and_apply_config()
    with _essay_cache_cond:
        _essay_cache = None
    if not SETUP_REQUIRED:
        refresh_essay_index()
    return {"ok": True, "setup": setup_payload(), "config": ui_config_payload()}


def candidate_config(form: dict[str, Any]) -> dict[str, Any]:
    """The config a form would produce, with nothing saved."""
    current, _, load_error = airdate_config.load_config(DATA_DIR)
    if load_error:
        raise ValueError(f"{load_error} Fix or remove the file, then try again.")
    if not EXPOSE_LOCAL_PATHS:
        form = {key: value for key, value in form.items() if key != "vault_path"}
    return airdate_config.apply_env_overrides(airdate_config.settings_from_form(current, form))


def check_settings(form: dict[str, Any]) -> dict[str, Any]:
    """Validate a form without saving it: the wizard checks each step this
    way, so config.json is written once, at finish."""
    shape_errors = airdate_config.form_errors(form)
    if shape_errors:
        # Check the rest of the form as if the malformed fields were not sent,
        # so each step still hears about its own fields.
        form = {key: value for key, value in form.items()
                if not airdate_config.form_errors({key: value})}
    candidate = candidate_config(form)
    vault = candidate["vault"]
    raw_path = str(vault.get("path") or "").strip()
    check = airdate_config.check_vault(candidate)
    return {
        "ok": True,
        "errors": shape_errors + airdate_config.validate_config(candidate),
        "vault_ok": bool(check.get("vault_ok")),
        "essays_ok": bool(check.get("essays_ok")),
        "vault_message": check.get("vault_message", ""),
        "essays_message": check.get("essays_message", ""),
        "vault_name": str(vault.get("name") or "").strip(),
        "publication": str(candidate["substack"].get("publication") or ""),
        "connector_folder": str(Path(raw_path).expanduser() / ".obsidian" / "plugins" / "airdate-connector")
        if EXPOSE_LOCAL_PATHS and check.get("vault_ok") else "",
    }


def create_essays_folder(form: dict[str, Any] | None = None) -> dict[str, Any]:
    """The one vault write first run may make: an empty essays folder, inside
    a folder already confirmed to be an Obsidian vault. A form names the
    vault and folder before they are saved (the wizard); without one, the
    saved settings are used."""
    if form and ("vault_path" in form or "essays_folder" in form):
        candidate = candidate_config(form)
        errors = airdate_config.validate_config(candidate)
        if errors:
            raise ValueError(" ".join(errors))
        check = airdate_config.check_vault(candidate)
        vault_dir = Path(str(candidate["vault"].get("path") or "")).expanduser()
        folder = str(candidate["vault"].get("essays_folder") or "")
    else:
        check, vault_dir, folder = VAULT_CHECK, VAULT_DIR, ESSAYS_FOLDER
    if not check.get("vault_ok"):
        raise ValueError(check.get("vault_message") or "Choose your Obsidian vault folder first.")
    target = (vault_dir / folder).resolve()
    if vault_dir.resolve() not in target.parents:
        raise ValueError("The essays folder must be inside the vault.")
    target.mkdir(parents=True, exist_ok=True)
    load_and_apply_config()
    return {"ok": True, "setup": setup_payload()}


def resolve_vault_asset(relative: str) -> Path | None:
    """An image inside the configured vault, for totem art. Read-only."""
    if SETUP_REQUIRED or not relative:
        return None
    try:
        vault_root = VAULT_DIR.resolve()
        target = (vault_root / relative).resolve()
    except (OSError, ValueError):
        return None
    if vault_root not in target.parents or not target.is_file():
        return None
    if target.suffix.lower() not in VAULT_ASSET_SUFFIXES:
        return None
    if any(part.startswith(".") for part in target.relative_to(vault_root).parts):
        return None
    return target


def resolve_essay_path(essay_id: str) -> Path:
    """Resolve an essay id via the cached index, falling back to a fresh scan
    when the id is unknown or the cached path has vanished (moved/renamed)."""
    _, id_map = get_essay_index()
    path = id_map.get(essay_id)
    if path is None or not path.exists():
        _, id_map = refresh_essay_index()
        path = id_map.get(essay_id)
    if path is None and current_id(essay_id) != essay_id:
        path = id_map.get(current_id(essay_id))
    if path is None:
        raise FileNotFoundError("Essay not found")
    return path


class RefusedError(Exception):
    """airdate will not do this, and says why in a sentence the room shows.

    Its own type, not a ValueError, so every route answers it the same way:
    409 with {error, refused: <marker>}. The client tells it apart from a
    file-changed 409 by the `refused` key; the marker says which rule."""

    def __init__(self, sentence: str, marker: str):
        super().__init__(sentence)
        self.payload = {"error": sentence, "refused": marker, "ok": False}


class SchedulingRefusedError(RefusedError):
    """A write would put a writers room essay on the board."""

    def __init__(self, sentence: str):
        super().__init__(sentence, "writers-room")


class EssayConflictError(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("message", "Essay changed on disk."))
        self.payload = payload


def conflict_payload(essay_id: str, path: Path, text: str | None = None) -> dict[str, Any]:
    if text is None:
        text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = split_frontmatter(text)
    state = file_state(path, text)
    return {
        "ok": False,
        "reason": "file_changed",
        "message": "This essay changed in Obsidian after airdate loaded it. Reload before saving.",
        "essay_id": essay_id,
        "path": public_path(path),
        "current_frontmatter": frontmatter,
        "current_body": body,
        "current_mtime": state["mtime"],
        "current_mtime_iso": state["mtime_iso"],
        "current_content_hash": state["content_hash"],
    }


CONTENT_HASH_SHAPE = re.compile(r"^[0-9a-f]{64}$")


def parse_expected_file_state(expected_mtime: Any, expected_content_hash: Any) -> tuple[float | None, str]:
    """The file state a write names, checked for shape before anything is read.

    Absent (None or "") means the caller names no state. Anything present but
    malformed is a bad request: skipping the guard would let a write through
    that the caller meant to be conditional."""
    digest = ""
    if expected_content_hash not in (None, ""):
        if not isinstance(expected_content_hash, str) or not CONTENT_HASH_SHAPE.match(expected_content_hash):
            raise ValueError("expected_content_hash must be the 64-character hash airdate sent.")
        digest = expected_content_hash
    mtime: float | None = None
    if expected_mtime not in (None, ""):
        if isinstance(expected_mtime, bool) or not isinstance(expected_mtime, (int, float, str)):
            raise ValueError("expected_mtime must be the number airdate sent.")
        try:
            mtime = float(expected_mtime)
        except ValueError:
            raise ValueError("expected_mtime must be the number airdate sent.") from None
        if mtime != mtime or mtime in (float("inf"), float("-inf")):
            raise ValueError("expected_mtime must be the number airdate sent.")
    return mtime, digest


def assert_expected_file_state(
    essay_id: str,
    path: Path,
    text: str,
    expected_mtime: Any = None,
    expected_content_hash: Any = None,
) -> None:
    mtime, digest = parse_expected_file_state(expected_mtime, expected_content_hash)
    if digest:
        if content_hash(text) != digest:
            raise EssayConflictError(conflict_payload(essay_id, path, text))
        return
    if mtime is None:
        return
    if abs(path.stat().st_mtime - mtime) > 0.001:
        raise EssayConflictError(conflict_payload(essay_id, path, text))


# One lock for every write to the vault. The server answers each request on its
# own thread, and a write is a read, a check of the file state the caller named,
# then the write: without one lock two requests can both pass the check and the
# last one wins silently. Reentrant, because the routes are built on each other
# (park -> set_essay_status -> save_essay_updates).
VAULT_WRITE_LOCK = threading.RLock()


def vault_write(func):
    """Run a vault write path under VAULT_WRITE_LOCK, check and write together."""
    @functools.wraps(func)
    def locked(*args: Any, **kwargs: Any) -> Any:
        with VAULT_WRITE_LOCK:
            return func(*args, **kwargs)
    locked.__vault_write__ = True  # type: ignore[attr-defined]
    return locked


# Ids a write retired, old -> new: a uid mint (path hash -> uid) or a folder
# move (old path hash -> new). A request that names the old id after the write
# that retired it (a double click, two tabs) still finds the note, instead of
# a 404. Memory only; the client remaps from new_id on its next answer.
_RETIRED_IDS: dict[str, str] = {}
_RETIRED_IDS_MAX = 2000


def retire_id(old_id: str, new_id: str) -> None:
    old_key, new_key = str(old_id or "").strip(), str(new_id or "").strip()
    if not old_key or not new_key or old_key == new_key:
        return
    with VAULT_WRITE_LOCK:
        _RETIRED_IDS[old_key] = new_key
        while len(_RETIRED_IDS) > _RETIRED_IDS_MAX:
            _RETIRED_IDS.pop(next(iter(_RETIRED_IDS)))


def current_id(essay_id: str) -> str:
    """Follow retired ids to the one a note answers to now."""
    seen = set()
    key = str(essay_id or "")
    while key in _RETIRED_IDS and key not in seen:
        seen.add(key)
        key = _RETIRED_IDS[key]
    return key


def warm_essay_index() -> int | None:
    """Load the persisted index at startup and kick a background re-scan.
    Returns the cached essay count, or None when no usable cache exists."""
    global _essay_cache
    loaded = _load_persisted_essay_index()
    if loaded is not None:
        with _essay_cache_cond:
            _essay_cache = loaded
    schedule_essay_refresh(force=True)
    return len(loaded["essays"]) if loaded is not None else None


def get_essay_detail(essay_id: str) -> dict[str, Any]:
    essays, id_map = get_essay_index()
    path = id_map.get(essay_id)
    if not path or not path.exists():
        essays, id_map = refresh_essay_index()
        path = id_map.get(essay_id)
    if not path:
        raise FileNotFoundError("Essay not found")
    # Match on any token that resolves to this file — a legacy path-hash deep
    # link must still hit the cached summary, not silently fall back to a full
    # re-derive from disk.
    summary = next(
        (essay for essay in essays
         if essay_id in (essay.get("id"), essay.get("uid"), essay.get("legacy_path_id"))),
        None,
    )
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = split_frontmatter(text)
    state = file_state(path, text)
    touched_days = days_since(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
    word_count = summary["word_count"] if summary else len(re.findall(r"\b\w+\b", body))
    source_role, is_long_source = source_role_for(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter, word_count)
    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        tags = clean_tags(tags)
    elif not isinstance(tags, list):
        tags = []
    title = summary["title"] if summary else path.stem
    summary_text = summary["summary"] if summary else (
        str(frontmatter.get("summary") or frontmatter.get("subtitle") or "").strip()
        or safe_excerpt(body, 170)
    )
    subtitle = summary.get("subtitle", str(frontmatter.get("subtitle") or "").strip()) if summary else str(frontmatter.get("subtitle") or "").strip()
    totem = summary["totem"] if summary else infer_totem(frontmatter.get("totem") or frontmatter.get("element"), path.name, path.stem, tags if isinstance(tags, list) else [], body)
    publish_readiness = summary.get("publish_readiness") if summary else None
    if not isinstance(publish_readiness, dict):
        publish_readiness = publish_readiness_for_card(frontmatter, body, title, subtitle, summary_text, tags, totem, source_role)
    detail_uid = frontmatter_uid(frontmatter)
    return {
        # Report the canonical id, not whichever token the caller happened to use.
        "id": (summary or {}).get("id") or detail_uid or essay_id,
        "uid": detail_uid,
        "legacy_path_id": essay_id_for(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()),
        "path": public_path(path),
        "absolute_path": str(path) if EXPOSE_LOCAL_PATHS else "",
        "relative_path": summary["relative_path"] if summary else path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(),
        "title": title,
        "summary": summary_text,
        "word_count": word_count,
        "status": summary["status"] if summary else effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter),
        "totem": totem,
        "frontmatter": frontmatter,
        "metadata_defaults": publish_readiness.get("defaults", {}),
        "publish_readiness": publish_readiness,
        "body": body,
        "body_preview": safe_excerpt(body, 350),
        "mtime": state["mtime"],
        "mtime_iso": state["mtime_iso"],
        "content_hash": state["content_hash"],
        "source_role": summary.get("source_role", source_role) if summary else source_role,
        "draft_of": summary.get("draft_of", str(frontmatter.get("draft_of") or "").strip()) if summary else str(frontmatter.get("draft_of") or "").strip(),
        "linked_drafts": summary.get("linked_drafts", []) if summary else [],
        "linked_draft_count": summary.get("linked_draft_count", 0) if summary else 0,
        "is_long_source": summary.get("is_long_source", is_long_source) if summary else is_long_source,
        "obsidian_url": obsidian_url_for(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()),
        "hero_url": hero_asset_url(frontmatter.get("hero_image") or frontmatter.get("hero")),
        # The catalog row, for what only the index knows: when the essay
        # arrived and when it was starred, which the editor's stamp shows.
        "row": summary,
    }


def hero_asset_url(value: Any) -> str:
    """The hero image as a URL the editor can show, or "" when it is not a
    file airdate can find. attach-hero writes the path relative to the essays
    folder; a writer may have typed one relative to the vault. Both are tried,
    the essays folder first."""
    raw = _stringify(value).strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    relative = raw[2:] if raw.startswith("./") else raw.lstrip("/")
    try:
        essays_prefix = OBSIDIAN_ESSAYS_DIR.resolve().relative_to(VAULT_DIR.resolve()).as_posix()
    except (OSError, ValueError):
        essays_prefix = ""
    candidates = []
    if essays_prefix and essays_prefix != ".":
        candidates.append(f"{essays_prefix}/{relative}")
    candidates.append(relative)
    for candidate in candidates:
        if resolve_vault_asset(candidate) is not None:
            return f"/vault-asset/{urllib_parse_quote_path(candidate)}"
    return ""


def sanitize_updates(updates: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in EDITOR_ALLOWED_FIELDS:
            continue
        if isinstance(value, str):
            value = value.strip()
        cleaned[key] = value

    if isinstance(cleaned.get("tags"), str):
        cleaned["tags"] = clean_tags(cleaned["tags"])
    if isinstance(cleaned.get("test_email_recipients"), str):
        cleaned["test_email_recipients"] = clean_tags(cleaned["test_email_recipients"])

    if "totem" in cleaned:
        # Totems off, or a value that is not a configured totem: drop it so the
        # note's existing frontmatter is preserved, never overwritten.
        totem_value = str(cleaned.get("totem") or "").strip().lower()
        if TOTEMS_ENABLED and totem_value in TOTEM_ITEMS:
            cleaned["totem"] = totem_value
        elif TOTEMS_ENABLED and cleaned.get("totem") == CLEAR_TOTEM:
            # The writer picked "none": apply_updates removes a blank key.
            cleaned["totem"] = ""
        else:
            cleaned.pop("totem")
    # The board note. A value the board cannot draw is refused, never stored
    # and never silently dropped: the writer picked it, so they hear why.
    # Blank clears the key, and the note falls back to the settings default.
    for key, allowed, sentence in (
        ("note_pad", NOTE_PADS, "a note pad is sticky, paper or index card."),
        ("note_color", NOTE_COLORS, "a note color is canary, blue, orange, pink or green."),
    ):
        if key not in cleaned:
            continue
        value = cleaned[key]
        if value is None or value == "":
            cleaned[key] = ""
            continue
        if not isinstance(value, str) or value.strip().lower() not in allowed:
            raise ValueError(sentence)
        cleaned[key] = value.strip().lower()
    if "status" in cleaned and cleaned.get("status"):
        cleaned["status"] = normalize_status(cleaned.get("status"))
    if "source_role" in cleaned and cleaned.get("source_role"):
        cleaned["source_role"] = normalize_source_role(cleaned.get("source_role")) or "standalone"

    if cleaned.get("scheduled_at") and not cleaned.get("status"):
        cleaned["status"] = "Ready for Air"

    return cleaned


def apply_updates(frontmatter: dict[str, Any], updates: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    merged = dict(frontmatter)
    changes: dict[str, dict[str, Any]] = {}

    for key, value in updates.items():
        before = merged.get(key)
        if value in (None, "", []):
            if key in merged:
                merged.pop(key, None)
                changes[key] = {"before": before, "after": None}
            continue
        merged[key] = value
        if before != value:
            changes[key] = {"before": before, "after": value}

    return merged, changes


def preview_essay_updates(essay_id: str, updates: dict[str, Any], body: str | None = None) -> dict[str, Any]:
    path = resolve_essay_path(essay_id)

    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, current_body = split_frontmatter(text)
    sanitized = sanitize_updates(updates)
    merged, changes = apply_updates(frontmatter, sanitized)
    preview_body = current_body if body is None else normalize_body(body)
    preview_markdown = compose_markdown(merged, preview_body)

    return {
        "ok": True,
        "essay_id": essay_id,
        "path": public_path(path),
        "changes": changes,
        "frontmatter": merged,
        "body": preview_body,
        "preview_markdown": preview_markdown,
    }


@vault_write
def save_essay_updates(
    essay_id: str,
    updates: dict[str, Any],
    body: str | None = None,
    expected_mtime: Any = None,
    expected_content_hash: Any = None,
) -> dict[str, Any]:
    path = resolve_essay_path(essay_id)

    text = path.read_text(encoding="utf-8", errors="ignore")
    assert_expected_file_state(essay_id, path, text, expected_mtime, expected_content_hash)
    frontmatter, current_body = split_frontmatter(text)
    sanitized = sanitize_updates(updates)
    # The scheduling gate. It lives here, at the write, rather than on one
    # route: /save reaches Ready for Air too (sanitize_updates promotes a bare
    # scheduled_at to Ready for Air), so one rule at the write closes every
    # path, including routes that do not exist yet.
    if sanitized.get("status") == "Ready for Air":
        current = effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter)
        refusal = scheduling_refusal(current)
        if refusal:
            raise SchedulingRefusedError(refusal)
    if sanitized.get("status") == "Live":
        # A live essay has no rainy-day phase to go back to, whichever route
        # took it live. Blank removes the keys.
        sanitized["previous_status"] = ""
        sanitized["archived_at"] = ""
    # Durable identity, stamped lazily on the write this call already performs
    # (no extra write, no extra mtime bump). Assigned AFTER sanitize_updates so a
    # client payload can never set or overwrite uid — "uid" is deliberately not
    # in EDITOR_ALLOWED_FIELDS, so any client-sent uid was already dropped. The
    # guard makes it write-once: an existing uid is never touched.
    minted_uid = not frontmatter_uid(frontmatter)
    if minted_uid:
        sanitized[UID_KEY] = uuid.uuid4().hex
    merged, changes = apply_updates(frontmatter, sanitized)

    if frontmatter_bounds(text) is None:
        # No frontmatter to preserve — build one fresh (legacy path). Using
        # frontmatter_bounds (not startswith '---\n') means BOM/CRLF files are
        # recognized as having frontmatter and edited surgically, not corrupted.
        new_text = compose_markdown(merged, current_body if body is None else normalize_body(body))
    else:
        # Surgical edit: touch only changed keys, keep the rest byte-for-byte.
        new_text = apply_frontmatter_edits(text, changes)
        if body is not None:
            bounds = frontmatter_bounds(new_text)
            if bounds is None:
                new_text = compose_markdown(merged, normalize_body(body))
            else:
                close_start, marker = bounds[1], bounds[2]
                new_text = new_text[: close_start + len(marker)] + "\n" + normalize_body(body).rstrip() + "\n"

    atomic_write_text(path, new_text)
    if minted_uid:
        # The id changes from the path hash to the uid on this write. Carry the
        # arrival across BEFORE the rescan, which would otherwise find no entry
        # under the new id and record now - handing an old essay fresh paper on
        # its very first write, which is usually the star.
        carry_arrival(essay_id, sanitized[UID_KEY])
        carry_star(essay_id, sanitized[UID_KEY])
        retire_id(essay_id, sanitized[UID_KEY])
        # The essay's id flips here (path hash -> uid). Refresh blocking, as the
        # other re-id sites do, so the client's very next /api/essays already
        # reports the canonical id — a background rescan loses that race and the
        # client would rekey local state to an id the catalog hasn't adopted yet.
        # Write-once, so this costs one blocking scan per essay, ever.
        refresh_essay_index()
    else:
        schedule_essay_refresh(force=True)
    state = file_state(path, new_text)
    # The file does not move here, so its canonical id after the write is its uid
    # (freshly stamped or pre-existing). When a stamp just happened the caller was
    # holding the path hash, so report the transition and let the client remap.
    new_id = frontmatter_uid(merged) or essay_id
    return {
        "ok": True,
        "essay_id": essay_id,
        "old_id": essay_id,
        "new_id": new_id,
        "path": public_path(path),
        "changes": changes,
        "saved_frontmatter": merged,
        "body_saved": body is not None,
        "mtime": state["mtime"],
        "mtime_iso": state["mtime_iso"],
        "content_hash": state["content_hash"],
    }


def safe_filename_stem(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", str(value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120].strip(" .") or "Untitled Draft"


def unique_markdown_path(folder: Path, stem: str) -> Path:
    safe_stem = safe_filename_stem(stem)
    path = folder / f"{safe_stem}.md"
    counter = 2
    while path.exists():
        path = folder / f"{safe_stem} {counter}.md"
        counter += 1
    return path


def move_essay_file(path: Path, target_dir: Path) -> Path:
    """Move an essay into target_dir (same volume, atomic rename). Returns the
    new path; a collision gets a numbered name via unique_markdown_path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    if path.parent.resolve() == target_dir.resolve():
        return path
    dest = target_dir / path.name
    if dest.exists():
        dest = unique_markdown_path(target_dir, path.stem)
    path.rename(dest)
    return dest


def folder_for_status(status: str, frontmatter: dict[str, Any], relative_path: str) -> Path | None:
    """Where a file belongs for its status; None = leave it where it is.
    Live/Archived pull files into their folders; a working status pulls a
    shelved/archived file back out into its category folder (stamped on the way
    in). The folders keep their old names — a live essay still lands in
    Published/ — because renaming them would move every essay on disk to buy
    nothing the writer can see."""
    top = relative_path.split("/", 1)[0].lower() if "/" in relative_path else ""
    in_published = top == "published"
    in_archive = top == "archive"
    if status == "Live":
        return None if in_published else OBSIDIAN_ESSAYS_DIR / "Published"
    if status == "Archived":
        return None if in_archive else OBSIDIAN_ESSAYS_DIR / "Archive"
    if in_published or in_archive:
        category = str(frontmatter.get("category") or "").strip() if CATEGORY_MODE == "folders" else ""
        if category and "/" not in category and "\\" not in category and not category.startswith("."):
            return OBSIDIAN_ESSAYS_DIR / category
        return OBSIDIAN_ESSAYS_DIR
    return None


@vault_write
def set_essay_status(
    essay_id: str,
    status_value: Any,
    extra_updates: dict[str, Any] | None = None,
    expected_mtime: Any = None,
    expected_content_hash: Any = None,
) -> dict[str, Any]:
    """Write status (+ extras), then enforce the status->folder policy.
    Frontmatter first, move second: if the move fails the status alone still
    classifies the essay correctly, and re-running retries the move."""
    if not str(status_value or "").strip():
        raise ValueError("status is required")
    status = normalize_status(status_value)
    updates = {"status": status}
    if extra_updates:
        updates.update(extra_updates)
    save_essay_updates(essay_id, updates, None, expected_mtime, expected_content_hash)

    path = resolve_essay_path(essay_id)
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, _ = split_frontmatter(text)
    relative = path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
    target = folder_for_status(status, frontmatter, relative)

    moved = False
    warning = ""
    new_path = path
    if target is not None:
        try:
            new_path = move_essay_file(path, target)
            moved = new_path != path
        except OSError as exc:
            warning = without_local_paths(f"Status saved, but the file move failed: {exc}")

    refresh_essay_index()  # blocking, so the path-derived id resolves immediately
    new_relative = new_path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
    # The uid survives the move (save_essay_updates above stamped it if absent),
    # so a uid'd essay keeps its id across a folder move — no client remap needed.
    new_id = frontmatter_uid(frontmatter) or essay_id_for(new_relative)
    retire_id(essay_id, new_id)
    new_text = new_path.read_text(encoding="utf-8", errors="ignore")
    state = file_state(new_path, new_text)
    result: dict[str, Any] = {
        "ok": True,
        "old_id": essay_id,
        "new_id": new_id,
        "moved": moved,
        "status": status,
        "relative_path": new_relative,
        "path": public_path(new_path),
        "mtime": state["mtime"],
        "mtime_iso": state["mtime_iso"],
        "content_hash": state["content_hash"],
    }
    if warning:
        result["warning"] = warning
    try:
        result["essay"] = get_essay_detail(new_id)
    except FileNotFoundError:
        pass
    return result


NOT_A_POST_LINK = "that is not a substack post link."
POST_PATH_SHAPE = re.compile(r"^/p/[^/\s]+/?$")
DNS_LABEL_SHAPE = re.compile(r"^[a-z0-9-]+$")


def is_dns_host(host: str) -> bool:
    """A host made of DNS labels: letters, digits and hyphens, none empty."""
    labels = str(host or "").split(".")
    return len(labels) >= 2 and all(DNS_LABEL_SHAPE.match(label) for label in labels)


def _bare_host(value: str) -> str:
    """The host of a publication setting, lowercased, without a leading www."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = f"https://{raw}"
    try:
        host = (urlparse(raw).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def live_link_refusal(url: Any) -> str | None:
    """Why a pasted link cannot mark an essay live, or None if it can.

    Checked by shape and never fetched, so airdate never claims live about a
    post it cannot see. A post link is https, on substack.com, a subdomain of
    it, or the writer's own custom domain from settings (with or without www),
    and names one post: the path is /p/<slug>, with a query or fragment
    allowed, so two links glued together are out. The host is compared whole,
    never as a substring, and must be DNS labels, so substack.com.evil.test, a
    user@host prefix, an empty label and a %-escaped host are all out."""
    text = str(url or "").strip()
    if not text:
        return NOT_A_POST_LINK
    try:
        parsed = urlparse(text)
        port = parsed.port
    except ValueError:
        return NOT_A_POST_LINK
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
        return NOT_A_POST_LINK
    if port not in (None, 443):
        return NOT_A_POST_LINK
    host = (parsed.hostname or "").lower().rstrip(".")
    if not is_dns_host(host):
        return NOT_A_POST_LINK
    custom = _bare_host(SUBSTACK_PUBLICATION)
    on_substack = host == "substack.com" or host.endswith(".substack.com")
    on_custom = bool(custom) and host in (custom, f"www.{custom}")
    if not (on_substack or on_custom):
        return NOT_A_POST_LINK
    if not POST_PATH_SHAPE.match(parsed.path or ""):
        return NOT_A_POST_LINK
    return None


def air_day_of(value: Any) -> str:
    """The calendar day of an air date ("2026-09-14" from "2026-09-14T09:00"),
    or "" when there is none. No time zone arithmetic: the board wrote a day."""
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(value or "").strip())
    if not match:
        return ""
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d")
    except ValueError:
        return ""
    return match.group(1)


def with_index_row(result: dict[str, Any]) -> dict[str, Any]:
    """Add the fresh catalog row, which is what the room renders from."""
    new_id = result.get("new_id") or result.get("old_id") or ""
    result["row"] = index_row_for(str(new_id)) if new_id else None
    return result


@vault_write
def publish_essay(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Bookkeeping for a publish the writer performed on Substack themselves:
    stamp the live URL + date, set status Live, move the file to Published/.
    The app itself never publishes anything.

    Going live stamps the air date, not today: the essay aired on its Monday,
    whenever the writer got round to pasting the link. Today is the fallback
    only for an essay that never had an air date. The air date itself stays.
    A rainy-day essay is refused: it comes back to the room first. The
    parking stamps are cleared at the write (save_essay_updates), as for every
    route to live."""
    substack_url = str(payload.get("substack_url") or "").strip()
    refusal = live_link_refusal(substack_url)
    if refusal:
        raise ValueError(refusal)
    path = resolve_essay_path(essay_id)
    frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
    current = effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter)
    if current == "Archived":
        raise RefusedError(SCHEDULE_REFUSALS["Archived"], "archived")
    published_date = air_day_of(frontmatter.get("scheduled_at"))
    if not published_date:
        published_date = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    return with_index_row(set_essay_status(
        essay_id,
        "Live",
        {"published_date": published_date, "substack_url": substack_url},
        payload.get("expected_mtime"),
        payload.get("expected_content_hash"),
    ))


# Why the board will not take an essay in these phases. Writers room has its
# own sentence at the write (scheduling_refusal); these two are past the board
# rather than before it.
SCHEDULE_REFUSALS = {
    "Live": "it is live already, so it stays on the shelf.",
    "Archived": "it is saved for a rainy day. bring it back to the room first.",
}

AIR_DATE_SHAPE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(T(?P<hour>\d{2}):(?P<minute>\d{2})(:(?P<second>\d{2})(\.\d+)?)?"
    r"(Z|[+-](?P<tzh>\d{2}):(?P<tzm>\d{2}))?)?$"
)


def air_date_is_valid(value: str) -> bool:
    """A real calendar date, and when it carries a time, a real clock time."""
    match = AIR_DATE_SHAPE.match(value)
    if not match or not air_day_of(value):
        return False
    limits = {"hour": 23, "minute": 59, "second": 59, "tzh": 23, "tzm": 59}
    return all(match.group(name) is None or int(match.group(name)) <= top for name, top in limits.items())


@vault_write
def schedule_essay(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Put an essay on the board: write scheduled_at and move it to Ready for Air.

    A date that is not a real calendar date is a bad request (ValueError, 400).
    A live or rainy-day essay is refused with a sentence (409, refused). A
    writers room essay is refused at the write, in save_essay_updates, which
    is the one rule every route passes through. Taken and past weeks are the
    client's to refuse: the server does not know which week the writer meant."""
    scheduled_at = str(payload.get("scheduled_at") or payload.get("scheduledAt") or "").strip()
    if not scheduled_at:
        raise ValueError("scheduled_at (an air date) is required")
    if not air_date_is_valid(scheduled_at):
        raise ValueError("scheduled_at must be a calendar date, like 2026-10-05.")
    path = resolve_essay_path(essay_id)
    frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
    current = effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter)
    if current in SCHEDULE_REFUSALS:
        raise RefusedError(SCHEDULE_REFUSALS[current], current.lower())
    return with_index_row(set_essay_status(
        essay_id,
        "Ready for Air",
        {"scheduled_at": scheduled_at},
        payload.get("expected_mtime"),
        payload.get("expected_content_hash"),
    ))


@vault_write
def unschedule_essay(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Clear the air date and drop back to Writers Likey, same folder, with the
    draft link and sent marker kept. Only an essay on the board can come off
    it; anything else is refused with a sentence, so a direct POST cannot pull
    a live essay off the shelf or a parked one out of the rain. An undo of a
    schedule sends the hash the schedule answered with, so a stale or doubled
    undo is a conflict."""
    path = resolve_essay_path(essay_id)
    frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
    current = effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter)
    if current != "Ready for Air":
        raise RefusedError("it is not on the board, so there is nothing to take down.", "not-on-the-board")
    return with_index_row(set_essay_status(
        essay_id,
        "Writers Likey",
        {"scheduled_at": ""},
        payload.get("expected_mtime"),
        payload.get("expected_content_hash"),
    ))


@vault_write
def did_not_air(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The board note's "it did not air": the Monday passed and the essay did
    not go out. Back to writers likey with its star date, and the slot stays
    empty. Only an essay on the board can not-air; unschedule_essay holds
    that rule."""
    return unschedule_essay(essay_id, payload)


# Why the umbrella cannot park an essay in these phases. The umbrella is only
# drawn on writers room and writers likey cards (and the editor header), but
# the server is the rule: a direct POST cannot park a scheduled or live essay
# either. Unschedule first.
PARK_REFUSALS = {
    "Ready for Air": "it is on the board, so it can't be parked. unschedule it first.",
    "Live": "it is live, so it stays on the shelf.",
}


@vault_write
def park_essay(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Save an essay for a rainy day: Archived, with the phase it had and the
    moment it was parked stamped into frontmatter so "back to the room" can
    restore it exactly, even if the runtime sidecars were ever lost.

    Idempotent: parking an already-parked essay is a no-op success, like the
    star's double-press, and the stamps are left untouched so a second park
    does not overwrite the phase the writer actually came from."""
    path = resolve_essay_path(essay_id)
    frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
    current = effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter)
    if current in PARK_REFUSALS:
        raise RefusedError(PARK_REFUSALS[current], current.lower().replace(" ", "-"))
    if current == "Archived":
        # The id the note answers to now, which is not the one the caller
        # holds when the first park minted a uid and moved the file.
        new_id = frontmatter_uid(frontmatter) or essay_id_for(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix())
        row = index_row_for(new_id)
        return {"ok": True, "changed": False, "status": "Archived",
                "old_id": essay_id, "new_id": (row or {}).get("id") or new_id, "row": row}
    archived_at = datetime.now(timezone.utc).isoformat()
    return with_index_row(set_essay_status(
        essay_id,
        "Archived",
        {"previous_status": current, "archived_at": archived_at},
        payload.get("expected_mtime"),
        payload.get("expected_content_hash"),
    ))


@vault_write
def back_to_room(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Undo a parking: restore the phase stamped by park_essay (falling back
    to Writers Room when it is absent or not a phase a parked essay can come
    back as — the writer never got to leave the room any other way), and
    clear both stamps. folder_for_status pulls the file back out of Archive/
    on the same write; the client never reconstructs the phase itself."""
    path = resolve_essay_path(essay_id)
    frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
    current = effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter)
    if current != "Archived":
        raise RefusedError("it is not saved for a rainy day, so there is nothing to bring back.", "not-parked")
    restored_raw = str(frontmatter.get("previous_status") or "").strip()
    restored = normalize_status(restored_raw) if restored_raw else "Writers Room"
    if restored not in ("Writers Room", "Writers Likey"):
        restored = "Writers Room"
    return with_index_row(set_essay_status(
        essay_id,
        restored,
        {"previous_status": "", "archived_at": ""},
        payload.get("expected_mtime"),
        payload.get("expected_content_hash"),
    ))


def category_folders() -> list[str]:
    """Categories are the essays folder's top-level folders plus any named in
    config, minus airdate's reserved folders and `_` folders. Empty when
    categories are off."""
    if CATEGORY_MODE != "folders":
        return []
    names = set(CATEGORY_KEYWORDS)
    try:
        for child in OBSIDIAN_ESSAYS_DIR.iterdir():
            if child.is_dir() and not child.name.startswith((".", "_")) and child.name.lower() not in airdate_config.RESERVED_FOLDERS:
                names.add(child.name)
    except OSError:
        pass
    return sorted(names, key=str.lower)


def suggest_category(relative_path: str, title: str, tags: list[str], body_text: str) -> tuple[str, str]:
    """Score an essay against the category folders. Returns (category, confidence)."""
    hay = " ".join(
        [
            relative_path.lower(),
            title.lower(),
            " ".join(str(tag).lower() for tag in tags),
            safe_excerpt(body_text, 600).lower(),
        ]
    )
    if not CATEGORY_KEYWORDS:
        return "", "low"
    scores = {name: 0 for name in CATEGORY_KEYWORDS}
    for name, markers in CATEGORY_KEYWORDS.items():
        for marker, weight in markers.items():
            if marker in hay:
                scores[name] += weight
    best, score = sorted(scores.items(), key=lambda item: item[1], reverse=True)[0]
    if score <= 0:
        return "", "low"
    confidence = "high" if score >= 5 else "medium" if score >= 2 else "low"
    return best, confidence


def intake_suggest(essay_id: str) -> dict[str, Any]:
    path = resolve_essay_path(essay_id)
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = split_frontmatter(text)
    relative = path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        tags = clean_tags(tags)
    elif not isinstance(tags, list):
        tags = []
    title = str(frontmatter.get("title") or "").strip() or path.stem
    category, confidence = suggest_category(relative, title, tags, body)
    totem = infer_totem(frontmatter.get("totem") or frontmatter.get("element"), relative, title, tags, body)
    return {
        "ok": True,
        "essay_id": essay_id,
        "title": title,
        "category": category,
        "category_confidence": confidence,
        "categories": category_folders(),
        "category_mode": CATEGORY_MODE,
        "totem": totem,
        "status_suggestion": "Writers Room",
        "has_frontmatter": bool(frontmatter),
    }


# Why filing will not touch an essay in these phases. Their folders
# (Published/, Archive/) are airdate's own, so a topic folder is not theirs.
INTAKE_REFUSALS = {
    "Live": "it is live, so it stays on the shelf.",
    "Archived": "it is saved for a rainy day. bring it back to the room first.",
}
INTAKE_STATUSES = ("Writers Room", "Writers Likey")


@vault_write
def intake_apply(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Confirmed intake: complete the frontmatter and sort the file into its
    category folder. Works on files with no frontmatter at all.

    Filing is not a phase change. The note keeps the phase it has and every
    lifecycle field with it (scheduled_at, the star, the sent marker), so a
    starred essay stays starred and a scheduled one stays on the board. Only a
    note with no status at all is given one: writers room, or the room phase
    the request names. Live and rainy-day essays are not filed: their folders
    are airdate's own."""
    category = str(payload.get("category") or "").strip()
    if CATEGORY_MODE == "folders":
        if not category or "/" in category or "\\" in category or category.startswith((".", "_")):
            raise ValueError("category must be a plain folder name")
        if category.lower() in airdate_config.RESERVED_FOLDERS:
            raise ValueError(f"{category} is a folder airdate manages; pick a category folder.")
    requested = payload.get("status")
    if requested not in (None, ""):
        if not isinstance(requested, str) or requested.strip() not in INTAKE_STATUSES:
            raise ValueError("a filed note can start in writers room or writers likey only.")
        requested = requested.strip()
    path = resolve_essay_path(essay_id)
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, _ = split_frontmatter(text)
    current = effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter)
    if current in INTAKE_REFUSALS:
        raise RefusedError(INTAKE_REFUSALS[current], current.lower())
    updates: dict[str, Any] = {}
    if not str(frontmatter.get("status") or "").strip():
        updates["status"] = requested or "Writers Room"
    if CATEGORY_MODE == "folders":
        updates["category"] = category
    if payload.get("totem"):
        updates["totem"] = payload.get("totem")
    if not str(frontmatter.get("title") or "").strip():
        updates["title"] = path.stem
    save_essay_updates(essay_id, updates, None, payload.get("expected_mtime"), payload.get("expected_content_hash"))

    path = resolve_essay_path(essay_id)
    new_path = move_essay_file(path, OBSIDIAN_ESSAYS_DIR / category) if CATEGORY_MODE == "folders" else path
    refresh_essay_index()
    new_relative = new_path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
    # Re-read post-save: the frontmatter above predates the uid stamp that
    # save_essay_updates just applied, so the uid is only visible from disk now.
    moved_frontmatter, _ = split_frontmatter(new_path.read_text(encoding="utf-8", errors="ignore"))
    new_id = frontmatter_uid(moved_frontmatter) or essay_id_for(new_relative)
    retire_id(essay_id, new_id)
    result: dict[str, Any] = {
        "ok": True,
        "old_id": essay_id,
        "new_id": new_id,
        "moved": new_path != path,
        "relative_path": new_relative,
        "path": public_path(new_path),
    }
    try:
        result["essay"] = get_essay_detail(new_id)
    except FileNotFoundError:
        pass
    return result


@vault_write
def create_linked_draft(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_path = resolve_essay_path(essay_id)
    source_text = source_path.read_text(encoding="utf-8", errors="ignore")
    source_frontmatter, source_body = split_frontmatter(source_text)
    relative_source = source_path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
    source_title = (
        str(source_frontmatter.get("title") or "").strip()
        or source_path.stem.replace("RAW_", "").replace("_", " ").strip()
        or "Untitled Source"
    )
    title = str(payload.get("title") or "").strip() or f"{source_title} - Draft"
    selected_body = str(payload.get("selected_text") or payload.get("body") or "").strip()
    draft_body = normalize_body(selected_body)
    source_tags = source_frontmatter.get("tags", [])
    if isinstance(source_tags, str):
        source_tags = clean_tags(source_tags)

    frontmatter: dict[str, Any] = {
        # Born with a durable id — this file never passes through the lazy
        # stamp in save_essay_updates before it is first indexed.
        UID_KEY: uuid.uuid4().hex,
        "title": title,
        "summary": str(source_frontmatter.get("summary") or source_frontmatter.get("subtitle") or safe_excerpt(source_body, 240)).strip(),
        "status": "Writers Room",
        "source_role": "draft",
        "draft_of": relative_source,
        "tags": source_tags,
        "source_note": f"Linked draft from {relative_source}",
    }
    for key in ("totem", "publication", "section"):
        value = source_frontmatter.get(key)
        if value not in (None, "", []):
            frontmatter[key] = value

    draft_path = unique_markdown_path(source_path.parent, title)
    atomic_write_text(draft_path, compose_markdown(frontmatter, draft_body))
    essays, _ = refresh_essay_index()
    new_relative = draft_path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
    new_id = frontmatter_uid(frontmatter) or essay_id_for(new_relative)
    detail = get_essay_detail(new_id)
    return {
        "ok": True,
        "source_essay_id": essay_id,
        "source_path": public_path(source_path),
        "draft_essay_id": new_id,
        "draft_path": public_path(draft_path),
        "draft_relative_path": new_relative,
        "essay_count": len(essays),
        "draft": detail,
    }


@vault_write
def attach_hero_image_to_essay(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = resolve_essay_path(essay_id)
    text = path.read_text(encoding="utf-8", errors="ignore")
    assert_expected_file_state(
        essay_id,
        path,
        text,
        payload.get("expected_mtime"),
        payload.get("expected_content_hash"),
    )
    frontmatter, body = split_frontmatter(text)
    word_count = len(re.findall(r"\b\w+\b", body))
    source_role, _ = source_role_for(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter, word_count)
    if source_role == "source":
        raise ValueError("Create a linked draft before attaching a Substack hero image to this source note.")

    filename, suffix, raw = decode_image_upload(payload)
    title = _stringify(frontmatter.get("title")) or path.stem
    source_stem = Path(filename).stem
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    image_path = unique_obsidian_asset_path(f"{title}-hero-{source_stem}-{stamp}", suffix)
    atomic_write_bytes(image_path, raw)
    try:
        relative_path = image_path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
    except ValueError:
        relative_path = str(image_path)

    updates: dict[str, Any] = {"hero_image": relative_path}
    if payload.get("also_social", True) is not False:
        updates["social_image"] = relative_path
    if not _stringify(frontmatter.get("thumbnail_alt")).strip():
        updates["thumbnail_alt"] = title

    saved = save_essay_updates(
        essay_id,
        updates,
        None,
        payload.get("expected_mtime"),
        payload.get("expected_content_hash"),
    )
    refresh_essay_index()
    detail = get_essay_detail(essay_id)
    return {
        "ok": True,
        "essay_id": essay_id,
        "path": public_path(image_path),
        "relativePath": relative_path,
        "updates": updates,
        "saved": saved,
        "essay": detail,
    }


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v not in ("", None))
    return str(value)


def send_essay_to_substack(
    essay_id: str,
    updates: dict[str, Any],
    publish_updates: dict[str, Any] | None = None,
    body: str | None = None,
    expected_mtime: Any = None,
    expected_content_hash: Any = None,
) -> dict[str, Any]:
    """Persist airdate changes, then send only the canonical saved note."""
    saved_state: dict[str, Any] | None = None
    if updates or body is not None:
        saved_state = save_essay_updates(essay_id, updates, body, expected_mtime, expected_content_hash)

    # The save above rewrote the note whether or not the send goes on to
    # succeed. A failed send must still hand the editor the file state it
    # just created, or the editor's next send carries the pre-save mtime and
    # the server refuses it as an Obsidian-side change (AD-014).
    def saved_file_state() -> dict[str, Any] | None:
        if not saved_state or "mtime" not in saved_state:
            return None
        return {key: saved_state.get(key) for key in ("old_id", "new_id", "mtime", "mtime_iso", "content_hash")}

    # A send must never be licensed by values that exist only in the browser
    # request. The save above is the atomic airdate -> Obsidian handoff; reload
    # that canonical note for both readiness and transport.
    preflight = preflight_essay_for_substack(essay_id)
    if not preflight["ready"]:
        # Refused before any transport ran: nothing was posted, nothing to
        # reconnect, nothing to retry. The blockers are the whole story.
        return {
            "ok": False,
            "error_kind": "blocked",
            "essay_id": essay_id,
            "saved": saved_state,
            "saved_state": saved_file_state(),
            "preflight": preflight,
            "warnings": [],
            "message": "Substack draft is not ready. Fix the blockers before sending.",
        }

    path = resolve_essay_path(essay_id)
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = split_frontmatter(text)
    if saved_state is None:
        saved_state = {
            "ok": True,
            "essay_id": essay_id,
            "path": public_path(path),
            "changes": {},
            "saved_frontmatter": frontmatter,
        }

    publish_payload, _, _, _ = prepare_essay_publish_payload(essay_id)
    result = publish_draft(publish_payload)
    result["essay_id"] = essay_id
    result["essay_path"] = saved_state["path"]
    result["saved_changes"] = saved_state["changes"]
    result["saved_state"] = saved_file_state()
    result["preflight"] = preflight
    if result.get("ok"):
        # Draft landed on Substack: keep its remote identity on the same
        # canonical note so the next send can update it rather than duplicate.
        # No expected-state args: this request just wrote the file itself.
        try:
            # finish_transport_result already lifted draft_id/edit_url from
            # the transport's stdout onto the result.
            # A send creates a DRAFT. It does not change the phase: live now
            # means published, and only the writer pasting the post link can
            # say that. The draft id below is the "sent" marker, and
            # effective_status reads it as Ready for Air, never as live.
            remote_updates: dict[str, Any] = {}
            draft_id = _stringify(result.get("draft_id")).strip()
            draft_url = _stringify(result.get("edit_url")).strip()
            if draft_id:
                remote_updates["substack_draft_id"] = draft_id
            if draft_url:
                remote_updates["substack_draft_url"] = draft_url
            live_state = save_essay_updates(essay_id, remote_updates)
            if draft_id:
                result["draft_id"] = draft_id
            if draft_url:
                result["edit_url"] = draft_url
            result["draft_recorded"] = True
            result["mtime"] = live_state["mtime"]
            result["mtime_iso"] = live_state["mtime_iso"]
            result["content_hash"] = live_state["content_hash"]
        except Exception as exc:
            result["status_after_send_error"] = str(exc)
    return result


_ARRIVALS_CACHE: dict[str, str] | None = None


def load_arrivals() -> dict[str, str]:
    """The arrivals sidecar, read once and held."""
    global _ARRIVALS_CACHE
    if _ARRIVALS_CACHE is None:
        try:
            data = json.loads(ARRIVALS_LOG.read_text(encoding="utf-8"))
            _ARRIVALS_CACHE = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _ARRIVALS_CACHE = {}
    return _ARRIVALS_CACHE


def save_arrivals(arrivals: dict[str, str]) -> None:
    global _ARRIVALS_CACHE
    _ARRIVALS_CACHE = dict(arrivals)
    ARRIVALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(ARRIVALS_LOG, json.dumps(arrivals, indent=2, sort_keys=True))


def arrival_for(essay_id: str, seen_at: str) -> str:
    """When this essay arrived, recording `seen_at` the first time it is asked.

    The clock only ever goes up. A later sighting does not move it forward, and
    an earlier one does not drag it back: rescheduling, re-indexing and a uid
    mint all have to leave the paper exactly as old as it was. The only thing
    that resets it is the writer asking for it, in settings."""
    key = str(essay_id or "").strip()
    if not key:
        return seen_at
    arrivals = load_arrivals()
    existing = arrivals.get(key)
    if existing:
        return existing
    arrivals[key] = seen_at
    save_arrivals(arrivals)
    return seen_at


def carry_arrival(old_id: str, new_id: str) -> None:
    """Carry an arrival across a uid mint so minting does not freshen paper."""
    old_key, new_key = str(old_id or "").strip(), str(new_id or "").strip()
    if not old_key or not new_key or old_key == new_key:
        return
    arrivals = load_arrivals()
    stamp = arrivals.get(old_key)
    if not stamp or arrivals.get(new_key):
        return
    arrivals[new_key] = stamp
    save_arrivals(arrivals)


def reset_arrivals() -> None:
    """Settings' "reset the writers room": every essay's paper starts fresh."""
    save_arrivals({})


def reset_room(payload: dict[str, Any]) -> dict[str, Any]:
    """The settings action behind "reset the writers room".

    It restarts every essay's paper clock at once and cannot be undone, so the
    request has to say so explicitly. The confirmation dialog is the interface's
    job; this is the rule underneath it, so a stray POST cannot age-reset a
    whole catalog. Nothing in the vault is touched either way."""
    if payload.get("confirm") is not True:
        return {"ok": False, "error": "resetting the writers room needs confirm: true."}
    count = len(load_arrivals())
    reset_arrivals()
    return {"ok": True, "cleared": count}


# --- The star ----------------------------------------------------------------
# The post-it on a script card. Its status lives in the note (Writers Likey);
# the date it was pressed lives here, beside arrivals, and never in the vault.
_STARRED_CACHE: dict[str, str] | None = None


def load_starred() -> dict[str, str]:
    """The star-date sidecar, read once and held."""
    global _STARRED_CACHE
    if _STARRED_CACHE is None:
        try:
            data = json.loads(STARRED_LOG.read_text(encoding="utf-8"))
            _STARRED_CACHE = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _STARRED_CACHE = {}
    return _STARRED_CACHE


def save_starred(starred: dict[str, str]) -> None:
    global _STARRED_CACHE
    _STARRED_CACHE = dict(starred)
    STARRED_LOG.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(STARRED_LOG, json.dumps(starred, indent=2, sort_keys=True))


def carry_star(old_id: str, new_id: str) -> None:
    """Move a star date across a uid mint, as carry_arrival does for paper.

    A move, not a copy: the old id is a path hash nobody will ask for again,
    and a stale entry under it would only confuse a later unstar."""
    old_key, new_key = str(old_id or "").strip(), str(new_id or "").strip()
    if not old_key or not new_key or old_key == new_key:
        return
    starred = dict(load_starred())
    stamp = starred.pop(old_key, None)
    if not stamp:
        return
    starred.setdefault(new_key, stamp)
    save_starred(starred)


def index_row_for(essay_id: str) -> dict[str, Any] | None:
    """The catalog row for an essay, by any token that names it."""
    essays, _ = get_essay_index()
    return next(
        (row for row in essays
         if essay_id in (row.get("id"), row.get("uid"), row.get("legacy_path_id"))),
        None,
    )


# Why a star cannot move an essay in each phase it does not cover. One
# sentence, lowercase, shown by the room verbatim.
STAR_REFUSALS = {
    "Ready for Air": "it is on the board, so its star stays until it is unscheduled.",
    "Live": "it is live, so its star is part of the record now.",
    "Archived": "it is saved for a rainy day. bring it back to the room first.",
}


@vault_write
def star_essay(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Press the post-it: writers room -> writers likey, or back.

    Deliberately narrow. Any other phase is refused with a sentence, so a star
    can never pull an essay off the board or out of the shelf. Pressing it into
    the state it is already in is a no-op success, because a double click is
    not an error. The write goes through set_essay_status, so the file-conflict
    check and the uid mint (with its arrival and star carry) apply."""
    starred = payload.get("starred")
    if not isinstance(starred, bool):
        raise ValueError("starred must be true or false.")
    path = resolve_essay_path(essay_id)
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, _ = split_frontmatter(text)
    current = effective_status(path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix(), frontmatter)
    if current not in ("Writers Room", "Writers Likey"):
        raise RefusedError(STAR_REFUSALS.get(current, "only a writers room essay can take a star."),
                           current.lower().replace(" ", "-"))

    target = "Writers Likey" if starred else "Writers Room"
    if current == target:
        row = index_row_for(essay_id)
        new_id = (row or {}).get("id") or essay_id
        return {"ok": True, "changed": False, "starred": starred, "status": current,
                "old_id": essay_id, "new_id": new_id, "essay": row}

    # The sidecars change first, under the id the caller holds, so a uid mint
    # inside the write carries them and the rescan that follows reads them.
    # If the write fails they are put back exactly as they were.
    starred_before = dict(load_starred())
    arrivals_before = dict(load_arrivals())
    now = datetime.now(timezone.utc).isoformat()
    marks = dict(starred_before)
    if starred:
        marks[essay_id] = now
    else:
        marks.pop(essay_id, None)
    save_starred(marks)
    if starred and PAPER_FRESH_ON_PROMOTION:
        # The writer asked for fresh paper on promotion. Off by default: the
        # clock otherwise only ever goes up.
        arrivals = dict(arrivals_before)
        arrivals[essay_id] = now
        save_arrivals(arrivals)
    try:
        result = set_essay_status(
            essay_id,
            target,
            None,
            payload.get("expected_mtime"),
            payload.get("expected_content_hash"),
        )
    except BaseException:
        save_starred(starred_before)
        if starred and PAPER_FRESH_ON_PROMOTION:
            save_arrivals(arrivals_before)
        raise
    new_id = result.get("new_id") or essay_id
    return {
        "ok": True,
        "changed": True,
        "starred": starred,
        "status": target,
        "old_id": essay_id,
        "new_id": new_id,
        "essay": index_row_for(new_id),
    }


def object_field(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """A request field that must be an object when present; absent is {}."""
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object.")
    return value


ABSOLUTE_PATH_IN_TEXT = re.compile(r"(?<![\w:/])/(?:[^\s'\"/]+/)+[^\s'\"]*")


def without_local_paths(message: str) -> str:
    """An error message with every absolute filesystem path reduced to its
    file name. OSError text names the path it failed on, and a message is
    shown to whoever is looking at the room; where the vault lives is not
    theirs to read there."""
    return ABSOLUTE_PATH_IN_TEXT.sub(lambda match: Path(match.group(0)).name or "a file", str(message or ""))


def parse_essay_route(path: str) -> tuple[str | None, str | None]:
    # /api/essays/:id or /api/essays/:id/:action
    parts = [part for part in path.split("/") if part]
    if len(parts) < 3 or parts[0] != "api" or parts[1] != "essays":
        return None, None
    essay_id = parts[2]
    action = parts[3] if len(parts) >= 4 else None
    return essay_id, action


class Handler(BaseHTTPRequestHandler):
    # HEAD answers exactly what GET would, headers and all, without the body.
    head_only = False

    def do_HEAD(self) -> None:
        self.head_only = True
        try:
            self.do_GET()
        finally:
            self.head_only = False

    def write_body(self, data: bytes) -> None:
        if not self.head_only:
            self.wfile.write(data)

    def is_authenticated(self) -> bool:
        if not AUTH_REQUIRED:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = b64decode(header.split(" ", 1)[1], validate=True).decode("utf-8")
        except (BinasciiError, UnicodeDecodeError):
            return False
        if ":" not in decoded:
            return False
        user, password = decoded.split(":", 1)
        return hmac.compare_digest(user, AUTH_USER) and hmac.compare_digest(password, AUTH_PASSWORD)

    def require_auth(self) -> bool:
        if self.is_authenticated():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="airdate"')
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_security_headers(no_store=True)
        self.end_headers()
        self.write_body(b"Authentication required.")
        return False

    def request_is_same_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.netloc == self.headers.get("Host", "")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/healthz":
            self.send_json({"ok": True, "auth_required": AUTH_REQUIRED})
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if not self.require_auth():
            return

        try:
            if path == "/":
                self.redirect("/airdate")
                return
            if path == "/airdate/room":
                # An older address for the room. Links to it still exist
                # (?essay= deep links), so the query string travels with the
                # redirect.
                self.redirect(room_redirect_location(parsed.query))
                return
            if path == "/airdate":
                # The writers room. First run is handled in the page: it reads
                # /api/app/status and opens the setup wizard until the vault is
                # configured.
                self.serve_file(ROOT / "room.html", "text/html; charset=utf-8")
                return

            if path.startswith("/static/"):
                target = (ROOT / path.lstrip("/")).resolve()
                if STATIC in target.parents and target.exists():
                    content_type = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
                    self.serve_file(target, content_type)
                    return

            if path.startswith("/vault-asset/"):
                target = resolve_vault_asset(path[len("/vault-asset/"):])
                if target is not None:
                    self.serve_file(target, CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream"))
                    return
                self.send_json({"error": "Not found"}, status=404)
                return

            if path == "/api/app/status":
                self.send_json(app_status_payload())
                return

            if path == "/api/substack/status":
                # Preflight for the Send button: is the pipeline ready to create a draft?
                self.send_json(substack_status_payload())
                return

            if SETUP_REQUIRED and path.startswith("/api/"):
                self.send_setup_required()
                return

            if path == "/api/essays":
                essays, _ = get_essay_index()
                params = parse_qs(parsed.query)
                scope = (params.get("scope", ["active"])[0] or "active").strip().lower()
                if scope not in {"active", "shelf", "archived", "all"}:
                    scope = "active"
                if scope == "all":
                    selected = essays
                else:
                    selected = [e for e in essays if str(e.get("collection") or "active") == scope]
                self.send_json({
                    "essays": selected,
                    "count": len(selected),
                    "scope": scope,
                    "collection_counts": collection_counts(essays),
                    "obsidian_dir": public_path(OBSIDIAN_ESSAYS_DIR),
                    "scanned_at": essay_index_scanned_at(),
                })
                return

            essay_id, action = parse_essay_route(path)
            if essay_id and not action:
                detail = get_essay_detail(essay_id)
                self.send_json(detail)
                return

            self.send_json({"error": "Not found"}, status=404)
        except FileNotFoundError:
            # A file vanished between the index/exists() check and the read
            # (Obsidian sync, external delete) — a clean 404, not a traceback.
            self.send_json({"error": "Not found"}, status=404)
        except BrokenPipeError:
            pass  # client hung up mid-response; nothing to send
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[airdate] GET {path} failed: {exc}\n")
            self.send_json({"error": "Internal server error"}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        try:
            if not self.require_auth():
                return
            if not self.request_is_same_origin():
                self.send_json({"error": "Cross-origin POSTs are not allowed."}, status=403)
                return
            content_type = self.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self.send_json({"error": "POST requests must use application/json."}, status=415)
                return
            length = int(self.headers.get("content-length", "0"))
            if length > MAX_REQUEST_BYTES:
                self.send_json({"error": "Request body is too large."}, status=413)
                return
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                self.send_json({"error": "JSON body must be an object."}, status=400)
                return

            if path == "/api/settings":
                result = save_settings(payload)
                self.send_json(result, status=200 if result.get("ok") else 400)
                return
            if path == "/api/settings/check":
                self.send_json(check_settings(payload))
                return
            if path == "/api/settings/create-essays-folder":
                self.send_json(create_essays_folder(payload))
                return
            if path == "/api/settings/reset-room":
                result = reset_room(payload)
                self.send_json(result, status=200 if result.get("ok") else 400)
                return
            if path == "/api/substack/connect":
                result = connector_request("/connect", {})
                self.send_json(result, status=202 if result.get("pending") else 400 if not result.get("ok") else 200)
                return

            if SETUP_REQUIRED:
                self.send_setup_required()
                return

            if path == "/api/upload-image":
                self.send_json(save_image_upload(payload))
                return

            if path == "/api/settings/editor":
                # After the setup check on purpose: a write here must never
                # create config.json on a fresh install, which would stop the
                # first-run wizard from opening.
                result = save_editor_settings(payload)
                self.send_json(result, status=200 if result.get("ok") else 400)
                return

            if path == "/api/app/refresh-essays":
                essays, _ = refresh_essay_index()
                self.send_json({
                    "ok": True,
                    "count": len(essays),
                    "scanned_at": essay_index_scanned_at(),
                })
                return

            essay_id, action = parse_essay_route(path)
            if essay_id and action == "preview":
                updates = object_field(payload, "updates")
                body = payload.get("body") if isinstance(payload.get("body"), str) else None
                self.send_json(preview_essay_updates(essay_id, updates, body))
                return
            if essay_id and action == "save":
                updates = object_field(payload, "updates")
                body = payload.get("body") if isinstance(payload.get("body"), str) else None
                result = save_essay_updates(
                    essay_id,
                    updates,
                    body,
                    payload.get("expected_mtime"),
                    payload.get("expected_content_hash"),
                )
                if payload.get("return_row") is True:
                    # The room's editor hands the fresh card to the pool and
                    # the board, so it asks for the row as it is on disk now.
                    # Without it the save leaves the index to its background
                    # rescan.
                    refresh_essay_index()
                    result["row"] = index_row_for(str(result.get("new_id") or essay_id))
                self.send_json(result)
                return
            if essay_id and action == "create-draft":
                self.send_json(create_linked_draft(essay_id, payload))
                return
            if essay_id and action == "attach-hero":
                self.send_json(attach_hero_image_to_essay(essay_id, payload))
                return
            if essay_id and action == "send":
                updates = object_field(payload, "updates")
                publish_updates = object_field(payload, "publish")
                body = payload.get("body") if isinstance(payload.get("body"), str) else None
                self.send_json(send_essay_to_substack(
                    essay_id,
                    updates,
                    publish_updates,
                    body,
                    payload.get("expected_mtime"),
                    payload.get("expected_content_hash"),
                ))
                return
            if essay_id and action == "preflight":
                publish_updates = object_field(payload, "publish")
                body = payload.get("body") if isinstance(payload.get("body"), str) else None
                self.send_json(preflight_essay_for_substack(essay_id, publish_updates, body))
                return
            if essay_id and action == "thumbnail-prompt":
                publish_updates = object_field(payload, "publish")
                self.send_json(essay_thumbnail_prompt(essay_id, publish_updates))
                return
            if essay_id and action == "star":
                # The post-it on a card: writers room <-> writers likey only.
                self.send_json(star_essay(essay_id, payload))
                return
            if essay_id and action == "ready-for-air":
                # Schedule (write scheduled_at to frontmatter, durable) AND
                # advance the status in one action. A bad date is a 400; a
                # live, rainy-day or writers room essay is a 409 with the
                # sentence the room shows.
                self.send_json(schedule_essay(essay_id, payload))
                return
            if essay_id and action == "unschedule":
                # Clear the air date and drop back to the schedulable pool.
                # scheduled_at="" is deleted surgically by apply_frontmatter_edits;
                # Writers Likey stays in the category folder (no move, no id churn).
                self.send_json(unschedule_essay(essay_id, payload))
                return
            if essay_id and action == "did-not-air":
                # The board note after air day: it never went out.
                self.send_json(did_not_air(essay_id, payload))
                return
            if essay_id and action == "publish":
                self.send_json(publish_essay(essay_id, payload))
                return
            if essay_id and action == "archive":
                # The umbrella: save for a rainy day. Refused for Ready for Air
                # and Live (409); stamps previous_status + archived_at so
                # back-to-room can restore the phase this essay actually had.
                self.send_json(park_essay(essay_id, payload))
                return
            if essay_id and action == "back-to-room":
                # Rainy day's undo: restore the stamped phase and pull the file
                # back out of Archive/.
                self.send_json(back_to_room(essay_id, payload))
                return
            if essay_id and action == "forget-draft-link":
                # Recovery for a draft deleted in Substack. Deliberately an
                # explicit, writer-initiated action: substack_draft.py refuses
                # to silently create a replacement, because if the old draft was
                # published instead of deleted a new one would duplicate a live
                # post. Clearing the link is the writer saying it is gone.
                # The effective status, which honors folder placement, not the
                # raw frontmatter value: clearing the link must not also move
                # the note.
                with VAULT_WRITE_LOCK:
                    current = _stringify(get_essay_detail(essay_id).get("status")) or "Live"
                    self.send_json(set_essay_status(
                        essay_id,
                        current,
                        {"substack_draft_id": "", "substack_draft_url": ""},
                        payload.get("expected_mtime"),
                        payload.get("expected_content_hash"),
                    ))
                return
            if essay_id and action == "intake-suggest":
                self.send_json(intake_suggest(essay_id))
                return
            if essay_id and action == "intake-apply":
                self.send_json(intake_apply(essay_id, payload))
                return

            self.send_json({"error": "Not found"}, status=404)
        except json.JSONDecodeError:
            self.send_json({"error": "Request body must be valid JSON."}, status=400)
        except FileNotFoundError:
            # Never the OSError's own text: it names the absolute path.
            self.send_json({"error": "Essay not found"}, status=404)
        except RefusedError as exc:
            self.send_json(exc.payload, status=409)
        except EssayConflictError as exc:
            self.send_json(exc.payload, status=409)
        except ValueError as exc:
            self.send_json({"error": without_local_paths(str(exc)), "ok": False}, status=400)
        except Exception as exc:
            self.send_json({"error": without_local_paths(str(exc))}, status=500)

    def serve_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.send_security_headers(no_store=content_type.startswith("text/html"))
        self.end_headers()
        self.write_body(data)

    def send_setup_required(self) -> None:
        self.send_json({
            "ok": False,
            "error_kind": "setup_required",
            "error": "Finish setup first: choose your Obsidian vault and essays folder in settings.",
            "setup": setup_payload(),
        }, status=409)

    def redirect(self, location: str, status: int = 302) -> None:
        data = f"Redirecting to {location}\n".encode("utf-8")
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.send_security_headers(no_store=True)
        self.end_headers()
        self.write_body(data)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.send_security_headers(no_store=True)
        self.end_headers()
        self.write_body(data)

    def send_security_headers(self, no_store: bool = False) -> None:
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "same-origin")
        self.send_header("x-frame-options", "DENY")
        self.send_header(
            "content-security-policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("cache-control", "no-store" if no_store else "private, max-age=300")


def validate_security_config() -> None:
    if not IS_LOOPBACK and not AUTH_REQUIRED:
        raise RuntimeError("Refusing to bind to a non-loopback host with auth disabled.")
    if AUTH_REQUIRED and not AUTH_PASSWORD:
        raise RuntimeError("AIR_DATE_AUTH_PASSWORD is required when auth is enabled or HOST is non-loopback.")


def sanity_check_paths() -> None:
    ensure_private_dir(DATA_DIR)
    ensure_private_dir(SECRET_DIR)
    ensure_private_dir(STATE_DIR)
    ensure_private_dir(DRAFTS)
    ensure_private_dir(UPLOADS)


if __name__ == "__main__":
    validate_security_config()
    sanity_check_paths()
    cached_count = None if SETUP_REQUIRED else warm_essay_index()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"airdate running at http://{HOST}:{PORT}/airdate")
    if SETUP_REQUIRED:
        print("First run: open the link above to choose your Obsidian vault and essays folder.")
    else:
        print(f"Essays folder: {OBSIDIAN_ESSAYS_DIR}")
    print(f"Auth required: {'yes' if AUTH_REQUIRED else 'no'}")
    print(f"Runtime data dir: {DATA_DIR}")
    if cached_count is not None:
        print(f"Essay index: serving {cached_count} cached essays; background re-scan started")
    else:
        print("Essay index: no cached scan yet; building one in the background")
    server.serve_forever()
