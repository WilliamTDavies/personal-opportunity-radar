from __future__ import annotations

from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Opportunity, PriorityTier


TRACKING_KEYS = {"fbclid", "gclid", "ref", "source", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parts.query) if key.lower() not in TRACKING_KEYS))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _normalise(value: str) -> str:
    return " ".join("".join(character.lower() if character.isalnum() else " " for character in value).split())


def _keys(record: Opportunity) -> set[str]:
    identity = f"{_normalise(record.organisation)}|{_normalise(record.title)}"
    keys = {
        f"stable:{record.canonical_id}",
        *(f"stable:{alias}" for alias in [*record.aliases, *record.merged_alias_ids]),
        f"url:{canonical_url(record.source_url)}|{identity}",
    }
    if record.application_url:
        keys.add(f"application:{canonical_url(record.application_url)}")
    return keys


def deduplicate(records: list[Opportunity]) -> list[Opportunity]:
    """Merge official/aggregator and alias collisions while preserving stable IDs."""

    def merge_pair(current: Opportunity, incoming: Opportunity) -> Opportunity:
        winner = incoming if (incoming.source_kind == "discovered" and current.source_kind != "discovered") or incoming.confidence > current.confidence else current
        aliases = sorted({
            *current.aliases, *current.merged_alias_ids,
            *incoming.aliases, *incoming.merged_alias_ids,
            current.canonical_id, incoming.canonical_id,
        } - {winner.canonical_id})
        evidence = current.evidence + [item for item in incoming.evidence if item not in current.evidence]
        # Manual records are explicit fallbacks for sources that sometimes
        # block automation. Once an official live parse succeeds it should
        # replace the fallback rather than create a false official conflict.
        conflicts = []
        if current.source_kind != "manual" and incoming.source_kind != "manual":
            for field in ("deadline", "opens_at", "lifecycle", "eligibility"):
                if getattr(current, field) != getattr(incoming, field) and getattr(current, field) is not None and getattr(incoming, field) is not None:
                    conflicts.append(field)
        reasons = sorted({
            *current.review_reasons, *incoming.review_reasons,
            *(f"conflicting official {field}" for field in conflicts),
        })
        return replace(
            winner, aliases=aliases, merged_alias_ids=aliases, evidence=evidence,
            source_conflict=bool(conflicts) or current.source_conflict or incoming.source_conflict,
            review_required=bool(conflicts) or current.review_required or incoming.review_required,
            review_reasons=reasons,
        )

    owners: dict[str, str] = {}
    records_by_owner: dict[str, Opportunity] = {}
    for record in records:
        keys = _keys(record)
        matching_owners = sorted({owners[key] for key in keys if key in owners})
        if not matching_owners:
            owner = record.canonical_id
            records_by_owner[owner] = record
        else:
            owner = matching_owners[0]
            current = records_by_owner[owner]
            # A new record can bridge two previously separate clusters (for
            # example an ATS job URL plus a historical title alias). Collapse
            # every matched owner and remap its keys before adding the bridge;
            # otherwise both clusters survive with the same alias.
            for merged_owner in matching_owners[1:]:
                current = merge_pair(current, records_by_owner.pop(merged_owner))
                owners = {key: owner if value == merged_owner else value for key, value in owners.items()}
            records_by_owner[owner] = merge_pair(current, record)
        for key in keys:
            owners[key] = owner
    tier_order = {PriorityTier.A: 0, PriorityTier.B: 1, PriorityTier.C: 2}
    return sorted(records_by_owner.values(), key=lambda item: (tier_order[item.priority_tier], item.deadline or "9999-12-31", item.title.lower()))


def alias_map(records: list[Opportunity]) -> dict[str, str]:
    return {alias: record.canonical_id for record in records for alias in [*record.aliases, *record.merged_alias_ids]}
