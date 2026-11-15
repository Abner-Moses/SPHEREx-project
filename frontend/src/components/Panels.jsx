import { useEffect, useMemo, useState } from 'react'
import { ask, buildExplainContext } from '../explainer.js'
import { LANDMARK_NOTES } from '../content.js'
import { TASKS, installModel, modelStatus, removeModel, runTask } from '../model/index.js'
import { COLORMAPS } from '../lib/colormaps.js'
import { Badge, Check, Section, Seg, Slider, Stat } from './ui.jsx'
import { LineChart } from './Charts.jsx'
import { ColormapPicker } from './AnalyzeTools.jsx'

const fmtDate = (iso) => (iso ? iso.slice(0, 10) : '—')
const BANDS = { 1: '0.75–1.1 µm', 2: '1.1–1.65 µm', 3: '1.6–2.4 µm', 4: '2.4–3.8 µm', 5: '3.8–4.4 µm', 6: '4.4–5.0 µm' }

function elapsed(t, now) { const s = Math.max(0, Math.round((now - t) / 1000)); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` }

// ------------------------------------------------------------------ observations
export function ObservationsPanel({ selected, observations, product, onLoad, onCancel, now, backendUp }) {
  const [band, setBand] = useState('all')
  const rows = useMemo(() => {
    const list = observations.rows.filter((o) => band === 'all' || String(o.detector) === band)
    return [...list].sort((a, b) => (b.mjd_start ?? 0) - (a.mjd_start ?? 0))
  }, [observations.rows, band])
  const detectors = [...new Set(observations.rows.map((o) => o.detector))].filter(Boolean).sort()
  const dates = observations.rows.map((o) => o.mjd_start).filter(Boolean)
  const loading = product.status === 'loading'
  if (!selected) return <p className="hint pad">Select a star, galaxy or position (click the sky or use search) to see every SPHEREx observation that covers it.</p>
  if (!backendUp) return <p className="hint pad">The archive backend is offline. Start it with <code>python main.py</code>.</p>
  return (
    <div>
      <Section title={`Archive coverage · ${selected.name}`}>
        {observations.status === 'loading' && <p className="hint"><span className="spinner" /> Asking the IRSA archive…</p>}
        {observations.status === 'error' && <p className="hint bad">{observations.error}</p>}
        {observations.status === 'ready' && (
          <>
            <dl className="stats">
              <Stat label="Images covering this spot" value={`${observations.rows.length}${observations.truncated ? '+' : ''}`} />
              <Stat label="Bands (detectors)" value={detectors.length ? detectors.join(' ') : '—'} />
              {dates.length > 0 && <Stat wide label="Observed" value={`${fmtDate(new Date((Math.min(...dates) - 40587) * 86400000).toISOString())} → ${fmtDate(new Date((Math.max(...dates) - 40587) * 86400000).toISOString())}`} />}
            </dl>
            {observations.truncated && <p className="hint">More exist than were listed; the first {observations.rows.length} covering images are shown.</p>}
            {observations.rows.length === 0 && <p className="hint">SPHEREx has not released an image covering this position yet.</p>}
          </>
        )}
      </Section>
      {detectors.length > 1 && (
        <ul className="chips" aria-label="Filter by band">
          <li><button type="button" className={band === 'all' ? 'on' : ''} onClick={() => setBand('all')}>All bands</button></li>
          {detectors.map((d) => <li key={d}><button type="button" className={band === String(d) ? 'on' : ''} onClick={() => setBand(String(d))} title={BANDS[d]}>D{d} · {BANDS[d]?.split(' ')[0]}</button></li>)}
        </ul>
      )}
      <ul className="rows obs">
        {rows.map((o) => {
          const active = product.meta && product.meta.obs_id === o.obs_id && product.meta.detector === o.detector
          return (
            <li key={`${o.obs_id}-${o.detector}`} className={`row${active ? ' active' : ''}`}>
              <button type="button" className="row-main" disabled={loading} onClick={() => onLoad(o)}>
                <span className="row-text"><b>{fmtDate(o.date_obs)} · D{o.detector}</b><small>{o.wavelength_um ? `${o.wavelength_um[0].toFixed(2)}–${o.wavelength_um[1].toFixed(2)} µm` : ''} · {o.obs_id}</small></span>
                <small className="sep">{o.separation_deg?.toFixed(2)}° off</small>
              </button>
            </li>
          )
        })}
      </ul>
      {loading && <button type="button" className="btn ghost wide" onClick={onCancel}><span className="spinner" /> Cancel · {elapsed(product.startedAt, now)}</button>}
      {loading && <p className="hint">Images are ~60 MB. Partial downloads resume if you cancel or reload.</p>}
    </div>
  )
}

// ------------------------------------------------------------------ image
export function ImagePanel({ product, look, setLook, layers, setLayers, onEpoch, onOpenView, now }) {
  if (product.status === 'loading') return <p className="hint pad"><span className="spinner" /> Loading image… {elapsed(product.startedAt, now)}</p>
  if (product.status === 'error') return <p className="hint pad bad">{product.error}</p>
  const m = product.meta
  if (!m) return <p className="hint pad">Load an observation from the Observations tab. It appears on the sky at its true position, and you can zoom from the whole sky down to individual pixels.</p>
  return (
    <div>
      <Section title="This image">
        {m.source === 'mock' && <Badge tone="sim">Simulated — not archive data{m.fallback_reason ? `: ${m.fallback_reason}` : ''}</Badge>}
        <dl className="stats">
          <Stat label="Observation" value={m.obs_id ?? '—'} wide />
          <Stat label="Date" value={fmtDate(m.date_obs)} />
          <Stat label="Detector" value={m.detector ?? '—'} />
          {m.wavelength && <Stat wide label="Wavelength" value={`${m.wavelength.min.toFixed(2)}–${m.wavelength.max.toFixed(2)} µm`} />}
          <Stat label="Pixel size" value={`${m.pixel_scale_arcsec?.toFixed?.(2) ?? '—'}″`} />
          <Stat label="Flagged pixels" value={`${(m.bad_fraction * 100).toFixed(2)}%`} />
          {m.stretch?.unit && <Stat wide label="Display range" value={`${m.stretch.lo.toFixed(2)} → ${m.stretch.hi.toFixed(2)} ${m.stretch.unit}`} />}
        </dl>
        {m.mjd_avg && <button type="button" className="btn small ghost" onClick={onEpoch}>Show sky at this observation’s date</button>}
      </Section>
      <Section title="Appearance">
        <ColormapPicker value={look.cmap} onChange={(cmap) => setLook({ cmap })} />
        <Seg small label="Stretch" value={look.stretch} onChange={(stretch) => setLook({ stretch })} options={[{ id: 'linear', label: 'Linear' }, { id: 'sqrt', label: 'Sqrt' }, { id: 'log', label: 'Log' }, { id: 'asinh', label: 'Asinh' }]} />
        <Slider label="Black point" value={look.black} min={0} max={254} onChange={(black) => setLook({ black, white: Math.max(look.white, black + 1) })} />
        <Slider label="White point" value={look.white} min={1} max={255} onChange={(white) => setLook({ white, black: Math.min(look.black, white - 1) })} />
        <Slider label="Opacity" value={Math.round(look.opacity * 100)} min={0} max={100} onChange={(v) => setLook({ opacity: v / 100 })} format={(v) => `${v}%`} />
        <div className="inline">
          <Check label="Smooth" checked={look.interpolate} onChange={(interpolate) => setLook({ interpolate })} />
          <Check label="Invert" checked={look.invert} onChange={(invert) => setLook({ invert })} />
          <Check label="Flag mask" checked={layers.mask} onChange={(mask) => setLayers({ mask })} />
        </div>
      </Section>
      <Section title="Open in">
        <div className="inline">
          <button type="button" className="btn small ghost" onClick={() => onOpenView('image')}>2D detector view</button>
          <button type="button" className="btn small ghost" onClick={() => onOpenView('surface')}>3D surface</button>
        </div>
        <p className="hint">The 2D view shows raw detector pixels with measurement tools; the sky view shows the same image where it really is.</p>
      </Section>
    </div>
  )
}

// ------------------------------------------------------------------ AI placeholders
export function AIPanel({ ctx, onResult, results, setResults }) {
  const [cache, setCache] = useState({ supported: true, installed: false, bytes: 0 })
  const [busy, setBusy] = useState(null)
  const [progress, setProgress] = useState(0)
  const [installing, setInstalling] = useState(false)
  useEffect(() => { modelStatus().then(setCache).catch(() => {}) }, [])
  const run = async (task) => {
    setBusy(task); setProgress(0)
    try { const r = await runTask(task, ctx, setProgress); setResults((x) => ({ ...x, [task]: r })); onResult?.(r) } finally { setBusy(null) }
  }
  const install = async () => { setInstalling(true); setProgress(0); try { await installModel(setProgress); setCache(await modelStatus()) } finally { setInstalling(false) } }
  return (
    <div>
      <Section title="Model">
        <div className="card muted">
          <p><b>Small on-device model</b> (6M parameters). Stored in this browser’s cache, so it downloads once.</p>
          <p className="fine">Status: {!cache.supported ? 'cache unavailable in this browser' : cache.installed ? `installed (${cache.placeholder ? 'placeholder' : 'trained'} · ${(cache.bytes / 1024).toFixed(0)} KB)` : 'not installed'}</p>
          {installing && <div className="meter"><i style={{ width: `${progress * 100}%` }} /></div>}
          <div className="inline">
            {!cache.installed && <button type="button" className="btn small primary" disabled={installing || !cache.supported} onClick={install}>Install placeholder</button>}
            {cache.installed && <button type="button" className="btn small ghost" onClick={async () => { await removeModel(); setCache(await modelStatus()) }}>Remove from cache</button>}
          </div>
        </div>
        <Badge tone="sim">Preview: results below are placeholders</Badge>
      </Section>
      <Section title="Tools">
        <ul className="tools">
          {TASKS.map((t) => (
            <li key={t.id} className={results[t.id] ? 'done' : ''}>
              <div className="tool-head">
                <div><b>{t.label}</b><small>{t.blurb}</small></div>
                <button type="button" className="btn small" disabled={!!busy} onClick={() => run(t.id)}>{busy === t.id ? `${Math.round(progress * 100)}%` : results[t.id] ? 'Re-run' : 'Run'}</button>
              </div>
              {busy === t.id && <div className="meter"><i style={{ width: `${progress * 100}%` }} /></div>}
              {results[t.id] && <Result r={results[t.id]} />}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  )
}

function Result({ r }) {
  return (
    <div className="result">
      <p>{r.summary}</p>
      {r.stats && <dl className="stats">{Object.entries(r.stats).map(([k, v]) => <Stat key={k} label={k} value={v} wide />)}</dl>}
      {r.track && <ul className="list">{r.track.map((p) => <li key={p.label}><span>{p.label}</span><b>{p.ra.toFixed(4)}°, {p.dec.toFixed(4)}°</b></li>)}</ul>}
      {r.curve && (
        <>
          <LineChart data={r.curve} xKey="x" yKey="y" xLabel="wavelength (µm)" yLabel="flux density (Jy)" yFormat={(v) => v.toFixed(1)} height={120} />
          {r.measured && <p className="fine">Measured at {r.measured.x.toFixed(2)} µm: {r.measured.lowerLimit ? '≥ ' : ''}{r.measured.y.toFixed(2)} Jy{r.measured.lowerLimit ? ` (lower limit: ${(r.measured.masked * 100).toFixed(0)}% of the aperture is masked, the star's core is saturated)` : ''}.</p>}
        </>
      )}
      {r.items?.length > 0 && !r.track && !r.curve && <ul className="list">{r.items.slice(0, 8).map((i) => <li key={i.id}><span>{i.label ?? `source at ${i.x.toFixed(0)}, ${i.y.toFixed(0)}`}</span><b>{i.score ? `${Math.round(i.score * 100)}%` : `${i.peak}/255`}</b></li>)}{r.items.length > 8 && <li><small>+{r.items.length - 8} more</small></li>}</ul>}
      <Badge tone="sim">placeholder output</Badge>
    </div>
  )
}

