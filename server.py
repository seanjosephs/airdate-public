#!/usr/bin/env python3
from __future__ import annotations

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
CONTACT_LOG = STATE_DIR / "creative_contact.json"
# Written by the connector's "Pair with airdate" command: {port, token}. The
# token is the only capability airdate holds; the Substack session itself
# stays in Obsidian's secret storage.
CONNECTOR_PAIRING_FILE = SECRET_DIR / "connector.json"

PLACEHOLDER_PUBLICATION_MARKERS = {"yourname", "yourpublication"}


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


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
STATUS_SET = {"Writers Room", "Writers Likey", "Ready for Air", "Live", "Published", "Archived"}
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
TAG_PRESETS: list[dict[str, Any]] = []
LINKS: list[dict[str, str]] = []


def apply_config(config: dict[str, Any], file_exists: bool, load_error: str = "") -> None:
    """Install a config (already merged with defaults) as the running settings."""
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
}

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


def unique_upload_path(stem: str, suffix: str) -> Path:
    safe_stem = slugify(stem)
    path = UPLOADS / f"{safe_stem}{suffix}"
    counter = 2
    while path.exists():
        path = UPLOADS / f"{safe_stem}-{counter}{suffix}"
        counter += 1
    return path


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
    """The ChatGPT bridge: Air Date prepares the styled prompt, the image is
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
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
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
    repaired_body, _ = obsidian_markdown.preprocess(_stringify(publish_payload.get("body")))
    fidelity_findings = obsidian_markdown.scan(repaired_body)

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
        blockers.append({
            "key": f"body_{finding['kind']}_{finding['line']}",
            "field": "",
            "label": f"{label} (line {finding['line']})",
            "message": f"line {finding['line']}: {finding['detail']}",
        })
    if len(blocking_findings) > BODY_BLOCKER_LIMIT:
        extra = len(blocking_findings) - BODY_BLOCKER_LIMIT
        blockers.append({
            "key": "body_blockers_more",
            "field": "",
            "label": f"+{extra} more",
            "message": f"...and {extra} more blocking body-fidelity findings.",
        })

    other_findings = [f for f in fidelity_findings if f["severity"] != obsidian_markdown.BLOCKER]
    for finding in other_findings[:BODY_WARNING_LIMIT]:  # cap the noise; the summary carries the rest
        warnings.append({
            "key": f"body_{finding['kind']}",
            "message": f"line {finding['line']}: {finding['detail']}",
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


def _tail(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return _stringify(value)[-4000:]


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
    for key in ("error_kind", "message", "draft_id", "edit_url"):
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
        # Air Date's own serializer writes JSON-compatible quoted scalars.
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


def _frontmatter_key_spans(lines: list[str]) -> tuple[dict[str, tuple[int, int]], dict[str, str]]:
    """Map each top-level key to the [start, end) line range it occupies (its key
    line plus any block-list / indented continuation lines) and to its style
    ('block' | 'inline' | 'scalar'). Comments and blank lines belong to no key."""
    spans: dict[str, tuple[int, int]] = {}
    styles: dict[str, str] = {}
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
        spans[key] = (i, j)
        styles[key] = "block" if is_block else ("inline" if after_colon.startswith("[") else "scalar")
        i = j
    return spans, styles


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
    spans, styles = _frontmatter_key_spans(lines)
    start_to_key = {span[0]: key for key, span in spans.items()}

    new_lines: list[str] = []
    handled: set[str] = set()
    i, n = 0, len(lines)
    while i < n:
        key = start_to_key.get(i)
        if key is None:
            new_lines.append(lines[i])  # comment / blank / stray line
            i += 1
            continue
        s_start, s_end = spans[key]
        if key in changes:
            handled.add(key)
            after = changes[key]["after"]
            if after not in (None, "", []):
                new_lines.extend(_render_key_lines(
                    key, after, styles.get(key, "scalar"), _block_indent(lines, s_start, s_end)))
            # else: deletion — emit nothing for this span
        else:
            new_lines.extend(lines[s_start:s_end])  # unchanged: verbatim
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
    # Lifecycle: Writers Room -> Writers Likey -> Ready for Air -> Live -> Published.
    # Writers Room = the default: any essay that exists is in the writers room,
    # so an absent/blank status means Writers Room (there is no "Inbox" status;
    # un-filed essays surface via the needs-intake flag, not a status).
    # Writers Likey = writing done, ready to be scheduled (no date yet) — the
    # pool you drag onto the calendar. Ready for Air = scheduled + on the
    # calendar (set by the Ready for Air button/drag); Live = draft sent to
    # Substack; Published = the writer pressed publish there. Archived sits outside the
    # flow. "airdate" is the tool's name, never a status: the old working status
    # "Air Date" aliases to "Ready for Air".
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
        "released": "Published",
        "published": "Published",
        "archived": "Archived",
        "archive": "Archived",
    }
    return lower_map.get(raw.lower(), "Writers Room")


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


def effective_status(relative_path: str, frontmatter: dict[str, Any]) -> str:
    """The single source of truth for an essay's lifecycle status.

    Folder wins for the two terminal states: a file physically in Published/ IS
    Published and a file in Archive/ IS Archived, no matter what the frontmatter
    says — that reconciliation is the whole point (it kills the bug where a
    Published/ essay showed a working status with a live Publish button).

    Off the terminal folders, the frontmatter status carries the working state
    (Inbox -> Writers Room -> Ready for Air -> Live). A frontmatter-declared
    Published/Archived that hasn't been physically moved yet is still honored
    here; set_essay_status relocates it on the next write."""
    rp = relative_path.lower()
    top = rp.split("/", 1)[0] if "/" in rp else ""
    if top == "published" or "/published/" in rp:
        return "Published"
    if top == "archive" or "/archive/" in rp:
        return "Archived"
    raw = frontmatter.get("status")
    if raw and str(raw).strip():
        return normalize_status(raw)
    if frontmatter.get("published") is True:
        return "Published"
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
    if status == "Published":
        return "shelf"
    return "active"


def days_since(dt: datetime) -> int:
    now = datetime.now(timezone.utc)
    delta = now - dt
    return max(0, int(delta.total_seconds() // 86400))


def obsidian_url_for(relative_from_essays: str) -> str:
    vault_file = f"{ESSAYS_FOLDER}/{relative_from_essays}".replace("\\", "/")
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
                    "status": status,
                    "collection": collection,
                    "category": category,
                    "published_date": published_date,
                    "substack_url": substack_url,
                    "scheduled_at": str(frontmatter.get("scheduled_at") or "").strip(),
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
ESSAY_INDEX_VERSION = 5  # 5: keyed to the settings fingerprint as well as the folder
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
        },
        "paths_editable": EXPOSE_LOCAL_PATHS,
    }


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
            # A vault image the writer chose, else the slot's placeholder icon.
            "image": f"/vault-asset/{urllib_parse_quote_path(image)}" if image
            else f"/static/totems/placeholder-{index % 5 + 1}.svg",
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
    }


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


def create_essays_folder() -> dict[str, Any]:
    """The one vault write first run may make: an empty essays folder, inside
    a folder already confirmed to be an Obsidian vault."""
    if not VAULT_CHECK.get("vault_ok"):
        raise ValueError(VAULT_CHECK.get("vault_message") or "Choose your Obsidian vault folder first.")
    target = (VAULT_DIR / ESSAYS_FOLDER).resolve()
    if VAULT_DIR.resolve() not in target.parents:
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
    if path is None:
        raise FileNotFoundError("Essay not found")
    return path


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


def assert_expected_file_state(
    essay_id: str,
    path: Path,
    text: str,
    expected_mtime: Any = None,
    expected_content_hash: Any = None,
) -> None:
    if expected_content_hash:
        if content_hash(text) != str(expected_content_hash):
            raise EssayConflictError(conflict_payload(essay_id, path, text))
        return
    if expected_mtime in (None, ""):
        return
    try:
        expected = float(expected_mtime)
    except (TypeError, ValueError):
        return
    if abs(path.stat().st_mtime - expected) > 0.001:
        raise EssayConflictError(conflict_payload(essay_id, path, text))


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
    }


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
        else:
            cleaned.pop("totem")
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
    Published/Archived pull files into their folders; a working status pulls a
    shelved/archived file back out into its category folder (stamped on the way in)."""
    top = relative_path.split("/", 1)[0].lower() if "/" in relative_path else ""
    in_published = top == "published"
    in_archive = top == "archive"
    if status == "Published":
        return None if in_published else OBSIDIAN_ESSAYS_DIR / "Published"
    if status == "Archived":
        return None if in_archive else OBSIDIAN_ESSAYS_DIR / "Archive"
    if in_published or in_archive:
        category = str(frontmatter.get("category") or "").strip() if CATEGORY_MODE == "folders" else ""
        if category and "/" not in category and "\\" not in category and not category.startswith("."):
            return OBSIDIAN_ESSAYS_DIR / category
        return OBSIDIAN_ESSAYS_DIR
    return None


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
            warning = f"Status saved, but the file move failed: {exc}"

    refresh_essay_index()  # blocking, so the path-derived id resolves immediately
    new_relative = new_path.relative_to(OBSIDIAN_ESSAYS_DIR).as_posix()
    # The uid survives the move (save_essay_updates above stamped it if absent),
    # so a uid'd essay keeps its id across a folder move — no client remap needed.
    new_id = frontmatter_uid(frontmatter) or essay_id_for(new_relative)
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


