"""Maintainable organisation/source registries and benchmark coverage checks."""

from __future__ import annotations

import json
import re
import tempfile
import csv
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .adapters import scan_source
from .config import ROOT, load_coverage_target, load_json, load_organisations, load_source_profiles, load_sources


TIERS = {"high", "daily", "weekly", "manual"}
KNOWN_ADAPTERS = {
    "html", "official_html", "university", "greenhouse", "lever", "ashby",
    "workday", "smartrecruiters", "teamtailor", "workable", "json", "rss",
    "atom", "jane_street",
}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:96]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def detect_source(url: str) -> dict[str, Any]:
    """Return a conservative adapter/profile suggestion for an official URL."""

    parsed = urlparse(url)
    host, path = parsed.netloc.lower(), parsed.path.strip("/")
    result: dict[str, Any] = {
        "url": url,
        "adapter": "html",
        "profile": "official_html",
        "confidence": "low",
        "review_required": True,
    }
    if host == "boards-api.greenhouse.io" and "/jobs" in parsed.path:
        return {**result, "adapter": "greenhouse", "profile": "greenhouse_api", "confidence": "high", "review_required": False}
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and path:
        path_token = path.split("/")[0]
        token = (parse_qs(parsed.query).get("for") or [path_token])[0] if path_token == "embed" else path_token
        return {**result, "url": f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", "adapter": "greenhouse", "profile": "greenhouse_api", "confidence": "high", "review_required": False}
    if host == "api.lever.co" and "/postings/" in parsed.path:
        return {**result, "adapter": "lever", "profile": "lever_api", "confidence": "high", "review_required": False}
    if host == "jobs.lever.co" and path:
        token = path.split("/")[0]
        return {**result, "url": f"https://api.lever.co/v0/postings/{token}?mode=json", "adapter": "lever", "profile": "lever_api", "confidence": "high", "review_required": False}
    if host == "api.ashbyhq.com" and "/posting-api/job-board/" in parsed.path:
        return {**result, "adapter": "ashby", "profile": "ashby_api", "confidence": "high", "review_required": False}
    if host == "jobs.ashbyhq.com" and path:
        token = path.split("/")[0]
        return {**result, "url": f"https://api.ashbyhq.com/posting-api/job-board/{token}", "adapter": "ashby", "profile": "ashby_api", "confidence": "high", "review_required": False}
    if any(value in host for value in ("myworkdayjobs.com", "myworkdaysite.com")):
        return {**result, "adapter": "workday", "profile": "candidate_url", "confidence": "medium", "review_required": True}
    if "smartrecruiters.com" in host:
        return {**result, "adapter": "smartrecruiters", "profile": "candidate_url", "confidence": "medium", "review_required": True}
    if "teamtailor.com" in host:
        return {**result, "adapter": "teamtailor", "profile": "candidate_url", "confidence": "medium", "review_required": True}
    if "workable.com" in host:
        return {**result, "adapter": "workable", "profile": "candidate_url", "confidence": "medium", "review_required": True}
    if parsed.path.lower().endswith((".rss", ".xml")) or "/feed" in parsed.path.lower():
        return {**result, "adapter": "rss", "profile": "rss_feed", "confidence": "medium", "review_required": False}
    return result


def validate_registry(*, config_root: Path | None = None) -> dict[str, Any]:
    root = config_root or ROOT / "config"
    organisations = load_organisations(config_root=root)
    sources = load_sources(config_root=root)
    profiles = load_source_profiles(config_root=root)
    errors: list[str] = []
    warnings: list[str] = []
    org_ids: set[str] = set()
    for organisation in organisations:
        identifier = organisation.get("id")
        if not identifier or not organisation.get("name"):
            errors.append(f"organisation in {organisation.get('registry_file', '<unknown>')} needs id and name")
        elif identifier in org_ids:
            errors.append(f"duplicate organisation id: {identifier}")
        else:
            org_ids.add(identifier)
    source_ids: set[str] = set()
    urls: dict[str, str] = {}
    for source in sources:
        identifier = source.get("id")
        if not identifier or not source.get("name") or not source.get("url"):
            errors.append(f"source in {source.get('registry_file', '<unknown>')} needs id, name, and url")
            continue
        if identifier in source_ids:
            errors.append(f"duplicate source id: {identifier}")
        source_ids.add(identifier)
        organisation_id = source.get("organisation_id")
        if not organisation_id:
            errors.append(f"{identifier}: missing organisation_id")
        elif organisation_id not in org_ids:
            errors.append(f"{identifier}: unknown organisation_id {organisation_id}")
        if source.get("scan_tier", "daily") not in TIERS:
            errors.append(f"{identifier}: invalid scan_tier {source.get('scan_tier')}")
        adapter = source.get("adapter", "html")
        if adapter not in KNOWN_ADAPTERS:
            errors.append(f"{identifier}: unknown adapter {adapter}")
        canonical_url = source["url"].rstrip("/").lower()
        if canonical_url in urls:
            errors.append(f"{identifier}: duplicate URL also used by {urls[canonical_url]}")
        urls[canonical_url] = identifier
        if source.get("profile") and source["profile"] not in profiles:
            errors.append(f"{identifier}: unknown profile {source['profile']}")
        if source.get("resolution_status") in {"unresolved", "candidate_url_needs_validation"} and source.get("enabled", True):
            warnings.append(f"{identifier}: unresolved candidate source is enabled")
    try:
        benchmark = benchmark_coverage("trackr", config_root=root)
        if benchmark["missing_from_organisation_registry"]:
            errors.append(f"Trackr benchmark missing {len(benchmark['missing_from_organisation_registry'])} organisation(s)")
    except FileNotFoundError:
        warnings.append("Trackr coverage benchmark is absent")
    return {
        "valid": not errors,
        "organisations": len(organisations),
        "sources": len(sources),
        "profiles": len(profiles),
        "errors": errors,
        "warnings": warnings,
    }


def benchmark_coverage(name: str = "trackr", *, config_root: Path | None = None) -> dict[str, Any]:
    root = config_root or ROOT / "config"
    benchmark = load_coverage_target(name, config_root=root)
    organisations = {item["id"]: item for item in load_organisations(config_root=root)}
    sources = load_sources(config_root=root)
    benchmark_items = benchmark.get("organisations", [])
    benchmark_ids = {item["id"] for item in benchmark_items}
    sources_by_org: Counter[str] = Counter(item.get("organisation_id") for item in sources if item.get("enabled", True))
    resolved_ids = {identifier for identifier in benchmark_ids if sources_by_org[identifier]}
    health_path = root.parent / "data" / "source_health.json"
    health_by_source = {item["source_id"]: item for item in load_json(health_path, [])}
    enabled_sources_by_org: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        if source.get("enabled", True):
            enabled_sources_by_org.setdefault(source.get("organisation_id", ""), []).append(source)
    resolutions = []
    status_counts: Counter[str] = Counter()
    names = {item["id"]: item["name"] for item in benchmark_items}
    for identifier in sorted(benchmark_ids):
        members = enabled_sources_by_org.get(identifier, [])
        member_health = [health_by_source[item["id"]] for item in members if item["id"] in health_by_source]
        states = {item.get("status") for item in member_health}
        if "healthy" in states:
            status = "working_official_source"
        elif "degraded" in states:
            status = "degraded_official_source"
        elif states.intersection({"failed", "stale"}):
            status = "inaccessible_official_source"
        else:
            status = "unresolved_official_source"
        status_counts[status] += 1
        resolutions.append({
            "id": identifier, "name": names[identifier], "status": status,
            "source_ids": [item["id"] for item in members], "tested_source_ids": [item["source_id"] for item in member_health],
        })
    sector_totals: Counter[str] = Counter()
    sector_registered: Counter[str] = Counter()
    sector_resolved: Counter[str] = Counter()
    for item in benchmark_items:
        for sector in item.get("sectors", []):
            sector_totals[sector] += 1
            sector_registered[sector] += item["id"] in organisations
            sector_resolved[sector] += item["id"] in resolved_ids
    missing = sorted(benchmark_ids - organisations.keys())
    return {
        "benchmark": name,
        "benchmark_id": benchmark.get("benchmark_id"),
        "captured_at": benchmark.get("captured_at"),
        "benchmark_organisations": len(benchmark_ids),
        "registered_organisations": len(benchmark_ids) - len(missing),
        "organisation_coverage_percent": round(100 * (len(benchmark_ids) - len(missing)) / max(1, len(benchmark_ids)), 2),
        "resolved_with_enabled_official_source": len(resolved_ids),
        "official_source_resolution_percent": round(100 * len(resolved_ids) / max(1, len(benchmark_ids)), 2),
        "missing_from_organisation_registry": missing,
        "without_enabled_official_source": sorted(benchmark_ids - resolved_ids),
        "official_source_status_counts": dict(sorted(status_counts.items())),
        "official_source_resolution": resolutions,
        "sectors": {
            sector: {"benchmark": total, "registered": sector_registered[sector], "resolved": sector_resolved[sector]}
            for sector, total in sorted(sector_totals.items())
        },
        "runtime_truth_note": "The benchmark checks organisation-name coverage only; production facts come from official configured sources.",
    }


def coverage_report(*, config_root: Path | None = None) -> dict[str, Any]:
    root = config_root or ROOT / "config"
    organisations = load_organisations(config_root=root)
    sources = load_sources(config_root=root)
    enabled = [item for item in sources if item.get("enabled", True)]
    return {
        "organisations": len(organisations),
        "sources": len(sources),
        "enabled_sources": len(enabled),
        "unresolved_organisations": len(unresolved_organisations(config_root=root)),
        "sources_by_adapter": dict(sorted(Counter(item.get("adapter", "html") for item in sources).items())),
        "sources_by_tier": dict(sorted(Counter(item.get("scan_tier", "daily") for item in enabled).items())),
        "sources_by_provenance": dict(sorted(Counter(item.get("provenance", "curated") for item in sources).items())),
        "organisations_by_sector": dict(sorted(Counter(sector for item in organisations for sector in item.get("sectors", ["other"])).items())),
        "trackr": benchmark_coverage("trackr", config_root=root),
    }


def unresolved_organisations(*, config_root: Path | None = None) -> list[dict[str, Any]]:
    root = config_root or ROOT / "config"
    sources = load_sources(config_root=root)
    enabled_orgs = {item.get("organisation_id") for item in sources if item.get("enabled", True)}
    return [
        {"id": item["id"], "name": item["name"], "sectors": item.get("sectors", []), "resolution_status": item.get("source_resolution_status", "unresolved")}
        for item in load_organisations(config_root=root)
        if item["id"] not in enabled_orgs
    ]


def _append_registry(path: Path, key: str, value: dict[str, Any]) -> None:
    payload = load_json(path, {"schema_version": 1, key: []})
    values = payload.get(key, []) if isinstance(payload, dict) else payload
    values.append(value)
    values.sort(key=lambda item: item["id"])
    _write_json(path, {"schema_version": 1, key: values})


def add_source(*, name: str, url: str, organisation_id: str | None = None, source_id: str | None = None,
               category: str = "custom", scan_tier: str = "daily", enabled: bool | None = None,
               config_root: Path | None = None) -> dict[str, Any]:
    root = config_root or ROOT / "config"
    organisations = load_organisations(config_root=root)
    requested_organisation_id = organisation_id or slug(name)
    organisation_match = next((item for item in organisations if requested_organisation_id == item["id"] or requested_organisation_id in item.get("aliases", []) or slug(name) == slug(item["name"])), None)
    organisation_id = organisation_match["id"] if organisation_match else requested_organisation_id
    source_id = source_id or f"{organisation_id}-opportunities"
    if any(item["id"] == source_id for item in load_sources(config_root=root)):
        raise ValueError(f"source id already exists: {source_id}")
    if scan_tier not in TIERS:
        raise ValueError(f"scan tier must be one of: {', '.join(sorted(TIERS))}")
    detected = detect_source(url)
    duplicate = next((item for item in load_sources(config_root=root) if item["url"].rstrip("/").lower() == detected["url"].rstrip("/").lower()), None)
    if duplicate:
        raise ValueError(f"source URL already exists on {duplicate['id']}")
    safe_enabled = not detected["review_required"] if enabled is None else enabled
    if not any(item["id"] == organisation_id for item in load_organisations(config_root=root)):
        _append_registry(root / "organisations" / "custom.json", "organisations", {
            "id": organisation_id, "name": name, "sectors": [category], "organisation_type": "employer",
            "provenance": ["cli"], "source_resolution_status": "resolved" if safe_enabled else "needs_validation",
        })
    source = {
        "id": source_id,
        "name": f"{name} opportunities",
        "organisation_id": organisation_id,
        "url": detected["url"],
        "profile": detected["profile"],
        "adapter": detected["adapter"],
        "scan_tier": scan_tier,
        "enabled": safe_enabled,
        "resolution_status": "resolved" if not detected["review_required"] else "candidate_url_needs_validation",
        "adapter_detection": {"confidence": detected["confidence"], "original_url": url},
        "provenance": "cli",
    }
    filename = f"{slug(category) or 'custom'}.json"
    _append_registry(root / "sources" / filename, "sources", source)
    return source


def remove_source(source_id: str, *, purge: bool = False, config_root: Path | None = None) -> dict[str, Any]:
    root = config_root or ROOT / "config"
    for path in sorted((root / "sources").glob("*.json")):
        payload = load_json(path, {})
        values = payload.get("sources", payload if isinstance(payload, list) else [])
        for item in values:
            if item.get("id") != source_id:
                continue
            if purge:
                values.remove(item)
                action = "purged"
            else:
                item["enabled"] = False
                item["resolution_status"] = "disabled_by_cli"
                action = "disabled"
            _write_json(path, {"schema_version": payload.get("schema_version", 1), "sources": values})
            return {"source_id": source_id, "action": action, "registry_file": path.name}
    raise ValueError(f"unknown source id: {source_id}")


def test_source(source_id: str, *, timeout: float = 12, config_root: Path | None = None) -> dict[str, Any]:
    source = next((item for item in load_sources(config_root=config_root or ROOT / "config") if item["id"] == source_id), None)
    if not source:
        raise ValueError(f"unknown source id: {source_id}")
    result = scan_source(source, timeout)
    expected = source.get("expected_terms", [])
    canary = "failed" if expected and "expected content" in result.warning else "passed" if expected else "not_configured"
    return {
        "source_id": source_id, "url": source["url"], "adapter": source.get("adapter", "html"),
        "profile": source.get("profile", "custom"),
        "http_status": result.http_status, "listings": len(result.listings), "index_listings": result.index_listing_count,
        "likely_opportunity_matches": len(result.listings), "parser_canary_status": canary,
        "detail_fetches": result.detail_fetch_count, "detail_successes": result.detail_success_count,
        "warning": result.warning, "parser_error": result.parser_error,
    }


def export_registry(*, config_root: Path | None = None) -> dict[str, Any]:
    root = config_root or ROOT / "config"
    return {
        "schema_version": 1,
        "organisations": [{key: value for key, value in item.items() if key != "registry_file"} for item in load_organisations(config_root=root)],
        "sources": [{key: value for key, value in item.items() if key != "registry_file"} for item in load_sources(config_root=root)],
    }


def import_registry(path: Path, *, category: str = "imported", config_root: Path | None = None) -> dict[str, Any]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            values = list(csv.DictReader(handle))
    else:
        payload = load_json(path)
        values = payload.get("sources", payload if isinstance(payload, list) else [])
    added = []
    rejected = []
    for item in values:
        enabled_value = item.get("enabled")
        if isinstance(enabled_value, str):
            enabled_value = enabled_value.strip().lower() in {"1", "true", "yes", "enabled"} if enabled_value.strip() else None
        try:
            added.append(add_source(
                name=item["name"], url=item["url"], organisation_id=item.get("organisation_id") or None,
                source_id=item.get("id") or None, category=item.get("sector") or category,
                scan_tier=item.get("scan_tier") or "weekly", enabled=enabled_value, config_root=config_root,
            ))
        except ValueError as exc:
            rejected.append({"id": item.get("id"), "url": item.get("url"), "reason": str(exc)})
    return {"imported": len(added), "source_ids": [item["id"] for item in added], "rejected": rejected}
