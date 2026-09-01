"""Network adapters and source-family detail-page discovery.

The adapter layer turns an official directory, ATS, or feed into fully fetched
detail-page listings. Eligibility decisions belong to the extractor rather
than this transport layer.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree


USER_AGENT = "PersonalOpportunityRadar/3.0 (+candidate-specific academic opportunity monitor)"
DEFAULT_MAX_BYTES = 16_000_000


class ResponseTooLarge(ValueError):
    """Raised before parsing when a response exceeds its configured safe cap."""


@dataclass(slots=True)
class Listing:
    title: str
    url: str
    body: str = ""
    location: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    discovered_via: str = ""
    detail_status: int | None = None
    detail_fingerprint: str = ""


@dataclass(slots=True)
class AdapterResult:
    http_status: int | None
    listings: list[Listing]
    fingerprint: str
    warning: str = ""
    elapsed_ms: int = 0
    parser_error: str = ""
    index_listing_count: int = 0
    detail_fetch_count: int = 0
    detail_success_count: int = 0
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


@dataclass(slots=True)
class _Document:
    title: str
    heading: str
    description: str
    text: str
    links: list[Listing]
    sections: list[Listing]


class _DocumentParser(HTMLParser):
    """Small dependency-free HTML reader retaining link text and page facts."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[Listing] = []
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.description = ""
        self._href: str | None = None
        self._link_parts: list[str] = []
        self._in_title = False
        self._in_heading = False
        self._ignored_depth = 0
        self._section_heading_parts: list[str] = []
        self._section_body_parts: list[str] = []
        self._sections: list[Listing] = []
        self._in_section_heading = False

    def _flush_section(self) -> None:
        heading = " ".join(self._section_heading_parts).strip()
        body = " ".join(self._section_body_parts).strip()
        if heading and body:
            self._sections.append(Listing(heading, self.base_url, body=body))
        self._section_heading_parts, self._section_body_parts = [], []

    def sections(self) -> list[Listing]:
        self._flush_section()
        return self._sections

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style", "svg", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and not self.heading_parts:
            self._in_heading = True
        elif tag in {"h2", "h3"}:
            self._flush_section()
            self._in_section_heading = True
        elif tag == "meta" and (attributes.get("name") or "").lower() == "description":
            self.description = " ".join((attributes.get("content") or "").split())
        elif tag == "a":
            self._href = attributes.get("href")
            label = attributes.get("aria-label") or attributes.get("title") or ""
            self._link_parts = [" ".join(label.split())] if label.strip() else []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in {"script", "style", "svg", "noscript"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        clean = " ".join(unescape(data).split())
        if not clean:
            return
        self.text_parts.append(clean)
        if self._in_title:
            self.title_parts.append(clean)
        if self._in_heading:
            self.heading_parts.append(clean)
        if self._in_section_heading:
            self._section_heading_parts.append(clean)
        elif self._section_heading_parts:
            self._section_body_parts.append(clean)
        if self._href:
            self._link_parts.append(clean)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "noscript"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_heading = False
        elif tag in {"h2", "h3"}:
            self._in_section_heading = False
        elif tag == "a" and self._href:
            title = " ".join(dict.fromkeys(part for part in self._link_parts if part)).strip()
            if title:
                self.links.append(Listing(title=title, url=urljoin(self.base_url, self._href)))
            self._href, self._link_parts = None, []


def _fetch(url: str, timeout: float, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[int, bytes]:
    """Return a complete response or fail before attempting to parse it."""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise ResponseTooLarge(f"response declares {declared} bytes; configured maximum is {max_bytes}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(131_072)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLarge(f"response exceeded configured maximum of {max_bytes} bytes")
            chunks.append(chunk)
        return response.status, b"".join(chunks)


def _fetch_conditional(url: str, timeout: float, max_bytes: int, previous: dict[str, Any]) -> tuple[int, bytes, dict[str, str]]:
    """Fetch an index with ETag/Last-Modified validators when publishers provide them."""

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8",
    }
    if previous.get("etag"):
        headers["If-None-Match"] = str(previous["etag"])
    if previous.get("last_modified"):
        headers["If-Modified-Since"] = str(previous["last_modified"])
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, b"", {
                "etag": exc.headers.get("ETag", previous.get("etag", "")),
                "last_modified": exc.headers.get("Last-Modified", previous.get("last_modified", "")),
            }
        raise
    with response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise ResponseTooLarge(f"response declares {declared} bytes; configured maximum is {max_bytes}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(131_072)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLarge(f"response exceeded configured maximum of {max_bytes} bytes")
            chunks.append(chunk)
        return response.status, b"".join(chunks), {
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
        }


def _parse_html(url: str, body: bytes | str) -> _Document:
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    parser = _DocumentParser(url)
    parser.feed(text)
    return _Document(
        title=" ".join(parser.title_parts).strip(),
        heading=" ".join(parser.heading_parts).strip(),
        description=parser.description,
        text=" ".join(parser.text_parts),
        links=parser.links,
        sections=parser.sections(),
    )


def _strip_html(value: str) -> str:
    return _parse_html("https://invalid.local/", value).text


_DEFAULT_INCLUDE = (
    "spring", "insight", "first year", "first-year", "intern", "placement",
    "research programme", "research program", "research project", "studentship",
    "bursary", "competition", "challenge", "academy", "summer school",
    "fellowship", "scholarship", "cryptography", "cyber", "technology programme",
)
_GENERIC_TITLES = {
    "about", "careers", "career opportunities", "early careers", "home", "jobs",
    "news", "privacy", "privacy policy", "programmes", "programs", "research",
    "students", "student", "student programs", "student programmes", "internships",
    "opportunities", "current opportunities", "puzzles", "current puzzle",
}
_NEGATIVE_TERMS = (
    "cookie", "privacy", "newsletter", "success stories", "logo", "site map",
    "sitemap", "terms of use", "press release", "our people", "story agency",
    "mba", "phd", "postdoctoral", "high school", "school pupils", "graduate programme",
    "senior ", "manager", "director", "experienced hire", "blog",
)


def _candidate_score(listing: Listing, source: dict[str, Any]) -> int:
    title = " ".join(listing.title.lower().split())
    url = listing.url.lower()
    if not title or title in _GENERIC_TITLES or any(term in title for term in _NEGATIVE_TERMS):
        return -10
    includes = tuple(str(term).lower() for term in source.get("include_patterns", _DEFAULT_INCLUDE))
    excludes = tuple(str(term).lower() for term in source.get("exclude_patterns", [])) + _NEGATIVE_TERMS
    if any(term in title or term in url for term in excludes):
        return -10
    score = 0
    score += 4 if any(term in title for term in includes) else 0
    score += 2 if re.search(r"\b20(26|27|28|29)\b", title + " " + url) else 0
    score += 2 if any(term in url for term in ("program", "intern", "insight", "student", "research", "opportun", "vacan", "job")) else 0
    score += 2 if any(term in title for term in ("apply", "register", "fttp", "bridge", "see london")) else 0
    return score


def _relevant(listing: Listing, source: dict[str, Any]) -> bool:
    return _candidate_score(listing, source) >= int(source.get("candidate_score", 4))


def _unique_links(items: list[Listing]) -> list[Listing]:
    result: dict[str, Listing] = {}
    for item in items:
        if urlparse(item.url).scheme not in {"http", "https"}:
            continue
        result.setdefault(item.url.split("#", 1)[0], item)
    return list(result.values())


def _document_listing(title_hint: str, url: str, document: _Document, status: int, fingerprint: str, discovered_via: str) -> Listing:
    actions = [
        {"title": link.title, "url": link.url}
        for link in document.links
        if any(term in link.title.lower() for term in ("apply", "register", "submit", "express interest", "join"))
    ]
    return Listing(
        title=(document.heading or title_hint or document.title)[:300],
        url=url,
        body=document.text[:240_000],
        raw={"page_title": document.title, "description": document.description, "actions": actions},
        discovered_via=discovered_via,
        detail_status=status,
        detail_fingerprint=fingerprint,
    )


def _html_index(source: dict[str, Any], body: bytes, status: int, fingerprint: str) -> tuple[list[Listing], int]:
    document = _parse_html(source["url"], body)
    if source.get("page_is_listing", True) is False:
        return [_document_listing(source.get("programme_title", ""), source["url"], document, status, fingerprint, source["url"])], 1
    if source.get("split_sections", False):
        sections = []
        for item in document.sections:
            item.url = f"{source['url'].split('#', 1)[0]}#{re.sub(r'[^a-z0-9]+', '-', item.title.lower()).strip('-')}"
            item.discovered_via = source["url"]
            item.detail_status = status
            item.detail_fingerprint = hashlib.sha256(item.body.encode()).hexdigest()
            if _relevant(item, source):
                sections.append(item)
        return sections, len(sections)
    links = _unique_links([item for item in document.links if _relevant(item, source)])
    if source.get("include_self", False):
        links.insert(0, Listing(document.heading or document.title or source["name"], source["url"]))
    return links, len(links)


def _json_items(source: dict[str, Any], body: bytes) -> list[Listing]:
    payload = json.loads(body)
    adapter = source.get("adapter")
    if adapter == "jane_street":
        allowed_offices = set(source.get("offices", ["LDN"]))
        result: list[Listing] = []
        for programme in payload:
            name = str(programme.get("name", "Programme"))
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            for session in programme.get("sessions", []):
                if session.get("office") not in allowed_offices:
                    continue
                categories = ", ".join(str(item.get("name", "")) for item in session.get("categories", []))
                custom = _strip_html(str(session.get("custom_text", "")))
                metadata = " ".join(filter(None, [
                    f"{name} programme.", str(session.get("dates", "")),
                    f"Application deadline {session.get('formatted_deadline')}." if session.get("formatted_deadline") else "",
                    str(session.get("additional_info", "")), custom,
                    f"Tracks: {categories}." if categories else "",
                ]))
                apply_path = session.get("apply_page")
                apply_url = urljoin("https://www.janestreet.com/", apply_path) if apply_path else None
                detail_url = f"https://www.janestreet.com/join-jane-street/programs-and-events/{slug}/"
                result.append(Listing(
                    title=f"{name} London {session.get('dates', '')}".strip(),
                    url=detail_url,
                    body=metadata,
                    location="London, UK",
                    raw={**session, "id": session.get("id"), "application_url": apply_url, "programme": programme},
                    discovered_via=source.get("directory_url", source["url"]),
                ))
        return result
    if adapter == "greenhouse":
        return [Listing(item.get("title", "Untitled role"), item.get("absolute_url", source["url"]), body=_strip_html(item.get("content", "")), location=(item.get("location") or {}).get("name", ""), raw=item, discovered_via=source["url"], detail_status=200) for item in payload.get("jobs", [])]
    if adapter == "lever":
        return [Listing(item.get("text", "Untitled role"), item.get("hostedUrl", source["url"]), body=item.get("descriptionPlain", "") or _strip_html(item.get("description", "")), location=(item.get("categories") or {}).get("location", ""), raw=item, discovered_via=source["url"], detail_status=200) for item in payload]
    if adapter == "ashby":
        return [Listing(item.get("title", "Untitled role"), item.get("jobUrl", source["url"]), body=_strip_html(item.get("descriptionHtml", "") or item.get("description", "")), location=item.get("location", ""), raw=item, discovered_via=source["url"], detail_status=200) for item in payload.get("jobs", [])]
    if adapter == "smartrecruiters":
        return [Listing(item.get("name", "Untitled role"), item.get("ref", source["url"]), location=((item.get("location") or {}).get("city") or ""), raw=item, discovered_via=source["url"]) for item in payload.get("content", [])]
    if adapter in {"workable", "teamtailor", "workday", "json"}:
        items = payload.get(source.get("items_key", "jobs"), payload if isinstance(payload, list) else [])
        body_key = source.get("body_key", "description")
        return [Listing(str(item.get(source.get("title_key", "title"), "Untitled role")), str(item.get(source.get("url_key", "url"), source["url"])), body=_strip_html(str(item.get(body_key, ""))), location=str(item.get(source.get("location_key", "location"), "")), raw=item, discovered_via=source["url"], detail_status=200 if item.get(body_key) else None) for item in items]
    return []


def _feed(source: dict[str, Any], body: bytes) -> list[Listing]:
    root = ElementTree.fromstring(body)
    result = [Listing(item.findtext("title", "Untitled"), item.findtext("link", source["url"]), _strip_html(item.findtext("description", "")), discovered_via=source["url"]) for item in root.findall(".//item")]
    atom = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//a:entry", atom):
        link = entry.find("a:link", atom)
        result.append(Listing(entry.findtext("a:title", "Untitled", atom), link.get("href") if link is not None else source["url"], _strip_html(entry.findtext("a:summary", "", atom)), discovered_via=source["url"]))
    return result


def _fetch_detail(listing: Listing, source: dict[str, Any], timeout: float) -> Listing:
    index_body, index_raw, index_location = listing.body, dict(listing.raw), listing.location
    status, body = _fetch(listing.url, timeout, int(source.get("detail_max_bytes", source.get("max_bytes", DEFAULT_MAX_BYTES))))
    fingerprint = hashlib.sha256(body).hexdigest()
    detail = _document_listing(listing.title, listing.url, _parse_html(listing.url, body), status, fingerprint, listing.discovered_via or source["url"])
    detail.body = f"{detail.body} {index_body}".strip()
    detail.location = index_location
    detail.raw.update(index_raw)
    return detail


def scan_source(source: dict[str, Any], timeout: float = 12, previous: dict[str, Any] | None = None) -> AdapterResult:
    started = time.monotonic()
    index_count = detail_fetch_count = detail_success_count = 0
    detail_errors: list[str] = []
    try:
        response_headers: dict[str, str] = {}
        if previous is None:
            status, body = _fetch(source["url"], timeout, int(source.get("max_bytes", DEFAULT_MAX_BYTES)))
        else:
            status, body, response_headers = _fetch_conditional(
                source["url"], timeout, int(source.get("max_bytes", DEFAULT_MAX_BYTES)), previous,
            )
        if status == 304:
            return AdapterResult(
                http_status=304, listings=[], fingerprint=previous.get("content_fingerprint", ""),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                etag=response_headers.get("etag", ""), last_modified=response_headers.get("last_modified", ""),
                not_modified=True,
            )
        fingerprint = hashlib.sha256(body).hexdigest()
        adapter = source.get("adapter", "html")
        if adapter in {"rss", "atom"}:
            candidates = _feed(source, body)
            index_count = len(candidates)
        elif adapter in {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "teamtailor", "workable", "json", "jane_street"}:
            candidates = _json_items(source, body)
            index_count = len(candidates)
        else:
            candidates, index_count = _html_index(source, body, status, fingerprint)

        relevant_candidates = [item for item in candidates if _relevant(item, source) or item.url == source["url"]]
        candidates = (
            list({(item.title, item.url): item for item in relevant_candidates}.values())
            if source.get("split_sections", False)
            else _unique_links(relevant_candidates)
        )
        context_parts: list[str] = []
        for context_url in source.get("context_urls", []):
            detail_fetch_count += 1
            try:
                context_status, context_body = _fetch(context_url, timeout, int(source.get("detail_max_bytes", source.get("max_bytes", DEFAULT_MAX_BYTES))))
                if context_status == 200:
                    context_parts.append(_parse_html(context_url, context_body).text)
                    detail_success_count += 1
            except (urllib.error.URLError, TimeoutError, ValueError, ResponseTooLarge) as exc:
                detail_errors.append(f"{context_url}: {exc}")
        listings: list[Listing] = []
        # Bound generic HTML fan-out so one broad careers directory cannot
        # consume an entire scheduled run. High-value families can opt into a
        # larger explicit limit in the source registry.
        for candidate in candidates[: int(source.get("max_detail_pages", 8))]:
            candidate.discovered_via = candidate.discovered_via or source["url"]
            same_page = candidate.url.split("#", 1)[0].rstrip("/") == source["url"].split("#", 1)[0].rstrip("/")
            needs_detail = bool(source.get("fetch_details", True)) and not same_page and (not candidate.body.strip() or source.get("force_details", False))
            if needs_detail:
                detail_fetch_count += 1
                try:
                    candidate = _fetch_detail(candidate, source, timeout)
                    detail_success_count += 1
                except (urllib.error.URLError, TimeoutError, ValueError, ResponseTooLarge) as exc:
                    candidate.raw["detail_error"] = str(exc)
                    detail_errors.append(f"{candidate.url}: {exc}")
            if context_parts:
                candidate.body = f"{candidate.body} {' '.join(context_parts)}".strip()
            listings.append(candidate)

        haystack = body.decode("utf-8", "replace").lower()
        expected = [str(term).lower() for term in source.get("expected_terms", [])]
        warning = ""
        if expected and not all(term in haystack for term in expected):
            warning = f"HTTP 200 but expected content was not parsed: {', '.join(expected)}"
        elif candidates and detail_fetch_count and detail_success_count == 0:
            warning = "listing discovery succeeded but every detail-page fetch failed"
        elif not listings and not source.get("allow_zero", False):
            warning = "HTTP succeeded but parser found no relevant listings"
        return AdapterResult(
            status, listings, fingerprint, warning, int((time.monotonic() - started) * 1000),
            "; ".join(detail_errors[:5]), index_count, detail_fetch_count, detail_success_count,
            response_headers.get("etag", ""), response_headers.get("last_modified", ""), False,
        )
    except (urllib.error.URLError, TimeoutError, ValueError, ResponseTooLarge, ElementTree.ParseError, json.JSONDecodeError) as exc:
        status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
        return AdapterResult(status, [], "", str(exc), int((time.monotonic() - started) * 1000), str(exc), index_count, detail_fetch_count, detail_success_count)
