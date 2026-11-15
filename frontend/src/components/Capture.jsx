import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api.js'
import { DEG, fmtDec, fmtRA, prettyStarName, spellGreek } from '../lib/astro.js'
import { typeLabel } from '../explorer.js'
import { logEvent } from '../store.js'
import { drawPigeon, inColumba, rare } from '../eggs.js'
import { demoMatch, starCap } from '../science/client.js'

const AnalysisDeck = lazy(() => import('./AnalysisDeck.jsx'))

// Capture: freeze the sky, frame a star or body, and identify what's in the frame.
//   1. aim    — the sky is paused under a viewfinder; drag a frame, or click for a quick one
//   2. shutter— the frame is snapshotted from the WebGL canvas
//   3. result — what's in it (drawn catalogue + SIMBAD for fainter things) and which SPHEREx images cover it

const QUICK = 56 // px: a click frames this much around the point
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

function sepDeg(ra1, dec1, ra2, dec2) {
  const a = ra1 * DEG, b = dec1 * DEG, c = ra2 * DEG, d = dec2 * DEG
  const x = Math.sin((d - b) / 2) ** 2 + Math.cos(b) * Math.cos(d) * Math.sin((c - a) / 2) ** 2
  return (2 * Math.asin(Math.min(1, Math.sqrt(x)))) / DEG
}
const fmtAngle = (deg) => (deg >= 1 ? `${deg.toFixed(deg >= 10 ? 0 : 1)}°` : deg >= 1 / 60 ? `${(deg * 60).toFixed(deg * 60 >= 10 ? 0 : 1)}′` : `${(deg * 3600).toFixed(0)}″`)

/** One shape for drawn stars, landmarks and SIMBAD rows. */
function norm(o) {
  if (o.marker) return { key: `lm:${o.label}`, name: o.label, type: o.otype ? typeLabel(o.otype) : 'Landmark', mag: null, dist: o.distance_ly, ra: o.ra, dec: o.dec, pmra: o.pmra, pmdec: o.pmdec, otype: o.otype, landmark: true, x: o.x, y: o.y, fromCamera: o.fromCamera_ly }
  const plx = o.plx ?? o.parallax_mas
  return {
    key: o.name, name: prettyStarName(o.name), type: o.otype ? typeLabel(o.otype) : 'Star',
    mag: o.mag ?? o.v ?? o.vmag ?? null, dist: o.distance_ly ?? (plx > 1 ? 3261.56 / plx : undefined),
    ra: o.ra, dec: o.dec, pmra: o.pmra ?? o.pmra_mas_yr, pmdec: o.pmdec ?? o.pmdec_mas_yr, otype: o.otype, x: o.x, y: o.y, remote: !!o.remote, fromCamera: o.fromCamera_ly,
    plx: plx ?? undefined, bv: o.bv ?? undefined, bmag: o.bmag, jmag: o.jmag, hmag: o.hmag, kmag: o.kmag, // for the multi-star graphs
  }
}

