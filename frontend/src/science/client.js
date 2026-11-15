// Client for the five-target science bridge (backend science/demo_targets.py, mounted at /api/demo).

async function get(url, opts) {
  const r = await fetch(url, opts)
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`)
  return body
}

export const demoTargets = (signal) => get('/api/demo/targets', { signal })
export const demoMatch = (ra, dec, radius, signal) => get(`/api/demo/match?ra=${ra}&dec=${dec}&radius=${Math.min(90, Math.max(1e-4, radius))}`, { signal })
export const startAnalysis = (id, refresh = false) => get(`/api/demo/targets/${id}/analysis${refresh ? '?refresh=1' : ''}`, { method: 'POST' })
export const analysis = (id, signal) => get(`/api/demo/targets/${id}/analysis`, { signal })

/** Successful per-frame photometry; observations.used counts attempted products. */
export const measuredFrames = (bundle) => (bundle?.spectrophotometry?.points?.length ?? 0) + (bundle?.exploratory?.points?.length ?? 0)

/** Start (or reuse) the analysis for a target and resolve with the bundle; onTick({elapsed}) while it runs. */
export async function runAnalysis(id, { refresh = false, signal, onTick } = {}) {
  await startAnalysis(id, refresh)
  for (;;) {
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
    const r = await analysis(id, signal)
    if (r.state === 'done') return r.bundle
    if (r.state === 'failed') throw new Error(r.reason || 'Analysis failed')
    if (r.state === 'idle') await startAnalysis(id, refresh)
    onTick?.(r)
    await new Promise((res) => setTimeout(res, 1500))
  }
}

/** Shift-capture star cap: proportional to the frame's area (CSS px²), 1 star per 2,500 px², between 2 and 250. */
export const starCap = (wPx, hPx) => Math.max(2, Math.min(250, Math.round((Math.abs(wPx) * Math.abs(hPx)) / 2500)))
