# Continuation notes

## Routine refresh

1. Run `python run.py scan --tier high --tier daily --allow-partial` (add `--tier weekly` for a full refresh).
2. Read `data/coverage_report.json`, `data/review_queue.json`, and changed canonical facts.
3. Resolve genuine parser suggestions with a source template or a narrow override; never promote generic navigation links.
4. Run `python run.py sources validate`, `python run.py sources coverage --benchmark trackr`, `python run.py audit-sources`, and `npm test`.
5. Commit source/config changes and all generated state together.

## Adding coverage

- Ordinary additions use `python run.py sources add --id example --name "Example" --url "https://example.com/careers/students"`, followed by `python run.py sources test example`.
- Bulk additions use `python run.py sources import companies.csv --category technology`; duplicate rows are reported, not silently overwritten.
- Prefer an official listing/API endpoint and the appropriate adapter.
- Set narrow include/exclude patterns and expected parse terms.
- Use `page_is_listing: false` for a single programme page so navigation links are not candidates.
- Use programme templates only when their `match_terms` are official facts present on the fetched page.
- Keep manual additions exceptional and evidence-backed.
- Preserve `canonical_id`; add prior IDs to aliases when identity changes.

## Review priorities

- Recheck Macquarie against the current UK FAQ if its 12 October–30 November 2026 window or 2029 graduation rule changes; older/non-UK pages are retained only as conflict context.
- Replace the Durham BSI evergreen lead only when a dated 2027 call is official.
- Recheck first-year access when the Fields 2027 application details open.
- Treat blocked/client-rendered pages as degraded or failed; do not call them healthy from HTTP status alone.
- A real scan may contain zero current first-year internships. Do not add filler.

## Release checklist

```console
python run.py scan --allow-partial
python run.py validate
python run.py audit-sources
python run.py sources validate
python run.py sources coverage --benchmark trackr
npm test
```

Then inspect the built `dist` site at desktop and mobile widths, test filters, local tracking, backup/restore, alias migration, and multi-date calendar export before deployment.
