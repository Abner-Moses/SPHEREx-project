// Talks only to our own backend (POST /api/voyager). The model key lives on the server.

const ENDPOINT = import.meta.env.VITE_VOYAGER_URL || '/api/voyager'

/** -> { ok: true, say, move, point, face } | { ok: false, status, detail } */
export async function askVoyager(body, signal) {
  try {
    const r = await fetch(ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
    if (r.ok) return { ok: true, ...(await r.json()) }
    let detail = ''
    try { detail = (await r.json()).detail ?? '' } catch { /* not JSON */ }
    return { ok: false, status: r.status, detail: typeof detail === 'string' ? detail : '' }
  } catch (e) {
    if (e.name === 'AbortError') throw e
    return { ok: false, status: 0, detail: '' }
  }
}

/** What Voyager says when it can't reach the model. Plain, in its voice, and honest about why. */
export function offlineLine(status, detail) {
  if (status === 503) return "I can't answer that properly yet. My link to the model isn't set up: the server needs a DEEPSEEK_API_KEY."
  if (status === 0 || status === 404 || status === 405) return "The archive server is offline, and my answers go through it. Start the backend and ask me again."
  return detail || "Something went wrong between me and the model. Try that again in a moment."
}
