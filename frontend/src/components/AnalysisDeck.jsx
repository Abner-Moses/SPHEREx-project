import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import Chart, { chartPng } from '../charts/Chart.jsx'
import { image as imageCharts, science as scienceCharts, stars as starCharts } from '../charts/builders.js'
import { webglAvailable } from '../charts/gl.js'
import { measuredFrames, runAnalysis } from '../science/client.js'
import { logEvent } from '../store.js'

const GROUPS = ['All', 'Spectrum', 'Time', 'Image', '3D', 'Statistics', 'Stars', 'Kinematics']

/**
 * Full-screen analysis deck for one capture.
 *   targets: demo targets in the frame [{id, name}]  -> SPHEREx science charts (backend)
 *   stars:   catalogued stars captured (multi-star)   -> catalogue charts
 *   png:     the captured frame                        -> image charts
 */
export default function AnalysisDeck({ targets = [], stars = [], png, onClose }) {
  const [active, setActive] = useState(targets[0]?.id ?? null)
  const [jobs, setJobs] = useState({}) // id -> {state, elapsed, bundle, error}
  const [group, setGroup] = useState('All')
  const [big, setBig] = useState(null)
  const [img, setImg] = useState([])
  const ctl = useRef({})
  const gl = useMemo(() => webglAvailable(), [])

  const run = (id, refresh = false) => {
    ctl.current[id]?.abort()
    const c = new AbortController()
    ctl.current[id] = c
    setJobs((j) => ({ ...j, [id]: { state: 'running', elapsed: 0 } }))
    runAnalysis(id, { refresh, signal: c.signal, onTick: (r) => setJobs((j) => ({ ...j, [id]: { state: 'running', elapsed: r.elapsed_s ?? 0 } })) })
      .then((bundle) => { setJobs((j) => ({ ...j, [id]: { state: 'done', bundle } })); logEvent('ok', `science · ${bundle.name} · ${bundle.status}`) })
      .catch((e) => { if (e.name !== 'AbortError') setJobs((j) => ({ ...j, [id]: { state: 'failed', error: e.message } })) })
  }
  useEffect(() => { for (const t of targets) run(t.id); const all = ctl.current; return () => Object.values(all).forEach((c) => c.abort()) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { let live = true; imageCharts(png).then((c) => { if (live) setImg(c) }).catch(() => {}); return () => { live = false } }, [png])
  useEffect(() => { const k = (e) => { if (e.key === 'Escape') { e.stopPropagation(); if (big) setBig(null); else onClose() } }; window.addEventListener('keydown', k, true); return () => window.removeEventListener('keydown', k, true) }, [big, onClose])

  const job = active ? jobs[active] : null
  const charts = useMemo(() => [...(job?.bundle ? scienceCharts(job.bundle) : []), ...starCharts(stars), ...img], [job, stars, img])
  const shown = group === 'All' ? charts : charts.filter((c) => c.group === group)
  const counts = useMemo(() => Object.fromEntries(GROUPS.map((g) => [g, g === 'All' ? charts.length : charts.filter((c) => c.group === g).length])), [charts])
  const b = job?.bundle
  const save = (id, title) => { const url = chartPng(document.getElementById(`chart-${id}`)); if (!url) return; const a = document.createElement('a'); a.href = url; a.download = `spherex-${id}-${title.replace(/\W+/g, '-').toLowerCase()}.png`; a.click() }

  // portalled to <body> so it sits above everything (Voyager, capture overlay) whatever the parent stacking context
  return createPortal(
    <div className="deck-analysis" role="dialog" aria-modal="true" aria-label="Capture analysis">
      <header className="da-head">
        <div className="da-title"><span className="da-tag">Analysis</span><h2>{b?.name ?? (targets[0]?.name || (stars.length ? `${stars.length} stars` : 'Captured frame'))}</h2>{b?.kind && <p>{b.kind}</p>}</div>
        {targets.length > 1 && <div className="da-targets" role="tablist">{targets.map((t) => <button key={t.id} type="button" role="tab" aria-selected={active === t.id} className={active === t.id ? 'on' : ''} onClick={() => setActive(t.id)}>{t.name}</button>)}</div>}
        <button type="button" className="icon-btn subtle" aria-label="Close analysis" onClick={onClose}>×</button>
      </header>
      {active && (
        <div className={`da-status ${job?.state ?? ''}`}>
          {job?.state === 'running' && <><span className="spinner" /> Measuring {targets.find((t) => t.id === active)?.name} with the SPHEREx science backend{job.elapsed ? ` · ${Math.round(job.elapsed)} s` : ''}. First run fetches archive frames (up to a few minutes); later runs are instant.</>}
          {job?.state === 'failed' && <>Science backend: {job.error} <button type="button" className="btn small" onClick={() => run(active, true)}>Retry</button></>}
          {job?.state === 'done' && b && <>
            <b className={`da-grade ${b.status}`}>{b.status === 'science' ? 'Science grade' : b.status === 'exploratory' ? 'Exploratory' : 'No measurements'}</b>
            {(b.sources ?? []).join(' · ') || 'no data source'} · {b.observations?.found ?? 0} frames found, {measuredFrames(b)} measured · position {b.astrometry?.source}
            {b.errors?.length > 0 && <span className="da-err" title={b.errors.map((e) => `${e.step}: ${e.reason}`).join('\n')}> · {b.errors.length} step{b.errors.length > 1 ? 's' : ''} unavailable ({b.errors[0].step}: {b.errors[0].reason.slice(0, 90)})</span>}
            <button type="button" className="btn small ghost" onClick={() => run(active, true)}>Recompute</button>
          </>}
        </div>
      )}
      {!gl && <p className="da-status failed">This browser has no WebGL2, so graphs cannot be drawn.</p>}
      <nav className="da-groups" aria-label="Graph groups">{GROUPS.filter((g) => counts[g] || g === 'All').map((g) => <button type="button" key={g} className={group === g ? 'on' : ''} onClick={() => setGroup(g)}>{g}<i>{counts[g]}</i></button>)}</nav>
      <div className="da-grid">
        {gl && shown.map((c) => (
          <figure key={c.id} className="da-card" id={`chart-${c.id}`}>
            <figcaption><b>{c.title}</b><span>{c.kind}</span>
              <span className="da-actions"><button type="button" onClick={() => setBig(c)} aria-label={`Enlarge ${c.title}`} title="Enlarge">⤢</button><button type="button" onClick={() => save(c.id, c.title)} aria-label={`Save ${c.title} as PNG`} title="Save PNG">↓</button></span>
            </figcaption>
            <Chart spec={c.spec} height={210} />
            {c.note && <p className="da-note">{c.note}</p>}
          </figure>
        ))}
        {!shown.length && <p className="da-empty">{job?.state === 'running' ? 'Graphs appear here as soon as the measurements arrive.' : 'Nothing to plot in this group for this capture.'}</p>}
      </div>
      {big && (
        <div className="da-big" onClick={() => setBig(null)}>
          <figure className="da-card" onClick={(e) => e.stopPropagation()} id={`chart-${big.id}-big`}>
            <figcaption><b>{big.title}</b><span>{big.kind}</span><span className="da-actions"><button type="button" onClick={() => setBig(null)} aria-label="Close">×</button></span></figcaption>
            <Chart spec={big.spec} height={Math.min(560, window.innerHeight - 200)} />
            {big.note && <p className="da-note">{big.note}</p>}
          </figure>
        </div>
      )}
    </div>,
    document.body,
  )
}
