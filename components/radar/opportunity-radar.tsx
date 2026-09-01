"use client"

import {
  ArrowUpRight, Bookmark, CalendarClock, Check, ChevronRight, CircleAlert,
  Clock3, Download, ExternalLink, FileJson, Filter, MapPin, Search,
  ShieldCheck, Sparkles, Upload,
} from "lucide-react"
import { ChangeEvent, useEffect, useRef, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"

type Stream = "spring_insight" | "research" | "competitions_development" | "internships"
type Lifecycle = "open" | "interest_open" | "officially_announced" | "unknown" | "closed" | "stale"
type Eligibility = "eligible" | "likely_eligible" | "uncertain" | "ineligible"
type Evidence = { statement: string; source_url: string; checked_at: string; source_type: string }
type RuleEvaluation = { rule: string; strength: string; outcome: string; reason: string }
type Opportunity = {
  canonical_id: string; title: string; organisation: string; stream: Stream;
  lifecycle: Lifecycle; eligibility: Eligibility; primary_action: string;
  source_url: string; location: string; start_date: string | null;
  end_date: string | null; deadline: string | null; deadline_status: "fixed" | "rolling" | "unknown" | "none_stated";
  deadline_verified: boolean; rolling: boolean; opens_at: string | null; research_mode: string | null;
  summary: string; why_it_fits: string; eligibility_note: string; next_step: string;
  tags: string[]; aliases: string[]; merged_alias_ids: string[]; evidence: Evidence[]; checked_at: string;
  first_seen: string; last_changed: string; priority_tier: "A" | "B" | "C";
  rule_evaluations: RuleEvaluation[]; change_summary: string[]; source_conflict: boolean; review_reasons: string[];
  application_url?: string | null; discovered_via?: string; primary_evidence_url?: string;
  last_verified?: string; source_family?: string; template_dependent?: boolean; auto_publish_reason?: string;
}
export type RadarData = {
  schema_version: number; generated_at: string; alias_map: Record<string, string>;
  opportunities: Opportunity[];
}
type PursuitStatus = "saved" | "planning" | "in_progress" | "submitted" | "selection_stage" | "accepted" | "completed" | "unsuccessful" | "not_pursuing"
type Pursuit = { status: PursuitStatus; notes: string; nextAction: string; dueDate: string; updatedAt: string }

const STORAGE_KEY = "personal-opportunity-radar:pursuits:v3"
const V2_STORAGE_KEY = "personal-opportunity-radar:pursuits:v2"
const LEGACY_STORAGE_KEY = "personal-opportunity-radar:pursuits:v1"
const LAST_VISIT_KEY = "personal-opportunity-radar:last-visit"
const TAB_LABELS: Record<string, string> = {
  home: "Actionable now", spring_insight: "Spring & insight", research: "Research",
  competitions_development: "Competitions & development", internships: "Internships", saved: "Saved & applications",
}
const LIFECYCLE_LABELS: Record<Lifecycle, string> = {
  open: "Open", interest_open: "Interest open", officially_announced: "Officially announced", unknown: "Not yet verified open", closed: "Closed", stale: "Stale",
}
const ELIGIBILITY_LABELS: Record<Eligibility, string> = {
  eligible: "Eligible", likely_eligible: "Likely eligible", uncertain: "Needs eligibility check", ineligible: "Ineligible",
}
const STATUS_LABELS: Record<PursuitStatus, string> = {
  saved: "Saved", planning: "Planning", in_progress: "In progress", submitted: "Submitted / entered / contacted",
  selection_stage: "Selection stage", accepted: "Accepted / finalist", completed: "Completed",
  unsuccessful: "Unsuccessful", not_pursuing: "Not pursuing",
}
const LEGACY_STATUS: Record<string, PursuitStatus> = {
  saved: "saved", preparing: "planning", applied: "submitted", interview: "selection_stage",
  offer: "accepted", declined: "unsuccessful", archived: "not_pursuing",
}

function parseDate(value: string | null): Date | null {
  if (!value || !/^\d{4}-\d{2}(-\d{2})?/.test(value)) return null
  const parsed = new Date(value.length === 7 ? `${value}-01T12:00:00Z` : value)
  return Number.isNaN(parsed.valueOf()) ? null : parsed
}
function formatDate(value: string | null, fallback = "Not stated") {
  if (!value) return fallback
  if (value.endsWith("-summer")) return `Summer ${value.slice(0, 4)}`
  if (value.endsWith("-spring")) return `Spring ${value.slice(0, 4)}`
  if (value === "flexible") return "Flexible"
  const parsed = parseDate(value)
  if (!parsed) return value
  return new Intl.DateTimeFormat("en-GB", {
    day: /^\d{4}-\d{2}-\d{2}/.test(value) ? "numeric" : undefined, month: "short", year: "numeric", timeZone: "UTC",
  }).format(parsed)
}
function daysUntil(value: string | null, reference: string) {
  const parsed = parseDate(value); const referenceDate = parseDate(reference)
  return parsed && referenceDate ? Math.ceil((parsed.valueOf() - referenceDate.valueOf()) / 86_400_000) : null
}
function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url)
}
function calendarContent(opportunity: Opportunity, pursuit?: Pursuit) {
  const dates = [
    ["Personal action", pursuit?.dueDate || null], ["Application deadline", opportunity.deadline],
    ["Applications open", opportunity.opens_at], ["Programme starts", opportunity.start_date],
    ["Programme ends", opportunity.end_date],
  ] as Array<[string, string | null]>
  const events = dates.flatMap(([label, value], index) => {
    const date = parseDate(value); if (!date) return []
    const day = date.toISOString().slice(0, 10).replaceAll("-", "")
    const next = new Date(date.valueOf() + 86_400_000).toISOString().slice(0, 10).replaceAll("-", "")
    const clean = (text: string) => text.replace(/[;,\\]/g, (part) => `\\${part}`).replace(/\n/g, "\\n")
    return ["BEGIN:VEVENT", `UID:${opportunity.canonical_id}-${index}@personal-opportunity-radar`,
      `DTSTART;VALUE=DATE:${day}`, `DTEND;VALUE=DATE:${next}`,
      `SUMMARY:${clean(`${label}: ${opportunity.title}`)}`,
      `DESCRIPTION:${clean(pursuit?.nextAction || opportunity.next_step)}`, `URL:${opportunity.application_url || opportunity.source_url}`, "END:VEVENT"]
  })
  if (!events.length) return null
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "")
  return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Personal Opportunity Radar//EN", `DTSTAMP:${stamp}`, ...events, "END:VCALENDAR"].join("\r\n")
}

