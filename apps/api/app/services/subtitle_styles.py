from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .renderer import normalize_subtitle_style
from .storage import ensure_storage


def list_subtitle_styles() -> dict[str, Any]:
    data = _read_store()
    styles = data.get("styles")
    if not isinstance(styles, list) or not styles:
        default_style = _default_style()
        data = {"active_style_id": default_style["id"], "styles": [default_style]}
        _write_store(data)
    return data


def active_subtitle_style() -> dict[str, Any]:
    data = list_subtitle_styles()
    active_id = str(data.get("active_style_id") or "")
    styles = data.get("styles") or []
    selected = next((style for style in styles if str(style.get("id")) == active_id), None)
    if not selected and styles:
        selected = styles[0]
    return normalize_subtitle_style((selected or {}).get("style"))


def save_subtitle_style(name: str, style: dict[str, Any], style_id: str | None = None, make_active: bool = True) -> dict[str, Any]:
    data = list_subtitle_styles()
    styles = list(data.get("styles") or [])
    normalized_id = _safe_id(style_id or name) or f"style-{uuid.uuid4().hex[:8]}"
    payload = {
        "id": normalized_id,
        "name": (name or "Subtitle Style").strip()[:120] or "Subtitle Style",
        "style": normalize_subtitle_style(style),
    }
    replaced = False
    for index, item in enumerate(styles):
        if str(item.get("id")) == normalized_id:
            styles[index] = payload
            replaced = True
            break
    if not replaced:
        styles.append(payload)
    data["styles"] = styles
    if make_active:
        data["active_style_id"] = normalized_id
    _write_store(data)
    return payload


def set_active_subtitle_style(style_id: str) -> dict[str, Any]:
    data = list_subtitle_styles()
    styles = data.get("styles") or []
    selected = next((style for style in styles if str(style.get("id")) == style_id), None)
    if not selected:
        raise ValueError("Subtitle style not found.")
    data["active_style_id"] = style_id
    _write_store(data)
    return selected


def _default_style() -> dict[str, Any]:
    return {
        "id": "default",
        "name": "Default Bottom Box",
        "style": normalize_subtitle_style(),
    }


def _read_store() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_store(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _store_path() -> Path:
    return ensure_storage() / "subtitle-styles.json"


def _safe_id(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in (value or "").strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:80]
