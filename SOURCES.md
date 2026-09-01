# Source inventory

The machine-readable universe is split by responsibility:

- `config/organisations/*.json` contains 738 canonical organisations across finance, technology, law, engineering, government, university research, institutes, and competitions.
- `config/sources/*.json` contains 215 official source records; 83 are enabled and 132 are explicit candidates awaiting safe validation.
- `config/source_profiles.json` contains reusable adapter defaults.
- `config/coverage_targets/trackr_uk_organisations.json` captures 672 unique public Trackr UK organisations: 413 Finance, 253 Technology, and 128 Law before cross-tracker deduplication.

Trackr is a benchmark only. Production opportunity records must come from official configured URLs. At the 2026-08-31 capture, Trackr's public navigation exposed UK Finance, UK Tech, and UK Law but no UK Engineering tracker; the benchmark records that absence instead of inventing membership.

Run `python run.py sources coverage --benchmark trackr` to prove 672/672 organisation registration with zero missing. The same report separately assigns every benchmark organisation one honest official-source state: working, degraded, inaccessible, or unresolved. Storing a name does not count as an operational source.

## Named audit sources

| Organisation/programme | Registry ID | Publication treatment |
|---|---|---|
| BlackRock 2027 Spring Insight | `blackrock-spring-2027` | Open; fixed 4 December 2026 deadline |
| Jane Street SEE London 2027 | `jane-street-see-london-2027` | Live official programme/admission route when parsed |
| G-Research Spring into Quant Finance 2027 | `g-research-spring-quant-2027` | Interest open; 2026 application closed |
| Wells Fargo EMEA Spring Week 2027 | `wells-fargo-spring-week-2027` | Official evidence retained manually when automated access is blocked |
| Evercore London Spring Weeks 2027 | `evercore-spring-week-2027` | First-year route |
| Lazard Spring Insight 2027 | `lazard-spring-insight-2027` | Officially announced; rolling once open |
| Barclays Discovery | `barclays-discovery-2027` | Interest open |
| Macquarie UK | `macquarie-uk-early-careers` | Conflicting official evidence; review queue |
| Fields FUSRP 2027 | `fields-fusrp-2027` | Worldwide mathematical research; official 2027 dates |
| LMS Bursary 2027 | `lms-bursary-2027` | Ineligible for first-year summer; archived |
| Durham BSI | `durham-bsi-bursary` | Evergreen page does not verify live 2027 call; review queue |
| CME September Trading Challenge | `cme-september-trading-2026` | Open but residence is unknown; uncertain |
| COMAP MCM/ICM 2027 | `comap-mcm-2027` | Open same-university undergraduate team route |

Search engines are not scheduled dependencies. A source may be found through research, but production evidence must be the publisher's official page. Duplicated registry URLs fail `python run.py audit-sources`.

## Coverage and provenance commands

```console
python run.py sources list
python run.py sources validate
python run.py sources unresolved
python run.py sources coverage
python run.py sources coverage --benchmark trackr
```

Coverage reports organisation totals, sector counts, adapters, scan tiers, provenance, enabled-source resolution, and live operational health separately. The Trackr-derived external links in `trackr_official_candidates.json` are provenance-labelled. Only safely converted public Greenhouse/Lever/Ashby APIs are enabled automatically; direct job/programme candidates remain disabled until tested.
