from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_opportunity_radar.adapters import AdapterResult
from personal_opportunity_radar.cli import main
from personal_opportunity_radar.config import ROOT, load_sources
from personal_opportunity_radar.models import SourceStatus
from personal_opportunity_radar.registry import (
    add_source, benchmark_coverage, coverage_report, detect_source, import_registry,
    remove_source, unresolved_organisations, validate_registry,
)
from personal_opportunity_radar.scanner import _health, _select_sources


class SourceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "config"
        (self.root / "organisations").mkdir(parents=True)
        (self.root / "sources").mkdir()
        (self.root / "coverage_targets").mkdir()
        self._write(self.root / "source_profiles.json", {
            "profiles": {
                "official_html": {"adapter": "html", "max_detail_pages": 9},
                "greenhouse_api": {"adapter": "greenhouse", "fetch_details": False},
                "lever_api": {"adapter": "lever", "fetch_details": False},
                "ashby_api": {"adapter": "ashby", "fetch_details": False},
                "rss_feed": {"adapter": "rss"},
                "candidate_url": {"adapter": "html", "enabled": False},
            }
        })
        self._write(self.root / "organisations" / "core.json", {"organisations": [
            {"id": "acme", "name": "Acme Ltd", "aliases": ["acme-ltd"], "sectors": ["technology"]},
            {"id": "beta", "name": "Beta Bank", "sectors": ["finance"]},
            {"id": "gamma", "name": "Gamma Law", "sectors": ["law"]},
        ]})
        self._write(self.root / "coverage_targets" / "trackr_uk_organisations.json", {
            "benchmark_id": "fixture", "captured_at": "2026-08-31T00:00:00Z",
            "organisations": [
                {"id": "acme", "name": "Acme Ltd", "sectors": ["technology"]},
                {"id": "beta", "name": "Beta Bank", "sectors": ["finance"]},
                {"id": "gamma", "name": "Gamma Law", "sectors": ["law"]},
            ],
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_01_add_new_company_via_cli(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["sources", "--config-root", str(self.root), "add", "--name", "Delta", "--url", "https://delta.example/careers"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["organisation_id"], "delta")
        self.assertTrue(any(item["id"] == "delta-opportunities" for item in load_sources(config_root=self.root)))

    def test_02_duplicate_alias_merges_to_canonical_organisation(self) -> None:
        source = add_source(name="Acme Ltd", organisation_id="acme-ltd", url="https://acme.example/jobs", config_root=self.root)
        self.assertEqual(source["organisation_id"], "acme")
        organisations = json.loads((self.root / "organisations" / "core.json").read_text())["organisations"]
        self.assertEqual(len(organisations), 3)

    def test_03_duplicate_source_url_is_rejected(self) -> None:
        add_source(name="Acme", url="https://acme.example/jobs", config_root=self.root)
        with self.assertRaisesRegex(ValueError, "source URL already exists"):
            add_source(name="Acme duplicate", source_id="acme-two", url="https://acme.example/jobs/", config_root=self.root)

    def test_04_adapter_auto_detection(self) -> None:
        greenhouse = detect_source("https://job-boards.greenhouse.io/example/jobs/123")
        lever = detect_source("https://jobs.lever.co/example/123")
        ashby = detect_source("https://jobs.ashbyhq.com/example/123")
        self.assertEqual((greenhouse["adapter"], lever["adapter"], ashby["adapter"]), ("greenhouse", "lever", "ashby"))
        self.assertFalse(greenhouse["review_required"])
        embedded = detect_source("https://job-boards.greenhouse.io/embed/job_board?for=jumptrading")
        self.assertIn("/boards/jumptrading/jobs", embedded["url"])

    def test_05_source_profile_defaults_are_applied(self) -> None:
        self._write(self.root / "sources" / "fixture.json", {"sources": [{
            "id": "acme-jobs", "name": "Acme jobs", "organisation_id": "acme",
            "url": "https://acme.example/jobs", "profile": "official_html",
        }]})
        source = load_sources(config_root=self.root)[0]
        self.assertEqual(source["adapter"], "html")
        self.assertEqual(source["max_detail_pages"], 9)

    def test_06_bulk_csv_import_reports_duplicates_safely(self) -> None:
        path = Path(self.temporary.name) / "sources.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "name", "url", "sector"])
            writer.writeheader()
            writer.writerow({"id": "delta", "name": "Delta", "url": "https://delta.example/jobs", "sector": "technology"})
            writer.writerow({"id": "epsilon", "name": "Epsilon", "url": "https://epsilon.example/jobs", "sector": "finance"})
            writer.writerow({"id": "duplicate", "name": "Duplicate", "url": "https://delta.example/jobs", "sector": "technology"})
        result = import_registry(path, config_root=self.root)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(len(result["rejected"]), 1)

    def test_07_trackr_benchmark_coverage_has_zero_missing(self) -> None:
        result = benchmark_coverage("trackr", config_root=self.root)
        self.assertEqual(result["organisation_coverage_percent"], 100.0)
        self.assertEqual(result["missing_from_organisation_registry"], [])

    def test_08_unresolved_organisation_appears_in_report(self) -> None:
        unresolved = unresolved_organisations(config_root=self.root)
        self.assertEqual({item["id"] for item in unresolved}, {"acme", "beta", "gamma"})

    def test_09_hundreds_of_sources_load_quickly(self) -> None:
        values = [{"id": f"source-{number}", "name": f"Source {number}", "organisation_id": "acme", "url": f"https://example.com/{number}"} for number in range(600)]
        self._write(self.root / "sources" / "large.json", {"sources": values})
        started = time.monotonic()
        loaded = load_sources(config_root=self.root)
        self.assertEqual(len(loaded), 600)
        self.assertLess(time.monotonic() - started, 2)

    def test_10_scan_tier_filtering(self) -> None:
        values = [
            {"id": "high", "enabled": True, "scan_tier": "high"},
            {"id": "daily", "enabled": True, "scan_tier": "daily"},
            {"id": "weekly", "enabled": True, "scan_tier": "weekly"},
        ]
        with patch("personal_opportunity_radar.scanner.load_sources", return_value=values):
            selected = _select_sources(None, None, {"high", "daily"}, None)
        self.assertEqual([item["id"] for item in selected], ["high", "daily"])

    def test_11_high_priority_sources_run_four_times_daily(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "17 0,6,12,18 * * *"', workflow)
        self.assertIn('TIERS="--tier high"', workflow)

    def test_12_zero_listing_health_is_honest(self) -> None:
        result = AdapterResult(200, [], "fingerprint")
        allowed = _health({"id": "allowed", "name": "Allowed", "url": "https://example.com", "allow_zero": True}, result, "2026-08-31T00:00:00Z")
        unexpected = _health({"id": "unexpected", "name": "Unexpected", "url": "https://example.com"}, result, "2026-08-31T00:00:00Z")
        self.assertEqual((allowed.status, allowed.opportunity_status), (SourceStatus.HEALTHY, "zero_allowed"))
        self.assertEqual((unexpected.status, unexpected.opportunity_status), (SourceStatus.DEGRADED, "zero_unexpected"))

    def test_13_coverage_report_counts_provenance(self) -> None:
        self._write(self.root / "sources" / "fixture.json", {"sources": [{
            "id": "acme", "name": "Acme", "organisation_id": "acme", "url": "https://acme.example/jobs",
            "provenance": "trackr_benchmark", "enabled": True,
        }]})
        report = coverage_report(config_root=self.root)
        self.assertEqual(report["sources_by_provenance"]["trackr_benchmark"], 1)

    def test_14_removal_does_not_delete_historical_state(self) -> None:
        self._write(self.root / "sources" / "fixture.json", {"sources": [{
            "id": "acme", "name": "Acme", "organisation_id": "acme", "url": "https://acme.example/jobs", "enabled": True,
        }]})
        historical = Path(self.temporary.name) / "data" / "discovered" / "opportunities.json"
        self._write(historical, {"opportunities": [{"canonical_id": "kept"}]})
        before = historical.read_bytes()
        result = remove_source("acme", config_root=self.root)
        self.assertEqual(result["action"], "disabled")
        self.assertEqual(historical.read_bytes(), before)

    def test_15_full_registry_validation(self) -> None:
        self.assertTrue(validate_registry(config_root=self.root)["valid"])


if __name__ == "__main__":
    unittest.main()
