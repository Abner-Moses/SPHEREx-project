import { useEffect, useRef, useState } from 'react'
import { SkyEngine } from '../engine/skyEngine.js'
import { SURVEYS } from '../engine/hips.js'
import { prettyStarName, spellGreek } from '../lib/astro.js'
import { typeLabel } from '../explorer.js'
import { useStore, viewStore } from '../store.js'

export default function SkyView({ active, engineRef, stars, markers, projection, onView, onPick, night, layers, setLayers, rail = true }) {
  const canvasRef = useRef(null)
  const labelsRef = useRef(null)
  const tipRef = useRef(null)
  const cb = useRef({})
  cb.current = { onView, onPick }

  useEffect(() => {
    const engine = new SkyEngine(canvasRef.current, labelsRef.current, {
      onView: (s) => cb.current.onView?.(s),
      onPick: (hit, sky, e) => cb.current.onPick?.(hit, sky, e),
      onHover: (hit, at) => {
        const t = tipRef.current
        if (!t) return
        if (!hit || !at) { t.style.opacity = '0'; return }
        const name = hit.label ?? spellGreek(prettyStarName(hit.name ?? ''))
        const bits = [hit.body ? (hit.planet ? 'Planet' : 'The Sun') : hit.otype ? typeLabel(hit.otype) : hit.label ? 'Landmark' : 'Star']
        if (hit.mag != null && !hit.body) bits.push(`mag ${Number(hit.mag).toFixed(1)}`)
        if (hit.distance_ly) bits.push(`${hit.distance_ly < 100 ? hit.distance_ly.toFixed(1) : Math.round(hit.distance_ly).toLocaleString()} ly`)
        t.firstChild.textContent = name
        t.lastChild.textContent = `${bits.join(' · ')} · click to select`
        t.style.transform = `translate(${Math.round(at.x + 14)}px, ${Math.round(at.y + 12)}px)`
        t.style.opacity = '1'
      },
    })
    engine.jumpTo(269.45, 4.69, 70)
    engineRef.current = engine
    if (import.meta.env.DEV) window.__sky = engine // dev-only handle for numerical verification in the browser
    return () => { engine.dispose(); engineRef.current = null }
  }, [engineRef])

  useEffect(() => { engineRef.current?.setBrightStars(stars) }, [stars, engineRef])
  // the full star catalogue and constellation figures (static files built by tools/build_sky_catalogue.py)
  useEffect(() => {
    const c = new AbortController()
    const get = (u) => fetch(u, { signal: c.signal }).then((r) => (r.ok ? r.json() : null)).catch(() => null)
    get('/data/hyg.json').then((h) => { if (h && !c.signal.aborted) engineRef.current?.setCatalogue(h) })
    get('/data/constellations.json').then((d) => { if (d && !c.signal.aborted) engineRef.current?.setConstellations(d) })
    return () => c.abort()
  }, [engineRef])
  useEffect(() => { engineRef.current?.setMarkers(markers) }, [markers, engineRef])
  useEffect(() => { engineRef.current?.setProjection(projection) }, [projection, engineRef])
  useEffect(() => { engineRef.current?.setActive(active) }, [active, engineRef])

  return (
    <div className={`layer${night ? ' night' : ''}`} aria-hidden={!active}>
      <canvas ref={canvasRef} className="gl" role="img" aria-label="Interactive sky. Drag to look around, scroll to zoom, click an object to select it." />
      <div ref={labelsRef} className="labels" />
      <div ref={tipRef} className="sky-tip" aria-hidden="true"><b /><span /></div>
      {active && rail && layers && <ZoomRail engineRef={engineRef} layers={layers} setLayers={setLayers} />}
    </div>
  )
}

const fmtSpan = (d) => (d >= 10 ? `${Math.round(d)}°` : d >= 1 ? `${d.toFixed(1)}°` : d * 60 >= 1 ? `${(d * 60).toFixed(d * 60 >= 10 ? 0 : 1)}′` : `${(d * 3600).toFixed(0)}″`)
const IMAGERY = [['infrared', 'IR'], ['dss', 'VIS'], ['none', 'OFF']]

/** Right-edge rail: zoom in / out, how much sky the screen covers, quick framings, and the background imagery. */
function ZoomRail({ engineRef, layers, setLayers }) {
  const [span, setSpan] = useState(null)
  const { fov } = useStore(viewStore) // the engine reports its view here ~10x a second
  useEffect(() => { const e = engineRef.current; if (e) setSpan(e.coverage()) }, [engineRef, fov])
  const z = (f) => engineRef.current?.zoomBy(f)
  const to = (deg) => engineRef.current?.zoomTo(deg)
  const survey = layers.survey ?? 'infrared'
  return (
    <div className="zoom-rail" role="group" aria-label="Zoom and sky imagery">
      <button type="button" onClick={() => z(1 / 1.6)} aria-label="Zoom in (+)" title="Zoom in (+)">+</button>
      <div className="zr-span" title="How much sky the screen shows (across × down)">{span ? <><b>{fmtSpan(span.w)}</b><i>×{fmtSpan(span.h)}</i></> : '—'}</div>
      <button type="button" onClick={() => z(1.6)} aria-label="Zoom out (−)" title="Zoom out (−)">−</button>
      <span className="zr-sep" />
      {[['Sky', 110, 'Wide: the whole region'], ['Field', 20, 'A constellation-sized field'], ['Detail', 2, 'Close up: single stars']].map(([l, d, t]) => <button type="button" key={l} className="zr-preset" onClick={() => to(d)} title={t}>{l}</button>)}
      <span className="zr-sep" />
      <div className="zr-img" role="radiogroup" aria-label="Background sky imagery (B)">
        {IMAGERY.map(([id, l]) => <button type="button" key={id} role="radio" aria-checked={survey === id} className={survey === id ? 'on' : ''} onClick={() => setLayers({ survey: id })} title={id === 'none' ? 'No imagery: catalogue stars only' : `${SURVEYS[id].name} imagery · ${SURVEYS[id].credit}`}>{l}</button>)}
      </div>
    </div>
  )
}
