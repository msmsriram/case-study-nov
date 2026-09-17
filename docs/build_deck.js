const pptxgen = require('pptxgenjs')
const pres = new pptxgen()
pres.layout = 'LAYOUT_16x9' // 10 x 5.625 in
pres.title = 'CARDIO4Cities City Intelligence'
pres.author = 'Sriram M'

// palette: deep clinical teal dominates, ink navy for bookends, terracotta as the single sharp accent
const C = { ink: '0E2A33', teal: '0B5C6B', tealSoft: 'E2F0F1', tealMid: '7FB3BB', accent: 'B4532A', accentSoft: 'F6E6DF',
  text: '1C2430', muted: '5D6673', line: 'D5DDE0', white: 'FFFFFF', ok: '1D7A46', okSoft: 'E3F3E9', bad: 'B3261E', badSoft: 'FBE6E4', warn: '9A6400', warnSoft: 'FBF0D6' }
const H = 'Cambria', B = 'Calibri'
const T = (slide, text, o) => slide.addText(text, Object.assign({ isTextBox: true, margin: 0, fontFace: B, color: C.text, valign: 'top' }, o))
const card = (slide, x, y, w, h, fill) => slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08, fill: { color: fill || C.white }, line: { color: C.line, width: 0.75 } })
const num = (slide, n, x, y, fill) => {
  slide.addShape(pres.shapes.OVAL, { x, y, w: 0.34, h: 0.34, fill: { color: fill || C.teal }, line: { color: fill || C.teal, width: 0 } })
  T(slide, String(n), { x, y, w: 0.34, h: 0.34, align: 'center', valign: 'middle', fontSize: 11, bold: true, color: C.white })
}
const header = (slide, kicker, title) => {
  slide.background = { color: C.white }
  T(slide, kicker.toUpperCase(), { x: 0.55, y: 0.36, w: 8.9, h: 0.24, fontSize: 10, bold: true, color: C.accent, charSpacing: 3 })
  T(slide, title, { x: 0.55, y: 0.6, w: 8.9, h: 0.62, fontFace: H, fontSize: 25, bold: true, color: C.ink })
}
const foot = (slide, n) => T(slide, `CARDIO4Cities City Intelligence  ·  ${n}`, { x: 0.55, y: 5.27, w: 8.9, h: 0.2, fontSize: 8.5, color: C.muted })

// ---------------------------------------------------------------- 1. title
{
  const s = pres.addSlide(); s.background = { color: C.ink }
  s.addShape(pres.shapes.OVAL, { x: 6.7, y: -1.4, w: 5.2, h: 5.2, fill: { color: C.teal, transparency: 55 }, line: { color: C.teal, width: 0 } })
  s.addShape(pres.shapes.OVAL, { x: 7.9, y: 2.3, w: 3.6, h: 3.6, fill: { color: C.accent, transparency: 70 }, line: { color: C.accent, width: 0 } })
  T(s, 'CASE STUDY  ·  SR. SPECIALIST, DATA SCIENCE & AI', { x: 0.6, y: 0.7, w: 7, h: 0.3, fontSize: 10.5, bold: true, color: C.tealMid, charSpacing: 3 })
  T(s, 'Understand a city before\nyou walk into the room.', { x: 0.6, y: 1.25, w: 7.4, h: 1.7, fontFace: H, fontSize: 38, bold: true, color: C.white })
  T(s, 'An agentic research system for CARDIO4Cities City Leads: live web research, independently fact-checked, every fact traceable to its source, and honest about what is not known.',
    { x: 0.6, y: 3.05, w: 6.3, h: 0.95, fontSize: 14.5, color: 'D4E4E7' })
  T(s, [{ text: 'Live  ', options: { bold: true, color: C.white } }, { text: 'case-study-nov-ft.onrender.com', options: { color: 'D4E4E7', breakLine: true } },
        { text: 'Code  ', options: { bold: true, color: C.white } }, { text: 'github.com/msmsriram/case-study-nov', options: { color: 'D4E4E7' } }],
    { x: 0.6, y: 4.3, w: 6.5, h: 0.6, fontSize: 12.5 })
  T(s, 'Sriram M', { x: 0.6, y: 5.0, w: 4, h: 0.3, fontSize: 12, bold: true, color: C.tealMid })
  s.addNotes('Open with the live URL ready in another tab. One sentence on what it is, then go straight to the problem framing.')
}

