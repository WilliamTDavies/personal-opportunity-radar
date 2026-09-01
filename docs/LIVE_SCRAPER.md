# Live scraper operations

## Discovery contract

Each source entry in `config/sources/*.json` points to a canonical organisation in `config/organisations/*.json` and names an official family-level index, directory, ATS endpoint, feed, or single programme page. Reusable defaults live in `config/source_profiles.json`. Generic HTML sources retain anchor text and URLs, score likely opportunity links, fetch bounded detail pages, and extract title, organisation, stream, dates, deadline, lifecycle, application route, location, structured eligibility, provenance, and confidence.

Programme templates are fallback parser aids only. Generic extraction is attempted first, and a new matching link does not need a title, cycle, date, or eligibility rule added to `config/sources/*.json`. The sole exception is a term-gated `prefer_verified_template` source: it is reserved for an official page that interleaves cohort dates or multiple programmes closely enough for generic extraction to borrow the wrong date or requirements, and it falls back to the live generic record whenever every verified term is not present.

## Publication decisions

A discovered record is auto-published only when its official detail evidence supports a relevant cycle, an actionable lifecycle, compatible structured eligibility, sufficient parser confidence, no important conflict, no identity-targeted restriction, and a stable deduplication result. Genuine uncertainty goes to `data/review_queue.json`. Closed, stale, explicitly ineligible, and identity-targeted records go to `data/archive/opportunities.json`.

Obvious navigation and marketing labels are discarded before review. The precision gate requires a named programme, scheme, internship, role, competition, or similarly formal opportunity signal; an employer's generic careers heading is not an opportunity.

## Failure and lifecycle semantics

- HTTP/network failures are isolated per source.
- Conditional ETag/Last-Modified requests skip an unchanged index and its redundant detail fan-out; a changed index still fetches every newly discovered link.
- Transport, parser, and opportunity states are separate. HTTP 200 with a parser failure, failed canary, or unexpected zero opportunities is degraded, not healthy.
- Each source retains `last_nonzero_parse`, `last_known_listing_count`, parser-canary state, and `last_successful_opportunity_extraction`.
- Failed or partial scans preserve retained records and cannot close them.
- A listing becomes stale only after three consecutive healthy, complete absences.
- Successful reappearance resets its missing count and re-extracts every tracked field.
- Meaningful field changes are appended to `data/change_log.json`.
- Stable IDs survive title/URL changes through aliases, application URLs, and source-family matching.

`data/coverage_report.json` reports operational totals, family summaries, transport/parser/opportunity distributions, adapters, sectors, tiers, provenance, and benchmark resolution. “Reachable” includes HTTP 200 and a validator-confirmed HTTP 304; “parsed” means the last current parser state is healthy; “relevant” counts detail records that passed generic extraction. These are deliberately separate claims.

## Eligibility boundaries

The profile contains only the facts in `config/profile.json`. Nationality never implies residency, work authorization, security clearance, grades, age, or sensitive identity. GCHQ's British nationality and UK-residency rules are evaluated separately; vetting is informational. Desirable/preferred degree subjects do not become hard exclusions. Explicit postgraduate-only, host-university-only, country-restricted, penultimate-year, and graduation-year rules are evaluated as structured requirements.

## Repeatable proofs

```console
python run.py clean-slate-test --workers 8 --timeout 12
python run.py sources validate
python run.py sources coverage --benchmark trackr
python -m unittest discover -s tests -p 'test_*.py'
npm test
```

The clean-slate command fetches every enabled source, starts with no discovered snapshot, and writes only `data/clean_slate_report.json`. It reports reconstructed/public/review/archive counts, manual and template dependency, source health, detail-page outcomes, and historical public IDs not independently rebuilt. It never overwrites production opportunity data.

Fixture tests independently prove that a never-preseeded programme is discovered from an index, fetched, structured, evaluated, and auto-published, and that a changed detail page updates lifecycle, deadline, verification time, stable identity, and change history.

## Scheduled persistence

`.github/workflows/deploy-pages.yml` runs high-tier sources at 00:17, 06:17, 12:17, and 18:17 UTC; high+daily at 05:37 and 17:37; and high+daily+weekly at 04:47 Sunday. Push/manual runs include all active tiers. It:

1. scans the scheduled tiers with partial-failure preservation;
2. validates schema, source registries, zero-missing Trackr organisation coverage, and pipeline fixtures;
3. commits generated JSON back to `main` with `[skip ci]` when it changed;
4. installs the locked Node dependencies;
5. runs the full test and production build;
6. uploads and deploys the resulting `dist` directory to GitHub Pages.

The browser dashboard is static. Pursuit state, notes, next actions, and personal dates remain in local storage and are portable through versioned JSON backup/restore.

## Known limitations

- Official sites may return 403/429/5xx responses, time out, or require client-side rendering. Those sources remain visible as degraded/failed rather than being treated as empty.
- A family directory that exposes no crawlable opportunity links can be monitored for health but cannot provide automatic detail discovery until it exposes HTML/JSON/feed data or receives a source-specific family adapter.
- Ambiguous external-student access, residence, work authorization, and conflicting official wording require review.
- Dates and criteria can change after a scan. The dashboard links primary evidence and should be checked before applying.
- Clean-slate counts vary with live third-party availability; the committed report records the actual run, including failures and template/manual dependencies.
- When an immediately repeated proof was dominated by transient network failures, that raw sample is retained separately as `data/clean_slate_transient_failure_report.json`; it is not substituted for the most recent representative completed run.
