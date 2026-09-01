"""Discovery, normalization, lifecycle, merge, validation and publication pipeline."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .adapters import AdapterResult, Listing, scan_source
from .config import ROOT, load_json, load_overrides, load_profile, load_sources
from .dedupe import alias_map, canonical_url, deduplicate
from .eligibility import evaluate
from .extractor import extract_listing, is_formal_title
from .models import (
    DeadlineStatus, Eligibility, Evidence, Lifecycle, Opportunity, PriorityTier,
    SourceHealth, SourceStatus, Stream,
)
from .validation import validate


DISCOVERED = ROOT / "data" / "discovered" / "opportunities.json"
MANUAL = ROOT / "data" / "manual" / "additions.json"
GENERATED = ROOT / "data" / "opportunities.json"
PUBLIC = ROOT / "public" / "data" / "opportunities.json"
HEALTH = ROOT / "data" / "source_health.json"
COVERAGE = ROOT / "data" / "coverage_report.json"
REVIEW = ROOT / "data" / "review_queue.json"
ARCHIVE = ROOT / "data" / "archive" / "opportunities.json"
CHANGES = ROOT / "data" / "change_log.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_records(path: Path) -> list[Opportunity]:
    payload = load_json(path, [])
    items = payload.get("opportunities", []) if isinstance(payload, dict) else payload
    return [Opportunity.from_dict(item) for item in items]


def load_discovered_records(path: Path = DISCOVERED) -> list[Opportunity]:
    return _load_records(path)


def load_manual_records(path: Path = MANUAL) -> list[Opportunity]:
    """Backward-compatible name: manual additions are an optional overlay only."""
    return _load_records(path)


def load_canonical_records(path: Path = GENERATED) -> list[Opportunity]:
    return _load_records(path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result[:72] or hashlib.sha256(value.encode()).hexdigest()[:16]


def _stream_for(text: str, source: dict[str, Any]) -> Stream | None:
    if source.get("stream"):
        return Stream(source["stream"])
    lowered = text.lower()
    if any(term in lowered for term in ("spring", "insight", "discovery", "first year")):
        return Stream.SPRING
    if any(term in lowered for term in ("research", "bursary", "studentship", "summer project")):
        return Stream.RESEARCH
    if any(term in lowered for term in ("competition", "challenge", "hackathon", "contest", "academy")):
        return Stream.COMPETITIONS
    if "intern" in lowered:
        return Stream.INTERNSHIPS
    return None


def _lifecycle(text: str, fallback: Lifecycle = Lifecycle.UNKNOWN) -> tuple[Lifecycle, str]:
    lowered = " ".join(text.lower().split())
    if re.search(r"register (your )?interest|join (our )?talent (community|network)|express (your )?interest", lowered):
        return Lifecycle.INTEREST_OPEN, "Register Interest"
    if re.search(r"applications? (are |is )?(now )?open|apply now|submit (an |your )?application", lowered):
        return Lifecycle.OPEN, "Apply"
    if re.search(r"applications? (are |is )?closed|no longer accepting applications", lowered):
        return Lifecycle.CLOSED, "Explore"
    if re.search(r"applications? (will )?open (on |in )", lowered):
        return Lifecycle.ANNOUNCED, "Apply"
    return fallback, "Explore"


def _identity_targeted(text: str) -> bool:
    lowered = text.lower()
    programme_markers = (
        "programme for women", "program for women", "black heritage programme",
        "lgbtq+ programme", "ethnic minority programme", "female students only",
    )
    return any(marker in lowered for marker in programme_markers)


def _new_record(source: dict[str, Any], listing: Listing, checked_at: str, profile: dict[str, Any] | None = None) -> Opportunity | None:
    """Backward-compatible entry point for the generic detail-page extractor."""

    return extract_listing(source, listing, checked_at, profile or load_profile())


def _apply_template(source: dict[str, Any], template: dict[str, Any], result: AdapterResult, checked_at: str) -> Opportunity | None:
    # Career pages frequently write dates with ordinal suffixes (``4th``),
    # while source templates use ISO-like natural language (``4 December``).
    # Normalising both sides avoids silently dropping an otherwise verified
    # programme—and its structured deadline—because of that presentation-only
    # difference.
    def normalise_match_text(text: str) -> str:
        lowered = text.lower()
        without_ordinals = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", lowered)
        return " ".join(without_ordinals.split())

    searchable = normalise_match_text(" ".join(f"{item.title} {item.body}" for item in result.listings))
    terms = [normalise_match_text(term) for term in template.get("match_terms", source.get("expected_terms", []))]
    if terms and not all(term in searchable for term in terms):
        return None
    value = {**template}
    value.setdefault("source_url", source["url"])
    value.setdefault("source_id", source["id"])
    value.setdefault("source_kind", "discovered")
    value.setdefault("checked_at", checked_at[:10])
    value.setdefault("first_seen", checked_at)
    value["last_seen"] = checked_at
    value.setdefault("last_changed", checked_at)
    value["content_fingerprint"] = result.fingerprint
    value.setdefault("confidence", 0.8)
    value.setdefault("application_url", value.get("source_url") if value.get("lifecycle") == "open" else None)
    value.setdefault("discovered_via", source["url"])
    value.setdefault("primary_evidence_url", value["source_url"])
    value.setdefault("last_verified", checked_at)
    value.setdefault("deadline_last_verified", checked_at if value.get("deadline") else None)
    value.setdefault("source_family", source.get("family", source["id"]))
    value["template_dependent"] = True
    value.setdefault("parser_version", 3)
    value.setdefault("evidence", [{"statement": f"The official source matched: {', '.join(terms) or template['title']}.", "source_url": value["source_url"], "checked_at": checked_at[:10], "source_type": "official"}])
    record = Opportunity.from_dict(value)
    detected, action = _lifecycle(searchable, record.lifecycle)
    if template.get("detect_lifecycle", False):
        record.lifecycle = detected
        record.primary_action = action
    return record


def _changes(before: Opportunity, after: Opportunity, when: str) -> list[dict[str, Any]]:
    tracked = (
        "title", "source_url", "application_url", "lifecycle", "deadline",
        "deadline_status", "opens_at", "start_date", "end_date", "eligibility",
        "location", "requirements",
    )
    changes = []
    for field in tracked:
        old, new = getattr(before, field), getattr(after, field)
        old = old.value if hasattr(old, "value") else old
        new = new.value if hasattr(new, "value") else new
        if field == "requirements":
            old = [f"{item.rule}:{item.value}:{item.strength.value}" for item in old]
            new = [f"{item.rule}:{item.value}:{item.strength.value}" for item in new]
        if old != new:
            changes.append({"canonical_id": after.canonical_id, "changed_at": when, "field": field, "from": old, "to": new})
    if changes:
        after.last_changed = when
        after.change_summary = [f"{item['field']}: {item['from']} → {item['to']}" for item in changes]
    return changes


def _record_url_keys(record: Opportunity) -> set[str]:
    return {
        canonical_url(url)
        for url in (record.source_url, record.application_url, record.primary_evidence_url, *record.alternate_sources)
        if url
    }


def _quality_candidates(records: list[Opportunity]) -> list[Opportunity]:
    """Keep review as an exception path by suppressing obvious navigation noise."""

    uk_markers = ("united kingdom", "london", "bristol", "birmingham", "manchester", "edinburgh", "glasgow", "durham", "cambridge", "oxford", "england", "scotland", "wales", "northern ireland")
    non_uk_markers = ("singapore", "hong kong", "shanghai", "chicago", "new york", "boston", "north america", "united states", "austin", "san francisco", "seattle", "california", "texas", "sydney", "tokyo", "mumbai", "bengaluru", "bangalore", "paris", "france", "denver", "colorado", "honolulu", "washington, d.c.", "palo alto", "seoul", "south korea", "toronto", "canada")

    def low_value(record: Opportunity) -> bool:
        if (record.parser_version >= 3 and record.source_kind == "discovered"
                and "automatically discovered" in record.tags and not is_formal_title(record.title, record.source_url)):
            return True
        if "experienced professionals" in record.title.lower():
            return True
        location = f"{record.location} {record.country} {record.city}".lower()
        # Keep the discovery layer broad, but do not flood personal review with
        # roles whose only stated offices are clearly outside the UK.
        return bool(location and any(term in location for term in non_uk_markers) and not any(term in location for term in uk_markers))

    return [record for record in records if not low_value(record)]


def _find_previous(fresh: Opportunity, existing: list[Opportunity], claimed: set[str]) -> Opportunity | None:
    for record in existing:
        if record.canonical_id not in claimed and fresh.canonical_id in {record.canonical_id, *record.aliases, *record.merged_alias_ids}:
            return record
    fresh_urls = _record_url_keys(fresh)
    for record in existing:
        if record.canonical_id not in claimed and fresh_urls.intersection(_record_url_keys(record)):
            return record
    candidates = [record for record in existing if record.canonical_id not in claimed and record.source_id == fresh.source_id and record.organisation.lower() == fresh.organisation.lower()]
    return next((record for record in candidates if SequenceMatcher(None, record.title.lower(), fresh.title.lower()).ratio() >= 0.86), None)


def _preserve_identity(previous: Opportunity, fresh: Opportunity, when: str) -> Opportunity:
    generated_id = fresh.canonical_id
    fresh.canonical_id = previous.canonical_id
    fresh.first_seen = previous.first_seen or when
    fresh.missing_count = 0
    fresh.aliases = sorted({*previous.aliases, *fresh.aliases, *( [generated_id] if generated_id != previous.canonical_id else [] )})
    fresh.merged_alias_ids = sorted({*previous.merged_alias_ids, *fresh.merged_alias_ids})
    return fresh


def _merge_scan(existing: list[Opportunity], sources: list[dict[str, Any]], results: dict[str, AdapterResult], when: str) -> tuple[list[Opportunity], list[dict[str, Any]]]:
    """Re-extract every matched detail page and track individual presence."""

    scanned_ids = {item["id"] for item in sources}
    configured_ids = {item["id"] for item in load_sources()}
    reclassified_ids = {
        item["id"] for item in sources
        if (result := results.get(item["id"]))
        and result.http_status == 200 and not result.warning and not result.parser_error
    }
    source_by_url = {canonical_url(source["url"]): source for source in sources}
    # Remove legacy link-only noise once its source is successfully processed by
    # the detail-page parser. It was never a reviewed opportunity and should not
    # survive as hidden seed data.
    retained = [item for item in existing if not (
        item.parser_version < 3 and item.review_required and item.confidence <= 0.55
        and "automatically discovered" in item.tags
        and (item.source_id in scanned_ids or item.source_id not in configured_ids)
    ) and not (
        item.parser_version >= 3 and item.review_required
        and item.source_kind == "discovered" and "automatically discovered" in item.tags
        and item.source_id in reclassified_ids
        and not is_formal_title(item.title, item.source_url)
    )]
    for record in retained:
        if not record.source_id:
            source = source_by_url.get(canonical_url(record.source_url))
            if source:
                record.source_id = source["id"]

    output = list(retained)
    changes: list[dict[str, Any]] = []
    claimed: set[str] = set()
    profile = load_profile()
    healthy_source_ids: set[str] = set()

    for source in sources:
        result = results.get(source["id"])
        if not result or result.http_status != 200 or result.warning:
            continue
        # Partial detail failures can still yield verified new records, but
        # absence on a partial parse must never age a retained record.
        if not result.parser_error:
            healthy_source_ids.add(source["id"])
        generic_records = [record for listing in result.listings if (record := _new_record(source, listing, when, profile))]

        # Templates normally remain exceptional fallbacks. A small number of
        # official family pages interleave several programme cohorts in one
        # document, so generic extraction can attach a neighbouring cohort's
        # requirements to the requested programme. Those sources may opt into
        # a verified, term-gated template; if its evidence is absent, the live
        # generic extraction still wins rather than publishing stale metadata.
        if source.get("prefer_verified_template"):
            template_records = [
                record for template in source.get("programmes", [])
                if (record := _apply_template(source, template, result, when))
            ]
            fresh_records = template_records or generic_records
        else:
            fresh_records = generic_records
            if not fresh_records:
                fresh_records = [
                    record for template in source.get("programmes", [])
                    if (record := _apply_template(source, template, result, when))
                ]

        for fresh in fresh_records:
            previous = _find_previous(fresh, output, claimed)
            if previous:
                before = Opportunity.from_dict(previous.to_dict())
                fresh = _preserve_identity(previous, fresh, when)
                claimed.add(previous.canonical_id)
                changes.extend(_changes(before, fresh, when))
                output[output.index(previous)] = fresh
            else:
                fresh.first_seen = fresh.first_seen or when
                fresh.last_changed = fresh.last_changed or when
                output.append(fresh)
                claimed.add(fresh.canonical_id)

    # A successful family scan is not proof that every historical listing is
    # still present. Absence is counted only on healthy parses; failures and
    # degraded pages leave listing state untouched.
    for index, record in enumerate(list(output)):
        if record.source_id not in healthy_source_ids or record.canonical_id in claimed:
            continue
        before = Opportunity.from_dict(record.to_dict())
        record.missing_count += 1
        if record.missing_count >= 3 and record.lifecycle not in {Lifecycle.CLOSED, Lifecycle.STALE}:
            record.lifecycle = Lifecycle.STALE
            record.review_required = False
            record.review_reasons = sorted({*record.review_reasons, "absent from three consecutive healthy source scans"})
        changes.extend(_changes(before, record, when))
        output[index] = record
    return output, changes


def _apply_overrides(records: list[Opportunity]) -> list[Opportunity]:
    overrides = load_overrides()
    result = []
    for record in records:
        values = overrides.get(record.canonical_id, {})
        payload = record.to_dict()
        payload.update(values)
        if values:
            payload["source_kind"] = "override"
        result.append(Opportunity.from_dict(payload))
    return result


def _assign_source_ids(records: list[Opportunity]) -> list[Opportunity]:
    sources = load_sources()
    by_url = {canonical_url(item["url"]): item["id"] for item in sources}
    for record in records:
        if not record.source_id:
            record.source_id = by_url.get(canonical_url(record.source_url), "")
    return records


def _priority(record: Opportunity) -> PriorityTier:
    if record.eligibility in {Eligibility.ELIGIBLE, Eligibility.LIKELY} and record.lifecycle in {Lifecycle.OPEN, Lifecycle.INTEREST_OPEN}:
        return PriorityTier.A
    if record.eligibility is not Eligibility.INELIGIBLE and record.lifecycle in {Lifecycle.ANNOUNCED, Lifecycle.UNKNOWN}:
        return PriorityTier.B
    return PriorityTier.C


def _public(records: list[Opportunity]) -> tuple[list[Opportunity], list[Opportunity], list[Opportunity]]:
    public, review, archive = [], [], []
    today = date.today()
    for record in records:
        record.priority_tier = _priority(record)
        past_deadline = False
        if record.deadline:
            try:
                past_deadline = date.fromisoformat(record.deadline[:10]) < today
            except ValueError:
                record.review_required = True
                record.review_reasons.append("deadline is not ISO-8601")
        if past_deadline and record.lifecycle in {Lifecycle.OPEN, Lifecycle.INTEREST_OPEN, Lifecycle.ANNOUNCED}:
            record.lifecycle = Lifecycle.CLOSED
        if record.review_required:
            review.append(record)
        if record.identity_targeted or record.eligibility is Eligibility.INELIGIBLE or record.lifecycle in {Lifecycle.CLOSED, Lifecycle.STALE}:
            archive.append(record)
        elif not record.review_required:
            public.append(record)
    return public, review, archive


def build_artifact(records: list[Opportunity] | None = None, output: Path = GENERATED, *, write: bool = True) -> dict[str, Any]:
    discovered = _quality_candidates(records if records is not None else load_discovered_records())
    discovered_urls = {_record_url for item in discovered for _record_url in _record_url_keys(item)}
    manual_overlay = [item for item in load_manual_records() if not _record_url_keys(item).intersection(discovered_urls)]
    combined = deduplicate(_apply_overrides(_assign_source_ids([*discovered, *manual_overlay])))
    profile = load_profile()
    combined = [evaluate(item, profile) for item in combined]
    errors = validate(combined)
    if errors:
        raise ValueError("\n".join(errors))
    public, review, archive = _public(combined)
    public = deduplicate(public)
    errors = validate(public, public=True)
    if errors:
        raise ValueError("\n".join(errors))
    now = utc_now()
    artifact = {
        "schema_version": 2, "generated_at": now, "profile_version": 1,
        "alias_map": alias_map(combined), "opportunities": [item.to_dict() for item in public],
    }
    if write:
        _write_json(output, artifact)
        if output == GENERATED:
            _write_json(PUBLIC, artifact)
            _write_json(REVIEW, {"generated_at": now, "opportunities": [item.to_dict() for item in review]})
            _write_json(ARCHIVE, {"generated_at": now, "opportunities": [item.to_dict() for item in archive]})
    return artifact


def _health(source: dict[str, Any], result: AdapterResult, checked_at: str, previous: dict[str, Any] | None = None) -> SourceHealth:
    previous = previous or {}
    configured_streams = source.get("streams") or ([source["stream"]] if source.get("stream") else [])
    if result.not_modified:
        previous_status = SourceStatus(previous.get("status", "healthy"))
        return SourceHealth(
            source_id=source["id"], url=source["url"], status=previous_status,
            adapter=source.get("adapter", "html"), last_attempted=checked_at,
            last_http_success=checked_at, last_parse_success=previous.get("last_parse_success"),
            http_status=304, listing_count=int(previous.get("listing_count", 0)),
            relevant_count=int(previous.get("relevant_count", 0)), freshness_days=previous.get("freshness_days"),
            content_fingerprint=result.fingerprint or previous.get("content_fingerprint", ""), elapsed_ms=result.elapsed_ms,
            source_name=source.get("name", source["id"]), source_family=source.get("family", source["id"]),
            streams=list(configured_streams), enabled=source.get("enabled", True),
            transport_status="not_modified", parser_status="not_run_unchanged", opportunity_status="not_run_unchanged",
            last_nonzero_parse=previous.get("last_nonzero_parse"),
            last_known_listing_count=int(previous.get("last_known_listing_count", previous.get("listing_count", 0))),
            parser_canary_status=previous.get("parser_canary_status", "not_configured"),
            last_successful_opportunity_extraction=previous.get("last_successful_opportunity_extraction"),
            etag=result.etag or previous.get("etag", ""), last_modified=result.last_modified or previous.get("last_modified", ""),
            not_modified=True,
        )
    profile = load_profile()
    relevant_count = sum(extract_listing(source, listing, checked_at, profile) is not None for listing in result.listings)
    transport_status = "ok" if result.http_status == 200 else "failed"
    parser_status = "failed" if result.parser_error and result.http_status == 200 else "warning" if result.warning and result.http_status == 200 else "ok" if result.http_status == 200 else "not_run"
    if relevant_count:
        opportunity_status = "nonzero"
    elif result.http_status != 200:
        opportunity_status = "not_run"
    elif source.get("allow_zero", False):
        opportunity_status = "zero_allowed"
    else:
        opportunity_status = "zero_unexpected"
    if result.http_status == 200 and parser_status == "ok" and opportunity_status != "zero_unexpected":
        status = SourceStatus.HEALTHY
    elif result.http_status == 200:
        status = SourceStatus.DEGRADED
    else:
        status = SourceStatus.FAILED
    last_http_success = checked_at if result.http_status == 200 else previous.get("last_http_success")
    last_parse_success = checked_at if status is SourceStatus.HEALTHY else previous.get("last_parse_success")
    freshness_days = None
    if last_parse_success:
        try:
            freshness_days = max(0, (date.fromisoformat(checked_at[:10]) - date.fromisoformat(last_parse_success[:10])).days)
        except ValueError:
            freshness_days = None
    if status is SourceStatus.FAILED and freshness_days is not None and freshness_days > 30:
        status = SourceStatus.STALE
    expected = source.get("expected_terms", [])
    parser_canary_status = "failed" if expected and "expected content" in result.warning else "passed" if expected else "not_configured"
    return SourceHealth(
        source_id=source["id"], url=source["url"], status=status, adapter=source.get("adapter", "html"),
        last_attempted=checked_at, last_http_success=last_http_success,
        last_parse_success=last_parse_success, http_status=result.http_status,
        listing_count=len(result.listings), relevant_count=relevant_count, warning=result.warning,
        freshness_days=freshness_days, content_fingerprint=result.fingerprint or previous.get("content_fingerprint", ""), elapsed_ms=result.elapsed_ms,
        source_name=source.get("name", source["id"]), source_family=source.get("family", source["id"]),
        streams=list(configured_streams), enabled=source.get("enabled", True), parser_error=result.parser_error,
        detail_fetch_count=result.detail_fetch_count, detail_success_count=result.detail_success_count,
        transport_status=transport_status, parser_status=parser_status, opportunity_status=opportunity_status,
        last_nonzero_parse=checked_at if result.listings else previous.get("last_nonzero_parse"),
        last_known_listing_count=len(result.listings) if parser_status == "ok" else int(previous.get("last_known_listing_count", previous.get("listing_count", 0))),
        parser_canary_status=parser_canary_status,
        last_successful_opportunity_extraction=checked_at if relevant_count else previous.get("last_successful_opportunity_extraction"),
        etag=result.etag or previous.get("etag", ""), last_modified=result.last_modified or previous.get("last_modified", ""),
        not_modified=False,
    )


def _select_sources(source_ids: set[str] | None, streams: set[Stream] | None, tiers: set[str] | None,
                    limit: int | None) -> list[dict[str, Any]]:
    sources = [item for item in load_sources() if item.get("enabled", True) and (not source_ids or item["id"] in source_ids)]
    if streams:
        sources = [item for item in sources if not item.get("stream") or Stream(item["stream"]) in streams]
    if tiers:
        sources = [item for item in sources if item.get("scan_tier", "daily") in tiers]
    return sources[:limit] if limit is not None else sources


def _fetch_sources(sources: list[dict[str, Any]], workers: int, timeout: float,
                   previous_health: dict[str, dict[str, Any]] | None = None) -> dict[str, AdapterResult]:
    results: dict[str, AdapterResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(scan_source, source, timeout, previous_health.get(source["id"], {}) if previous_health is not None else None): source
            for source in sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                results[source["id"]] = future.result()
            except Exception as exc:  # One source must never abort unrelated families.
                results[source["id"]] = AdapterResult(None, [], "", str(exc), parser_error=str(exc))
    return results


def _combined_records(discovered: list[Opportunity]) -> list[Opportunity]:
    discovered = _quality_candidates(discovered)
    discovered_urls = {_record_url for item in discovered for _record_url in _record_url_keys(item)}
    manual_overlay = [item for item in load_manual_records() if not _record_url_keys(item).intersection(discovered_urls)]
    combined = deduplicate(_apply_overrides(_assign_source_ids([*discovered, *manual_overlay])))
    profile = load_profile()
    return [evaluate(item, profile) for item in combined]


def _family_coverage(sources: list[dict[str, Any]], health: list[SourceHealth]) -> list[dict[str, Any]]:
    """Summarise operational coverage at organisation/source-family level."""

    health_by_id = {item.source_id: item for item in health}
    families: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        if source.get("enabled", True):
            families.setdefault(source.get("family", source["id"]), []).append(source)

    summaries: list[dict[str, Any]] = []
    for family, members in sorted(families.items()):
        member_health = [health_by_id[item["id"]] for item in members if item["id"] in health_by_id]
        parsed = sum(item.status is SourceStatus.HEALTHY for item in member_health)
        reachable = sum(item.http_status in {200, 304} for item in member_health)
        failed = sum(item.status in {SourceStatus.FAILED, SourceStatus.STALE} for item in member_health)
        if member_health and parsed == len(members):
            status = "healthy"
        elif member_health and not reachable:
            status = "failed"
        else:
            status = "degraded"
        summaries.append({
            "family": family,
            "organisations": sorted({item.get("organisation", item.get("name", item["id"])) for item in members}),
            "source_ids": [item["id"] for item in members],
            "status": status,
            "configured": len(members),
            "attempted": len(member_health),
            "reachable": reachable,
            "parsed": parsed,
            "failed_or_stale": failed,
            "listings": sum(item.listing_count for item in member_health),
            "relevant": sum(item.relevant_count for item in member_health),
            "detail_pages_fetched": sum(item.detail_fetch_count for item in member_health),
            "detail_pages_parsed": sum(item.detail_success_count for item in member_health),
        })
    return summaries


def run_scan(*, source_ids: set[str] | None = None, streams: set[Stream] | None = None, tiers: set[str] | None = None,
             limit: int | None = None,
             workers: int = 6, timeout: float = 12, dry_run: bool = False, allow_partial: bool = False,
             offline: bool = False, clean_slate: bool = False) -> tuple[dict[str, Any], list[SourceHealth]]:
    sources = _select_sources(source_ids, streams, tiers, limit)
    when, results = utc_now(), {}
    previous_health = {item["source_id"]: item for item in load_json(HEALTH, [])}
    if not offline:
        results = _fetch_sources(sources, workers, timeout, previous_health)
    health = [_health(source, results[source["id"]], when, previous_health.get(source["id"])) for source in sources if source["id"] in results]
    failures = [item for item in health if item.status is SourceStatus.FAILED]
    if failures and not allow_partial:
        raise RuntimeError(f"{len(failures)} source(s) failed; rerun with --allow-partial to preserve successful results")
    existing = [] if clean_slate else load_discovered_records()
    merged, changes = _merge_scan(existing, sources, results, when) if not offline else (existing, [])
    artifact = build_artifact(merged, write=not dry_run)
    if not dry_run:
        _write_json(DISCOVERED, {"schema_version": 2, "updated_at": when, "opportunities": [item.to_dict() for item in merged]})
        previous_changes = load_json(CHANGES, [])
        _write_json(CHANGES, [*previous_changes, *changes][-1000:])
        if not offline:
            all_source_ids = {item["id"] for item in load_sources() if item.get("enabled", True)}
            merged_health = {
                source_id: item for source_id, item in previous_health.items()
                if source_id in all_source_ids
            }
            merged_health.update({item.source_id: item.to_dict() for item in health})
            persisted_health = [SourceHealth(
                **{**item, "status": SourceStatus(item["status"])}
            ) for item in merged_health.values()]
            persisted_health.sort(key=lambda item: item.source_id)
            _write_json(HEALTH, [item.to_dict() for item in persisted_health])
            configured_sources = load_sources()
            from .registry import coverage_report as registry_coverage_report

            registry_coverage = registry_coverage_report()
            previous_coverage = load_json(COVERAGE, {})
            is_full_scan = {item["id"] for item in sources} == all_source_ids
            last_full_scan = when if is_full_scan else previous_coverage.get("last_full_scan")
            last_full_scan_sources = len(sources) if is_full_scan else previous_coverage.get("last_full_scan_sources")
            if not last_full_scan:
                attempted = Counter(item.last_attempted for item in persisted_health if item.last_attempted)
                inferred_at, inferred_count = attempted.most_common(1)[0] if attempted else (None, 0)
                if inferred_count >= max(1, int(len(all_source_ids) * 0.8)):
                    last_full_scan, last_full_scan_sources = inferred_at, inferred_count
            _write_json(COVERAGE, {
                "generated_at": when, "configured": len(all_source_ids), "scanned_this_run": len(sources),
                "last_full_scan": last_full_scan, "last_full_scan_sources": last_full_scan_sources,
                "reachable": sum(item.http_status in {200, 304} for item in persisted_health),
                "parsed": sum(item.status is SourceStatus.HEALTHY for item in persisted_health),
                "degraded": sum(item.status is SourceStatus.DEGRADED for item in persisted_health),
                "stale": sum(item.status is SourceStatus.STALE for item in persisted_health),
                "failed": sum(item.status is SourceStatus.FAILED for item in persisted_health),
                "listings": sum(item.listing_count for item in persisted_health),
                "relevant": sum(item.relevant_count for item in persisted_health),
                "detail_pages_fetched": sum(item.detail_fetch_count for item in persisted_health),
                "detail_pages_parsed": sum(item.detail_success_count for item in persisted_health),
                "not_modified": sum(item.not_modified for item in persisted_health),
                "transport_states": dict(Counter(item.transport_status for item in persisted_health)),
                "parser_states": dict(Counter(item.parser_status for item in persisted_health)),
                "opportunity_states": dict(Counter(item.opportunity_status for item in persisted_health)),
                "universe": registry_coverage,
                "families": _family_coverage(configured_sources, persisted_health),
                "sources": [item.to_dict() for item in persisted_health],
            })
    return artifact, health


def clean_slate_test(*, source_ids: set[str] | None = None, streams: set[Stream] | None = None,
                     tiers: set[str] | None = None,
                     limit: int | None = None, workers: int = 6, timeout: float = 12,
                     write_report: bool = True) -> dict[str, Any]:
    """Prove what a network scan reconstructs with no discovered seed state."""

    sources = _select_sources(source_ids, streams, tiers, limit)
    when = utc_now()
    results = _fetch_sources(sources, workers, timeout)
    previous_health = {item["source_id"]: item for item in load_json(HEALTH, [])}
    health = [_health(source, results[source["id"]], when, previous_health.get(source["id"])) for source in sources]
    discovered, changes = _merge_scan([], sources, results, when)
    combined = _combined_records(discovered)
    public, review, archive = _public(combined)
    previous_records = {item.canonical_id: item for item in load_canonical_records()}
    previous_public = set(previous_records)
    rebuilt_public = {item.canonical_id for item in public}
    rebuilt_historical = previous_public.intersection(rebuilt_public)
    non_manual_public = {identifier for identifier, item in previous_records.items() if item.source_kind != "manual"}
    rebuilt_non_manual = non_manual_public.intersection(rebuilt_public)
    health_by_source = {item.source_id: item for item in health}
    missing_ids = sorted(previous_public - rebuilt_public)
    missing_details = []
    for canonical_id in missing_ids:
        record = previous_records[canonical_id]
        source_health = health_by_source.get(record.source_id)
        if record.source_kind == "manual":
            reason = "manual_overlay_not_rebuilt_from_scanner"
        elif source_health and source_health.status in {SourceStatus.FAILED, SourceStatus.STALE}:
            reason = "official_source_inaccessible"
        elif source_health and source_health.status is SourceStatus.DEGRADED:
            reason = "parser_or_zero_listing_degraded"
        elif record.template_dependent:
            reason = "verified_template_not_matched"
        elif not source_health:
            reason = "no_enabled_source_scanned"
        else:
            reason = "current_primary_source_did_not_reconstruct_historical_record"
        missing_details.append({
            "canonical_id": canonical_id, "source_id": record.source_id, "source_kind": record.source_kind,
            "template_dependent": record.template_dependent, "reason": reason,
            "source_status": source_health.status.value if source_health else "not_scanned",
            "http_status": source_health.http_status if source_health else None,
            "warning": source_health.warning if source_health else "",
        })
    report = {
        "schema_version": 1,
        "generated_at": when,
        "clean_slate": True,
        "sources_configured": len([item for item in load_sources() if item.get("enabled", True)]),
        "sources_scanned": len(sources),
        "healthy_sources": sum(item.status is SourceStatus.HEALTHY for item in health),
        "degraded_sources": sum(item.status is SourceStatus.DEGRADED for item in health),
        "failed_sources": sum(item.status is SourceStatus.FAILED for item in health),
        "detail_pages_fetched": sum(item.detail_fetch_count for item in health),
        "detail_pages_parsed": sum(item.detail_success_count for item in health),
        "discovered_records": len(discovered),
        "public_records": len(public),
        "auto_published_discovered": sum(item.source_kind == "discovered" and bool(item.auto_publish_reason) for item in public),
        "review_records": len(review),
        "archived_records": len(archive),
        "manual_public_records": sum(item.source_kind == "manual" for item in public),
        "template_dependent_public_records": sum(item.template_dependent for item in public),
        "historical_public_baseline": len(previous_public),
        "historical_public_reconstructed": len(rebuilt_historical),
        "historical_public_reconstruction_percent": round(100 * len(rebuilt_historical) / max(1, len(previous_public)), 2),
        "non_manual_public_baseline": len(non_manual_public),
        "non_manual_public_reconstructed": len(rebuilt_non_manual),
        "non_manual_public_reconstruction_percent": round(100 * len(rebuilt_non_manual) / max(1, len(non_manual_public)), 2),
        "historical_public_records_not_reconstructed": missing_ids,
        "historical_public_records_not_reconstructed_details": missing_details,
        "change_events": len(changes),
        "source_results": [item.to_dict() for item in health],
    }
    if write_report:
        _write_json(ROOT / "data" / "clean_slate_report.json", report)
    return report


def audit_sources() -> dict[str, Any]:
    sources, records = load_sources(), load_discovered_records()
    configured = {item["id"] for item in sources}
    enabled = [item for item in sources if item.get("enabled", True)]
    mismatches = []
    seen_urls: dict[str, str] = {}
    for source in sources:
        url = canonical_url(source["url"])
        if url in seen_urls:
            mismatches.append({"source_id": source["id"], "issue": "duplicate configured URL", "other_source_id": seen_urls[url]})
        seen_urls[url] = source["id"]
    for record in records:
        if not record.source_id or record.source_id not in configured:
            mismatches.append({"canonical_id": record.canonical_id, "issue": "missing or unknown source_id", "source_id": record.source_id})
    return {
        "configured_sources": len(sources),
        "enabled_sources": len(enabled),
        "disabled_sources": len(sources) - len(enabled),
        "source_families": len({item.get("family", item["id"]) for item in enabled}),
        "discovered_records": len(records),
        "mismatches": mismatches,
    }