// ---------------------------------------------------------------- 2. the problem as I understand it
{
  const s = pres.addSlide(); header(s, 'The problem as I understand it', 'The real risk: saying something wrong in the room')
  T(s, 'A City Lead meets government and healthcare leaders in a city nobody on the team has researched. A fluent model can fail them in three specific ways:',
    { x: 0.55, y: 1.42, w: 8.9, h: 0.5, fontSize: 13.5, color: C.muted })
  const fails = [
    ['National data dressed as local', '“24% of adults have hypertension” is a Kenya figure. Said about Nairobi, it is wrong, and someone in the room will know.'],
    ['Invented specifics', 'A programme that does not exist, a director who never said that, an attitude nobody holds. Plausible, confident, unsourced.'],
    ['Silence about gaps', 'No city-level prevalence exists. If the system hides that, the Lead walks in believing they are briefed.'],
  ]
  fails.forEach((f, i) => {
    const x = 0.55 + i * 3.02
    card(s, x, 2.08, 2.84, 1.82, C.badSoft)
    num(s, i + 1, x + 0.2, 2.26, C.bad)
    T(s, f[0], { x: x + 0.64, y: 2.26, w: 2.05, h: 0.36, fontSize: 13.5, bold: true, color: C.bad, valign: 'middle' })
    T(s, f[1], { x: x + 0.2, y: 2.74, w: 2.46, h: 1.08, fontSize: 11.5, color: C.text })
  })
  card(s, 0.55, 4.1, 8.9, 0.98, C.tealSoft)
  T(s, 'Design goal', { x: 0.8, y: 4.22, w: 1.6, h: 0.26, fontSize: 10.5, bold: true, color: C.teal, charSpacing: 2 })
  T(s, 'Nothing reaches the user unless it traces to a source that permitted reading, a quote that exists in that source, and a second model that failed to disprove it. Absence of evidence is a reported result.',
    { x: 0.8, y: 4.48, w: 8.4, h: 0.55, fontSize: 12.5, color: C.ink })
  foot(s, 2)
  s.addNotes('This framing drives every design decision that follows: gates, not just agents.')
}

// ---------------------------------------------------------------- 3. workflow
{
  const s = pres.addSlide(); header(s, 'Proposed solution', 'One LangGraph workflow, two gates that can say no')
  const nodes = [
    ['plan', 'Research Planner', 'LLM · resolves city, 6 categories, records assumptions', 'agent'],
    ['search', 'Live Search', 'Serper → Tavily → Ollama → DDG, key rotation', 'code'],
    ['crawl_check', 'Crawlability Agent', 'terms · robots.txt (RFC 9309) · probe, before any fetch', 'gate'],
    ['fetch', 'Polite Reader', 'rate-limited, official sources first', 'code'],
    ['extract_claims', 'Claim Extractor', 'fan-out per document · quote must exist in source', 'agent'],
    ['fact_check', 'Independent Fact Checker', 'different model · tries to falsify · corrects geography', 'gate'],
    ['conflicts', 'Conflict Detector', 'flags disagreeing statistics, never picks a winner', 'agent'],
    ['gaps', 'Gap Analyst', 'none · national-only · single-source · outdated', 'code'],
    ['persist', 'Knowledge Builder', 'relational audit trail + vector index', 'store'],
    ['build_graph', 'Graph Builder', 'Graphiti on Neo4j, verified relationships only', 'store'],
  ]
  const col = { agent: [C.tealSoft, C.teal], gate: [C.badSoft, C.bad], code: ['EEF1F3', C.muted], store: [C.okSoft, C.ok] }
  nodes.forEach((n, i) => {
    const c = i % 5, r = Math.floor(i / 5)
    const x = 0.55 + c * 1.8, y = 1.5 + r * 1.62, [fill, ink] = col[n[3]]
    card(s, x, y, 1.66, 1.4, fill)
    T(s, n[0], { x: x + 0.12, y: y + 0.1, w: 1.42, h: 0.2, fontSize: 8.5, fontFace: 'Courier New', color: ink })
    T(s, n[1], { x: x + 0.12, y: y + 0.32, w: 1.42, h: 0.42, fontSize: 11.5, bold: true, color: C.ink })
    T(s, n[2], { x: x + 0.12, y: y + 0.76, w: 1.42, h: 0.6, fontSize: 9, color: C.text })
    if (c < 4) T(s, '›', { x: x + 1.66, y: y + 0.45, w: 0.14, h: 0.4, fontSize: 16, bold: true, color: C.tealMid, align: 'center' })
  })
  const legend = [['LLM agent', 'agent'], ['Gate: can stop a claim or a source', 'gate'], ['Deterministic code', 'code'], ['Datastore writer', 'store']]
  let lx = 0.55
  legend.forEach(l => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: lx, y: 4.8, w: 0.2, h: 0.2, rectRadius: 0.04, fill: { color: col[l[1]][0] }, line: { color: col[l[1]][1], width: 0.75 } })
    T(s, l[0], { x: lx + 0.27, y: 4.78, w: 2.6, h: 0.24, fontSize: 10, color: C.muted, valign: 'middle' })
    lx += l[0].length * 0.072 + 0.62
  })
  T(s, 'Not shown: collect_claims, the reduce step after the fan-out.', { x: 4.4, y: 5.27, w: 5.05, h: 0.2, fontSize: 8.5, color: C.muted, align: 'right' })
  foot(s, 3)
  s.addNotes('Walk left to right. Stress: crawlability is a node in the graph, not a helper inside fetch. Fan-out uses LangGraph Send with reducers so parallel branches never overwrite each other. Progress streams to the UI over SSE.')
}

