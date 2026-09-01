from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists() and default is not None:
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_profile(path: Path | None = None) -> dict[str, Any]:
    return load_json(path or ROOT / "config" / "profile.json")


def _registry_items(directory: Path, key: str) -> list[dict[str, Any]]:
    """Load a deterministic, category-split JSON registry."""

    items: list[dict[str, Any]] = []
    if not directory.exists():
        return items
    for registry_file in sorted(directory.glob("*.json")):
        payload = load_json(registry_file, [])
        values = payload.get(key, []) if isinstance(payload, dict) else payload
        if not isinstance(values, list):
            raise ValueError(f"{registry_file} must contain a JSON list or a '{key}' list")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError(f"{registry_file} contains a non-object registry entry")
            items.append({**value, "registry_file": registry_file.name})
    return items


def load_organisations(path: Path | None = None, *, config_root: Path | None = None) -> list[dict[str, Any]]:
    target = path or (config_root or ROOT / "config") / "organisations"
    if target.is_file():
        payload = load_json(target, [])
        return payload.get("organisations", []) if isinstance(payload, dict) else payload
    return _registry_items(target, "organisations")


def load_source_profiles(*, config_root: Path | None = None) -> dict[str, dict[str, Any]]:
    payload = load_json((config_root or ROOT / "config") / "source_profiles.json", {})
    return payload.get("profiles", payload)


def load_sources(path: Path | None = None, *, config_root: Path | None = None) -> list[dict[str, Any]]:
    root = config_root or ROOT / "config"
    target = path or root / "sources"
    if target.is_file():
        raw_sources = load_json(target, [])
    else:
        raw_sources = _registry_items(target, "sources")
        legacy = root / "sources.json"
        if not raw_sources and legacy.exists():
            raw_sources = load_json(legacy)

    profiles = load_source_profiles(config_root=root)
    organisations = {item["id"]: item for item in load_organisations(config_root=root) if item.get("id")}
    resolved: list[dict[str, Any]] = []
    for raw in raw_sources:
        profile_id = raw.get("profile")
        if profile_id and profile_id not in profiles:
            raise ValueError(f"source {raw.get('id', '<unknown>')} references unknown profile {profile_id}")
        source = {**profiles.get(profile_id, {}), **raw}
        organisation = organisations.get(source.get("organisation_id", ""), {})
        if organisation:
            source.setdefault("organisation", organisation.get("name", source.get("name", source["id"])))
            source.setdefault("sector", (organisation.get("sectors") or ["other"])[0])
            source.setdefault("family", source.get("organisation_id"))
        source.setdefault("scan_tier", "daily")
        resolved.append(source)
    return resolved


def load_coverage_target(name: str = "trackr", *, config_root: Path | None = None) -> dict[str, Any]:
    filename = "trackr_uk_organisations.json" if name == "trackr" else f"{name}.json"
    return load_json((config_root or ROOT / "config") / "coverage_targets" / filename)


def load_overrides(path: Path | None = None) -> dict[str, dict[str, Any]]:
    payload = load_json(path or ROOT / "config" / "overrides.json", {})
    return payload.get("overrides", payload)
