// Plumbing for the AI explainer (planned: DeepSeek V4 Lite behind the backend).
//
// DESIGN NOTES
// * The model API key must live on the server. This file only talks to YOUR backend.
// * Expected backend contract (not implemented yet in main.py):
//     POST {VITE_EXPLAIN_URL || "/api/explain"}
//     body : { level: "explorer"|"astronomer", question: string, context: object }
//     reply: { answer: string }            (plain JSON; add streaming later)
// * `context` is a compact, factual snapshot of what the user is looking at, so the model can
//   ground its answer in real numbers instead of guessing. Build it with buildExplainContext().
// * Until the endpoint exists, ask() resolves with `offline: true` and a locally generated,
//   deterministic summary. It never pretends to be the model.

const ENDPOINT = import.meta.env.VITE_EXPLAIN_URL || '/api/explain'

export function buildExplainContext({ level, view, probe, selectedLandmark, hover }) {
  const context = { level, view }
  if (selectedLandmark) context.landmark = selectedLandmark
  if (hover) context.cursor_sky_position = hover
  if (probe) {
    context.observation = {
      ra_deg: probe.ra,
      dec_deg: probe.dec,
      data_source: probe.source === 'irsa' ? 'live SPHEREx archive image (IRSA)' : probe.source === 'mock' ? 'SIMULATED placeholder image, not archive data' : probe.source,
      status: probe.status,
      image_pixels: probe.image ? `${probe.image.width}x${probe.image.height}` : null,
      pixel_values_are: '8-bit display units after percentile stretch (not physical flux)',
      backend_metadata: probe.meta ?? {},
    }
  }
  return context
}

function localSummary(question, context) {
  const lines = []
  const obs = context.observation
  if (obs) {
    lines.push(`You are looking at ${obs.data_source} for RA ${obs.ra_deg.toFixed(4)}°, Dec ${obs.dec_deg.toFixed(4)}°.`)
    if (obs.data_source.startsWith('SIMULATED')) lines.push('This picture is generated for testing, so it says nothing about the real sky at that spot.')
  } else if (context.landmark) {
    lines.push(`${context.landmark.label} sits at RA ${context.landmark.ra.toFixed(3)}°, Dec ${context.landmark.dec.toFixed(3)}°.`)
  } else {
    lines.push('Pick a landmark or click the map, then press Observe to fetch an image.')
  }
  lines.push(`(Question received: “${question}”. The AI explainer is not connected yet, so this is a built-in note, not an AI answer.)`)
  return lines.join(' ')
}

export async function ask(question, context, signal) {
  try {
    const response = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level: context.level, question, context }),
      signal,
    })
    if (response.status === 404 || response.status === 405) return { offline: true, answer: localSummary(question, context) }
    if (!response.ok) throw new Error(`The explainer returned HTTP ${response.status}.`)
    const data = await response.json()
    if (typeof data.answer !== 'string') throw new Error('The explainer reply had no "answer" text.')
    return { offline: false, answer: data.answer }
  } catch (error) {
    if (error.name === 'AbortError') throw error
    if (error instanceof TypeError) return { offline: true, answer: localSummary(question, context) }
    throw error
  }
}