export default function OpportunityRadar({ data }: { data: RadarData }) {
  const [tab, setTab] = useState("home")
  const [homeLens, setHomeLens] = useState("actionable")
  const [query, setQuery] = useState("")
  const [lifecycle, setLifecycle] = useState("all")
  const [eligibility, setEligibility] = useState("all")
  const [priority, setPriority] = useState("all")
  const [focus, setFocus] = useState("all")
  const [sort, setSort] = useState("priority")
  const [pursuits, setPursuits] = useState<Record<string, Pursuit>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [notice, setNotice] = useState("")
  const [lastVisit, setLastVisit] = useState<string | null>(null)
  const importRef = useRef<HTMLInputElement>(null)

  /* eslint-disable react-hooks/set-state-in-effect -- browser-only state is hydrated after SSR to avoid a hydration mismatch. */
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(V2_STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY)
      if (saved) {
        const parsed = JSON.parse(saved) as Record<string, Pursuit>
        const migrated = Object.fromEntries(Object.entries(parsed).map(([id, pursuit]) => [data.alias_map[id] || id, {
          ...pursuit, status: LEGACY_STATUS[pursuit.status] || pursuit.status,
        }]))
        setPursuits(migrated); localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated))
      }
      const previousVisit = localStorage.getItem(LAST_VISIT_KEY)
      setLastVisit(previousVisit)
      localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString())
    }
    catch { setNotice("A previous local tracker file could not be read. No opportunity data was changed.") }
  }, [data.alias_map])
  /* eslint-enable react-hooks/set-state-in-effect */
  const savePursuits = (next: Record<string, Pursuit>) => { setPursuits(next); localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) }
  const selected = data.opportunities.find((item) => item.canonical_id === selectedId) ?? null
  const actionable = data.opportunities.filter((item) => ["open", "interest_open", "officially_announced"].includes(item.lifecycle))
  const closingSoon = actionable.filter((item) => { const days = daysUntil(item.deadline, data.generated_at); return days !== null && days >= 0 && days <= 45 })
  const openingSoon = data.opportunities.filter((item) => { const days = daysUntil(item.opens_at, data.generated_at); return days !== null && days >= 0 && days <= 45 })
  const newSinceVisit = data.opportunities.filter((item) => Boolean(lastVisit && item.first_seen && item.first_seen > lastVisit))
  const recentlyChanged = data.opportunities.filter((item) => { const days = daysUntil(item.last_changed, data.generated_at); return days !== null && days >= -14 && days <= 0 })
  const priorityA = data.opportunities.filter((item) => item.priority_tier === "A")
  const needsAction = data.opportunities.filter((item) => {
    const pursuit = pursuits[item.canonical_id]; if (!pursuit) return false
    const days = daysUntil(pursuit.dueDate || item.deadline, data.generated_at)
    return days !== null && days >= 0 && days <= 7
  })
  const homeSets: Record<string, Opportunity[]> = { actionable, open: data.opportunities.filter((item) => item.lifecycle === "open"),
    interest: data.opportunities.filter((item) => item.lifecycle === "interest_open"), opening: openingSoon,
    closing: closingSoon, new: newSinceVisit, changed: recentlyChanged, priority: priorityA, needsAction }

  const filtered = (() => {
    const records = data.opportunities.filter((item) => {
      if (tab === "home" && !homeSets[homeLens].some((record) => record.canonical_id === item.canonical_id)) return false
      if (tab === "saved" && !pursuits[item.canonical_id]) return false
      if (!["home", "saved"].includes(tab) && item.stream !== tab) return false
      if (lifecycle !== "all" && item.lifecycle !== lifecycle) return false
      if (eligibility !== "all" && item.eligibility !== eligibility) return false
      if (priority !== "all" && item.priority_tier !== priority) return false
      if (focus !== "all" && ![item.title, item.summary, item.location, ...item.tags].join(" ").toLowerCase().includes(focus)) return false
      return [item.title, item.organisation, item.summary, item.location, ...item.tags].join(" ").toLowerCase().includes(query.trim().toLowerCase())
    })
    const tiers = { A: 0, B: 1, C: 2 }
    return records.sort((a, b) => sort === "deadline" ? (a.deadline || "9999").localeCompare(b.deadline || "9999") : sort === "organisation" ? a.organisation.localeCompare(b.organisation) : tiers[a.priority_tier] - tiers[b.priority_tier])
  })()

  const ensurePursuit = (id: string) => {
    if (pursuits[id]) return
    savePursuits({ ...pursuits, [id]: { status: "saved", notes: "", nextAction: "", dueDate: "", updatedAt: new Date().toISOString() } })
    setNotice("Saved locally in this browser.")
  }
  const updatePursuit = (id: string, patch: Partial<Pursuit>) => {
    const current = pursuits[id] || { status: "saved", notes: "", nextAction: "", dueDate: "", updatedAt: "" }
    savePursuits({ ...pursuits, [id]: { ...current, ...patch, updatedAt: new Date().toISOString() } })
  }
  const exportBackup = () => download("opportunity-radar-backup.json", JSON.stringify({ schema_version: 3, exported_at: new Date().toISOString(), data_schema_version: data.schema_version, pursuits }, null, 2), "application/json")
  const importBackup = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return
    try {
      const payload = JSON.parse(await file.text()) as { schema_version?: number; pursuits?: Record<string, Pursuit> }
      if (![1, 2, 3].includes(payload.schema_version || 0) || !payload.pursuits || typeof payload.pursuits !== "object") throw new Error("Invalid backup")
      const allowed = new Set(data.opportunities.map((item) => item.canonical_id))
      const migratedEntries: Array<[string, Pursuit]> = Object.entries(payload.pursuits).map(
        ([id, pursuit]) => [data.alias_map[id] || id, { ...pursuit, status: LEGACY_STATUS[pursuit.status] || pursuit.status }],
      )
      const restored = Object.fromEntries(migratedEntries.filter(([id]) => allowed.has(id)))
      savePursuits(restored); setNotice(`Restored ${Object.keys(restored).length} local tracker records.`)
    } catch { setNotice("That file is not a valid Opportunity Radar backup.") }
    finally { event.target.value = "" }
  }

  return (
    <div className="radar-shell">
      <header className="site-header"><div className="header-inner">
        <a className="brand" href="#top" aria-label="Personal Opportunity Radar home"><span className="brand-mark"><Sparkles size={18} /></span><span><strong>Opportunity Radar</strong><small>Durham · Maths & Physics · 2026–29</small></span></a>
        <div className="header-meta"><span className="scan-status"><span className="status-dot" /> Updated {formatDate(data.generated_at)}</span><Button variant="outline" size="sm" onClick={exportBackup}><Download /> Backup</Button></div>
      </div></header>

      <main id="top">
        <section className="hero-section"><div><Badge className="eyebrow" variant="outline">Personal decision dashboard</Badge><h1>Know what is real.<br />Act on what matters.</h1><p className="hero-copy">A strict radar for verified first-year opportunities—not a list of everything with “student” in the title.</p></div>
          <div className="summary-grid" aria-label="Radar summary"><div className="summary-card accent"><span>Open now</span><strong>{homeSets.open.length}</strong><small>application or entry action live</small></div><div className="summary-card"><span>Interest open</span><strong>{homeSets.interest.length}</strong><small>actionable notification routes</small></div><div className="summary-card"><span>Closing soon</span><strong>{closingSoon.length}</strong><small>within 45 days</small></div><div className="summary-card"><span>Tracking</span><strong>{Object.keys(pursuits).length}</strong><small>{needsAction.length} need action this week</small></div></div>
        </section>

        <section className="workspace">
          <Tabs value={tab} onValueChange={setTab} className="radar-tabs"><div className="tabs-scroll"><TabsList variant="line" className="tab-list">{Object.entries(TAB_LABELS).map(([value, label]) => <TabsTrigger key={value} value={value} className="tab-trigger">{label}{value === "saved" && Object.keys(pursuits).length > 0 ? <span className="tab-count">{Object.keys(pursuits).length}</span> : null}</TabsTrigger>)}</TabsList></div></Tabs>
          {tab === "home" ? <div className="home-lenses" aria-label="Home opportunity views">{[
            ["actionable", "Actionable", actionable.length], ["open", "Open", homeSets.open.length], ["interest", "Interest open", homeSets.interest.length],
            ["opening", "Opening soon", openingSoon.length], ["closing", "Closing soon", closingSoon.length], ["new", "New since visit", newSinceVisit.length],
            ["changed", "Recently changed", recentlyChanged.length], ["priority", "A priority", priorityA.length], ["needsAction", "Needs action", needsAction.length],
          ].map(([value, label, count]) => <button key={value} className={homeLens === value ? "active" : ""} onClick={() => setHomeLens(String(value))}>{label}<span>{count}</span></button>)}</div> : null}
          <div className="toolbar"><label className="search-box"><Search size={17} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search firm, field, skill or location" aria-label="Search opportunities" /></label><div className="filter-group"><Filter size={16} aria-hidden="true" /><select value={lifecycle} onChange={(event) => setLifecycle(event.target.value)} aria-label="Filter by lifecycle"><option value="all">All lifecycle states</option><option value="open">Open</option><option value="interest_open">Interest open</option><option value="officially_announced">Officially announced</option><option value="unknown">Not verified open</option></select><select value={eligibility} onChange={(event) => setEligibility(event.target.value)} aria-label="Filter by eligibility"><option value="all">All eligibility states</option><option value="eligible">Eligible</option><option value="likely_eligible">Likely eligible</option><option value="uncertain">Needs eligibility check</option></select><select value={priority} onChange={(event) => setPriority(event.target.value)} aria-label="Filter by priority"><option value="all">All priorities</option><option value="A">Priority A</option><option value="B">Priority B</option><option value="C">Priority C</option></select><select value={focus} onChange={(event) => setFocus(event.target.value)} aria-label="Filter by focus"><option value="all">All focus areas</option><option value="quant">Quant</option><option value="software">Software</option><option value="finance">Finance</option><option value="research">Research</option><option value="physics">Physics</option><option value="mathematics">Mathematics</option><option value="durham">Durham</option><option value="london">London</option><option value="worldwide">Worldwide</option><option value="first-year">First year</option></select><select value={sort} onChange={(event) => setSort(event.target.value)} aria-label="Sort opportunities"><option value="priority">Priority tier</option><option value="deadline">Deadline</option><option value="organisation">Organisation</option></select></div></div>
          {notice ? <div className="notice" role="status"><Check size={16} />{notice}<button onClick={() => setNotice("")} aria-label="Dismiss notice">×</button></div> : null}
          <div className="results-heading"><div><span className="section-kicker">{TAB_LABELS[tab]}</span><h2>{filtered.length} {filtered.length === 1 ? "opportunity" : "opportunities"}</h2></div>{tab === "saved" ? <div className="backup-actions"><input ref={importRef} type="file" accept="application/json" hidden onChange={importBackup} /><Button variant="outline" size="sm" onClick={() => importRef.current?.click()}><Upload /> Restore backup</Button><Button variant="outline" size="sm" onClick={exportBackup}><FileJson /> Export JSON</Button></div> : null}</div>

          {filtered.length ? <div className="opportunity-list">{filtered.map((opportunity) => {
            const pursuit = pursuits[opportunity.canonical_id]; const days = daysUntil(opportunity.deadline || opportunity.opens_at, data.generated_at)
            return <article className="opportunity-card" key={opportunity.canonical_id}><div className="card-rail" data-stream={opportunity.stream} /><div className="card-main"><div className="card-topline"><div className="badge-row"><span className={`lifecycle lifecycle-${opportunity.lifecycle}`}>{LIFECYCLE_LABELS[opportunity.lifecycle]}</span><span className={`eligibility eligibility-${opportunity.eligibility}`}>{opportunity.eligibility === "uncertain" ? <CircleAlert size={13} /> : <ShieldCheck size={13} />}{ELIGIBILITY_LABELS[opportunity.eligibility]}</span>{pursuit ? <span className="pursuit-chip">{STATUS_LABELS[pursuit.status]}</span> : null}</div><span className={`priority-tier priority-${opportunity.priority_tier}`} title="Rule-based priority tier">Priority {opportunity.priority_tier}</span></div><p className="organisation">{opportunity.organisation}</p><h3>{opportunity.title}</h3><p className="summary">{opportunity.summary}</p><div className="meta-row"><span><MapPin />{opportunity.location}</span><span><CalendarClock />{opportunity.deadline ? `Deadline ${formatDate(opportunity.deadline)}` : opportunity.rolling ? "Rolling deadline — act early" : opportunity.opens_at ? `Opens ${formatDate(opportunity.opens_at)}` : "No verified date"}</span>{days !== null && days >= 0 && days <= 45 ? <strong>{days === 0 ? "Today" : `${days} days`}</strong> : null}</div><div className="tag-row">{opportunity.tags.slice(0, 6).map((tag) => <span key={tag}>{tag}</span>)}</div></div><div className="card-actions"><div className="fit-note"><strong>Why it fits</strong><p>{opportunity.why_it_fits}</p></div><div className="action-row"><Button variant="outline" size="sm" onClick={() => { ensurePursuit(opportunity.canonical_id); setSelectedId(opportunity.canonical_id) }}><Bookmark className={pursuit ? "bookmark-filled" : ""} />{pursuit ? "Update" : "Track"}</Button><Button size="sm" asChild><a href={opportunity.application_url || opportunity.source_url} target="_blank" rel="noreferrer">{opportunity.primary_action}<ArrowUpRight /></a></Button><Button variant="ghost" size="icon-sm" onClick={() => setSelectedId(opportunity.canonical_id)} aria-label={`View details for ${opportunity.title}`}><ChevronRight /></Button></div></div></article>
          })}</div> : <div className="empty-state"><Search /><h3>No matching opportunities</h3><p>Clear a filter or switch streams. The radar does not invent filler records.</p><Button variant="outline" onClick={() => { setQuery(""); setLifecycle("all"); setEligibility("all"); setPriority("all"); setFocus("all") }}>Clear filters</Button></div>}
        </section>

        <section className="method-strip"><div><ShieldCheck /><span><strong>Official evidence</strong><small>Every public record has a primary-source statement.</small></span></div><div><CircleAlert /><span><strong>No silent assumptions</strong><small>Unknown age, grades and residency stay unknown.</small></span></div><div><Clock3 /><span><strong>Lifecycle is literal</strong><small>“Announced” requires an official future opening date.</small></span></div></section>
      </main>
      <footer><span>Personal Opportunity Radar · local-first</span><span>Generated {formatDate(data.generated_at)} · schema v{data.schema_version}</span></footer>

      <Sheet open={Boolean(selected)} onOpenChange={(open) => { if (!open) setSelectedId(null) }}><SheetContent className="detail-sheet sm:max-w-xl">{selected ? <><SheetHeader className="detail-header"><div className="badge-row"><span className={`lifecycle lifecycle-${selected.lifecycle}`}>{LIFECYCLE_LABELS[selected.lifecycle]}</span><span className={`eligibility eligibility-${selected.eligibility}`}>{ELIGIBILITY_LABELS[selected.eligibility]}</span><span className={`priority-tier priority-${selected.priority_tier}`}>Priority {selected.priority_tier}</span></div><SheetTitle>{selected.title}</SheetTitle><SheetDescription>{selected.organisation} · {selected.location}</SheetDescription></SheetHeader><div className="detail-scroll"><section><h4>What it is</h4><p>{selected.summary}</p></section><section><h4>Why this is on the radar</h4><p>{selected.why_it_fits}</p></section><section className={selected.eligibility === "uncertain" ? "attention-section" : ""}><h4>Eligibility judgement</h4><p>{selected.eligibility_note}</p>{selected.rule_evaluations?.map((rule) => <div className="rule-row" key={rule.rule}><strong>{rule.rule.replaceAll("_", " ")} · {rule.outcome}</strong><span>{rule.reason}</span></div>)}</section><section><h4>Recommended next step</h4><p>{selected.next_step}</p></section>{selected.research_mode ? <section><h4>Research route</h4><p className="mode-label">{selected.research_mode.replaceAll("_", " ")}</p></section> : null}{selected.change_summary?.length ? <section><h4>Recently changed</h4>{selected.change_summary.map((change) => <p className="change-line" key={change}>{change}</p>)}</section> : null}<section><h4>Official evidence</h4>{selected.evidence.map((item) => <div className="evidence-box" key={item.statement}><p>{item.statement}</p><a href={item.source_url} target="_blank" rel="noreferrer">Open source <ExternalLink /></a><small>Checked {formatDate(item.checked_at)}</small></div>)}</section><section><h4>Discovery provenance</h4><p>Source family: {selected.source_family || "official source"}. Discovered via <a href={selected.discovered_via || selected.source_url} target="_blank" rel="noreferrer">the monitored directory</a>{selected.template_dependent ? "; template fallback used" : "; generic detail extraction used"}. Last verified {formatDate(selected.last_verified || selected.checked_at)}.</p></section><section className="tracker-form"><div className="tracker-heading"><h4>Your local tracker</h4><span>Stored only in this browser</span></div><label>Status<select value={pursuits[selected.canonical_id]?.status || "saved"} onChange={(event) => updatePursuit(selected.canonical_id, { status: event.target.value as PursuitStatus })}>{Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label>Next action<Input value={pursuits[selected.canonical_id]?.nextAction || ""} onChange={(event) => updatePursuit(selected.canonical_id, { nextAction: event.target.value })} placeholder="e.g. Finish CV evidence bank" /></label><label>Personal due date<Input type="date" value={pursuits[selected.canonical_id]?.dueDate || ""} onChange={(event) => updatePursuit(selected.canonical_id, { dueDate: event.target.value })} /></label><label>Notes<Textarea value={pursuits[selected.canonical_id]?.notes || ""} onChange={(event) => updatePursuit(selected.canonical_id, { notes: event.target.value })} placeholder="Contacts, application details, interview notes…" rows={4} /></label></section></div><div className="detail-footer"><Button variant="outline" onClick={() => { const content = calendarContent(selected, pursuits[selected.canonical_id]); if (content) download(`${selected.canonical_id}.ics`, content, "text/calendar"); else setNotice("This item has no calendar-ready dates.") }}><CalendarClock />Export all dates</Button><Button asChild><a href={selected.application_url || selected.source_url} target="_blank" rel="noreferrer">{selected.primary_action}<ArrowUpRight /></a></Button></div></> : null}</SheetContent></Sheet>
    </div>
  )
}
