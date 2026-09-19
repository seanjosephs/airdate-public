"""AirDate settings: one JSON file in the data dir, neutral defaults, env overrides.

Precedence for every value: environment variable, then config.json, then the
neutral default below. Nothing here assumes who the writer is; a writer's own
vault, publication and taxonomy arrive through config.json.

Stdlib only, like the server.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

CONFIG_VERSION = 1
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
CATEGORY_MODES = ("folders", "off")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# Folder names AirDate owns directly under the essays folder. A category can
# never be one of these.
RESERVED_FOLDERS = ("published", "archive")

DEFAULT_STYLE_PROMPT = (
    "Create a text-free Substack essay thumbnail for {publication_name}. "
    "Use a refined editorial illustration style, high-contrast enough for small "
    "previews, no lettering, no captions, no logos. Favor a single strong visual "
    "metaphor over a collage."
)

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "vault": {
        "path": "",
        "name": "",
        "essays_folder": "Essays",
        "hidden": {
            "filename_prefixes": [],
            "title_contains": [],
            "toplevel_title_contains": [],
        },
    },
    "substack": {
        "publication": "",
        "publication_name": "",
    },
    "connector": {"port": 17777},
    "calendar": {"publish_day": None},
    "thumbnail": {"style_prompt": DEFAULT_STYLE_PROMPT},
    # Five placeholder totems, on by default: a writer relabels, recolors and
    # re-icons them in settings to mark whatever they want to tell apart.
    "totems": {
        "enabled": True,
        "default": None,
        "items": [
            {"key": "circle", "label": "Circle", "color": "#8f79ff", "role": "", "image": "", "keywords": {}},
            {"key": "triangle", "label": "Triangle", "color": "#d9653b", "role": "", "image": "", "keywords": {}},
            {"key": "square", "label": "Square", "color": "#3f9a6e", "role": "", "image": "", "keywords": {}},
            {"key": "diamond", "label": "Diamond", "color": "#3a7bd5", "role": "", "image": "", "keywords": {}},
            {"key": "star", "label": "Star", "color": "#d4a017", "role": "", "image": "", "keywords": {}},
        ],
    },
    "categories": {"mode": "folders", "items": []},
    "tag_presets": [],
    "links": [],
}


def config_path(data_dir: Path) -> Path:
    return data_dir / "config.json"


def _merge(defaults: Any, value: Any) -> Any:
    """Overlay a loaded value on its default, keeping the default's shape for
    dict keys the file leaves out. Lists and scalars replace wholesale."""
    if isinstance(defaults, dict):
        if not isinstance(value, dict):
            return copy.deepcopy(defaults)
        merged = {key: _merge(default, value.get(key, default)) for key, default in defaults.items()}
        return merged
    return copy.deepcopy(value)


def load_config(data_dir: Path) -> tuple[dict[str, Any], bool, str]:
    """(config, file_exists, load_error). Never raises: an unreadable file is
    reported and the neutral defaults are used, which puts the app in setup."""
    path = config_path(data_dir)
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG), False, ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return copy.deepcopy(DEFAULT_CONFIG), True, f"config.json could not be read: {exc}"
    if not isinstance(raw, dict):
        return copy.deepcopy(DEFAULT_CONFIG), True, "config.json must hold a JSON object."
    return _merge(DEFAULT_CONFIG, raw), True, ""


def save_config(data_dir: Path, config: dict[str, Any]) -> Path:
    path = config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".config.json.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(config, tmp, indent=2, ensure_ascii=False)
            tmp.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with environment overrides applied (operator use; the
    config file stays the normal place for settings)."""
    out = copy.deepcopy(config)
    vault = out["vault"]
    essays_abs = _env("OBSIDIAN_ESSAYS_DIR")
    vault_dir = _env("AIRDATE_VAULT_DIR")
    if vault_dir:
        vault["path"] = vault_dir
    if essays_abs:
        essays = Path(essays_abs).expanduser()
        if not vault_dir and not vault["path"]:
            vault["path"] = str(essays.parent)
        try:
            vault["essays_folder"] = essays.resolve().relative_to(Path(vault["path"]).expanduser().resolve()).as_posix()
        except ValueError:
            vault["path"] = str(essays.parent)
            vault["essays_folder"] = essays.name
    if _env("AIRDATE_ESSAYS_FOLDER"):
        vault["essays_folder"] = _env("AIRDATE_ESSAYS_FOLDER")
    if _env("OBSIDIAN_VAULT_NAME"):
        vault["name"] = _env("OBSIDIAN_VAULT_NAME")
    if _env("SUBSTACK_PUB"):
        out["substack"]["publication"] = _env("SUBSTACK_PUB")
    if _env("AIRDATE_CONNECTOR_PORT"):
        try:
            out["connector"]["port"] = int(_env("AIRDATE_CONNECTOR_PORT") or "")
        except ValueError:
            pass
    return out


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _check_keywords(value: Any, where: str, errors: list[str]) -> None:
    if value in (None, {}):
        return
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and k.strip() and isinstance(v, int) and not isinstance(v, bool) for k, v in value.items()
    ):
        errors.append(f"{where}.keywords must map words to whole-number weights.")


