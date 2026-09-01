#!/usr/bin/env python3
"""Deterministically split the legacy registry and rebuild organisation files."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"

SOURCE_ORGANISATIONS = {
    "blackrock": "blackrock", "rothschild": "rothschild-co", "deutsche-bank": "deutsche-bank",
    "bank-of-america": "bank-of-america", "citi": "citi", "fidelity": "fidelity-international", "ubs": "ubs",
    "jane-street": "jane-street", "g-research": "g-research", "wells-fargo": "wells-fargo", "evercore": "evercore",
    "lazard": "lazard", "barclays": "barclays", "macquarie": "macquarie-group", "goldman-sachs": "goldman-sachs",
    "jpmorgan": "j-p-morgan", "morgan-stanley": "morgan-stanley", "optiver": "optiver", "imc": "imc-trading",
    "citadel": "citadel", "sig": "susquehanna-international-group", "flow-traders": "flow-traders",
    "man-aahl": "man-group", "hsbc": "hsbc", "bnp-paribas": "bnp-paribas", "socgen": "societe-generale",
    "nomura": "nomura", "rbc": "rbc-capital-markets", "santander": "santander", "jefferies": "jefferies",
    "moelis": "moelis-co", "houlihan-lokey": "houlihan-lokey", "blackstone": "blackstone", "drw": "drw",
    "xtx": "xtx-markets", "qrt": "qube-rt", "squarepoint": "squarepoint-capital", "maven": "maven-securities",
    "point72": "point72", "mathworks": "mathworks",
    "durham": "durham-university", "comap": "comap", "nasa": "nasa", "icpc": "icpc",
    "cern": "cern", "fields": "fields-institute", "lms": "london-mathematical-society",
    "cme": "cme-group", "cambridge": "university-of-cambridge", "oxford": "university-of-oxford",
    "warwick": "university-of-warwick", "esa": "european-space-agency", "gchq": "gchq", "ncsc": "ncsc",
    "dstl": "dstl", "ukri": "ukri", "ukaea": "ukaea", "npl": "national-physical-laboratory",
    "met-office": "met-office",
}

CURATED_ORGANISATIONS = [
    ("durham-university", "Durham University", ["research", "technology"], "university"),
    ("university-of-cambridge", "University of Cambridge", ["research"], "university"),
    ("university-of-oxford", "University of Oxford", ["research"], "university"),
    ("university-of-warwick", "University of Warwick", ["research"], "university"),
    ("imperial-college-london", "Imperial College London", ["research"], "university"),
    ("university-college-london", "University College London", ["research"], "university"),
    ("university-of-bristol", "University of Bristol", ["research"], "university"),
    ("university-of-bath", "University of Bath", ["research"], "university"),
    ("university-of-manchester", "University of Manchester", ["research"], "university"),
    ("university-of-edinburgh", "University of Edinburgh", ["research"], "university"),
    ("university-of-glasgow", "University of Glasgow", ["research"], "university"),
    ("kings-college-london", "King's College London", ["research"], "university"),
    ("queen-mary-university-of-london", "Queen Mary University of London", ["research"], "university"),
    ("university-of-southampton", "University of Southampton", ["research"], "university"),
    ("university-of-birmingham", "University of Birmingham", ["research"], "university"),
    ("university-of-nottingham", "University of Nottingham", ["research"], "university"),
    ("university-of-leeds", "University of Leeds", ["research"], "university"),
    ("university-of-sheffield", "University of Sheffield", ["research"], "university"),
    ("university-of-york", "University of York", ["research"], "university"),
    ("university-of-exeter", "University of Exeter", ["research"], "university"),
    ("lancaster-university", "Lancaster University", ["research"], "university"),
    ("loughborough-university", "Loughborough University", ["research"], "university"),
    ("queens-university-belfast", "Queen's University Belfast", ["research"], "university"),
    ("university-of-st-andrews", "University of St Andrews", ["research"], "university"),
    ("heriot-watt-university", "Heriot-Watt University", ["research"], "university"),
    ("university-of-strathclyde", "University of Strathclyde", ["research"], "university"),
    ("university-of-surrey", "University of Surrey", ["research"], "university"),
    ("university-of-sussex", "University of Sussex", ["research"], "university"),
    ("royal-holloway-university-of-london", "Royal Holloway, University of London", ["research"], "university"),
    ("cardiff-university", "Cardiff University", ["research"], "university"),
    ("swansea-university", "Swansea University", ["research"], "university"),
    ("alan-turing-institute", "The Alan Turing Institute", ["research", "technology"], "research_institute"),
    ("francis-crick-institute", "The Francis Crick Institute", ["research"], "research_institute"),
    ("wellcome-sanger-institute", "Wellcome Sanger Institute", ["research"], "research_institute"),
    ("fields-institute", "Fields Institute", ["research"], "research_institute"),
    ("isaac-newton-institute", "Isaac Newton Institute", ["research"], "research_institute"),
    ("london-mathematical-society", "London Mathematical Society", ["research"], "professional_body"),
    ("cern", "CERN", ["research", "engineering"], "research_institute"),
    ("embl-ebi", "EMBL-EBI", ["research", "technology"], "research_institute"),
    ("ukri", "UK Research and Innovation", ["research"], "public_body"),
    ("ukaea", "UK Atomic Energy Authority", ["research", "engineering"], "public_body"),
    ("national-physical-laboratory", "National Physical Laboratory", ["research", "engineering"], "public_body"),
    ("met-office", "Met Office", ["research", "technology"], "public_body"),
    ("european-space-agency", "European Space Agency", ["research", "engineering", "competitions"], "public_body"),
    ("nasa", "NASA", ["research", "engineering", "competitions"], "public_body"),
    ("gchq", "GCHQ", ["government", "technology"], "public_body"),
    ("ncsc", "National Cyber Security Centre", ["government", "technology"], "public_body"),
    ("dstl", "Defence Science and Technology Laboratory", ["government", "research", "engineering"], "public_body"),
    ("bank-of-england", "Bank of England", ["finance", "government"], "public_body"),
    ("financial-conduct-authority", "Financial Conduct Authority", ["finance", "government"], "public_body"),
    ("ofcom", "Ofcom", ["government", "technology"], "public_body"),
    ("national-grid", "National Grid", ["engineering"], "employer"),
    ("bae-systems", "BAE Systems", ["engineering", "technology"], "employer"),
    ("rolls-royce", "Rolls-Royce", ["engineering"], "employer"),
    ("airbus", "Airbus", ["engineering", "technology"], "employer"),
    ("arup", "Arup", ["engineering"], "employer"),
    ("atkinsrealis", "AtkinsRéalis", ["engineering"], "employer"),
    ("jacobs", "Jacobs", ["engineering"], "employer"),
    ("aecom", "AECOM", ["engineering"], "employer"),
    ("mott-macdonald", "Mott MacDonald", ["engineering"], "employer"),
    ("siemens", "Siemens", ["engineering", "technology"], "employer"),
    ("ge-vernova", "GE Vernova", ["engineering"], "employer"),
    ("schneider-electric", "Schneider Electric", ["engineering", "technology"], "employer"),
    ("comap", "COMAP", ["competitions"], "competition_organiser"),
    ("icpc", "ICPC", ["competitions", "technology"], "competition_organiser"),
    ("cme-group", "CME Group", ["competitions", "finance"], "employer"),
    ("cyber-security-challenge-uk", "Cyber Security Challenge UK", ["competitions", "technology"], "competition_organiser"),
    ("kaggle", "Kaggle", ["competitions", "technology"], "competition_organiser"),
    ("iet", "Institution of Engineering and Technology", ["competitions", "engineering"], "professional_body"),
    ("institute-of-physics", "Institute of Physics", ["research", "competitions"], "professional_body"),
    ("royal-statistical-society", "Royal Statistical Society", ["research", "competitions"], "professional_body"),
]


def source_organisation(source_id: str) -> str | None:
    matches = [(prefix, organisation) for prefix, organisation in SOURCE_ORGANISATIONS.items() if source_id.startswith(prefix)]
    return max(matches, key=lambda item: len(item[0]))[1] if matches else None


def source_category(source: dict) -> str:
    identifier, stream = source["id"], source.get("stream")
    if stream == "research" or identifier.startswith(("durham-", "fields-", "lms-", "cambridge-", "oxford-", "warwick-", "ukri-")):
        return "research_universities"
    if stream == "competitions_development" or identifier.startswith(("comap-", "nasa-", "icpc-", "citadel-quant", "esa-")):
        return "competitions_development"
    if identifier.startswith(("gchq-", "ncsc-", "dstl-", "ukaea-", "npl-", "met-office-", "cern-")):
        return "government_engineering"
    if identifier.startswith(("jane-street", "optiver", "imc-", "citadel-", "sig-", "flow-traders", "man-aahl", "drw-", "xtx-", "qrt-", "squarepoint-", "maven-", "point72-", "g-research")):
        return "quant_technology"
    return "finance"


def load_sources() -> list[dict]:
    legacy = CONFIG / "sources.json"
    if legacy.exists():
        return json.loads(legacy.read_text(encoding="utf-8"))
    values = []
    for path in sorted((CONFIG / "sources").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        values.extend(payload.get("sources", payload))
    return values


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate generation without writing")
    args = parser.parse_args()
    benchmark = json.loads((CONFIG / "coverage_targets" / "trackr_uk_organisations.json").read_text(encoding="utf-8"))
    benchmark_by_id = {item["id"]: item for item in benchmark["organisations"]}
    sources = load_sources()
    curated = {identifier: {"id": identifier, "name": name, "sectors": sectors, "organisation_type": kind}
               for identifier, name, sectors, kind in CURATED_ORGANISATIONS}

    for source in sources:
        organisation_id = source.get("organisation_id") or source_organisation(source["id"])
        if not organisation_id:
            name = source.get("organisation") or re.sub(r"\b(careers?|students?|programmes?|programs?|opportunities|internships?)\b.*$", "", source["name"], flags=re.I).strip()
            organisation_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            curated.setdefault(organisation_id, {"id": organisation_id, "name": name, "sectors": [source_category(source).split("_")[0]], "organisation_type": "employer"})
        source["organisation_id"] = organisation_id
        source["sector"] = source_category(source)
        source["scan_tier"] = source.get("scan_tier") or ("high" if source.get("stream") == "spring_insight" else "daily" if source.get("stream") else "weekly")
        source["provenance"] = source.get("provenance", "curated_official_registry")
        source["resolution_status"] = source.get("resolution_status", "resolved")
        source["enabled"] = source.get("enabled", True)
        source.setdefault("family", organisation_id)

    resolved = {source["organisation_id"] for source in sources if source.get("enabled", True)}
    organisation_groups: dict[str, list[dict]] = defaultdict(list)
    for item in benchmark["organisations"]:
        sectors = item.get("sectors", [])
        primary = "finance" if "finance" in sectors else "technology" if "technology" in sectors else "law"
        organisation_groups[f"trackr_{primary}"].append({
            "id": item["id"], "name": item["name"], "sectors": sectors, "organisation_type": "employer",
            "provenance": [{"kind": "trackr_benchmark", "benchmark_id": benchmark["benchmark_id"], "company_path": item["trackr_company_path"]}],
            "source_resolution_status": "resolved" if item["id"] in resolved else "unresolved",
        })
    extra_items = []
    for identifier, item in curated.items():
        if identifier in benchmark_by_id:
            continue
        extra_items.append({**item, "provenance": ["curated_project_universe"], "source_resolution_status": "resolved" if identifier in resolved else "unresolved"})
    for item in extra_items:
        sectors = item.get("sectors", [])
        group = "research" if "research" in sectors else "competitions" if "competitions" in sectors else "government_engineering"
        organisation_groups[group].append(item)

    source_groups: dict[str, list[dict]] = defaultdict(list)
    for source in sources:
        source.pop("registry_file", None)
        source_groups[source_category(source)].append(source)

    if args.check:
        print(json.dumps({"organisations": sum(map(len, organisation_groups.values())), "sources": len(sources), "groups": sorted(source_groups)}, indent=2))
        return 0
    for group, values in organisation_groups.items():
        write_json(CONFIG / "organisations" / f"{group}.json", {"schema_version": 1, "organisations": sorted(values, key=lambda item: item["id"])})
    for group, values in source_groups.items():
        write_json(CONFIG / "sources" / f"{group}.json", {"schema_version": 1, "sources": sorted(values, key=lambda item: item["id"])})
    print(json.dumps({"organisations": sum(map(len, organisation_groups.values())), "sources": len(sources), "groups": sorted(source_groups)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
