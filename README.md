# Personal Opportunity Radar

A local-first discovery pipeline and static decision dashboard for a Durham University BSc Mathematics and Physics student starting October 2026 and graduating June 2029.

The scanner discovers official listing/index pages, follows relevant detail links, extracts and normalizes previously unknown programmes, evaluates only explicit candidate facts, preserves lifecycle history, and publishes a validated static artifact. Manual additions and human overrides are separate overlays rather than the primary database.

## Architecture

```text
official sources → adapters → discovered snapshots → normalization + eligibility
                                           ↓
manual additions → dedupe + overrides → review/archive/public artifacts → Vite dashboard
```

Organisations and official sources are separate, category-split registries. The current universe contains 738 organisations and 215 source records, including a reproducible 672-organisation Trackr UK benchmark. Trackr supplies names for coverage auditing only; the production scanner never treats Trackr as opportunity truth. Adapters support ordinary and structured HTML, university/research pages, Greenhouse, Lever, Ashby, Workday-style JSON, SmartRecruiters, Teamtailor, Workable, arbitrary public JSON, and RSS/Atom.

## Quick start

Requirements: Python 3.11+, Node 22+, and npm. Commands work in PowerShell, Command Prompt, macOS, and Linux shells.

```console
npm ci
python run.py scan --allow-partial
python run.py validate
npm test
npm run dev
```

`python run.py` performs the normal broad scan. Network-free rebuilding is available with `python run.py scan --offline`.

## Scan commands

```console
python run.py scan --spring --allow-partial
python run.py scan --research --allow-partial
python run.py scan --competitions --allow-partial
python run.py scan --internships --allow-partial
python run.py scan --source jane-street-see-london-2027 --allow-partial
python run.py scan --tier high --allow-partial
python run.py audit-sources
python run.py clean-slate-test --workers 8 --timeout 12
python run.py build
python run.py validate
```

Useful bounds are `--workers`, `--timeout`, `--limit`, and `--dry-run`. Without `--allow-partial`, any failed source makes the scan exit non-zero. With it, successful sources are persisted while failed sources retain their previous reviewed records.

## Data layers

| Path | Purpose |
|---|---|
| `config/organisations/*.json` | Canonical organisation universe, sectors, type, provenance, and source-resolution state |
| `config/sources/*.json` | Category-split official source registry, adapter settings, tiers, and parse expectations |
| `config/source_profiles.json` | Reusable adapter/profile defaults |
| `config/coverage_targets/trackr_uk_organisations.json` | Reproducible Trackr names benchmark; never a runtime opportunity feed |
| `data/discovered/opportunities.json` | Automatically discovered and retained source snapshots |
| `data/manual/additions.json` | Small, explicit official-source additions when a site blocks automation |
| `config/overrides.json` | Human corrections and review decisions keyed by stable canonical ID |
| `data/opportunities.json` | Canonical publishable schema-v2 artifact |
| `data/review_queue.json` | Parser suggestions, conflicts, and unresolved current-cycle questions |
| `data/archive/opportunities.json` | Closed, stale, identity-restricted, and ineligible records |
| `data/source_health.json` | Per-source HTTP and parser health |
| `data/coverage_report.json` | Source- and family-level configured/reachable/parsed/degraded/failed coverage |
| `data/clean_slate_report.json` | Repeatable no-history reconstruction proof and source results |
| `data/clean_slate_transient_failure_report.json` | Preserved network-throttled sample; prevents a failed environment run from being mistaken for parser coverage |
| `data/change_log.json` | Meaningful field changes across scans |

## Add a company in 30 seconds

Use an official directory or ATS URL rather than hand-coding its current programmes:

```console
python run.py sources add --id example --name "Example" --url "https://example.com/careers/students" --category technology
python run.py sources test example
python run.py sources validate
git add config
git commit -m "Add Example source"
```

Known Greenhouse, Lever, and Ashby URLs are converted to their public API adapters automatically. An unrecognised generic careers URL is stored disabled with `candidate_url_needs_validation`; pass `--enable` only after `sources test` proves it parses correctly. `sources remove SOURCE_ID` safely disables a source and does not delete historical opportunities; permanent registry removal requires `--purge`.

The complete management surface is:

```console
python run.py sources list
python run.py sources unresolved
python run.py sources coverage
python run.py sources coverage --benchmark trackr
python run.py sources export
```

Bulk import accepts JSON or CSV. CSV needs at least `name,url`; optional columns are `id,organisation_id,sector,scan_tier,enabled`:

```console
python run.py sources import companies.csv --category technology
```

The import reports duplicate IDs/URLs as rejected rows without losing valid rows. Use `data/manual/additions.json` only when an official site blocks automation, and `config/overrides.json` for narrow reviewed corrections keyed by stable ID.

## Lifecycle and eligibility

Lifecycle states are `open`, `interest_open`, `officially_announced`, `unknown`, `closed`, and `stale`. Announced requires an explicit official future opening. Interest-open requires a live registration action. Deadline semantics are separately structured as fixed, rolling, unknown, or none stated.

Eligibility rules record their evidence, strength, and outcome. The engine knows only institution, course, study dates, graduation year, institution country, and British nationality. It never derives residence, grades, age, work authorization, security clearance, or sensitive traits. GCHQ residency remains an explicit check even when nationality matches, and security vetting is recorded as an appointment condition rather than predicted. Programme-level identity restrictions are suppressed; generic equal-opportunity text is not.

Priority uses explainable A/B/C tiers, never pseudo-precise public scores.

## Browser-only tracking

Saved applications, notes, next actions, and personal deadlines stay in local storage. Backup JSON is versioned and stable-ID aliases migrate older state. Calendar export includes every available opening, deadline, start, end, and personal-action date. “New since visit” compares `first_seen` with the previous local visit timestamp.

## Automation and GitHub Pages

`.github/workflows/deploy-pages.yml` runs high-priority sources four times daily, daily sources twice daily, and weekly sources each Sunday; pushes/manual dispatches run all three active tiers. It preserves records behind failed/degraded sources, validates the 100% Trackr organisation benchmark, commits refreshed state with `[skip ci]`, and deploys the tested build. No database, server runtime, search-engine scraping, Trackr runtime dependency, or private feed is required.

## Verification

`npm test` runs schema validation, 56 Python pipeline/regression tests (including all source-management, benchmark, tier, health, unknown-programme, changed-page, clean-slate, response-size, deduplication, and staleness cases), TypeScript checking, the production Vite build, and eight Node UI/artifact checks. Live facts still require reading the linked official page before acting; blocked, rate-limited, or client-rendered sites are honestly reported as degraded or failed and retained facts are not silently marked current.

The current no-history proof is recorded in `data/clean_slate_report.json`. Network results will vary by run; the committed report is evidence, not a promise that a third-party site will remain reachable. See `docs/LIVE_SCRAPER.md` for operations, failure semantics, and known limitations.

## License

MIT. Opportunity facts remain subject to their publishers' terms.
