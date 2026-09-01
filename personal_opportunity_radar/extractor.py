"""Generic structured extraction from independently discovered detail pages."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from .adapters import Listing
from .eligibility import evaluate
from .models import (
    DeadlineStatus,
    Eligibility,
    Evidence,
    Lifecycle,
    Opportunity,
    PriorityTier,
    Requirement,
    RequirementStrength,
    Stream,
)


PARSER_VERSION = 3
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})\s*,?\s*(20\d{{2}})\b", re.I)
DATE_MDY = re.compile(rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(20\d{{2}})\b", re.I)
DATE_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")

GENERIC_TITLES = {
    "careers", "early careers", "students", "student", "programmes", "programs",
    "opportunities", "research", "internships", "jobs", "current opportunities",
    "academy", "esa academy", "graduates", "research opportunities",
    "competitions and hackathons", "training future opportunities",
}
NOISE_TERMS = (
    "privacy policy", "cookie", "newsletter", "site map", "sitemap", "logo",
    "success stor", "generic webinar", "career fair", "mba", "phd", "postdoctoral",
    "high school", "school pupils", "current puzzle", "blog",
)


def _normalise(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:72]


def _date_value(match: re.Match[str]) -> str | None:
    try:
        if match.re is DATE_ISO:
            year, month, day = map(int, match.groups())
        elif match.re is DATE_DMY:
            day, month_name, year = match.groups()
            day, month, year = int(day), MONTHS[month_name.lower()], int(year)
        else:
            month_name, day, year = match.groups()
            day, month, year = int(day), MONTHS[month_name.lower()], int(year)
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _first_date(value: str) -> str | None:
    matches: list[tuple[int, str]] = []
    for pattern in (DATE_ISO, DATE_DMY, DATE_MDY):
        for match in pattern.finditer(value):
            parsed = _date_value(match)
            if parsed:
                matches.append((match.start(), parsed))
    return min(matches, default=(0, None), key=lambda item: item[0])[1]


def _labelled_date(text: str, labels: tuple[str, ...]) -> str | None:
    lowered = text.lower()
    positions = [lowered.find(label) for label in labels if lowered.find(label) >= 0]
    for position in sorted(positions):
        parsed = _first_date(text[position: position + 180])
        if parsed:
            return parsed
    return None


def _programme_dates(text: str) -> tuple[str | None, str | None]:
    range_pattern = re.compile(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})(?:\s+20\d{{2}})?\s*(?:-|–|to)\s*"
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})\s+(20\d{{2}})\b",
        re.I,
    )
    match = range_pattern.search(text)
    if match:
        start_day, start_month, end_day, end_month, year = match.groups()
        try:
            return (
                date(int(year), MONTHS[start_month.lower()], int(start_day)).isoformat(),
                date(int(year), MONTHS[end_month.lower()], int(end_day)).isoformat(),
            )
        except ValueError:
            return None, None
    month_first = re.search(
        rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:-|–|to)\s*"
        rf"({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(20\d{{2}})\b",
        text,
        re.I,
    )
    if month_first:
        start_month, start_day, end_month, end_day, year = month_first.groups()
        try:
            return (
                date(int(year), MONTHS[start_month.lower()], int(start_day)).isoformat(),
                date(int(year), MONTHS[end_month.lower()], int(end_day)).isoformat(),
            )
        except ValueError:
            return None, None
    return None, None


def _stream(title: str, text: str, source: dict[str, Any]) -> Stream | None:
    hint = source.get("stream")
    title_lower = title.lower()
    joined = f"{title} {text[:8000]}".lower()
    # Genuine employment/placements take precedence even when the work is research.
    if re.search(r"\b(intern(ship)?|industrial placement|student placement|vacation placement)\b", title_lower):
        return Stream.INTERNSHIPS
    if any(term in title_lower for term in ("spring insight", "spring week", "spring into", "discovery programme", "discovery program", "first-year programme", "first year programme", "fttp", "bridge london", "see london", "insight event")) or re.search(r"\bfirst[ -]year\b.{0,90}\b(?:programme|program|insight|event)\b", title_lower):
        return Stream.SPRING
    if hint:
        return Stream(hint)
    if any(term in joined for term in ("spring insight", "spring week", "discovery programme", "discovery program", "first-year programme", "first year programme", "fttp", "bridge london", "see london", "insight event")) or re.search(r"\bfirst[ -]year\b.{0,90}\b(?:programme|program|insight|event)\b", joined):
        return Stream.SPRING
    if any(term in joined for term in ("research programme", "research program", "research project", "studentship", "research bursary", "summer research", "supervisor")):
        return Stream.RESEARCH
    if any(term in joined for term in ("competition", "challenge", "contest", "hackathon", "academy", "summer school", "masterclass", "fellowship", "scholarship")):
        return Stream.COMPETITIONS
    return None


def _identity_targeted(text: str) -> bool:
    lowered = text.lower()
    formal_patterns = (
        r"(?:programme|program|event|scheme)\s+(?:is\s+)?(?:exclusively\s+)?for women\b",
        r"open (?:only|exclusively) to (?:women|female students|black students|lgbtq\+? students)",
        r"black heritage (?:programme|program|insight|internship)",
        r"students who identify as (?:women|female|black|lgbtq)",
        r"(?:women|female students|black students|lgbtq\+? students) only\b",
        r"(?:programme|program)[^.!]{0,80}for[^.!]{0,100}under-represented groups[^.!]{0,100}(?:women|ethnic minority)",
    )
    return any(re.search(pattern, lowered) for pattern in formal_patterns)


def _action_url(listing: Listing, interest: bool = False) -> str | None:
    for key in ("application_url", "applyUrl", "apply_url", "absolute_url", "hostedUrl", "jobUrl"):
        value = listing.raw.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    actions = listing.raw.get("actions", [])
    preferred = ("register interest", "express interest", "sign up to be notified") if interest else ("apply", "submit application", "register")
    for action in actions:
        label = str(action.get("title", "")).lower()
        url = str(action.get("url", ""))
        if url.startswith("http") and any(term in label for term in preferred):
            return url
    return None


def _lifecycle(text: str, listing: Listing, checked_at: str) -> tuple[Lifecycle, str, str | None, str | None]:
    lowered = text.lower()
    interest = bool(re.search(r"register (?:your )?interest|express (?:your )?interest|join (?:our )?talent (?:community|network)|sign up to be notified", lowered))
    interest_url = _action_url(listing, interest=True)
    apply_url = _action_url(listing)
    deadline = _labelled_date(text, ("application deadline", "applications close", "closing date", "deadline", "apply by"))
    opens_at = _labelled_date(text, ("applications open", "applications will open", "opening date", "opens on"))
    if listing.raw.get("application_url") and apply_url:
        return Lifecycle.OPEN, "Apply", opens_at, deadline
    if interest and interest_url:
        return Lifecycle.INTEREST_OPEN, "Register Interest", opens_at, deadline
    if re.search(r"applications? (?:are|is|have)?\s*(?:now )?closed|no longer accepting applications|this opportunity has closed", lowered):
        return Lifecycle.CLOSED, "Explore", opens_at, deadline
    if opens_at:
        try:
            if date.fromisoformat(opens_at) > date.fromisoformat(checked_at[:10]):
                return Lifecycle.ANNOUNCED, "Apply", opens_at, deadline
        except ValueError:
            pass
    if apply_url or re.search(r"applications? (?:are|is) (?:now )?open|accepting applications|apply now|submit (?:an|your) application", lowered):
        return Lifecycle.OPEN, "Apply", opens_at, deadline
    if interest:
        return Lifecycle.INTEREST_OPEN, "Register Interest", opens_at, deadline
    return Lifecycle.UNKNOWN, "Explore", opens_at, deadline


def _requirements(text: str, start_date: str | None) -> tuple[list[Requirement], str | None]:
    lowered = text.lower()
    requirements: list[Requirement] = []

    graduation_years = sorted({int(year) for year in re.findall(r"(?:graduat(?:e|ing)|complet(?:e|ing) (?:your )?studies)[^.!;]{0,45}\b(20\d{2})\b", lowered)})
    if graduation_years:
        requirements.append(Requirement("graduation_year", graduation_years, evidence="Official graduation-year wording"))

    if re.search(r"\b(?:first|1st)[ -]year (?:undergraduate|student)s?\b|\bstudents? in (?:their )?first year\b|\bfirst-? and second-year\b", lowered):
        accepted = [1, 2] if re.search(r"first(?:(?:-year)?|-)? (?:or|and) second(?:-year)?", lowered) else [1]
        requirements.append(Requirement("study_year", accepted, evidence="Official year-of-study wording"))
    elif re.search(r"(?:at least|minimum of) two (?:full )?academic years|first-year undergraduates? will not be considered", lowered):
        requirements.append(Requirement("study_year", [2, 3], evidence="Official completed-study requirement"))
    elif re.search(r"penultimate(?: or final)? year", lowered):
        requirements.append(Requirement("penultimate_year", True, evidence="Official penultimate-year requirement"))
    elif re.search(r"\bundergraduate students?\b|\benrolled (?:on|in) (?:an? )?undergraduate", lowered):
        requirements.append(Requirement("undergraduate", True, evidence="Official undergraduate-status requirement"))

    if re.search(
        r"(?:must|required to|applicants? (?:should|must))[^.!]{0,80}(?:hold|have)[^.!]{0,40}(?:master(?:['’]s)?|postgraduate) degree|"
        r"\bhold (?:an? )?master(?:['’]s)? degree|"
        r"(?:postgraduate|graduate) students? only|only open to (?:postgraduate|graduate) students?",
        lowered,
    ):
        requirements.append(Requirement("postgraduate_required", True, evidence="Official postgraduate-level requirement"))

    subjects = [subject for subject in ("mathematics", "physics", "computer science", "engineering", "statistics", "data science") if subject in lowered]
    subject_context = re.search(r"(?:must|required|open to|studying|degree in|background in)[^.!]{0,120}(?:mathematics|physics|computer science|engineering|stem)", lowered)
    preferred_context = re.search(r"(?:prefer|preferred|desirable)[^.!]{0,100}(?:mathematics|physics|computer science|engineering|stem)", lowered)
    if subject_context or preferred_context:
        accepted = subjects or ["mathematics", "physics", "computer science", "engineering"]
        strength = RequirementStrength.PREFERRED if preferred_context and not subject_context else RequirementStrength.REQUIRED
        requirements.append(Requirement("degree_subject", accepted, strength, "Official subject wording"))

    if re.search(r"students? (?:from|at) any uk universit|enrolled at a uk universit", lowered):
        requirements.append(Requirement("uk_university", True, evidence="Official UK-university access wording"))
    country_restriction = re.search(r"(?:students?[^.!]{0,50})?(?:enrolled|studying) (?:at|in) universit(?:y|ies) in ([^.!]{3,180})", lowered)
    if country_restriction:
        countries = [
            country for country in (
                "United Kingdom", "Austria", "Belgium", "Denmark", "Germany", "Netherlands",
                "Norway", "Sweden", "Switzerland", "India", "United States", "Canada",
            ) if country.lower() in country_restriction.group(1)
        ]
        if countries:
            requirements.append(Requirement("institution_country", countries, evidence="Official university-country restriction"))
    if re.search(r"external students? (?:are )?(?:eligible|welcome|may apply)|students? from other universit(?:y|ies) (?:may|can) apply", lowered):
        requirements.append(Requirement("external_students", True, evidence="Official external-student wording"))
    if re.search(r"only (?:open|available) to (?:current )?students? (?:of|at) ([a-z][a-z .&'-]+ university)", lowered):
        host = re.search(r"only (?:open|available) to (?:current )?students? (?:of|at) ([a-z][a-z .&'-]+ university)", lowered)
        if host:
            requirements.append(Requirement("institution", [host.group(1).title()], evidence="Official host-university restriction"))
    if re.search(r"students? (?:from )?(?:around the world|worldwide)|international students? (?:are )?(?:eligible|welcome|may apply)", lowered):
        requirements.append(Requirement("worldwide", True, evidence="Official international-access wording"))

    if re.search(r"british citizens? only|must be (?:a )?(?:british citizen|uk national)|uk nationals? only", lowered):
        requirements.append(Requirement("nationality", ["British"], evidence="Official nationality requirement"))
    if re.search(r"(?:uk |united kingdom )?residen(?:t|cy)(?: requirement|required| for)|resident in (?:the )?uk for", lowered):
        requirements.append(Requirement("uk_residency", True, evidence="Official residency wording"))
    if re.search(r"right to work|work authori[sz]ation|eligible to work", lowered):
        requirements.append(Requirement("work_authorization", True, evidence="Official work-authorisation wording"))

    vetting = None
    if re.search(r"developed vetting|\bdv clearance\b", lowered):
        vetting = "Developed Vetting"
    elif re.search(r"security check|\bsc clearance\b|security clearance|security vetting|\bvetting\b", lowered):
        vetting = "Security vetting"
    if vetting:
        requirements.append(Requirement("security_vetting", vetting, RequirementStrength.INFORMATIONAL, "Appointment is subject to official vetting"))

    unique: dict[tuple[str, str], Requirement] = {}
    for requirement in requirements:
        unique[(requirement.rule, str(requirement.value))] = requirement
    return list(unique.values()), vetting


def _fields(text: str) -> tuple[list[str], list[str]]:
    lowered = text.lower()
    careers = [label for label, terms in {
        "quant": ("quant", "trading", "market making"), "software": ("software", "programming", "developer"),
        "finance": ("finance", "banking", "markets", "investment"), "cyber": ("cyber", "cryptography", "security"),
        "research": ("research",), "engineering": ("engineering", "hardware", "fpga"), "data": ("data", "machine learning", "ai"),
    }.items() if any(term in lowered for term in terms)]
    subjects = [subject for subject in ("mathematics", "physics", "computer science", "statistics", "engineering", "data science") if subject in lowered]
    return careers, subjects


def _location(value: str) -> tuple[str, str, str, str]:
    lowered = value.lower()
    city = next((name for name in ("London", "Durham", "Edinburgh", "Manchester", "Cambridge", "Oxford", "Bristol", "Toronto", "Geneva", "Amsterdam", "Dublin") if name.lower() in lowered), "")
    country = "United Kingdom" if any(term in lowered for term in ("london", "durham", "edinburgh", "manchester", "cambridge", "oxford", "bristol", "united kingdom", " uk")) else ""
    if "canada" in lowered or "toronto" in lowered:
        country = "Canada"
    elif "switzerland" in lowered or "geneva" in lowered:
        country = "Switzerland"
    elif "netherlands" in lowered or "amsterdam" in lowered:
        country = "Netherlands"
    elif "ireland" in lowered or "dublin" in lowered:
        country = "Ireland"
    mode = "remote" if "remote" in lowered or "online" in lowered else "hybrid" if "hybrid" in lowered else "on_site"
    return value, city, country, mode


def _summary(listing: Listing, text: str) -> str:
    description = _normalise(str(listing.raw.get("description", "")))
    if 45 <= len(description) <= 500:
        return description
    sentences = re.split(r"(?<=[.!?])\s+", text)
    terms = [term for term in re.findall(r"[a-z]{5,}", listing.title.lower()) if term not in {"programme", "program", "internship", "spring"}]
    for sentence in sentences:
        clean = _normalise(sentence)
        if 55 <= len(clean) <= 450 and (not terms or any(term in clean.lower() for term in terms[:4])):
            return clean
    return _normalise(text)[:320].rstrip()


def is_formal_title(title: str, url: str = "") -> bool:
    """Reject navigation/marketing labels before they become review records."""

    lowered_title = title.lower().strip()
    if lowered_title in GENERIC_TITLES or any(term in lowered_title for term in NOISE_TERMS):
        return False
    if re.match(r"^(?:about|search jobs?|where |this is |you can |world-class |open and |the trading floor$)", lowered_title):
        return False
    title_signal = re.search(
        r"\b(?:programme|program|scheme|intern(?:ship)?|placement|bursar(?:y|ies)|studentship|"
        r"competition|challenge|hackathon|summer school|fellowship|scholarship|insight|spring week|"
        r"consultant|analyst|engineer|technologist|researcher|fttp|bridge)\b|\bsee london\b|\bspring into\b",
        lowered_title,
    )
    role_url = re.search(r"/(?:jobs?|vacancies|job-offers?)/[^/?#]{5,}", url.lower())
    return len(title.strip()) >= 5 and bool(title_signal or role_url)


def _formal_opportunity(title: str, text: str, application_url: str | None, url: str) -> bool:
    lowered = f"{title} {text[:12000]}".lower()
    vocabulary = ("programme", "program", "scheme", "intern", "placement", "bursary", "studentship", "competition", "challenge", "summer school", "academy", "insight", "spring week", "fttp", "bridge")
    return is_formal_title(title, url) and any(term in lowered for term in vocabulary) and (bool(application_url) or len(text) >= 180)


def _cycle(text: str, start_date: str | None) -> str:
    years = sorted({int(item) for item in re.findall(r"\b20(?:26|27|28|29)\b", text)})
    if start_date and start_date[:4].isdigit():
        years.append(int(start_date[:4]))
    years = sorted(set(years))
    return str(years[-1]) if years else "current"


def _relevant_cycle(stream: Stream, cycle: str, lifecycle: Lifecycle, requirements: list[Requirement]) -> bool:
    if cycle == "current":
        # No date is uncertainty, not evidence of age. Lifecycle ambiguity is
        # routed to review; only an explicit old cycle becomes stale.
        return True
    year = int(cycle)
    if year < 2026 or year > 2029:
        return False
    if stream is Stream.INTERNSHIPS and year == 2027:
        penultimate = next((item for item in requirements if item.rule == "penultimate_year"), None)
        return not penultimate
    return True


def extract_listing(source: dict[str, Any], listing: Listing, checked_at: str, profile: dict[str, Any]) -> Opportunity | None:
    """Extract and evaluate an opportunity without a programme-specific template."""

    title = _normalise(listing.title or str(listing.raw.get("page_title", "")))
    text = _normalise(f"{title}. {listing.body}")
    if not title or not text or any(term in title.lower() for term in NOISE_TERMS):
        return None
    stream = _stream(title, text, source)
    if stream is None:
        return None
    lifecycle, action, opens_at, deadline = _lifecycle(text, listing, checked_at)
    application_url = _action_url(listing, interest=lifecycle is Lifecycle.INTEREST_OPEN)
    if lifecycle is Lifecycle.OPEN and not application_url:
        application_url = listing.url
    if not _formal_opportunity(title, text, application_url, listing.url):
        return None

    start_date, end_date = _programme_dates(text)
    requirements, vetting = _requirements(text, start_date)
    cycle = _cycle(text, start_date)
    if not start_date and cycle.isdigit():
        month = "04" if stream is Stream.SPRING else "07" if stream in {Stream.RESEARCH, Stream.INTERNSHIPS} else "01"
        start_date = f"{cycle}-{month}"
    identity_targeted = _identity_targeted(text)
    location_text = listing.location or next((name for name in ("London, UK", "Durham, UK", "Edinburgh, UK", "United Kingdom", "Online", "Remote") if name.lower().split(",")[0] in text.lower()), "Not stated")
    location, city, country, work_mode = _location(location_text)
    organisation = source.get("organisation", source.get("name", urlsplit(listing.url).netloc))
    external_id = listing.raw.get("id") or listing.raw.get("jobId") or listing.raw.get("requisitionId")
    canonical_id = f"{_slug(organisation)}-{_slug(str(external_id) if external_id else title)}"
    careers, subjects = _fields(text)
    deadline_status = DeadlineStatus.FIXED if deadline else DeadlineStatus.ROLLING if "rolling" in text.lower() else DeadlineStatus.NONE_STATED
    duration_match = re.search(r"\b(\d{1,2})[- ](?:day|week|month)|\b(\d{1,2})\s+(days?|weeks?|months?)\b", text, re.I)
    duration = duration_match.group(0) if duration_match else None
    documents = [name for name, terms in {"CV": (" cv", "curriculum vitae"), "cover letter": ("cover letter",), "transcript": ("transcript",), "proposal": ("research proposal", "project proposal")}.items() if any(term in text.lower() for term in terms)]
    confidence = 0.30
    confidence += 0.16 if listing.detail_status == 200 and len(listing.body) >= 180 else 0
    confidence += 0.14 if application_url or lifecycle in {Lifecycle.INTEREST_OPEN, Lifecycle.ANNOUNCED} else 0
    confidence += 0.14 if requirements else 0
    confidence += 0.12 if cycle != "current" else 0
    confidence += 0.10 if start_date or deadline or opens_at else 0
    confidence += 0.08 if careers or subjects else 0
    confidence = min(confidence, 0.99)
    current_cycle = _relevant_cycle(stream, cycle, lifecycle, requirements)
    fingerprint = listing.detail_fingerprint or hashlib.sha256(text.encode()).hexdigest()
    evidence = [Evidence(f"The official detail page identifies {title} as a current or announced opportunity.", listing.url, checked_at[:10])]
    if deadline:
        evidence.append(Evidence(f"The official detail page states an application deadline of {deadline}.", listing.url, checked_at[:10]))
    if requirements:
        evidence.append(Evidence("The eligibility rules were extracted from the official detail-page wording.", listing.url, checked_at[:10]))

    record = Opportunity(
        canonical_id=canonical_id,
        title=title[:240],
        organisation=organisation,
        stream=stream,
        lifecycle=lifecycle if current_cycle else Lifecycle.STALE,
        eligibility=Eligibility.UNCERTAIN,
        primary_action=action,
        source_url=listing.url,
        location=location,
        start_date=start_date,
        end_date=end_date,
        deadline=deadline,
        deadline_status=deadline_status,
        deadline_source=listing.url if deadline else None,
        deadline_verified=bool(deadline),
        rolling=deadline_status is DeadlineStatus.ROLLING,
        opens_at=opens_at,
        research_mode=("funding_or_bursary" if any(term in text.lower() for term in ("bursary", "funding")) else "supervisor_outreach" if "contact supervisor" in text.lower() else "structured_programme") if stream is Stream.RESEARCH else None,
        summary=_summary(listing, text),
        why_it_fits="Official subject, timing and access evidence will be evaluated against the Durham Mathematics & Physics profile.",
        eligibility_note="Needs eligibility check against the extracted official criteria.",
        next_step="Apply through the official route." if lifecycle is Lifecycle.OPEN else "Register interest through the official route." if lifecycle is Lifecycle.INTEREST_OPEN else "Prepare for the stated opening date." if lifecycle is Lifecycle.ANNOUNCED else "Review the official page before acting.",
        tags=sorted(set([*careers, *subjects, cycle, "automatically discovered"])),
        evidence=evidence,
        requirements=requirements,
        checked_at=checked_at[:10],
        first_seen=checked_at,
        last_seen=checked_at,
        last_changed=checked_at,
        source_id=source["id"],
        source_kind="discovered",
        content_fingerprint=fingerprint,
        confidence=confidence,
        priority_tier=PriorityTier.C,
        identity_targeted=identity_targeted,
        opportunity_type="internship" if stream is Stream.INTERNSHIPS else "insight_programme" if stream is Stream.SPRING else "research_programme" if stream is Stream.RESEARCH else "competition_or_development",
        career_fields=careers,
        subject_fields=subjects,
        country=country,
        city=city,
        work_mode=work_mode,
        cycle=cycle,
        application_url=application_url,
        discovered_via=listing.discovered_via or source["url"],
        primary_evidence_url=listing.url,
        alternate_sources=[source["url"]] if source["url"] != listing.url else [],
        last_verified=checked_at,
        deadline_last_verified=checked_at if deadline else None,
        source_family=source.get("family", source["id"]),
        security_vetting=vetting,
        required_documents=documents,
        duration=duration,
        parser_version=PARSER_VERSION,
    )
    record = evaluate(record, profile)
    if record.eligibility is Eligibility.ELIGIBLE:
        record.why_it_fits = "The extracted official requirements match the known Durham Mathematics & Physics profile."
    elif record.eligibility is Eligibility.LIKELY:
        record.why_it_fits = "The opportunity is relevant and no required extracted rule conflicts with the known profile."
    elif vetting and record.eligibility is not Eligibility.INELIGIBLE:
        record.eligibility_note += " Academic and nationality criteria may be compatible; appointment remains subject to official security vetting."

    review_reasons: list[str] = []
    if listing.raw.get("detail_error") or listing.detail_status != 200:
        review_reasons.append("detail page could not be fully verified")
    if lifecycle is Lifecycle.UNKNOWN:
        review_reasons.append("current application lifecycle is unclear")
    if record.eligibility is Eligibility.UNCERTAIN:
        review_reasons.append("important eligibility evidence remains unknown")
    if confidence < 0.76:
        review_reasons.append("generic extraction confidence is below the automatic-publication threshold")
    if not current_cycle:
        review_reasons.append("page does not describe a relevant current cycle")
    if identity_targeted:
        review_reasons.append("programme-level identity restriction detected")
    record.review_reasons = review_reasons
    record.review_required = bool(
        review_reasons and record.lifecycle is not Lifecycle.STALE
        and not identity_targeted and record.eligibility is not Eligibility.INELIGIBLE
    )
    if not record.review_required and not identity_targeted and record.lifecycle in {Lifecycle.OPEN, Lifecycle.INTEREST_OPEN, Lifecycle.ANNOUNCED} and record.eligibility in {Eligibility.ELIGIBLE, Eligibility.LIKELY}:
        record.auto_publish_reason = "Official detail page, relevant cycle, actionable lifecycle and compatible structured eligibility."
    return record