// ---------------------------------------------------------------- 4. data architecture
{
  const s = pres.addSlide(); header(s, 'AI & data design', 'Three stores, each answering a different question')
  const stores = [
    ['Relational', 'Postgres (Neon)', 'The system of record', ['Every source with its crawl decision', 'Every claim: quote, verdict, rationale, models, geography', 'Rejected claims too, for audit', 'Gaps, conflicts, conversations'], '“Where did this come from?”'],
    ['Vector', 'Qdrant Cloud', 'Recall by meaning', ['Verified claims only', 'Verbatim source passages, labelled unverified', 'Local ONNX embeddings, no API key', 'Payloads point back to SQL ids'], '“What do we know about this?”'],
    ['Knowledge graph', 'Graphiti on Neo4j', 'Relationships in time', ['Verified relationship claims only; statistics stay out', 'Typed entities, one namespace per city', 'Facts carry valid / invalid dates', 'Rebuildable from SQL'], '“Who runs, funds or partners with what?”'],
  ]
  stores.forEach((st, i) => {
    const x = 0.55 + i * 3.02
    card(s, x, 1.42, 2.84, 2.62, C.white)
    T(s, st[0], { x: x + 0.2, y: 1.54, w: 2.44, h: 0.3, fontFace: H, fontSize: 16, bold: true, color: C.teal })
    T(s, st[1] + '  ·  ' + st[2], { x: x + 0.2, y: 1.86, w: 2.44, h: 0.24, fontSize: 9.5, color: C.muted })
    T(s, st[3].map((t, k) => ({ text: t, options: { bullet: { indent: 11 }, breakLine: k < st[3].length - 1, paraSpaceAfter: 3 } })), { x: x + 0.2, y: 2.18, w: 2.5, h: 1.3, fontSize: 10.5, color: C.text })
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + 0.2, y: 3.54, w: 2.44, h: 0.38, rectRadius: 0.06, fill: { color: C.tealSoft }, line: { color: C.tealSoft, width: 0 } })
    T(s, st[4], { x: x + 0.2, y: 3.54, w: 2.44, h: 0.38, fontSize: 10.5, italic: true, color: C.ink, align: 'center', valign: 'middle' })
  })
  card(s, 0.55, 4.2, 8.9, 0.9, C.ink)
  T(s, 'AT QUESTION TIME', { x: 0.8, y: 4.31, w: 2, h: 0.22, fontSize: 9.5, bold: true, color: C.tealMid, charSpacing: 2 })
  T(s, 'follow-up resolved from conversation memory  →  Graphiti + Qdrant searched in parallel, SQL adds verdict and geography  →  numbered evidence  →  answer where every sentence cites it. “Ask” stays locked until the graph is ready, so no answer can bypass it.',
    { x: 0.8, y: 4.53, w: 8.4, h: 0.52, fontSize: 10.5, color: C.white })
  foot(s, 4)
  s.addNotes('Be ready to justify exclusions: statistics are poor graph entities; rejected claims never reach vector or graph; nothing lives only in the vector store. Provenance chain: edge -> episode -> claims -> quote -> source -> crawl decision.')
}