// ------------------------------------------------------------------ learn + ask
export function LearnPanel({ selected, product }) {
  const note = selected && LANDMARK_NOTES[selected.name]
  return (
    <div>
      {note && <article className="card accent"><h4>{selected.name}</h4><p>{note}</p></article>}
      {product.meta?.source === 'irsa' && <article className="card live"><h4>You are looking at real data</h4><p>This picture was taken by NASA’s SPHEREx space telescope in {product.meta.wavelength ? `${product.meta.wavelength.min.toFixed(1)}–${product.meta.wavelength.max.toFixed(1)} µm infrared light` : 'infrared light'}. It is placed on the sky exactly where it was pointing, using the telescope’s own coordinate calibration.</p></article>}
      <article className="card"><h4>How to explore</h4><p>Drag to look around, scroll to zoom (down to a few arcseconds), double-click an object to fly to it, and use the search box for any name or coordinates. Press <b>G</b> for the coordinate grid.</p></article>
      <article className="card"><h4>Why does the star move?</h4><p>Stars drift across the sky very slowly, but nearby ones add up over decades. Use the clock at the bottom right to change the date: Barnard’s Star, the fastest mover we know, shifts about 10 arcseconds every year.</p></article>
      <article className="card muted"><h4>Honest limits</h4><p>Fainter stars come from the SIMBAD catalogue on demand. Pixel brightness in the 8-bit display is relative; physical units (MJy/sr) come from the display range shown in the Image tab.</p></article>
    </div>
  )
}