def publish_essay(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Bookkeeping for a publish the writer performed on Substack themselves: stamp the
    live URL + date, set status Published, move the file to Published/. The app
    itself never publishes anything."""
    substack_url = str(payload.get("substack_url") or "").strip()
    if not re.match(r"^https?://", substack_url):
        raise ValueError("substack_url must be the full http(s) link to the live post.")
    published_date = str(payload.get("published_date") or "").strip()
    if not published_date:
        published_date = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    return set_essay_status(
        essay_id,
        "Published",
        {"published_date": published_date, "substack_url": substack_url},
        payload.get("expected_mtime"),
        payload.get("expected_content_hash"),
    )


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


def intake_apply(essay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Confirmed intake: create/complete frontmatter and sort the file into its
    category folder. Works on files with no frontmatter at all."""
    category = str(payload.get("category") or "").strip()
    if CATEGORY_MODE == "folders":
        if not category or "/" in category or "\\" in category or category.startswith((".", "_")):
            raise ValueError("category must be a plain folder name")
        if category.lower() in airdate_config.RESERVED_FOLDERS:
            raise ValueError(f"{category} is a folder airdate manages; pick a category folder.")
    path = resolve_essay_path(essay_id)
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, _ = split_frontmatter(text)
    updates: dict[str, Any] = {"status": str(payload.get("status") or "Writers Room")}
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
            remote_updates: dict[str, Any] = {"status": "Live"}
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
            result["status_after_send"] = "Live"
            result["mtime"] = live_state["mtime"]
            result["mtime_iso"] = live_state["mtime_iso"]
            result["content_hash"] = live_state["content_hash"]
        except Exception as exc:
            result["status_after_send_error"] = str(exc)
    return result


def load_contact_log() -> dict[str, Any]:
    if not CONTACT_LOG.exists():
        return {"total": 0, "events": []}
    try:
        return json.loads(CONTACT_LOG.read_text(encoding="utf-8"))
    except Exception:
        return {"total": 0, "events": []}


def save_contact_log(data: dict[str, Any]) -> None:
    atomic_write_text(CONTACT_LOG, json.dumps(data, indent=2))


def track_contact(essay_id: str, action: str) -> dict[str, Any]:
    log = load_contact_log()
    events = log.get("events", [])
    action = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(action or "contact"))[:80]
    events.append(
        {
            "essay_id": essay_id,
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    log["events"] = events[-1200:]
    log["total"] = int(log.get("total", 0)) + 1
    save_contact_log(log)
    return {"ok": True, "total": log["total"], "last_action": action}


def parse_essay_route(path: str) -> tuple[str | None, str | None]:
    # /api/essays/:id or /api/essays/:id/:action
    parts = [part for part in path.split("/") if part]
    if len(parts) < 3 or parts[0] != "api" or parts[1] != "essays":
        return None, None
    essay_id = parts[2]
    action = parts[3] if len(parts) >= 4 else None
    return essay_id, action


class Handler(BaseHTTPRequestHandler):
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
        self.wfile.write(b"Authentication required.")
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
            if path == "/airdate":
                # First run is handled in the page: it reads /api/app/status and
                # opens settings in its setup state until the vault is configured.
                self.serve_file(ROOT / "airdate.html", "text/html; charset=utf-8")
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
            if path == "/api/settings/create-essays-folder":
                self.send_json(create_essays_folder())
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
                updates = payload.get("updates", {}) if isinstance(payload, dict) else {}
                body = payload.get("body") if isinstance(payload.get("body"), str) else None
                self.send_json(preview_essay_updates(essay_id, updates, body))
                return
            if essay_id and action == "save":
                updates = payload.get("updates", {}) if isinstance(payload, dict) else {}
                body = payload.get("body") if isinstance(payload.get("body"), str) else None
                self.send_json(save_essay_updates(
                    essay_id,
                    updates,
                    body,
                    payload.get("expected_mtime"),
                    payload.get("expected_content_hash"),
                ))
                return
            if essay_id and action == "create-draft":
                self.send_json(create_linked_draft(essay_id, payload))
                return
            if essay_id and action == "attach-hero":
                self.send_json(attach_hero_image_to_essay(essay_id, payload))
                return
            if essay_id and action == "send":
                updates = payload.get("updates", {}) if isinstance(payload, dict) else {}
                publish_updates = payload.get("publish", {}) if isinstance(payload, dict) else {}
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
                publish_updates = payload.get("publish", {}) if isinstance(payload, dict) else {}
                body = payload.get("body") if isinstance(payload.get("body"), str) else None
                self.send_json(preflight_essay_for_substack(essay_id, publish_updates, body))
                return
            if essay_id and action == "thumbnail-prompt":
                publish_updates = payload.get("publish", {}) if isinstance(payload, dict) else {}
                self.send_json(essay_thumbnail_prompt(essay_id, publish_updates))
                return
            if essay_id and action == "contact":
                act = str(payload.get("action") or "contact")
                self.send_json(track_contact(essay_id, act))
                return
            if essay_id and action == "set-status":
                self.send_json(set_essay_status(
                    essay_id,
                    payload.get("status"),
                    None,
                    payload.get("expected_mtime"),
                    payload.get("expected_content_hash"),
                ))
                return
            if essay_id and action == "ready-for-air":
                # The Ready for Air button: schedule (write scheduled_at to
                # frontmatter, durable) AND advance the status in one action.
                scheduled_at = str(payload.get("scheduled_at") or payload.get("scheduledAt") or "").strip()
                if not scheduled_at:
                    self.send_json({"error": "scheduled_at (an air date) is required"}, status=400)
                    return
                self.send_json(set_essay_status(
                    essay_id,
                    "Ready for Air",
                    {"scheduled_at": scheduled_at},
                    payload.get("expected_mtime"),
                    payload.get("expected_content_hash"),
                ))
                return
            if essay_id and action == "unschedule":
                # Clear the air date and drop back to the schedulable pool.
                # scheduled_at="" is deleted surgically by apply_frontmatter_edits;
                # Writers Likey stays in the category folder (no move, no id churn).
                self.send_json(set_essay_status(
                    essay_id,
                    "Writers Likey",
                    {"scheduled_at": ""},
                    payload.get("expected_mtime"),
                    payload.get("expected_content_hash"),
                ))
                return
            if essay_id and action == "publish":
                self.send_json(publish_essay(essay_id, payload))
                return
            if essay_id and action == "archive":
                self.send_json(set_essay_status(
                    essay_id,
                    "Archived",
                    None,
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
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, status=404)
        except EssayConflictError as exc:
            self.send_json(exc.payload, status=409)
        except ValueError as exc:
            self.send_json({"error": str(exc), "ok": False}, status=400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def serve_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.send_security_headers(no_store=content_type.startswith("text/html"))
        self.end_headers()
        self.wfile.write(data)

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
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.send_security_headers(no_store=True)
        self.end_headers()
        self.wfile.write(data)

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
