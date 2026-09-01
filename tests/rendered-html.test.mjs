import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import test from "node:test"

test("static export contains an accessible dashboard entry point", async () => {
  const html = await readFile("dist/index.html", "utf8")
  assert.match(html, /Personal Opportunity Radar/)
  assert.match(html, /viewport/)
  assert.match(html, /<div id="root">/)
  assert.match(html, /requires JavaScript/)
  assert.doesNotMatch(html, /Supabase|subscriber|sign in/i)
})

test("the generated canonical artifact is copied exactly", async () => {
  const source = JSON.parse(await readFile("data/opportunities.json", "utf8"))
  const exported = JSON.parse(await readFile("dist/data/opportunities.json", "utf8"))
  assert.deepEqual(exported, source)
  assert.equal(source.schema_version, 2)
  assert.ok(source.opportunities.length > 0)
  assert.ok(source.opportunities.every((item) => item.source_id && item.priority_tier))
})

test("production bundle has no Cloudflare or server runtime", async () => {
  const html = await readFile("dist/index.html", "utf8")
  assert.doesNotMatch(html, /_next|vinext|cloudflare|worker/i)
})
