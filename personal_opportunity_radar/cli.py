from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ROOT, load_sources
from .models import Stream
from .registry import (
    add_source, benchmark_coverage, coverage_report, export_registry, import_registry,
    remove_source, test_source, unresolved_organisations, validate_registry,
)
from .scanner import audit_sources, build_artifact, clean_slate_test, load_canonical_records, run_scan
from .validation import validate


STREAM_FLAGS = {"spring": Stream.SPRING, "research": Stream.RESEARCH, "competitions": Stream.COMPETITIONS, "internships": Stream.INTERNSHIPS}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="personal-opportunity-radar", description="Discover official opportunities and build the static radar.")
    commands = result.add_subparsers(dest="command")
    scan = commands.add_parser("scan", help="Run discovery adapters and rebuild canonical data")
    scan.add_argument("--source", action="append", default=[], help="Source ID; repeatable")
    for flag in STREAM_FLAGS:
        scan.add_argument(f"--{flag}", action="store_true")
    scan.add_argument("--limit", type=int)
    scan.add_argument("--workers", type=int, default=6)
    scan.add_argument("--timeout", type=float, default=12)
    scan.add_argument("--tier", action="append", choices=["high", "daily", "weekly", "manual"], default=[])
    scan.add_argument("--dry-run", action="store_true")
    scan.add_argument("--allow-partial", action="store_true")
    scan.add_argument("--offline", action="store_true", help="Rebuild from snapshots without network")
    commands.add_parser("build", help="Build from discovered/manual/override layers")
    commands.add_parser("validate", help="Validate the generated canonical dataset")
    commands.add_parser("audit-sources", help="Report source coverage and configuration mismatches")
    clean = commands.add_parser("clean-slate-test", help="Scan with no discovered seed state and report what is reconstructed")
    clean.add_argument("--source", action="append", default=[], help="Source ID; repeatable")
    for flag in STREAM_FLAGS:
        clean.add_argument(f"--{flag}", action="store_true")
    clean.add_argument("--limit", type=int)
    clean.add_argument("--workers", type=int, default=6)
    clean.add_argument("--timeout", type=float, default=12)
    clean.add_argument("--tier", action="append", choices=["high", "daily", "weekly", "manual"], default=[])
    clean.add_argument("--no-write", action="store_true", help="Do not update data/clean_slate_report.json")

    sources = commands.add_parser("sources", help="Inspect and maintain organisation/source registries")
    sources.add_argument("--config-root", type=Path, default=ROOT / "config", help=argparse.SUPPRESS)
    source_commands = sources.add_subparsers(dest="sources_command", required=True)
    listing = source_commands.add_parser("list", help="List configured sources")
    listing.add_argument("--enabled", action="store_true", help="Show only enabled sources")
    add = source_commands.add_parser("add", help="Add an organisation and official source")
    add.add_argument("--name", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--id", dest="source_id")
    add.add_argument("--organisation-id")
    add.add_argument("--category", default="custom")
    add.add_argument("--tier", choices=["high", "daily", "weekly", "manual"], default="daily")
    enable = add.add_mutually_exclusive_group()
    enable.add_argument("--enable", action="store_true", help="Enable even when adapter validation is required")
    enable.add_argument("--disable", action="store_true", help="Store as a disabled source candidate")
    remove = source_commands.add_parser("remove", help="Safely disable a source")
    remove.add_argument("source_id")
    remove.add_argument("--purge", action="store_true", help="Permanently remove instead of disabling")
    source_commands.add_parser("validate", help="Validate every registry and benchmark invariant")
    test = source_commands.add_parser("test", help="Run one adapter without changing generated data")
    test.add_argument("source_id")
    test.add_argument("--timeout", type=float, default=12)
    source_commands.add_parser("unresolved", help="List organisations without an enabled official source")
    coverage = source_commands.add_parser("coverage", help="Report source and benchmark coverage")
    coverage.add_argument("--benchmark", choices=["trackr"])
    importing = source_commands.add_parser("import", help="Import a JSON source list")
    importing.add_argument("file", type=Path)
    importing.add_argument("--category", default="imported")
    source_commands.add_parser("export", help="Print the complete registry as JSON")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = args.command
    try:
        if command is None:
            artifact, health = run_scan(allow_partial=True)
            print(json.dumps(_summary(artifact, health, False), indent=2))
            return 0
        if command == "validate":
            errors = validate(load_canonical_records(), public=True)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            print("Validated generated public records successfully.")
            return 0
        if command == "build":
            artifact = build_artifact()
            print(f"Built {len(artifact['opportunities'])} public opportunities.")
            return 0
        if command == "audit-sources":
            result = audit_sources()
            print(json.dumps(result, indent=2))
            return 1 if result["mismatches"] else 0
        if command == "clean-slate-test":
            streams = {value for flag, value in STREAM_FLAGS.items() if getattr(args, flag)} or None
            result = clean_slate_test(source_ids=set(args.source) or None, streams=streams, tiers=set(args.tier) or None, limit=args.limit,
                                      workers=args.workers, timeout=args.timeout, write_report=not args.no_write)
            print(json.dumps(result, indent=2))
            return 0
        if command == "sources":
            return _sources_command(args)
        streams = {value for flag, value in STREAM_FLAGS.items() if getattr(args, flag)} or None
        artifact, health = run_scan(source_ids=set(args.source) or None, streams=streams, tiers=set(args.tier) or None, limit=args.limit,
                                    workers=args.workers, timeout=args.timeout, dry_run=args.dry_run,
                                    allow_partial=args.allow_partial, offline=args.offline)
        print(json.dumps(_summary(artifact, health, args.dry_run), indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _sources_command(args: argparse.Namespace) -> int:
    root = args.config_root
    action = args.sources_command
    if action == "list":
        values = load_sources(config_root=root)
        if args.enabled:
            values = [item for item in values if item.get("enabled", True)]
        payload = [{
            "id": item["id"], "organisation_id": item.get("organisation_id"), "adapter": item.get("adapter", "html"),
            "tier": item.get("scan_tier", "daily"), "enabled": item.get("enabled", True), "url": item["url"],
        } for item in values]
    elif action == "add":
        enabled = True if args.enable else False if args.disable else None
        payload = add_source(name=args.name, url=args.url, organisation_id=args.organisation_id, source_id=args.source_id,
                             category=args.category, scan_tier=args.tier, enabled=enabled, config_root=root)
    elif action == "remove":
        payload = remove_source(args.source_id, purge=args.purge, config_root=root)
    elif action == "validate":
        payload = validate_registry(config_root=root)
        print(json.dumps(payload, indent=2))
        return 0 if payload["valid"] else 1
    elif action == "test":
        payload = test_source(args.source_id, timeout=args.timeout, config_root=root)
    elif action == "unresolved":
        payload = unresolved_organisations(config_root=root)
    elif action == "coverage":
        payload = benchmark_coverage(args.benchmark, config_root=root) if args.benchmark else coverage_report(config_root=root)
    elif action == "import":
        payload = import_registry(args.file, category=args.category, config_root=root)
    else:
        payload = export_registry(config_root=root)
    print(json.dumps(payload, indent=2))
    return 0


def _summary(artifact: dict, health: list, dry_run: bool) -> dict:
    return {"opportunities": len(artifact["opportunities"]), "sources_scanned": len(health),
            "healthy_sources": sum(item.status.value == "healthy" for item in health),
            "degraded_sources": sum(item.status.value == "degraded" for item in health),
            "failed_sources": sum(item.status.value == "failed" for item in health), "dry_run": dry_run}
