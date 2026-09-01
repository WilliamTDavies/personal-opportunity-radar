"""Evidence-based eligibility evaluation using only explicitly known facts."""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import Eligibility, Opportunity, Requirement, RequirementStrength, RuleEvaluation, RuleOutcome


def academic_year_on(profile: dict[str, Any], when: date) -> int:
    start = date.fromisoformat(profile["start_date"])
    if when < start:
        return 0
    return when.year - start.year + (1 if when.month >= 9 else 0)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _opportunity_study_year(profile: dict[str, Any], start_date: str | None) -> int:
    if not start_date or len(start_date) < 4:
        return 0
    try:
        suffix = start_date[5:].lower() if len(start_date) > 5 else ""
        month = 4 if suffix == "spring" else 7 if suffix == "summer" else int(start_date[5:7]) if len(start_date) >= 7 else 7
        return academic_year_on(profile, date(int(start_date[:4]), month, 1))
    except ValueError:
        return 0


def _evaluate(requirement: Requirement, profile: dict[str, Any], start_date: str | None) -> RuleEvaluation:
    rule, wanted = requirement.rule, requirement.value
    outcome, reason = RuleOutcome.UNKNOWN, "The profile does not contain enough evidence."
    if rule == "graduation_year":
        actual = profile.get("graduation_year")
        outcome = RuleOutcome.MET if actual in _as_list(wanted) else RuleOutcome.CONFLICT
        reason = f"Known graduation year is {actual}; accepted: {', '.join(map(str, _as_list(wanted)))}."
    elif rule == "study_year":
        year, accepted = _opportunity_study_year(profile, start_date), {int(item) for item in _as_list(wanted)}
        outcome = RuleOutcome.MET if year in accepted else RuleOutcome.CONFLICT
        reason = f"Candidate would be in academic year {year}; accepted: {sorted(accepted)}."
    elif rule == "penultimate_year":
        actual = bool(start_date and len(start_date) >= 4 and int(start_date[:4]) == int(profile["graduation_year"]) - 1)
        outcome = RuleOutcome.MET if actual == bool(wanted) else RuleOutcome.CONFLICT
        reason = f"Known degree dates make penultimate-year status {actual} for this cycle."
    elif rule == "degree_subject":
        course, accepted = profile.get("course", "").lower(), [str(item).lower() for item in _as_list(wanted)]
        outcome = RuleOutcome.MET if any(item in course for item in accepted) else RuleOutcome.CONFLICT
        reason = f"Known course is {profile.get('course')}; required subject set: {accepted}."
    elif rule == "undergraduate":
        year = _opportunity_study_year(profile, start_date)
        outcome = RuleOutcome.MET if wanted and 1 <= year <= 3 else RuleOutcome.UNKNOWN
        reason = f"Known degree dates place the candidate in undergraduate year {year} for the programme date."
    elif rule == "postgraduate_required":
        outcome = RuleOutcome.CONFLICT if wanted and str(profile.get("course", "")).lower().startswith("bsc") else RuleOutcome.UNKNOWN
        reason = f"Known course is {profile.get('course')}; the opportunity requires postgraduate status."
    elif rule == "uk_university":
        institution_country = profile.get("institution_country")
        outcome = RuleOutcome.MET if wanted and institution_country == "United Kingdom" else RuleOutcome.UNKNOWN
        reason = f"Known institution is {profile.get('institution')} in {institution_country or 'an unstated country'}."
    elif rule == "institution_country":
        actual = str(profile.get("institution_country", ""))
        accepted = [str(item) for item in _as_list(wanted)]
        outcome = RuleOutcome.MET if actual.lower() in {item.lower() for item in accepted} else RuleOutcome.CONFLICT
        reason = f"Known university country is {actual or 'not stated'}; accepted: {accepted}."
    elif rule == "institution":
        actual = profile.get("institution", "")
        outcome = RuleOutcome.MET if actual.lower() in {str(item).lower() for item in _as_list(wanted)} else RuleOutcome.CONFLICT
        reason = f"Known institution is {actual}."
    elif rule == "external_students":
        outcome = RuleOutcome.MET if bool(wanted) else RuleOutcome.CONFLICT
        reason = "The programme explicitly states whether external students are accepted."
    elif rule == "nationality":
        actual = str(profile.get("nationality", ""))
        accepted = [str(item) for item in _as_list(wanted)]
        outcome = RuleOutcome.MET if any(actual.lower() in item.lower() or item.lower() in actual.lower() for item in accepted) else RuleOutcome.CONFLICT
        reason = f"Known nationality is {actual}."
    elif rule in {"uk_residency", "residency", "age", "grade", "work_authorization", "sponsorship"}:
        outcome = RuleOutcome.UNKNOWN
        reason = f"{rule.replace('_', ' ').title()} is deliberately not inferred from the profile."
    elif rule in {"security_clearance", "security_vetting"}:
        outcome = RuleOutcome.NOT_APPLICABLE
        reason = "Security vetting is recorded as a later appointment condition, not predicted as an eligibility result."
    elif rule == "worldwide":
        outcome = RuleOutcome.MET if wanted else RuleOutcome.NOT_APPLICABLE
        reason = "The official criteria explicitly describe worldwide access."
    elif rule == "same_university_team":
        outcome, reason = RuleOutcome.MET, "This is an actionable team condition, not a profile conflict."
    elif rule == "identity_restricted":
        outcome = RuleOutcome.CONFLICT if wanted else RuleOutcome.NOT_APPLICABLE
        reason = "The programme is defined by an identity restriction." if wanted else "No programme-level restriction."
    return RuleEvaluation(rule=rule, strength=requirement.strength, outcome=outcome, reason=reason)


def evaluate(record: Opportunity, profile: dict[str, Any]) -> Opportunity:
    record.rule_evaluations = [_evaluate(requirement, profile, record.start_date) for requirement in record.requirements]
    required = [item for item in record.rule_evaluations if item.strength is RequirementStrength.REQUIRED]
    preferred = [item for item in record.rule_evaluations if item.strength in {RequirementStrength.PREFERRED, RequirementStrength.DESIRABLE}]
    if record.identity_targeted or any(item.rule == "identity_restricted" and item.outcome is RuleOutcome.CONFLICT for item in required):
        record.eligibility = Eligibility.INELIGIBLE
    elif any(item.outcome is RuleOutcome.CONFLICT for item in required):
        record.eligibility = Eligibility.INELIGIBLE
    elif any(item.outcome is RuleOutcome.UNKNOWN for item in required):
        record.eligibility = Eligibility.UNCERTAIN
    elif required and all(item.outcome in {RuleOutcome.MET, RuleOutcome.NOT_APPLICABLE} for item in required):
        record.eligibility = Eligibility.ELIGIBLE
    elif any(item.outcome is RuleOutcome.CONFLICT for item in preferred):
        record.eligibility = Eligibility.LIKELY
    reasons = [item.reason for item in record.rule_evaluations if item.outcome in {RuleOutcome.CONFLICT, RuleOutcome.UNKNOWN}]
    if reasons:
        record.eligibility_note = "Needs eligibility check: " + " ".join(reasons)
    return record