// ---------------------------------------------------------------- 5. trust & evidence
{
  const s = pres.addSlide(); header(s, 'Trust & evidence', 'Five checks between a web page and the City Lead')
  const steps = [
    ['May we read it?', 'Crawlability gate. We identify honestly as a bot and accept refusals.', 'code'],
    ['Is the quote real?', 'Code searches the source text for the verbatim quote. No match, no claim.', 'code'],
    ['Can it be disproved?', 'A different model sees only claim + excerpt and tries to falsify it.', 'model'],
    ['Where is it true?', 'City, regional, national or global. The checker corrects the extractor.', 'model'],
    ['Is the answer grounded?', 'Citations validated; confidence capped by the evidence actually cited.', 'code'],
  ]
  steps.forEach((st, i) => {
    const y = 1.42 + i * 0.7
    num(s, i + 1, 0.55, y + 0.06, st[2] === 'code' ? C.teal : C.accent)
    T(s, st[0], { x: 1.02, y: y, w: 3.9, h: 0.26, fontSize: 13, bold: true, color: C.ink })
    T(s, st[1], { x: 1.02, y: y + 0.27, w: 3.95, h: 0.36, fontSize: 10.5, color: C.muted })
  })
  T(s, [{ text: '●', options: { color: C.teal } }, { text: ' enforced in code    ', options: { color: C.muted } }, { text: '●', options: { color: C.accent } }, { text: ' judged by an independent model', options: { color: C.muted } }],
    { x: 1.02, y: 4.95, w: 4, h: 0.22, fontSize: 9.5 })
  // measured column
  card(s, 5.3, 1.42, 4.15, 3.72, C.tealSoft)
  T(s, 'MEASURED ON A LIVE RUN  ·  NAIROBI', { x: 5.52, y: 1.56, w: 3.8, h: 0.22, fontSize: 9.5, bold: true, color: C.teal, charSpacing: 2 })
  const stats = [['69', 'sources found'], ['32', 'refused us or unreachable: kept on record, never read'], ['80', 'claims with a quote located in the source'], ['3', 'claims dropped: quote not in the source'], ['9', 'rejected by the fact checker'], ['8', 'geography labels corrected'], ['9', 'knowledge gaps reported']]
  stats.forEach((st, i) => {
    const y = 1.9 + i * 0.45
    T(s, st[0], { x: 5.52, y, w: 0.8, h: 0.4, fontFace: H, fontSize: 21, bold: true, color: i === 1 || i === 3 || i === 4 ? C.bad : C.ink, valign: 'middle' })
    T(s, st[1], { x: 6.36, y, w: 2.95, h: 0.4, fontSize: 10.5, color: C.text, valign: 'middle' })
  })
  foot(s, 5)
  s.addNotes('Consequence of the fact checker: unsupported and missing claims are excluded from verified ids, so they never reach vector, graph, answers or the report. Example it caught: "7.4 million coronary deaths in Kenya" was a global figure.')
}

// ---------------------------------------------------------------- 6. user experience
{
  const s = pres.addSlide(); header(s, 'User experience', 'Built around trust signals, not around a chat box')
  const views = [
    ['Live progress', 'Eight plain-language stages stream as they happen, including which sources refused us and why. Findings open before the graph finishes.'],
    ['Overview', '“How local is this evidence?” One bar shows what share is about the city itself. Categories show no evidence, national only, or local.'],
    ['Findings', 'Grouped by geography. Every fact has a verdict badge and “Where did this come from?”: quote, context, source, checker reasoning, models used.'],
    ['Ask the city', 'Conversational, with memory for follow-ups. Clickable citation numbers; evidence panel separates what the answer used from what was merely retrieved.'],
    ['Gaps & sources', '“What we do not know” is a first-class panel. Official sources we could not read are listed so the Lead can open them.'],
    ['Briefing', 'Downloadable report. Body assembled from verified claims, never free-written. Only the summary is model-written, and it is citation-checked.'],
  ]
  views.forEach((v, i) => {
    const c = i % 3, r = Math.floor(i / 3), x = 0.55 + c * 3.02, y = 1.42 + r * 1.84
    card(s, x, y, 2.84, 1.66, r === 0 ? C.white : C.tealSoft)
    num(s, i + 1, x + 0.18, y + 0.16)
    T(s, v[0], { x: x + 0.62, y: y + 0.16, w: 2.1, h: 0.34, fontSize: 13, bold: true, color: C.ink, valign: 'middle' })
    T(s, v[1], { x: x + 0.18, y: y + 0.6, w: 2.5, h: 1.0, fontSize: 10.2, color: C.text })
  })
  foot(s, 6)
  s.addNotes('Demo order: research a city they name, open findings while the graph builds, show a national-only category, open one "where did this come from", then Ask with a follow-up, then a question it should refuse.')
}