def validate_config(config: dict[str, Any]) -> list[str]:
    """Structural checks on the whole file. Filesystem checks live in
    check_vault so a config can be valid while its vault is offline."""
    errors: list[str] = []
    vault = config.get("vault", {})
    essays_folder = str(vault.get("essays_folder") or "").strip()
    if not essays_folder:
        errors.append("vault.essays_folder is required.")
    elif essays_folder != "." and (essays_folder.startswith("/") or ".." in Path(essays_folder).parts):
        errors.append("vault.essays_folder must be a folder inside the vault, written relative to it.")
    hidden = vault.get("hidden", {})
    for key in ("filename_prefixes", "title_contains", "toplevel_title_contains"):
        if not _is_str_list(hidden.get(key, [])):
            errors.append(f"vault.hidden.{key} must be a list of strings.")

    port = config.get("connector", {}).get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        errors.append("connector.port must be a number from 1024 to 65535.")

    publish_day = config.get("calendar", {}).get("publish_day")
    if publish_day is not None and publish_day not in WEEKDAYS:
        errors.append("calendar.publish_day must be null or a weekday name in lowercase.")

    style = config.get("thumbnail", {}).get("style_prompt")
    if not isinstance(style, str) or not style.strip():
        errors.append("thumbnail.style_prompt must be text.")

    totems = config.get("totems", {})
    if not isinstance(totems.get("enabled"), bool):
        errors.append("totems.enabled must be true or false.")
    keys: set[str] = set()
    items = totems.get("items", [])
    if not isinstance(items, list):
        errors.append("totems.items must be a list.")
        items = []
    for index, item in enumerate(items):
        where = f"totems.items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where} must be an object.")
            continue
        key = item.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            errors.append(f"{where}.key must be lowercase letters, digits, - or _.")
        elif key in keys:
            errors.append(f"{where}.key '{key}' is used twice.")
        else:
            keys.add(key)
        color = item.get("color", "")
        if color and not (isinstance(color, str) and HEX_COLOR_RE.match(color)):
            errors.append(f"{where}.color must look like #a1b2c3.")
        image = item.get("image", "")
        if image and (not isinstance(image, str) or image.startswith("/") or ".." in Path(image).parts):
            errors.append(f"{where}.image must be a path inside the vault, written relative to it.")
        _check_keywords(item.get("keywords"), where, errors)
    default = totems.get("default")
    if default is not None and default not in keys:
        errors.append("totems.default must be null or one of the totem keys.")
    if totems.get("enabled") is True and not keys:
        errors.append("totems.enabled is true but totems.items is empty.")

    categories = config.get("categories", {})
    if categories.get("mode") not in CATEGORY_MODES:
        errors.append("categories.mode must be \"folders\" or \"off\".")
    cat_items = categories.get("items", [])
    if not isinstance(cat_items, list):
        errors.append("categories.items must be a list.")
        cat_items = []
    for index, item in enumerate(cat_items):
        where = f"categories.items[{index}]"
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip() or "/" in name or "\\" in name or name.startswith((".", "_")):
            errors.append(f"{where}.name must be a plain folder name.")
        elif name.strip().lower() in RESERVED_FOLDERS:
            errors.append(f"{where}.name '{name}' is reserved by AirDate.")
        if isinstance(item, dict):
            _check_keywords(item.get("keywords"), where, errors)

    presets = config.get("tag_presets", [])
    if not isinstance(presets, list):
        errors.append("tag_presets must be a list.")
        presets = []
    seen_names: set[str] = set()
    for index, preset in enumerate(presets):
        where = f"tag_presets[{index}]"
        if not isinstance(preset, dict) or not isinstance(preset.get("name"), str) or not preset["name"].strip():
            errors.append(f"{where}.name is required.")
            continue
        # Presets are picked by name, so a repeat would hide the first one.
        folded = preset["name"].strip().lower()
        if folded in seen_names:
            errors.append(f"{where}.name \"{preset['name'].strip()}\" is used twice. Give each preset its own name.")
        seen_names.add(folded)
        color = preset.get("color", "")
        if color and not (isinstance(color, str) and HEX_COLOR_RE.match(color)):
            errors.append(f"{where}.color must look like #a1b2c3.")
        tags = preset.get("tags", [])
        if not _is_str_list(tags) or len(tags) > 5:
            errors.append(f"{where}.tags must be a list of up to five tags.")

    links = config.get("links", [])
    if not isinstance(links, list):
        errors.append("links must be a list.")
        links = []
    for index, link in enumerate(links):
        if not isinstance(link, dict) or not isinstance(link.get("label"), str) or not link["label"].strip():
            errors.append(f"links[{index}].label is required.")
        elif not isinstance(link.get("url"), str) or not re.match(r"^https?://", link["url"]):
            errors.append(f"links[{index}].url must start with http:// or https://.")
    return errors


