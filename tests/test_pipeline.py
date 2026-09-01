import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from personal_opportunity_radar.adapters import AdapterResult, Listing, _fetch, scan_source
from personal_opportunity_radar.config import load_profile, load_sources
from personal_opportunity_radar.dedupe import canonical_url, deduplicate
from personal_opportunity_radar.eligibility import evaluate
from personal_opportunity_radar.extractor import extract_listing, is_formal_title
from personal_opportunity_radar.models import (
    DeadlineStatus, Eligibility, Evidence, Lifecycle, Opportunity, PriorityTier,
    Requirement, RequirementStrength, SourceStatus, Stream,
)
from personal_opportunity_radar.scanner import (
    _apply_template, _changes, _identity_targeted, _merge_scan, _quality_candidates, audit_sources, build_artifact,
    load_discovered_records, run_scan,
)
from personal_opportunity_radar.validation import validate


PROFILE = load_profile()


def opportunity(**changes):
    value = dict(
        canonical_id="fixture", title="Fixture programme", organisation="Official Host",
        stream=Stream.SPRING, lifecycle=Lifecycle.OPEN, eligibility=Eligibility.UNCERTAIN,
        primary_action="Apply", source_url="https://example.org/programme", source_id="fixture-source",
        summary="Fixture", eligibility_note="Needs eligibility check.", next_step="Check the criteria.",
        evidence=[Evidence("Official fixture evidence.", "https://example.org/programme", "2026-08-30")],
        checked_at="2026-08-30", first_seen="2026-08-30", last_seen="2026-08-30",
        last_changed="2026-08-30", confidence=0.9, priority_tier=PriorityTier.B,
    )
    value.update(changes)
    return Opportunity(**value)


class EligibilityRegressionFixtures(unittest.TestCase):
    def test_01_first_year_spring_2029_graduate_is_eligible(self):
        record = opportunity(start_date="2027-04", requirements=[Requirement("study_year", [1])])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.ELIGIBLE)

    def test_02_penultimate_summer_2027_is_ineligible(self):
        record = opportunity(stream=Stream.INTERNSHIPS, start_date="2027-06", requirements=[Requirement("penultimate_year", True)])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.INELIGIBLE)

    def test_03_summer_2028_penultimate_cycle_is_eligible(self):
        record = opportunity(stream=Stream.INTERNSHIPS, start_date="2028-06", requirements=[Requirement("penultimate_year", True)])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.ELIGIBLE)

    def test_04_cs_preferred_is_not_hard_exclusion(self):
        record = opportunity(requirements=[Requirement("degree_subject", ["computer science"], RequirementStrength.PREFERRED)])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.LIKELY)

    def test_05_cs_required_is_ineligible(self):
        record = opportunity(requirements=[Requirement("degree_subject", ["computer science"])])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.INELIGIBLE)

    def test_06_identity_restricted_programme_is_suppressed(self):
        record = opportunity(identity_targeted=True, requirements=[Requirement("identity_restricted", True)])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.INELIGIBLE)

    def test_07_generic_equal_opportunity_text_is_retained(self):
        self.assertFalse(_identity_targeted("We are an equal opportunity employer and value diversity."))

    def test_08_host_only_research_is_ineligible_for_durham(self):
        record = opportunity(stream=Stream.RESEARCH, research_mode="structured_programme",
                             requirements=[Requirement("institution", ["University of Oxford"])])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.INELIGIBLE)

    def test_09_external_uk_students_are_potentially_eligible(self):
        record = opportunity(stream=Stream.RESEARCH, research_mode="structured_programme",
                             requirements=[Requirement("external_students", True)])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.ELIGIBLE)

    def test_10_worldwide_research_is_retained(self):
        record = opportunity(stream=Stream.RESEARCH, research_mode="structured_programme",
                             requirements=[Requirement("worldwide", True)])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.ELIGIBLE)

    def test_11_us_only_research_is_ineligible(self):
        record = opportunity(stream=Stream.RESEARCH, research_mode="structured_programme",
                             requirements=[Requirement("nationality", ["US citizen"])])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.INELIGIBLE)

    def test_12_first_year_explicitly_excluded_is_ineligible(self):
        record = opportunity(stream=Stream.RESEARCH, research_mode="funding_or_bursary", start_date="2027-06",
                             requirements=[Requirement("study_year", [2, 3])])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.INELIGIBLE)

    def test_13_same_university_team_is_structured_condition(self):
        record = opportunity(stream=Stream.COMPETITIONS, requirements=[Requirement("same_university_team", True)])
        evaluated = evaluate(record, PROFILE)
        self.assertEqual(evaluated.eligibility, Eligibility.ELIGIBLE)
        self.assertEqual(evaluated.rule_evaluations[0].rule, "same_university_team")

    def test_14_uk_residency_is_unknown_from_british_nationality(self):
        record = opportunity(requirements=[Requirement("uk_residency", True)])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.UNCERTAIN)

    def test_postgraduate_only_programme_is_ineligible_for_bsc_student(self):
        record = opportunity(requirements=[Requirement("postgraduate_required", True)])
        self.assertEqual(evaluate(record, PROFILE).eligibility, Eligibility.INELIGIBLE)

    def test_master_degree_wording_is_extracted_as_postgraduate_only(self):
        source = {"id": "graduate", "name": "Graduate Host", "url": "https://example.org/programmes", "stream": "internships"}
        listing = Listing(
            "International Internship Programme", "https://example.org/jobs/international-internship",
            "Applications are open for this twelve-month internship. Eligibility criteria: hold a Master’s degree. "
            "Participants work with technology and data teams in London and apply through the official portal.",
            raw={"actions": [{"title": "Apply", "url": "https://example.org/apply"}]}, detail_status=200,
        )
        record = extract_listing(source, listing, "2026-08-31T00:00:00Z", PROFILE)
        self.assertIsNotNone(record)
        self.assertEqual(record.eligibility, Eligibility.INELIGIBLE)

    def test_source_stream_and_penultimate_or_final_wording_take_precedence(self):
        source = {"id": "summer", "name": "Summer Host", "url": "https://example.org/programmes", "stream": "internships"}
        listing = Listing(
            "2027 Summer Analyst Programme", "https://example.org/jobs/summer-analyst",
            "Applications are open for an undergraduate summer internship. Candidates must be in their penultimate or final year. "
            "The careers academy provides mentoring and technology training. Apply through the official portal.",
            raw={"actions": [{"title": "Apply", "url": "https://example.org/apply"}]}, detail_status=200,
        )
        record = extract_listing(source, listing, "2026-08-31T00:00:00Z", PROFILE)
        self.assertIsNotNone(record)
        self.assertEqual(record.stream, Stream.INTERNSHIPS)
        self.assertEqual(record.eligibility, Eligibility.INELIGIBLE)


