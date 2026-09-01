"""Typed domain model for the opportunity radar.

The public JSON contains evidence and rule outcomes rather than an opaque
score. Dataclasses accept older v1 records so snapshots retain stable IDs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Stream(str, Enum):
    SPRING = "spring_insight"
    RESEARCH = "research"
    COMPETITIONS = "competitions_development"
    INTERNSHIPS = "internships"


class Lifecycle(str, Enum):
    OPEN = "open"
    INTEREST_OPEN = "interest_open"
    ANNOUNCED = "officially_announced"
    UNKNOWN = "unknown"
    CLOSED = "closed"
    STALE = "stale"


class Eligibility(str, Enum):
    ELIGIBLE = "eligible"
    LIKELY = "likely_eligible"
    UNCERTAIN = "uncertain"
    INELIGIBLE = "ineligible"


class DeadlineStatus(str, Enum):
    FIXED = "fixed"
    ROLLING = "rolling"
    UNKNOWN = "unknown"
    NONE_STATED = "none_stated"


class PriorityTier(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class RequirementStrength(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    DESIRABLE = "desirable"
    UNCLEAR = "unclear"
    INFORMATIONAL = "informational"


class RuleOutcome(str, Enum):
    MET = "met"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class SourceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    FAILED = "failed"


@dataclass(slots=True)
class Evidence:
    statement: str
    source_url: str
    checked_at: str
    source_type: str = "official"


@dataclass(slots=True)
class Requirement:
    rule: str
    value: Any
    strength: RequirementStrength = RequirementStrength.REQUIRED
    evidence: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Requirement":
        return cls(
            rule=value["rule"], value=value.get("value"),
            strength=RequirementStrength(value.get("strength", "required")),
            evidence=value.get("evidence", ""),
        )


@dataclass(slots=True)
class RuleEvaluation:
    rule: str
    strength: RequirementStrength
    outcome: RuleOutcome
    reason: str


@dataclass(slots=True)
class Opportunity:
    canonical_id: str
    title: str
    organisation: str
    stream: Stream
    lifecycle: Lifecycle
    eligibility: Eligibility
    primary_action: str
    source_url: str
    location: str = ""
    start_date: str | None = None
    end_date: str | None = None
    deadline: str | None = None
    deadline_status: DeadlineStatus = DeadlineStatus.UNKNOWN
    deadline_source: str | None = None
    deadline_verified: bool = False
    rolling: bool = False
    opens_at: str | None = None
    research_mode: str | None = None
    summary: str = ""
    why_it_fits: str = ""
    eligibility_note: str = ""
    next_step: str = ""
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    merged_alias_ids: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    rule_evaluations: list[RuleEvaluation] = field(default_factory=list)
    checked_at: str = ""
    first_seen: str = ""
    last_seen: str = ""
    last_changed: str = ""
    source_id: str = ""
    source_kind: str = "discovered"
    content_fingerprint: str = ""
    confidence: float = 0.0
    priority_tier: PriorityTier = PriorityTier.C
    review_required: bool = False
    review_reasons: list[str] = field(default_factory=list)
    identity_targeted: bool = False
    access_scope: str = "unknown"
    source_conflict: bool = False
    change_summary: list[str] = field(default_factory=list)
    opportunity_type: str = ""
    career_fields: list[str] = field(default_factory=list)
    subject_fields: list[str] = field(default_factory=list)
    country: str = ""
    city: str = ""
    work_mode: str = "unknown"
    cycle: str = ""
    application_url: str | None = None
    discovered_via: str = ""
    primary_evidence_url: str = ""
    alternate_sources: list[str] = field(default_factory=list)
    last_verified: str = ""
    deadline_last_verified: str | None = None
    missing_count: int = 0
    source_family: str = ""
    security_vetting: str | None = None
    required_documents: list[str] = field(default_factory=list)
    duration: str | None = None
    template_dependent: bool = False
    auto_publish_reason: str = ""
    parser_version: int = 3

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Opportunity":
        evidence = [item if isinstance(item, Evidence) else Evidence(**item) for item in value.get("evidence", [])]
        requirements = [item if isinstance(item, Requirement) else Requirement.from_dict(item) for item in value.get("requirements", [])]
        evaluations = [
            item if isinstance(item, RuleEvaluation) else RuleEvaluation(
                rule=item["rule"], strength=RequirementStrength(item.get("strength", "required")),
                outcome=RuleOutcome(item["outcome"]), reason=item.get("reason", ""),
            ) for item in value.get("rule_evaluations", [])
        ]
        deadline = value.get("deadline")
        deadline_status = value.get("deadline_status") or ("fixed" if deadline else "none_stated")
        priority = value.get("priority_tier")
        if not priority:
            legacy_score = int(value.get("relevance_score", 0))
            priority = "A" if legacy_score >= 90 else "B" if legacy_score >= 75 else "C"
        return cls(
            canonical_id=value["canonical_id"], title=value["title"], organisation=value["organisation"],
            stream=Stream(value["stream"]), lifecycle=Lifecycle(value["lifecycle"]),
            eligibility=Eligibility(value.get("eligibility", "uncertain")),
            primary_action=value.get("primary_action", "Review"), source_url=value["source_url"],
            location=value.get("location", ""), start_date=value.get("start_date"), end_date=value.get("end_date"),
            deadline=deadline, deadline_status=DeadlineStatus(deadline_status), deadline_source=value.get("deadline_source"),
            deadline_verified=bool(value.get("deadline_verified", bool(deadline))),
            rolling=bool(value.get("rolling", False) or deadline_status == "rolling"), opens_at=value.get("opens_at"),
            research_mode=value.get("research_mode"), summary=value.get("summary", ""), why_it_fits=value.get("why_it_fits", ""),
            eligibility_note=value.get("eligibility_note", ""), next_step=value.get("next_step", ""),
            tags=list(value.get("tags", [])), aliases=list(value.get("aliases", [])),
            merged_alias_ids=list(value.get("merged_alias_ids", [])), evidence=evidence, requirements=requirements,
            rule_evaluations=evaluations, checked_at=value.get("checked_at", ""),
            first_seen=value.get("first_seen", value.get("checked_at", "")),
            last_seen=value.get("last_seen", value.get("checked_at", "")),
            last_changed=value.get("last_changed", value.get("checked_at", "")), source_id=value.get("source_id", ""),
            source_kind=value.get("source_kind", "discovered"), content_fingerprint=value.get("content_fingerprint", ""),
            confidence=float(value.get("confidence", 0.0)), priority_tier=PriorityTier(priority),
            review_required=bool(value.get("review_required", False)), review_reasons=list(value.get("review_reasons", [])),
            identity_targeted=bool(value.get("identity_targeted", False)), access_scope=value.get("access_scope", "unknown"),
            source_conflict=bool(value.get("source_conflict", False)), change_summary=list(value.get("change_summary", [])),
            opportunity_type=value.get("opportunity_type", ""), career_fields=list(value.get("career_fields", [])),
            subject_fields=list(value.get("subject_fields", [])), country=value.get("country", ""),
            city=value.get("city", ""), work_mode=value.get("work_mode", "unknown"), cycle=value.get("cycle", ""),
            application_url=value.get("application_url"), discovered_via=value.get("discovered_via", ""),
            primary_evidence_url=value.get("primary_evidence_url", value.get("source_url", "")),
            alternate_sources=list(value.get("alternate_sources", [])),
            last_verified=value.get("last_verified", value.get("checked_at", "")),
            deadline_last_verified=value.get("deadline_last_verified"), missing_count=int(value.get("missing_count", 0)),
            source_family=value.get("source_family", ""), security_vetting=value.get("security_vetting"),
            required_documents=list(value.get("required_documents", [])), duration=value.get("duration"),
            template_dependent=bool(value.get("template_dependent", False)),
            auto_publish_reason=value.get("auto_publish_reason", ""), parser_version=int(value.get("parser_version", 2)),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update(stream=self.stream.value, lifecycle=self.lifecycle.value, eligibility=self.eligibility.value,
                     deadline_status=self.deadline_status.value, priority_tier=self.priority_tier.value)
        for requirement in value["requirements"]:
            requirement["strength"] = requirement["strength"].value
        for evaluation in value["rule_evaluations"]:
            evaluation["strength"] = evaluation["strength"].value
            evaluation["outcome"] = evaluation["outcome"].value
        return value


@dataclass(slots=True)
class SourceHealth:
    source_id: str
    url: str
    status: SourceStatus
    adapter: str
    last_attempted: str
    last_http_success: str | None = None
    last_parse_success: str | None = None
    http_status: int | None = None
    listing_count: int = 0
    relevant_count: int = 0
    warning: str = ""
    freshness_days: int | None = None
    content_fingerprint: str = ""
    elapsed_ms: int = 0
    source_name: str = ""
    source_family: str = ""
    streams: list[str] = field(default_factory=list)
    enabled: bool = True
    parser_error: str = ""
    detail_fetch_count: int = 0
    detail_success_count: int = 0
    transport_status: str = "unknown"
    parser_status: str = "unknown"
    opportunity_status: str = "unknown"
    last_nonzero_parse: str | None = None
    last_known_listing_count: int = 0
    parser_canary_status: str = "not_configured"
    last_successful_opportunity_extraction: str | None = None
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value