export default function Capture({ engineRef, mode = 'sky', backendUp, onSelect, onOpenImage, onAskVoyager, onClose, presentationRef }) {
  const [phase, setPhase] = useState('aim')
  const [drag, setDrag] = useState(null)
  const [flash, setFlash] = useState(0)
  const [result, setResult] = useState(null)
  const [deck, setDeck] = useState(false)
  const [shiftHeld, setShiftHeld] = useState(false)
  const start = useRef(null)
  const ctl = useRef(null)
  useEffect(() => () => ctl.current?.abort(), [])
  useEffect(() => { const k = (e) => setShiftHeld(e.shiftKey); window.addEventListener('keydown', k); window.addEventListener('keyup', k); return () => { window.removeEventListener('keydown', k); window.removeEventListener('keyup', k) } }, [])
  // hold the 3D camera still while framing (the sky view is held by the overlay itself)
  useEffect(() => { const e = engineRef.current; e?.freeze?.(phase === 'aim'); return () => e?.freeze?.(false) }, [engineRef, phase])

  const shoot = useCallback(async (rectScreen, multi = false) => {
    const e = engineRef.current
    if (!e) return
    const cr = e.canvas.getBoundingClientRect()
    const r = { x0: rectScreen.x0 - cr.left, y0: rectScreen.y0 - cr.top, x1: rectScreen.x1 - cr.left, y1: rectScreen.y1 - cr.top }
    const png = e.snapshot() // first, before anything changes on screen
    if (!reducedMotion()) { setFlash((n) => n + 1) }
    setPhase('result')
    setDrag(null)

    const cx = (r.x0 + r.x1) / 2
    const cy = (r.y0 + r.y1) / 2
    const center = e.skyAt(cx, cy)
    const corner = e.skyAt(r.x0, r.y0) ?? e.skyAt(r.x1, r.y1)
    const radius = center && corner ? sepDeg(center.ra, center.dec, corner.ra, corner.dec) : 0.1
    const size = { w: center ? sepDeg(...Object.values(e.skyAt(r.x0, cy) ?? center), ...Object.values(e.skyAt(r.x1, cy) ?? center)) : 0, h: center ? sepDeg(...Object.values(e.skyAt(cx, r.y0) ?? center), ...Object.values(e.skyAt(cx, r.y1) ?? center)) : 0 }

    // what the sky engine drew inside the frame
    const local = e.objectsIn(r).map(norm)
    // Shift capture: every star in the frame, up to a cap proportional to its area (science/client.js starCap)
    const cap = multi ? starCap(r.x1 - r.x0, r.y1 - r.y0) : null
    const score = (o) => Math.hypot(o.x - cx, o.y - cy) / Math.max(20, (r.x1 - r.x0) / 2) + (o.mag ?? 4) * 0.35 - (o.landmark ? 2 : 0)
    local.sort((a, b) => score(a) - score(b))
    const skyDirections = !e.atSun || e.atSun() // in the 3D view, only when standing at the Sun
    // Los Palomos: now and then, a capture inside Columba (the Dove) catches a pigeon in frame
    const pigeon = skyDirections && center && inColumba(center.ra, center.dec) && rare(import.meta.env.DEV && window.__pigeonCapture ? 1 : 0.12)
      ? { fx: 0.25 + Math.random() * 0.5, fy: 0.25 + Math.random() * 0.4, flip: Math.random() < 0.5 } : null
    if (pigeon) logEvent('i', 'capture · 1 unidentified object (feathered)')
    const base = { png, r, cx, cy, center, size, radius, pigeon, multi: cap ? { cap, total: local.length } : null, demo: [], objects: cap ? byMag(local).slice(0, cap) : local, primary: local[0] ?? null, simbad: backendUp && skyDirections ? 'loading' : 'off', images: { status: 'idle' }, mode, skyDirections }
    setResult(base)
    logEvent('c', `capture · ${fmtAngle(size.w)} × ${fmtAngle(size.h)} · ${local.length} drawn`)

    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    let primary = base.primary
    let objects = local
    // which of the five SPHEREx demo targets sit inside the frame (backend science is limited to these)
    if (backendUp && center && skyDirections) demoMatch(center.ra, center.dec, Math.max(radius, 0.01), c.signal).then((m) => setResult((s) => s && { ...s, demo: m.targets })).catch(() => {})
    // fainter things from SIMBAD, kept only if they fall inside the frame
    if (backendUp && center && skyDirections) {
      const fov = e.getState().fov
      const maglim = (fov > 30 ? 8.5 : fov > 8 ? 10 : fov > 2 ? 12.5 : fov > 0.3 ? 15 : 17) + (cap ? 2.5 : 0)
      try {
        const rows = await api.coneStars(center.ra, center.dec, Math.min(2.5, Math.max(0.01, radius)), Math.min(20, maglim), c.signal)
        const view = e.getView()
        const seen = new Set(local.map((o) => o.key))
        const extra = rows.map((o) => { const p = view.project(o.ra * DEG, o.dec * DEG); return { ...o, remote: true, x: p.x, y: p.y, visible: p.visible } })
          .filter((o) => o.visible && o.x >= r.x0 && o.x <= r.x1 && o.y >= r.y0 && o.y <= r.y1 && !seen.has(o.name)).map(norm)
        objects = [...local, ...extra]
        if (!primary && extra.length) primary = [...extra].sort((a, b) => score(a) - score(b))[0]
        const kept = cap ? byMag(objects).slice(0, cap) : objects
        setResult((s) => s && { ...s, objects: kept, primary, simbad: 'ready', multi: cap ? { cap, total: objects.length } : null })
      } catch (err) {
        if (err.name === 'AbortError') return
        setResult((s) => s && { ...s, simbad: 'error' })
      }
    }
    logEvent(primary ? 'ok' : 'i', primary ? `identified · ${primary.name}` : 'nothing catalogued in that frame')

    // which SPHEREx images cover it
    const at = primary ?? (center && skyDirections && { ra: center.ra, dec: center.dec })
    if (!backendUp || !at) return
    setResult((s) => s && { ...s, images: { status: 'loading' } })
    try {
      const res = await api.listObservations(at.ra, at.dec, 0.05, 200, c.signal)
      const rows = res.observations.filter((o) => o.covers).sort((a, b) => (b.mjd_start ?? 0) - (a.mjd_start ?? 0))
      setResult((s) => s && { ...s, images: { status: 'ready', rows, truncated: res.truncated } })
      logEvent(rows.length ? 'ok' : 'i', `SPHEREx · ${rows.length}${res.truncated ? '+' : ''} images cover it`)
    } catch (err) {
      if (err.name === 'AbortError') return
      setResult((s) => s && { ...s, images: { status: 'error', error: err.message } })
      logEvent('r', 'SPHEREx archive search failed')
    }
  }, [engineRef, backendUp])

  // aim: drag a frame, or click for a quick one
  useEffect(() => {
    if (presentationRef) presentationRef.current = shoot
    return () => { if (presentationRef) presentationRef.current = null }
  }, [presentationRef, shoot])

  const down = (ev) => { if (ev.button !== 0) return; ev.currentTarget.setPointerCapture(ev.pointerId); start.current = { x: ev.clientX, y: ev.clientY, shift: ev.shiftKey }; setDrag({ x0: ev.clientX, y0: ev.clientY, x1: ev.clientX, y1: ev.clientY }) }
  const move = (ev) => { if (start.current) setDrag({ x0: Math.min(start.current.x, ev.clientX), y0: Math.min(start.current.y, ev.clientY), x1: Math.max(start.current.x, ev.clientX), y1: Math.max(start.current.y, ev.clientY) }) }
  const up = (ev) => {
    if (!start.current) return
    const s = start.current
    start.current = null
    const big = Math.abs(ev.clientX - s.x) > 8 || Math.abs(ev.clientY - s.y) > 8
    const multi = s.shift || ev.shiftKey
    const q = multi ? QUICK * 3 : QUICK // a Shift-click frames a wider patch
    shoot(big ? { x0: Math.min(s.x, ev.clientX), y0: Math.min(s.y, ev.clientY), x1: Math.max(s.x, ev.clientX), y1: Math.max(s.y, ev.clientY) } : { x0: s.x - q / 2, y0: s.y - q / 2, x1: s.x + q / 2, y1: s.y + q / 2 }, multi)
  }

  const again = () => { ctl.current?.abort(); setResult(null); setDeck(false); setPhase('aim') }

  return (
    <>
      {phase === 'aim' && (
        <div className="capture-aim" onPointerDown={down} onPointerMove={move} onPointerUp={up} role="application" aria-label="Capture: drag a frame around a star or body, or click one. Escape cancels.">
          <div className="cap-vf" aria-hidden="true"><i /><i /><i /><i /><b /></div>
          <div className="cap-hint"><span className="cap-paused">Sky paused</span>Drag a frame around a star or body · click for a quick frame · <b>Shift</b> + drag captures every star in the frame · Esc to cancel</div>
          {drag && <DragFrame d={drag} engineRef={engineRef} multi={shiftHeld || !!start.current?.shift} />}
        </div>
      )}
      {flash > 0 && <div className="capture-flash" key={flash} aria-hidden="true" />}
      {phase === 'result' && result && !deck && <CaptureCard res={result} onClose={onClose} onAgain={again} onSelect={onSelect} onOpenImage={onOpenImage} onAskVoyager={onAskVoyager} backendUp={backendUp} onAnalyze={() => setDeck(true)} />}
      {phase === 'result' && result && deck && <Suspense fallback={<div className="deck-analysis loading">Loading graphs…</div>}><AnalysisDeck targets={result.demo} stars={result.multi ? result.objects : []} png={result.png} onClose={() => setDeck(false)} /></Suspense>}
    </>
  )
}