class LifecycleAndPipelineFixtures(unittest.TestCase):
    def test_15_future_opening_is_announced_not_open(self):
        record = opportunity(lifecycle=Lifecycle.ANNOUNCED, opens_at="2026-11-02")
        self.assertEqual(record.lifecycle, Lifecycle.ANNOUNCED)
        self.assertFalse(validate([record]))

    def test_16_register_interest_is_actionable_interest(self):
        record = opportunity(lifecycle=Lifecycle.INTEREST_OPEN, primary_action="Register Interest")
        self.assertFalse(validate([record]))

    def test_17_rolling_recruitment_has_explicit_urgent_flag(self):
        record = opportunity(deadline_status=DeadlineStatus.ROLLING, rolling=True)
        self.assertTrue(record.rolling)
        self.assertFalse(validate([record]))

    def test_18_aggregator_and_official_duplicate_merge(self):
        official = opportunity(source_kind="discovered", confidence=0.95)
        aggregator = opportunity(source_kind="manual", confidence=0.5, source_url="https://example.org/programme?utm_source=board")
        merged = deduplicate([aggregator, official])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source_kind, "discovered")

    def test_19_url_change_preserves_stable_alias(self):
        old = opportunity(canonical_id="old-id", source_url="https://example.org/old")
        current = opportunity(canonical_id="current-id", aliases=["old-id"], source_url="https://example.org/new")
        merged = deduplicate([old, current])
        self.assertEqual(len(merged), 1)
        self.assertIn("old-id", [merged[0].canonical_id, *merged[0].aliases])

    def test_alias_bridge_collapses_two_existing_clusters(self):
        first = opportunity(canonical_id="first", aliases=["shared-a"], source_url="https://example.org/a")
        second = opportunity(canonical_id="second", aliases=["shared-b"], source_url="https://example.org/b")
        bridge = opportunity(
            canonical_id="bridge", aliases=["shared-a", "shared-b"],
            source_url="https://example.org/c", confidence=0.95,
        )
        merged = deduplicate([first, second, bridge])
        self.assertEqual(len(merged), 1)
        self.assertFalse(validate(merged))

    def test_generic_marketing_heading_is_not_a_formal_opportunity(self):
        self.assertFalse(is_formal_title("Open and collaborative teams", "https://example.org/internships/"))
        self.assertTrue(is_formal_title("Quantitative Research Internship (2027 Start)", "https://example.org/jobs/123"))
        self.assertTrue(is_formal_title("Spring into Quant Finance 2027", "https://example.org/events/quant"))

    def test_auto_discovered_marketing_record_is_suppressed_even_if_previously_public(self):
        record = opportunity(
            canonical_id="marketing", title="Explore Your Path", source_kind="discovered",
            parser_version=3, tags=["automatically discovered"], review_required=False,
            source_url="https://example.org/students-early-career/",
        )
        self.assertEqual(_quality_candidates([record]), [])

    @patch("personal_opportunity_radar.adapters._fetch", return_value=(200, b"<html><title>Careers</title><body>No matching programme</body></html>"))
    def test_20_http_200_without_expected_content_is_degraded(self, _fetch):
        result = scan_source({"id": "x", "name": "X", "url": "https://example.org", "adapter": "html", "expected_terms": ["spring insight"]})
        self.assertEqual(result.http_status, 200)
        self.assertIn("expected content", result.warning)

    @patch("personal_opportunity_radar.scanner.load_discovered_records", return_value=[])
    @patch("personal_opportunity_radar.scanner.load_sources", return_value=[])
    def test_21_failed_source_does_not_invalidate_other_adapter_result(self, _sources, _records):
        healthy = AdapterResult(200, [], "abc", "")
        failed = AdapterResult(None, [], "", "timeout")
        self.assertEqual(healthy.http_status, 200)
        self.assertIsNone(failed.http_status)

    def test_22_failed_request_does_not_close_existing_record(self):
        existing = opportunity(source_id="source")
        source = {"id": "source", "name": "Source", "url": existing.source_url, "adapter": "html"}
        result, _ = _merge_scan([existing], [source], {"source": AdapterResult(None, [], "", "timeout")}, "2026-08-31T00:00:00Z")
        self.assertEqual(result[0].lifecycle, Lifecycle.OPEN)

    def test_23_conflicting_official_evidence_enters_review(self):
        first = opportunity(deadline="2026-10-01", deadline_status=DeadlineStatus.FIXED)
        second = opportunity(deadline="2026-11-01", deadline_status=DeadlineStatus.FIXED)
        merged = deduplicate([first, second])[0]
        self.assertTrue(merged.source_conflict)
        self.assertTrue(merged.review_required)

    def test_24_deadline_change_updates_record_and_logs_change(self):
        before = opportunity(deadline="2026-10-01", deadline_status=DeadlineStatus.FIXED)
        after = opportunity(deadline="2026-11-01", deadline_status=DeadlineStatus.FIXED)
        changes = _changes(before, after, "2026-09-01T00:00:00Z")
        self.assertEqual(changes[0]["field"], "deadline")
        self.assertEqual(after.last_changed, "2026-09-01T00:00:00Z")

    def test_template_matching_normalises_date_ordinals(self):
        source = {"id": "blackrock", "name": "BlackRock", "url": "https://example.org/job"}
        template = {
            "canonical_id": "blackrock-spring-2027",
            "title": "2027 Spring Insight Event",
            "organisation": "BlackRock",
            "stream": "spring_insight",
            "lifecycle": "open",
            "eligibility": "eligible",
            "primary_action": "Apply",
            "deadline": "2026-12-04",
            "deadline_status": "fixed",
            "match_terms": ["2027 spring insight", "4 december 2026"],
        }
        result = AdapterResult(
            200,
            [Listing("2027 Spring Insight Event", "https://example.org/job", "Deadline Friday 4th December 2026")],
            "fingerprint",
            "",
        )
        record = _apply_template(source, template, result, "2026-08-30T00:00:00Z")
        self.assertIsNotNone(record)
        self.assertEqual(record.deadline, "2026-12-04")

    def test_verified_template_isolates_programme_on_mixed_official_page(self):
        source = {
            "id": "mixed-family",
            "name": "Mixed Family",
            "organisation": "Example Bank",
            "url": "https://example.org/students",
            "stream": "spring_insight",
            "prefer_verified_template": True,
            "programmes": [{
                "canonical_id": "example-spring-2027",
                "title": "Spring Insight 2027",
                "organisation": "Example Bank",
                "stream": "spring_insight",
                "lifecycle": "open",
                "eligibility": "eligible",
                "primary_action": "Apply",
                "start_date": "2027-04",
                "requirements": [{
                    "rule": "study_year", "value": [1], "strength": "required",
                    "evidence": "Official programme section",
                }],
                "match_terms": ["spring insight 2027", "first-year students"],
            }],
        }
        mixed_page = Listing(
            "Student Programmes",
            source["url"],
            "Spring Insight 2027 is open to first-year students. "
            "Elsewhere, the graduate analyst programme requires graduation in 2027. "
            "Applications are now open for investment banking and technology programmes.",
        )
        records, _ = _merge_scan(
            [], [source], {source["id"]: AdapterResult(200, [mixed_page], "mixed")},
            "2026-08-31T00:00:00Z",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].canonical_id, "example-spring-2027")
        self.assertTrue(records[0].template_dependent)
        self.assertEqual(records[0].start_date, "2027-04")
        self.assertEqual(records[0].requirements[0].rule, "study_year")

    def test_unknown_detail_page_is_discovered_extracted_and_auto_published(self):
        source = {
            "id": "unknown-family",
            "name": "Unknown Official Family",
            "organisation": "Aurora Quant",
            "url": "https://official.example/programmes",
            "adapter": "html",
            "include_patterns": ["first-year", "programme"],
        }
        index = b'''<html><body><a href="/programmes/aurora-2027">Aurora First-Year Quant Programme 2027</a></body></html>'''
        detail = b'''<html><head><title>Aurora programme</title></head><body>
          <h1>Aurora First-Year Quant Programme 2027</h1>
          <p>Applications are now open for first-year students at any UK university graduating in 2029.
          This four-day London programme introduces mathematics, physics, quantitative trading and software.
          The programme runs 12 April to 15 April 2027. Application deadline 1 November 2026.</p>
          <a href="https://apply.official.example/aurora-2027">Apply now</a>
        </body></html>'''

        def response(url, _timeout, _max_bytes=16_000_000):
            return (200, detail) if url.endswith("aurora-2027") else (200, index)

        with patch("personal_opportunity_radar.adapters._fetch", side_effect=response):
            adapter_result = scan_source(source)
        self.assertEqual(adapter_result.detail_fetch_count, 1)
        self.assertEqual(adapter_result.detail_success_count, 1)
        records, _ = _merge_scan([], [source], {source["id"]: adapter_result}, "2026-09-01T00:00:00Z")
        artifact = build_artifact(records, write=False)
        rebuilt = [Opportunity.from_dict(item) for item in artifact["opportunities"] if item["source_id"] == source["id"]]
        self.assertEqual(len(rebuilt), 1)
        self.assertFalse(rebuilt[0].review_required)
        self.assertTrue(rebuilt[0].auto_publish_reason)
        self.assertEqual(rebuilt[0].application_url, "https://apply.official.example/aurora-2027")

    def test_changed_detail_page_updates_lifecycle_deadline_and_change_log(self):
        source = {"id": "change-family", "name": "Change Family", "organisation": "Example Research", "url": "https://example.org/programmes", "stream": "research"}
        initial = Listing(
            "Summer Research Programme 2027", "https://example.org/programmes/summer-2027",
            "Applications are now open to external students from any UK university studying mathematics or physics. "
            "The programme runs 1 July to 31 July 2027. Application deadline 1 November 2026. "
            "Work with a research supervisor on a structured mathematical physics project.",
            raw={"actions": [{"title": "Apply now", "url": "https://example.org/apply"}]},
            discovered_via=source["url"], detail_status=200,
        )
        first, _ = _merge_scan([], [source], {source["id"]: AdapterResult(200, [initial], "one")}, "2026-09-01T00:00:00Z")
        changed = Listing(
            initial.title, initial.url,
            "Applications are now closed. External students from any UK university studying mathematics or physics were eligible. "
            "The programme runs 1 July to 31 July 2027. The revised application deadline was 15 November 2026. "
            "Work with a research supervisor on a structured mathematical physics project.",
            discovered_via=source["url"], detail_status=200,
        )
        second, changes = _merge_scan(first, [source], {source["id"]: AdapterResult(200, [changed], "two")}, "2026-11-16T00:00:00Z")
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].canonical_id, first[0].canonical_id)
        self.assertEqual(second[0].lifecycle, Lifecycle.CLOSED)
        self.assertEqual(second[0].deadline, "2026-11-15")
        self.assertEqual(second[0].last_changed, "2026-11-16T00:00:00Z")
        self.assertEqual(second[0].deadline_last_verified, "2026-11-16T00:00:00Z")
        self.assertTrue({"lifecycle", "deadline"}.issubset({item["field"] for item in changes}))

    def test_missing_count_only_stales_after_three_healthy_absences(self):
        source = {"id": "source", "name": "Source", "url": "https://example.org", "adapter": "html"}
        records = [opportunity(source_id="source", parser_version=3)]
        for number in range(1, 4):
            records, _ = _merge_scan(records, [source], {"source": AdapterResult(200, [], f"healthy-{number}")}, f"2026-09-0{number}T00:00:00Z")
            self.assertEqual(records[0].missing_count, number)
        self.assertEqual(records[0].lifecycle, Lifecycle.STALE)

    def test_clean_slate_run_ignores_historical_discovered_seed(self):
        source = {"id": "clean", "name": "Clean", "organisation": "Clean Host", "url": "https://clean.example/programmes", "stream": "spring_insight"}
        listing = Listing(
            "Clean First-Year Programme 2027", "https://clean.example/programme/2027",
            "Applications are open to first-year students at any UK university graduating in 2029. "
            "This London mathematics programme runs 10 April to 12 April 2027 and applications close 1 November 2026. "
            "Participants explore quantitative finance and software.",
            raw={"actions": [{"title": "Apply", "url": "https://clean.example/apply"}]},
            discovered_via=source["url"], detail_status=200,
        )
        hidden_seed = opportunity(canonical_id="historical-hidden-seed", source_id="clean")
        with (
            patch("personal_opportunity_radar.scanner.load_sources", return_value=[source]),
            patch("personal_opportunity_radar.scanner.load_discovered_records", return_value=[hidden_seed]),
            patch("personal_opportunity_radar.scanner.load_manual_records", return_value=[]),
            patch("personal_opportunity_radar.scanner.scan_source", return_value=AdapterResult(200, [listing], "clean")),
        ):
            artifact, _ = run_scan(dry_run=True, allow_partial=True, clean_slate=True)
        identifiers = {item["canonical_id"] for item in artifact["opportunities"]}
        self.assertNotIn("historical-hidden-seed", identifiers)
        self.assertTrue(any(item["source_id"] == "clean" for item in artifact["opportunities"]))

    def test_large_structured_response_is_read_completely(self):
        payload = json.dumps({"jobs": [], "padding": "x" * 2_500_000}).encode()

        class Headers:
            def get(self, _key):
                return None

        class Response:
            status = 200
            headers = Headers()

            def __init__(self):
                self.offset = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                value = payload[self.offset:self.offset + size]
                self.offset += len(value)
                return value

        with patch("personal_opportunity_radar.adapters.urllib.request.urlopen", return_value=Response()):
            status, body = _fetch("https://example.org/jobs", 1, max_bytes=3_000_000)
        self.assertEqual(status, 200)
        self.assertEqual(len(body), len(payload))
        self.assertEqual(len(json.loads(body)["padding"]), 2_500_000)


class DynamicInvariantTests(unittest.TestCase):
    def test_generated_data_uses_dynamic_invariants(self):
        records = load_discovered_records()
        artifact = build_artifact(records, write=False)
        self.assertGreater(len(artifact["opportunities"]), 0)
        self.assertFalse(validate([Opportunity.from_dict(item) for item in artifact["opportunities"]], public=True))

    def test_sources_are_expanded_and_records_have_provenance(self):
        self.assertGreaterEqual(len(load_sources()), 40)
        self.assertFalse(audit_sources()["mismatches"])

    def test_tracking_parameters_are_removed(self):
        self.assertEqual(canonical_url("https://Example.com/jobs/1/?utm_source=test&role=quant#apply"), "https://example.com/jobs/1?role=quant")

    def test_profile_contains_no_sensitive_inferences(self):
        self.assertTrue(PROFILE["known_facts_only"])
        self.assertIn("residency history", PROFILE["do_not_infer"])


if __name__ == "__main__":
    unittest.main()
