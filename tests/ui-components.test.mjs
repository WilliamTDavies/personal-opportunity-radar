import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import test from "node:test"

const component = await readFile("components/radar/opportunity-radar.tsx", "utf8")

test("dashboard exposes all streams and local application tracking", () => {
  for (const label of ["Actionable now", "Spring & insight", "Research", "Competitions & development", "Internships", "Saved & applications"]) {
    assert.match(component, new RegExp(label.replace(/[&]/g, "\\&")))
  }
  for (const feature of ["localStorage", "Export JSON", "Export all dates", "Personal due date"]) assert.match(component, new RegExp(feature))
})

test("home aggregates required action lenses", () => {
  for (const label of ["Interest open", "Opening soon", "Closing soon", "New since visit", "Recently changed", "A priority", "Needs action"]) {
    assert.match(component, new RegExp(label))
  }
})

test("v3 local state supports alias and status migration with versioned backups", () => {
  assert.match(component, /alias_map/)
  assert.match(component, /schema_version: 3/)
  assert.match(component, /V2_STORAGE_KEY/)
  assert.match(component, /LEGACY_STORAGE_KEY/)
  for (const status of ["Planning", "In progress", "Submitted / entered / contacted", "Selection stage", "Accepted / finalist", "Completed", "Unsuccessful", "Not pursuing"]) assert.match(component, new RegExp(status))
  assert.match(component, /LAST_VISIT_KEY/)
})

test("actions use the extracted application route and expose discovery provenance", () => {
  assert.match(component, /application_url \|\| opportunity\.source_url/)
  assert.match(component, /application_url \|\| selected\.source_url/)
  assert.match(component, /Discovery provenance/)
  assert.match(component, /source_family/)
})

test("dashboard uses transparent tiers and separate filters", () => {
  assert.match(component, /Filter by lifecycle/)
  assert.match(component, /Filter by eligibility/)
  assert.match(component, /Filter by priority/)
  assert.match(component, /Priority \{opportunity\.priority_tier\}/)
  assert.doesNotMatch(component, /relevance_score/)
})
