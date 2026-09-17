const configured = import.meta.env.VITE_API_URL as string | undefined
// dev: local API; production: explicit VITE_API_URL, or same origin when the API serves this bundle
export const API = configured ? configured.replace(/\/$/, '') : import.meta.env.DEV ? 'http://localhost:8000' : ''

export type GeoLevel = 'city' | 'metro' | 'district' | 'state' | 'national' | 'global' | 'unknown'

export interface CitySummary {
  city_id: string; name: string; country: string; admin_region: string | null
  last_researched_at: string | null; verified_claims: number; graph_status: string | null
}
export interface Gap { category: string; severity: 'low' | 'medium' | 'high'; description: string; suggestion: string | null }
export interface CategoryCount { key: string; name: string; why: string; verified: number; city_level: number }
export interface Overview {
  city: { city_id: string; name: string; country: string; admin_region: string | null; aliases: string[]; population_hint: string | null; last_researched_at: string }
  run: { run_id: string; graph_status: string; finished_at: string; stats: Record<string, unknown>; assumptions: string[] } | null
  categories: CategoryCount[]
  counts: {
    verified: number; partially_supported: number; not_accepted: Record<string, number>; by_geo: Record<string, number>
    in_conflict: number; sources: number; sources_read: number; sources_denied: number
    denied_reasons: Record<string, number>; sources_by_tier: Record<string, number>; gaps: number; conflicts: number
  }
  gaps: Gap[]
  conflicts: { claim_a: string; claim_b: string; description: string }[]
  stores: { relational: string; vector: string; graph: string }
}
export interface SourceRef {
  id?: string; url: string; title?: string; publisher?: string | null; tier?: string
  published_date?: string | null; retrieved_at?: string | null; robots_status?: string | null
}
export interface Claim {
  id: string; category: string; claim_type: string; statement: string; quote: string; evidence_window: string
  quote_verified: boolean; geo_level: GeoLevel; extractor_geo_level: GeoLevel; geo_mismatch: boolean; year: number | null
  entities: string[]; status: string; verdict: string | null; verdict_rationale: string | null
  checker_confidence: number | null; extractor_model: string | null; checker_model: string | null
  in_conflict: boolean; in_graph: boolean; source: SourceRef
}
export interface Source {
  id: string; url: string; title: string; publisher: string | null; tier: string; provider: string
  published_date: string | null; snippet: string; queries: string[]; crawl_allowed: boolean; crawl_reason: string
  robots_status: string | null; fetch_status: string | null; extraction_quality: string | null
  word_count: number; retrieved_at: string | null; verified_claims: number
}
export interface GraphData {
  nodes: { id: string; name: string; type: string }[]
  edges: { source: string; target: string; relation: string; fact: string; invalidated: boolean }[]
}
export interface Evidence {
  n: number; kind: 'graph_fact' | 'claim' | 'passage'; text: string; quote: string | null; relation?: string
  geo_level: GeoLevel | null; verdict: string; verdict_rationale?: string; year: number | string | null
  source_urls: string[]; source_title: string | null; source_tier: string | null; backing_claim_ids?: string[]
  in_conflict?: boolean; published_date?: string | null
}
export interface Answer {
  question: string; answer: string; confidence: 'high' | 'medium' | 'low'; caveats: string[]
  insufficient_evidence: boolean; cited: number[]; stores_used_in_answer: string[]; evidence: Evidence[]; gaps: Gap[]
  retrieval: { graph_facts: number; claims: number; passages: number }; timings: Record<string, number>
}
export interface RunEvent {
  t: number; type: 'progress' | 'node_done' | 'complete' | 'failed'; stage?: string; message?: string; node?: string
  city_id?: string; findings_ready?: boolean; graph_ready?: boolean; denied_reasons?: Record<string, number>
  stats?: Record<string, unknown>; errors?: string[]
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API + path, init)
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail ?? detail } catch { /* not json */ }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

export const api = {
  cities: () => j<CitySummary[]>('/api/cities'),
  overview: (id: string) => j<Overview>(`/api/cities/${id}`),
  claims: (id: string, status = 'verified') => j<Claim[]>(`/api/cities/${id}/claims?status=${status}`),
  sources: (id: string) => j<Source[]>(`/api/cities/${id}/sources`),
  graph: (id: string) => j<GraphData>(`/api/cities/${id}/graph`),
  ask: (id: string, question: string) =>
    j<Answer>(`/api/cities/${id}/ask`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) }),
  research: (city: string) =>
    j<{ run_id: string; city: string }>('/api/research', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ city }) }),
  reportUrl: (id: string, format: 'md' | 'html') => `${API}/api/cities/${id}/report?format=${format}`,
  eventsUrl: (runId: string) => `${API}/api/runs/${runId}/events`,
}

export const GEO_LABEL: Record<string, string> = {
  city: 'City-level', metro: 'Metro-level', district: 'County / district', state: 'State-level',
  national: 'National, not city-specific', global: 'Global, not city-specific', unknown: 'Geography unclear',
}
export const isLocal = (g: string | null) => g === 'city' || g === 'metro' || g === 'district'
export const TIER_LABEL: Record<string, string> = {
  government: 'Government', intergovernmental: 'Intergovernmental', academic: 'Academic', ngo: 'NGO',
  reference: 'Reference', news: 'News', commercial: 'Commercial', other: 'Other',
}
export const fmtDate = (s?: string | null) => (s ? new Date(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : 'unknown')