export function AskPanel({ level, selected, product }) {
  const [q, setQ] = useState('')
  const [thread, setThread] = useState([])
  const [busy, setBusy] = useState(false)
  async function submit(text) {
    const t = text.trim(); if (!t || busy) return
    setQ(''); setBusy(true)
    const context = buildExplainContext({ level, view: 'sky', probe: product.meta ? { ra: selected?.ra, dec: selected?.dec, source: product.meta.source, status: 'ok', image: product.image, meta: { obs_id: product.meta.obs_id, detector: product.meta.detector, date: product.meta.date_obs } } : null, selectedLandmark: selected ? { label: selected.name, ra: selected.ra, dec: selected.dec } : null })
    try { const r = await ask(t, context); setThread((x) => [...x, { q: t, ...r }]) } catch (e) { setThread((x) => [...x, { q: t, answer: e.message, failed: true }]) } finally { setBusy(false) }
  }
  return (
    <div className="ask">
      <p className="hint">Ask in plain words. Answers use what is on screen (target, observation) so they stay grounded.</p>
      <ul className="chips">{['What am I looking at?', 'Why does this star move?', 'What is infrared light?'].map((s) => <li key={s}><button type="button" onClick={() => submit(s)} disabled={busy}>{s}</button></li>)}</ul>
      <ol className="thread" aria-live="polite">{thread.map((m, i) => <li key={i}><p className="q">{m.q}</p><p className={`a${m.failed ? ' bad' : ''}`}>{m.answer}</p>{m.offline && <Badge tone="sim">built-in note · AI not connected</Badge>}</li>)}{busy && <li className="thinking"><span className="spinner" /> Thinking…</li>}</ol>
      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); submit(q) }}>
        <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={2} placeholder="Ask about the sky, the image, or the numbers…" onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(q) } }} />
        <button type="submit" className="btn primary small" disabled={busy || !q.trim()}>Ask</button>
      </form>
    </div>
  )
}

export { COLORMAPS }