// ---------------------------------------------------------------- 7. trade-offs
{
  const s = pres.addSlide(); header(s, 'Trade-offs & limitations', 'What I chose, what it cost, and what I cut')
  const rows = [
    ['Honest bot identity', 'We do not spoof a browser. About a third of discovered URLs refuse us, including some government portals.'],
    ['Strict robots.txt (RFC 9309)', 'Unreachable robots.txt means deny. Conservative by default, configurable.'],
    ['Zero-cost models', 'Free tiers do not enforce JSON schemas: schema-in-prompt, validation, one repair, provider fallback. Graph entities carry types but no attributes.'],
    ['Graph build is slow', 'About 20 s per episode, so it runs after findings are available and ingests relationship claims only.'],
    ['Deterministic report body', 'Less fluent than a generated narrative, but it cannot contain an unsourced sentence.'],
  ]
  rows.forEach((r, i) => {
    const y = 1.42 + i * 0.56
    T(s, r[0], { x: 0.55, y, w: 2.35, h: 0.5, fontSize: 11.5, bold: true, color: C.teal })
    T(s, r[1], { x: 2.95, y, w: 6.5, h: 0.5, fontSize: 10.8, color: C.text })
  })
  card(s, 0.55, 4.28, 4.35, 0.86, C.accentSoft)
  T(s, 'CUT FOR THE TIMEBOX', { x: 0.75, y: 4.36, w: 3.9, h: 0.2, fontSize: 9.5, bold: true, color: C.accent, charSpacing: 2 })
  T(s, 'JavaScript rendering · human review queue at the fact-check gate · re-planning from gaps · authentication · scheduled re-research · labelled evaluation set',
    { x: 0.75, y: 4.58, w: 4.0, h: 0.52, fontSize: 10, color: C.text })
  card(s, 5.1, 4.28, 4.35, 0.86, C.tealSoft)
  T(s, 'MEASURED', { x: 5.3, y: 4.36, w: 3.9, h: 0.2, fontSize: 9.5, bold: true, color: C.teal, charSpacing: 2 })
  T(s, 'Research about 2 to 3 min · graph about 5 min in the background · answers 3 to 8 s · 3 unseen cities run end to end on the deployed system',
    { x: 5.3, y: 4.58, w: 4.0, h: 0.52, fontSize: 10, color: C.text })
  foot(s, 7)
  s.addNotes('Each cut is a bounded addition to the existing structure, not a redesign. Human review attaches at the fact-check gate; the audit trail already holds what a reviewer needs.')
}

// ---------------------------------------------------------------- 8. demo
{
  const s = pres.addSlide(); s.background = { color: C.ink }
  s.addShape(pres.shapes.OVAL, { x: 6.9, y: 1.9, w: 4.8, h: 4.8, fill: { color: C.teal, transparency: 60 }, line: { color: C.teal, width: 0 } })
  s.addShape(pres.shapes.OVAL, { x: 8.4, y: -0.9, w: 2.6, h: 2.6, fill: { color: C.accent, transparency: 72 }, line: { color: C.accent, width: 0 } })
  T(s, 'DEMONSTRATION', { x: 0.6, y: 0.7, w: 6, h: 0.3, fontSize: 10.5, bold: true, color: C.tealMid, charSpacing: 3 })
  T(s, 'Name a city.', { x: 0.6, y: 1.15, w: 8.8, h: 0.9, fontFace: H, fontSize: 40, bold: true, color: C.white })
  const d = ['Research it live, and watch which sources refuse us', 'Open a fact and trace it to its quote and source', 'Find a category where only national data exists', 'Ask a question, then a follow-up that needs memory', 'Ask something it should not answer, and see it decline']
  d.forEach((t, i) => {
    num(s, i + 1, 0.6, 2.32 + i * 0.5, C.accent)
    T(s, t, { x: 1.1, y: 2.32 + i * 0.5, w: 8, h: 0.34, fontSize: 14.5, color: 'E6EFF1', valign: 'middle' })
  })
  T(s, 'case-study-nov-ft.onrender.com', { x: 0.6, y: 5.0, w: 8.8, h: 0.3, fontSize: 13, bold: true, color: C.tealMid })
  s.addNotes('Hand over to the live demo.')
}

pres.writeFile({ fileName: process.argv[2] || 'deck.pptx' }).then(f => console.log('wrote', f))