def publication_url(value: str) -> str:
    """Normalize a publication to https://host with no trailing slash."""
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    if not re.match(r"^https?://", value):
        value = f"https://{value}"
    return value


def check_vault(config: dict[str, Any]) -> dict[str, Any]:
    """Filesystem readiness for the configured vault and essays folder."""
    vault = config.get("vault", {})
    raw_path = str(vault.get("path") or "").strip()
    result: dict[str, Any] = {
        "vault_ok": False,
        "essays_ok": False,
        "vault_message": "",
        "essays_message": "",
    }
    if not raw_path:
        result["vault_message"] = "Choose your Obsidian vault folder."
        return result
    vault_dir = Path(raw_path).expanduser()
    if not vault_dir.is_absolute():
        result["vault_message"] = "Write the vault folder as a full path, starting with / or ~."
        return result
    if not vault_dir.is_dir():
        result["vault_message"] = "That folder does not exist."
        return result
    if not (vault_dir / ".obsidian").is_dir():
        result["vault_message"] = "That folder is not an Obsidian vault (no .obsidian folder inside)."
        return result
    result["vault_ok"] = True
    essays_dir = vault_dir / str(vault.get("essays_folder") or "")
    if essays_dir.is_dir():
        result["essays_ok"] = True
    else:
        result["essays_message"] = "The essays folder does not exist in this vault yet."
    return result


def settings_from_form(current: dict[str, Any], form: dict[str, Any]) -> dict[str, Any]:
    """Apply the first-run / settings form to a config. The form edits the
    everyday fields; taxonomy stays in the file."""
    out = copy.deepcopy(current)
    vault = out["vault"]
    old_path = str(vault.get("path") or "")
    old_default_name = Path(old_path).expanduser().name if old_path else ""
    if "vault_path" in form:
        vault["path"] = str(form.get("vault_path") or "").strip()
    if "essays_folder" in form:
        folder = str(form.get("essays_folder") or "").strip().strip("/")
        # "." (or "/") means the essays are at the vault root.
        vault["essays_folder"] = "." if folder in ("", ".") and str(form.get("essays_folder") or "").strip() in (".", "/") else folder
    if "vault_name" in form:
        vault["name"] = str(form.get("vault_name") or "").strip()
    # The name follows the folder unless the writer set a different one.
    if vault["path"] and (not vault["name"] or (vault["path"] != old_path and vault["name"] == old_default_name)):
        vault["name"] = Path(vault["path"]).expanduser().name
    if "publication" in form:
        out["substack"]["publication"] = publication_url(str(form.get("publication") or ""))
    if "publication_name" in form:
        out["substack"]["publication_name"] = str(form.get("publication_name") or "").strip()
    if "publish_day" in form:
        day = str(form.get("publish_day") or "").strip().lower()
        out["calendar"]["publish_day"] = day or None
    if "totems_enabled" in form:
        out["totems"]["enabled"] = bool(form.get("totems_enabled"))
    if isinstance(form.get("tag_presets"), list):
        presets = []
        for edit in form["tag_presets"]:
            if not isinstance(edit, dict):
                continue
            raw_tags = edit.get("tags")
            if isinstance(raw_tags, str):
                raw_tags = raw_tags.split(",")
            tags = [str(t).strip() for t in raw_tags or [] if str(t).strip()]
            name = str(edit.get("name") or "").strip()
            if not name and not tags:
                continue
            presets.append({"name": name, "color": str(edit.get("color") or "").strip(), "tags": tags})
        out["tag_presets"] = presets
    if "category_mode" in form:
        # Validated with the rest of the file, so a bad value is reported, not dropped.
        out["categories"]["mode"] = str(form.get("category_mode") or "").strip().lower()
    if isinstance(form.get("totems"), list):
        # Keys stay fixed: notes store the key in frontmatter, so a relabel
        # must never orphan them. Label, color and icon are the writer's.
        by_key = {item.get("key"): item for item in out["totems"].get("items", []) if isinstance(item, dict)}
        for edit in form["totems"]:
            if not isinstance(edit, dict) or edit.get("key") not in by_key:
                continue
            item = by_key[edit["key"]]
            if "label" in edit:
                item["label"] = str(edit.get("label") or "").strip() or item.get("label") or item["key"]
            if "color" in edit:
                item["color"] = str(edit.get("color") or "").strip()
            if "image" in edit:
                item["image"] = str(edit.get("image") or "").strip().lstrip("/")
    out["config_version"] = CONFIG_VERSION
    return out
