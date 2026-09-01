# Methodology

## Candidate facts

The engine may use only Durham University, BSc Mathematics and Physics, October 2026 start, June 2029 graduation, and British nationality. Age, grades, residence, work authorization, security clearance, and sensitive characteristics remain unknown unless the user explicitly adds them outside the public repository.

## Pipeline decisions

1. A category-split source record resolves a canonical organisation to an official listing or programme page; third-party benchmarks never supply runtime facts.
2. Conditional validators avoid refetching unchanged indexes. Transport, parser, and opportunity-extraction success are recorded separately.
3. Candidate listings are normalized into stable IDs, evidence, lifecycle, dates, requirements, and source fingerprints.
4. Official and duplicate/aggregator records merge through canonical IDs, aliases, and normalized URL/title identity.
5. Structured required/preferred rules are evaluated against known facts.
6. Human overrides apply by stable ID and are visible as a separate source kind.
7. Conflicts and new low-confidence discoveries enter review; ineligible, closed, stale, or identity-targeted programmes enter archive.
8. Only validated publishable records reach `data/opportunities.json`.

## Lifecycle

| State | Meaning |
|---|---|
| `open` | A current official action route accepts an application or entry. |
| `interest_open` | A current official registration/interest action is usable, but full applications are not open. |
| `officially_announced` | The official source explicitly states a future opening. |
| `unknown` | The programme is real but a current action/opening is not verified. |
| `closed` | A successful authoritative parse says closed or a verified deadline passed. |
| `stale` | Evidence no longer supports current publication. |

A failed request never causes a lifecycle downgrade. A parser regression is degraded source health and a review signal.

## Deadlines

Deadline status is `fixed`, `rolling`, `unknown`, or `none_stated`, with the source and verification flag stored separately. Rolling recruitment is surfaced as urgent because a nominal deadline may not preserve availability.

## Eligibility and suppression

Required conflicts are ineligible; missing required facts are uncertain; preferred conflicts do not create a hard exclusion. Programme-level identity restrictions are suppressed, while ordinary pages that merely mention equal opportunity or diversity are retained. Research access distinguishes host-only, external UK, and worldwide routes. British nationality never proves UK residence.

## Priority

- **A:** current open/interest action and eligible or likely eligible.
- **B:** credible announced/unknown horizon worth preparing or monitoring.
- **C:** uncertain or lower-actionability record that still passes publication review.

The dashboard does not expose numeric relevance scores.

## Source health and change control

Each source records last attempt, last HTTP success, last parse success, last nonzero parse, last known listing count, parser-canary state, last successful opportunity extraction, listing/relevant counts, validators, fingerprints, elapsed time, and healthy/degraded/stale/failed state. A transport success with an unexpected zero extraction is degraded unless that source explicitly allows zero current listings. Meaningful canonical field changes update `last_changed` and append to the change log. Stable aliases migrate browser state after URL/title/ID changes.

Organisation coverage and operational source resolution are deliberately different metrics. The Trackr benchmark must be 100% present in the organisation registry, while each name is separately classified as working, degraded, inaccessible, or unresolved against tested official sources.