function DragFrame({ d, engineRef, multi }) {
  const e = engineRef.current
  let label = ''
  if (e && d.x1 - d.x0 > 8) {
    const cr = e.canvas.getBoundingClientRect()
    const a = e.skyAt(d.x0 - cr.left, d.y0 - cr.top)
    const b = e.skyAt(d.x1 - cr.left, d.y0 - cr.top)
    if (a && b) label = fmtAngle(sepDeg(a.ra, a.dec, b.ra, b.dec))
  }
  if (multi) label = `MULTI · up to ${starCap(d.x1 - d.x0, d.y1 - d.y0)} stars${label ? ` · ${label}` : ''}`
  return (
    <div className={`cap-frame${multi ? ' multi' : ''}`} style={{ left: d.x0, top: d.y0 - 54, width: d.x1 - d.x0, height: d.y1 - d.y0 }}>
      {label && <span>{label}</span>}
    </div>
  )
}

function Thumb({ res }) {
  const ref = useRef(null)
  useEffect(() => {
    const img = new Image()
    img.onload = () => {
      const c = ref.current
      if (!c) return
      const k = img.width / (window.innerWidth || img.width) // canvas pixels per CSS pixel
      const { x0, y0, x1, y1 } = res.r
      const w = Math.max(1, (x1 - x0) * k)
      const h = Math.max(1, (y1 - y0) * k)
      const W = 284
      const H = Math.round(Math.min(170, Math.max(90, (W * h) / w)))
      c.width = W
      c.height = H
      const g = c.getContext('2d')
      g.imageSmoothingEnabled = false
      g.fillStyle = '#07080a'
      g.fillRect(0, 0, W, H)
      const s = Math.min(W / w, H / h)
      g.drawImage(img, x0 * k, y0 * k, w, h, (W - w * s) / 2, (H - h * s) / 2, w * s, h * s)
      // amber brackets on the identified object
      const p = res.primary
      if (p && p.x != null) {
        const px = (W - w * s) / 2 + (p.x - x0) * k * s
        const py = (H - h * s) / 2 + (p.y - y0) * k * s
        g.strokeStyle = '#f2b705'
        g.lineWidth = 2
        const r = 9
        for (const [sx, sy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) { g.beginPath(); g.moveTo(px + sx * r, py + sy * (r - 5)); g.lineTo(px + sx * r, py + sy * r); g.lineTo(px + sx * (r - 5), py + sy * r); g.stroke() }
      }
      if (res.pigeon) drawPigeon(g, 'up', Math.round(res.pigeon.fx * W), Math.round(res.pigeon.fy * H), 2, res.pigeon.flip)
    }
    img.src = res.png
  }, [res.png, res.r, res.primary, res.pigeon])
  return <canvas ref={ref} className="cap-thumb" aria-label="The captured frame" />
}

function CaptureCard({ res, onClose, onAgain, onSelect, onOpenImage, onAskVoyager, backendUp, onAnalyze }) {
  const p = res.primary
  // skip the primary and anything drawn on top of it (a landmark and its catalogue star are the same object)
  const others = res.objects.filter((o) => o !== p && !(p && Math.hypot(o.x - p.x, o.y - p.y) < 2)).slice(0, res.multi ? 40 : 8)
  const im = res.images
  return (
    <aside className="infocard focus capture-card" aria-label="Capture result">
      <div className="target-tag"><span>Capture · {fmtAngle(res.size.w)} × {fmtAngle(res.size.h)}</span><span className="lamp">{p ? 'Identified' : 'No match'}</span></div>
      <Thumb res={res} />
      <header>
        <div><h2>{p ? spellGreek(p.name) : 'Nothing catalogued'}</h2><p>{p ? `${p.type}${p.remote ? ' · from SIMBAD, too faint to draw at this zoom' : ''}` : res.simbad === 'loading' ? 'Checking SIMBAD for fainter objects…' : 'No star or body in our catalogues falls inside that frame.'}</p></div>
        <button type="button" className="icon-btn subtle" onClick={onClose} aria-label="Close capture">×</button>
      </header>
      <dl>
        {p?.mag != null && <div><dt>Magnitude</dt><dd>V {Number(p.mag).toFixed(2)}</dd></div>}
        {p?.dist != null && <div><dt>Distance</dt><dd>{p.dist < 100 ? p.dist.toFixed(1) : Math.round(p.dist).toLocaleString()} light-years</dd></div>}
        {(p || (res.center && res.skyDirections)) && <div><dt>RA / Dec</dt><dd>{fmtRA(p?.ra ?? res.center.ra).replace(/\.\d+s/, 's')} {fmtDec(p?.dec ?? res.center.dec).replace(/\.\d+″/, '″')}</dd></div>}
        {p?.fromCamera != null && res.mode === 'depth' && !res.skyDirections && <div><dt>From you</dt><dd>{p.fromCamera.toFixed(0)} light-years</dd></div>}
        {res.pigeon && <div><dt>Also</dt><dd>1 pigeon · unidentified, probably harmless</dd></div>}
        {res.multi && <div><dt>Captured</dt><dd>{res.objects.length} star{res.objects.length === 1 ? '' : 's'} · limit {res.multi.cap} for this frame{res.multi.total > res.multi.cap ? ` (${res.multi.total} found, brightest kept)` : ''}</dd></div>}
        {res.demo?.length > 0 && <div><dt>SPHEREx science</dt><dd>{res.demo.map((t) => t.name).join(', ')}</dd></div>}
        <div><dt>In frame</dt><dd>{res.objects.length} object{res.objects.length === 1 ? '' : 's'}{res.simbad === 'loading' ? ' · searching…' : res.simbad === 'error' ? ' · SIMBAD unavailable' : ''}</dd></div>
      </dl>
      {res.mode === 'depth' && <p className="fine">{res.skyDirections ? 'Standing at the Sun, so this frame is a real patch of sky; fainter stars come from SIMBAD.' : 'Away from the Sun, so only the bright stars in this 3D map are identified.'}</p>}
      {others.length > 0 && <ul className="chips cap-others" aria-label="Also in frame">{others.map((o) => <li key={o.key}><button type="button" onClick={() => onSelect(o)} title={o.type}>{o.name}{o.mag != null ? ` · ${Number(o.mag).toFixed(1)}` : ''}</button></li>)}</ul>}
      <section className="cap-images">
        <h3>SPHEREx images</h3>
        {!backendUp && <p className="hint">The archive backend is offline, so I can't look for images.</p>}
        {im.status === 'loading' && <p className="hint"><span className="spinner" /> Asking the IRSA archive…</p>}
        {im.status === 'error' && <p className="hint bad" title={im.error}>{/irsa|timed out|timeout|502|503|504|proxy/i.test(im.error ?? '') ? "The SPHEREx archive (IRSA) isn't responding right now. That's on their side; try again in a while." : im.error}</p>}
        {im.status === 'ready' && (im.rows.length === 0 ? <p className="hint">No released SPHEREx image covers this spot yet.</p> : (
          <>
            <p className="hint">{im.rows.length}{im.truncated ? '+' : ''} image{im.rows.length === 1 ? '' : 's'} cover it · bands {[...new Set(im.rows.map((o) => `D${o.detector}`))].sort().join(' ')} · latest {im.rows[0].date_obs?.slice(0, 10) ?? '—'}</p>
            <button type="button" className="btn small primary" onClick={() => onOpenImage(p ?? { ra: res.center.ra, dec: res.center.dec }, im.rows[0])}>Open latest image</button>
          </>
        ))}
      </section>
      <div className="inline">
        {p && <button type="button" className="btn small" onClick={() => onSelect(p)}>{res.mode === 'depth' ? 'Show on sky' : 'Select'}</button>}
        {p && onAskVoyager && <button type="button" className="btn small ghost" onClick={() => onAskVoyager(p)}>Ask Voyager</button>}
        <button type="button" className="btn small primary" onClick={onAnalyze} title="WebGL graphs for this capture">{res.demo?.length ? 'Analyze · science' : res.multi ? `Analyze · ${res.objects.length} stars` : 'Analyze'}</button>
        <button type="button" className="btn small" onClick={onAgain}>New capture</button>
      </div>
    </aside>
  )
}

/** Brightest first; objects without a magnitude go last. */
function byMag(list) { return [...list].sort((a, b) => (a.mag ?? 99) - (b.mag ?? 99)) }
