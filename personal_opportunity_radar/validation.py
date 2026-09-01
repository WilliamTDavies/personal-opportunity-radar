from __future__ import annotations

from collections import Counter
from datetime import date
from urllib.parse import urlparse

from .models import DeadlineStatus, Eligibility, Lifecycle, Opportunity, Stream


ALLOWED_ACTIONS = {"Apply", "Contact Supervisor", "Enter", "Explore", "Form Team", "Join Society", "Register", "Register Interest", "Review", "Submit Proposal"}


def validate(records: list[Opportunity], *, today: date | None = None, public: bool = False) -> list[str]:
    now, errors, identifiers = today or date.today(), [], Counter()
    aliases: dict[str, str] = {}
    for index, record in enumerate(records):
        prefix = f"opportunities[{index}] ({record.canonical_id})"
        identifiers[record.canonical_id] += 1
        if record.primary_action not in ALLOWED_ACTIONS:
            errors.append(f"{prefix}: unsupported primary_action {record.primary_action!r}")
        if urlparse(record.source_url).scheme != "https":
            errors.append(f"{prefix}: source_url must use https")
        if not record.source_id:
            errors.append(f"{prefix}: source_id is required")
        if not record.evidence:
            errors.append(f"{prefix}: at least one evidence item is required")
        if record.stream is Stream.RESEARCH and not record.research_mode:
            errors.append(f"{prefix}: research_mode is required for research records")
        if record.lifecycle is Lifecycle.ANNOUNCED and not record.opens_at:
            errors.append(f"{prefix}: officially_announced requires opens_at")
        if record.lifecycle is Lifecycle.INTEREST_OPEN and record.primary_action != "Register Interest":
            errors.append(f"{prefix}: interest_open requires Register Interest action")
        if record.deadline_status is DeadlineStatus.FIXED and not record.deadline:
            errors.append(f"{prefix}: fixed deadline requires deadline")
        if record.deadline_status is DeadlineStatus.ROLLING and not record.rolling:
            errors.append(f"{prefix}: rolling deadline must set rolling")
        if record.deadline:
            try:
                parsed = date.fromisoformat(record.deadline[:10])
                if public and record.lifecycle in {Lifecycle.OPEN, Lifecycle.INTEREST_OPEN, Lifecycle.ANNOUNCED} and parsed < now:
                    errors.append(f"{prefix}: active public record has a past deadline")
            except ValueError:
                errors.append(f"{prefix}: deadline is not ISO-8601")
        if record.eligibility is Eligibility.UNCERTAIN and "check" not in (record.eligibility_note + record.next_step).lower():
            errors.append(f"{prefix}: uncertain eligibility must explain a check")
        if record.confidence < 0 or record.confidence > 1:
            errors.append(f"{prefix}: confidence must be between 0 and 1")
        if public and (record.identity_targeted or record.eligibility is Eligibility.INELIGIBLE or record.review_required):
            errors.append(f"{prefix}: suppressed/review record entered public output")
        for alias in [*record.aliases, *record.merged_alias_ids]:
            if alias in aliases and aliases[alias] != record.canonical_id:
                errors.append(f"{prefix}: alias {alias!r} owned by multiple records")
            aliases[alias] = record.canonical_id
    errors.extend(f"duplicate canonical_id: {key}" for key, count in identifiers.items() if count > 1)
    return errors

