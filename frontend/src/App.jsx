import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useExplorer, BARNARD } from './explorer.js'
import { loadSetting, logEvent, saveSetting, voyagerAsk } from './store.js'
import SkyView from './views/SkyView.jsx'
import { nearestStars } from './lib/depth.js'
import { DEG, prettyStarName } from './lib/astro.js'
import { propagate, yearsSinceJ2000 } from './lib/skyview.js'
import { typeLabel } from './explorer.js'

// The 2D detector, 3D surface and depth views are secondary: load them (and their engines) only when first opened.
const DepthView = lazy(() => import('./views/DepthView.jsx'))
const DetectorView = lazy(() => import('./views/DetectorView.jsx'))
const SurfaceView = lazy(() => import('./views/SurfaceView.jsx'))
import { DepthTools, DetectorTools, SurfaceTools } from './components/AnalyzeTools.jsx'
import { AIPanel, AskPanel, ImagePanel, LearnPanel, ObservationsPanel } from './components/Panels.jsx'
import { ContextPill, FlightLog, ICONS, InfoCard, MenuDrawer, SearchBar, TimePill, Toolbar } from './components/Shell.jsx'

const Intro = lazy(() => import('./intro/Intro.jsx'))
const Voyager = lazy(() => import('./voyager/Voyager.jsx'))
const Capture = lazy(() => import('./components/Capture.jsx'))
// on-screen parts Voyager can point at (only those currently in the DOM are offered)
const UI_TARGETS = [['ui:search', '.search', 'the command line (search)'], ['ui:views', '.topbar .seg', 'the view switches'], ['ui:toolbar', '.toolbar', 'the sky layer toolbar'], ['ui:dial', '.attitude', 'the attitude dial'], ['ui:clocks', '.deck-clocks', 'the sky-epoch and your-time clocks'], ['ui:log', '.flightlog', 'the flight log'], ['ui:target', '.infocard', 'the target card'], ['ui:panel', '.panel.right', 'the side panel']]
const introSeen = () => loadSetting('spherex.intro.seen', false)
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
import { Badge, Icon } from './components/ui.jsx'

const DEFAULT_IMAGE_LOOK = { black: 0, white: 255, stretch: 'asinh', invert: false, cmap: 'ember', opacity: 1, interpolate: false }
const DEFAULT_2D = { black: 0, white: 255, stretch: 'asinh', invert: false, interpolate: false, cmap: 'gray' }
const VIEWS = [{ id: 'sky', label: 'Sky' }, { id: 'image', label: 'Image' }, { id: 'surface', label: '3D' }, { id: 'depth', label: 'Depth' }]
const TABS = [{ id: 'obs', label: 'Observations' }, { id: 'image', label: 'Image' }, { id: 'ai', label: 'AI tools' }, { id: 'learn', label: 'Learn' }, { id: 'ask', label: 'Ask' }]

export default function App() {
  const x = useExplorer()
  const { engineRef, sky, layers, setLayers, projection, setProjection, epochMs, setEpoch, timeRate, setTimeRate, selected, product, observations, backendUp } = x
  const [view, setView] = useState('sky')
  const [visited, setVisited] = useState(() => new Set(['sky']))
  const [panel, setPanel] = useState('obs')
  const [panelOpen, setPanelOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [night, setNight] = useState(false)
  const [level, setLevelState] = useState(() => loadSetting('spherex.level', 'explorer'))
  const [look, setLookState] = useState(DEFAULT_IMAGE_LOOK)
  const [look2d, setLook2d] = useState(DEFAULT_2D)
  const [tool, setTool] = useState('inspect')
  const [aperture, setAperture] = useState(null)
  const [line, setLine] = useState(null)
  const [surface, setSurface] = useState({ region: 'crop', exaggeration: 0.45, contours: true, replay: 0 })
  const [flattened, setFlattened] = useState(false)
  const [depthCount, setDepthCount] = useState(0)
  const [fitSignal, setFitSignal] = useState(0)
  const [aiResults, setAiResults] = useState({})
  const [pins, setPins] = useState([])
  const [now, setNow] = useState(Date.now())
  const searchRef = useRef(null)
  const [intro, setIntro] = useState(() => (reducedMotion() ? null : introSeen() ? 'return' : 'first'))
  const [uplink, setUplink] = useState(0)
  const [capture, setCapture] = useState(false)
  const depthRef = useRef(null)
  const canCapture = view === 'sky' || view === 'depth'
  const pausedRate = useRef(0)
  // capturing pauses time (so moving stars hold still) and restores the previous rate afterwards
  const openCapture = useCallback(() => { pausedRate.current = timeRate; if (timeRate) setTimeRate(0); setCapture(true); logEvent('i', 'capture · sky paused') }, [timeRate, setTimeRate])
  const closeCapture = useCallback(() => { setCapture(false); if (pausedRate.current) setTimeRate(pausedRate.current); pausedRate.current = 0 }, [setTimeRate])
  useEffect(() => { if (capture) closeCapture() }, [view]) // eslint-disable-line react-hooks/exhaustive-deps
  const [voy, setVoyState] = useState(() => ({ show: true, humor: 50, detail: 60, ...loadSetting('spherex.voyager', {}) }))
  const setVoy = useCallback((patch) => setVoyState((v) => { const n = { ...v, ...patch }; saveSetting('spherex.voyager', n); return n }), [])

  const setLevel = useCallback((l) => { setLevelState(l); saveSetting('spherex.level', l) }, [])
  const setLook = useCallback((patch) => { setLookState((l) => { const n = { ...l, ...patch }; engineRef.current?.setLook(n); return n }) }, [engineRef])
  useEffect(() => { if (product.status === 'loading') { const t = setInterval(() => setNow(Date.now()), 1000); setNow(Date.now()); return () => clearInterval(t) } return undefined }, [product.status])
  useEffect(() => { engineRef.current?.setLook(look) }, [product.status, engineRef]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (layers.mask && product.meta) import('./api.js').then((api) => api.loadMask(product.meta).then((m) => engineRef.current?.setMaskImage(m))).catch(() => {}) }, [layers.mask, product.meta, engineRef])
  useEffect(() => { if (product.status === 'ready') { setPanel('image'); setPanelOpen(true) } }, [product.status])

  // ---- flight log: real events only
  useEffect(() => { if (sky.status === 'ready') logEvent('ok', `catalogue · ${sky.stars.length.toLocaleString()} stars`) }, [sky.status]) // eslint-disable-line react-hooks/exhaustive-deps
  // log the archive link only once its state has held for a moment (the first health check can flicker)
  useEffect(() => {
    if (backendUp === null) return undefined
    const t = setTimeout(() => (backendUp ? logEvent('ok', 'archive link · IRSA') : logEvent('r', 'archive offline · coordinates only')), 1200)
    return () => clearTimeout(t)
  }, [backendUp])
  useEffect(() => { if (selected) logEvent('c', `target · ${selected.name}`) }, [selected?.name]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (x.search.error) logEvent('r', `resolve failed · ${x.search.error}`) }, [x.search.error])
  useEffect(() => {
    if (product.status === 'loading') logEvent('v', 'requesting image from the archive')
    else if (product.status === 'ready' && product.meta) { logEvent('r', `uplink D${product.meta.detector} · ${product.meta.obs_id ?? 'image'}`); setUplink((n) => n + 1) }
    else if (product.status === 'error') logEvent('r', `uplink failed · ${product.error}`)
  }, [product.status]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!uplink) return undefined; const t = setTimeout(() => setUplink(0), 2100); return () => clearTimeout(t) }, [uplink])
  const finishIntro = useCallback(() => { saveSetting('spherex.intro.seen', true); setIntro(null) }, [])
  useEffect(() => { setAperture(null); setLine(null) }, [product.meta?.id])
  useEffect(() => { if (selected) { setPanel((p) => (p === 'learn' || p === 'ask' ? p : 'obs')) } }, [selected])
  useEffect(() => { if (x.toast) { const t = setTimeout(() => x.setToast(null), 8000); return () => clearTimeout(t) } return undefined }, [x.toast]) // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch the archive list only when the Observations tab is actually showing it (a stray click costs nothing).
  useEffect(() => {
    if (!selected || !backendUp || !panelOpen || panel !== 'obs') return
    if (observations.key !== `${selected.ra.toFixed(5)}|${selected.dec.toFixed(5)}`) x.loadObservationList(selected.ra, selected.dec)
  }, [selected, backendUp, panelOpen, panel, observations.key]) // eslint-disable-line react-hooks/exhaustive-deps

  const goView = useCallback((v) => { setView(v); setVisited((s) => (s.has(v) ? s : new Set(s).add(v))); logEvent('i', `view · ${VIEWS.find((o) => o.id === v)?.label ?? v}`) }, [])

  // ---- picking on the sky
  const onPick = useCallback((hit, skyPoint) => {
    if (hit?.kind === 1) {
      const lm = sky.landmarks.find((l) => l.label === hit.label)
      if (lm) { x.select({ name: lm.label, ra: lm.ra, dec: lm.dec, pmra: lm.pmra, pmdec: lm.pmdec, otype: lm.otype, distance_ly: lm.distance_ly, landmark: true }, { fly: true, fov: 6 }); return }
      return // the selection ring or a result pin: nothing new to select
    }
    if (hit?.star) { x.select({ name: hit.name, ra: hit.ra0, dec: hit.dec0, pmra: hit.pmra, pmdec: hit.pmdec, otype: hit.otype, vmag: hit.mag, plx: hit.plx, distance_ly: hit.plx > 1 ? Math.round(3261.56 / hit.plx * 100) / 100 : undefined }, { fly: false }); return }
    if (skyPoint) x.select({ name: `${skyPoint.ra.toFixed(4)}°, ${skyPoint.dec.toFixed(4)}°`, ...skyPoint, point: true }, { fly: false })
  }, [sky.landmarks, x])

  const markers = useMemo(() => {
    const list = sky.landmarks.map((l) => ({ id: `lm:${l.label}`, ra: l.ra, dec: l.dec, pmra: l.pmra, pmdec: l.pmdec, moves: true, kind: 'landmark', label: l.label, selected: false }))
    if (selected) list.push({ id: 'sel', ra: selected.ra, dec: selected.dec, pmra: selected.pmra, pmdec: selected.pmdec, moves: true, kind: selected.point ? 'candidate' : 'landmark', label: selected.point ? undefined : undefined, selected: true })
    for (const p of pins) list.push({ id: `pin:${p.id}`, ra: p.ra, dec: p.dec, kind: 'pin', label: p.label, pin: true })
    return list
  }, [sky.landmarks, selected, pins])

  // ---- Voyager: what it can see and where things are
  const vref = useRef({})
  vref.current = { sky, selected, product, view, epochMs, level }
  const skyPoint = useCallback((o) => {
    const e = engineRef.current
    const { view: v, epochMs: ep } = vref.current
    if (!e || v !== 'sky' || !o) return null
    const p = propagate(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, yearsSinceJ2000(ep))
    const s = e.getView().project(p.lon, p.lat)
    const r = e.canvas.getBoundingClientRect()
    if (!s || !s.visible || s.x < 8 || s.y < 60 || s.x > r.width - 8 || s.y > r.height - 8) return null
    return { x: r.left + s.x, y: r.top + s.y }
  }, [engineRef])
  const findSkyObject = useCallback((id) => {
    const { sky: k, selected: sel } = vref.current
    if (id === 'sel') return sel
    if (id.startsWith('lm:')) return k.landmarks.find((l) => l.label === id.slice(3))
    if (id.startsWith('star:')) { const s = k.stars.find((t) => t.name === id.slice(5)); return s && { ...s, pmra: s.pmra ?? 0, pmdec: s.pmdec ?? 0 } }
    return null
  }, [])
  const resolveTarget = useCallback((id) => {
    if (!id) return null
    if (id.startsWith('ui:')) {
      const sel = UI_TARGETS.find((u) => u[0] === id)?.[1]
      const b = sel ? document.querySelector(sel)?.getBoundingClientRect() : null
      return b && b.width ? { x: b.left + b.width / 2, y: b.top + b.height / 2 } : null
    }
    return skyPoint(findSkyObject(id))
  }, [skyPoint, findSkyObject])
  const getTargets = useCallback(() => {
    const { sky: k, selected: sel } = vref.current
    const out = []
    if (sel && skyPoint(sel)) out.push({ id: 'sel', label: `selected: ${sel.name}` })
    for (const l of k.landmarks) if (skyPoint(l)) out.push({ id: `lm:${l.label}`, label: l.label })
    k.stars.filter((s) => s.name && s.v < 2.6).sort((a, b) => a.v - b.v).filter((s) => skyPoint(s)).slice(0, 12).forEach((s) => out.push({ id: `star:${s.name}`, label: prettyStarName(s.name) }))
    for (const [id, css, label] of UI_TARGETS) if (document.querySelector(css)) out.push({ id, label })
    return out
  }, [skyPoint])
  const getContext = useCallback(() => {
    const { selected: sel, product: pr, view: v, epochMs: ep, level: lv } = vref.current
    const st = engineRef.current?.getState()
    const ctx = { level: lv, view: v, sky_epoch: new Date(ep).toISOString().slice(0, 10), your_date: new Date().toISOString().slice(0, 10) }
    if (st) ctx.looking_at = { ra_deg: +((((st.lon0 / DEG) % 360) + 360) % 360).toFixed(3), dec_deg: +(st.lat0 / DEG).toFixed(3), fov_deg: +st.fov.toFixed(3), projection: st.projection }
    if (sel) ctx.selected = { name: sel.name, type: sel.point ? 'a chosen position' : typeLabel(sel.otype), ra_deg: +sel.ra.toFixed(4), dec_deg: +sel.dec.toFixed(4), vmag: sel.vmag ?? undefined, distance_ly: sel.distance_ly ?? undefined, pm_mas_yr: sel.pmra != null ? [sel.pmra, sel.pmdec] : undefined }
    if (pr.meta) ctx.image = { source: pr.meta.source === 'irsa' ? 'real SPHEREx archive image' : 'simulated test image', obs_id: pr.meta.obs_id, detector: pr.meta.detector, date: pr.meta.date_obs ?? undefined, note: 'pixel values are display units after a percentile stretch, not physical flux' }
    return ctx
  }, [engineRef])

  // ---- AI results -> pins on the sky
  const onResult = useCallback((r) => {
    if (r.task === 'moving' && r.track) setPins(r.track.map((t, i) => ({ id: `mv${i}`, ra: t.ra, dec: t.dec, label: t.label })))
    else if (r.task === 'detect' && r.items.length) setPins(r.items.slice(0, 40).map((i) => ({ id: i.id, ra: i.sky.ra, dec: i.sky.dec })))
    else if (r.task === 'noise') x.setLayers({ mask: true })
    else setPins([])
  }, [x])

  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const tag = e.target.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      const k = e.key.toLowerCase()
      const toggle = { g: 'equGrid', h: 'galGrid', e: 'ecliptic', p: 'galPlane', l: 'labels', d: 'deep', f: 'footprints', i: 'overlay', k: 'constellations' }[k]
      // sky navigation from the keyboard: + / - zoom, arrows pan, B cycles the background imagery
      if (view === 'sky' && !capture) {
        const eng = engineRef.current
        if (k === '+' || k === '=') { e.preventDefault(); eng?.zoomBy(1 / 1.5); return }
        if (k === '-' || k === '_') { e.preventDefault(); eng?.zoomBy(1.5); return }
        const pan = { arrowleft: [-0.18, 0], arrowright: [0.18, 0], arrowup: [0, -0.18], arrowdown: [0, 0.18] }[k]
        if (pan) { e.preventDefault(); eng?.panBy(...pan); return }
        if (k === 'b') { const order = ['infrared', 'dss', 'none']; setLayers({ survey: order[(order.indexOf(layers.survey ?? 'infrared') + 1) % 3] }); return }
      }
      // layer toggles belong to the sky; in the 3D views these letters steer the camera (W A S D Q E)
      if (capture && k === 'escape') { closeCapture(); return }
      if (toggle) { if (view === 'sky') setLayers({ [toggle]: !layers[toggle] }) }
      else if (k === 'c' && (view === 'sky' || view === 'depth')) { if (capture) closeCapture(); else openCapture() }
      else if (k === 'n') setNight((n) => !n)
      else if (k === '/') { e.preventDefault(); searchRef.current?.focus() }
      else if (k === 'escape') { x.clearSelection(); setPins([]) }
      else if ('1234'.includes(k) && k) goView(VIEWS['1234'.indexOf(k)].id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [layers, setLayers, x, goView, view, capture, openCapture, closeCapture, engineRef])

  const fullscreen = () => { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen?.() }
  const image = product.image
  const insets = useMemo(() => ({ left: 0, right: panelOpen && window.innerWidth > 900 ? 396 : 0 }), [panelOpen])
  const isBarnard = selected?.name === BARNARD.name || selected?.name === "Barnard's star"

  const rightPanel = (() => {
    if (view === 'image') return <DetectorTools probe={{ image }} look={look2d} setLook={setLook2d} tool={tool} setTool={setTool} aperture={aperture} setAperture={setAperture} line={line} onFit={() => setFitSignal((n) => n + 1)} level={level} />
    if (view === 'surface') return <SurfaceTools image={image} surface={surface} setSurface={setSurface} cmap={look2d.cmap} onCmap={(cmap) => setLook2d((l) => ({ ...l, cmap }))} hasCenter={!!aperture} />
    if (view === 'depth') return <DepthTools nearest={nearestStars(sky.stars)} flattened={flattened} onFlat={setFlattened} count={depthCount} total={sky.stars.length} />
    return null
  })()

  if (sky.status === 'error') return <div className="fatal"><h1>The sky catalogue didn’t load</h1><p>Expected <code>/data/sky.json</code> ({sky.error}). Run <code>python tools/fetch_sky_data.py</code> and reload.</p></div>

  return (
    <div className={`app${night ? ' night' : ''}${panelOpen ? ' panel-open' : ''}`} data-level={level}>
      <div className="stage">
        <SkyView active={view === 'sky'} rail={!capture} engineRef={engineRef} stars={sky.stars} markers={markers} projection={projection} onView={x.onView} onPick={onPick} night={night} layers={layers} setLayers={setLayers} />
        <Suspense fallback={null}>
        {visited.has('depth') && <DepthView active={view === 'depth'} stars={sky.stars} landmarks={sky.landmarks} flattened={flattened} onCount={setDepthCount} engineOut={depthRef} />}
        {visited.has('image') && <DetectorView active={view === 'image'} image={image} look={look2d} tool={tool} aperture={aperture} line={line} insets={insets} onAperture={(p) => setAperture((a) => ({ x: p.x, y: p.y, r: a?.r ?? 12 }))} onLine={(ph, p) => (ph === 'start' ? setLine({ x0: p.x, y0: p.y, x1: p.x, y1: p.y }) : setLine((l) => (l ? { ...l, x1: p.x, y1: p.y } : l)))} fitSignal={fitSignal} />}
        {visited.has('surface') && <SurfaceView active={view === 'surface'} image={image} region={surface.region} center={aperture} cmap={look2d.cmap} exaggeration={surface.exaggeration} contours={surface.contours} replayKey={surface.replay} />}
        </Suspense>
      </div>

      {sky.status === 'loading' && <div className="loading">Loading the sky…</div>}
      {(view === 'image' || view === 'surface') && !image && <div className="empty"><h2>No image loaded</h2><p>Open an observation from the sky first.</p><button type="button" className="btn primary" onClick={() => { goView('sky'); setPanel('obs'); setPanelOpen(true) }}>Back to the sky</button>{backendUp && <button type="button" className="btn ghost" onClick={() => { goView('sky'); x.openBarnardPreview() }}>Open the Barnard’s Star preview</button>}</div>}

      <header className="topbar">
        <button type="button" className="round" aria-label="Menu" onClick={() => setMenuOpen(true)}>{ICONS.menu}</button>
        <span className="deck-mark">SPHEREx</span>
        <SearchBar search={x.search} setSearch={x.setSearch} onSubmit={x.runSearch} inputRef={searchRef} backendUp={backendUp} />
        <div className="top-right">
          {canCapture && <button type="button" className={`round capture-btn${capture ? ' on' : ''}`} aria-label="Capture a star or body (C)" title="Capture a star or body (C)" aria-pressed={capture} onClick={() => (capture ? closeCapture() : openCapture())}>{ICONS.capture}</button>}
          <div className="seg small" role="radiogroup" aria-label="View">{VIEWS.map((v) => <button key={v.id} type="button" role="radio" aria-checked={view === v.id} className={view === v.id ? 'on' : ''} onClick={() => goView(v.id)}>{v.label}</button>)}</div>
          {product.meta && <Badge tone={product.meta.source === 'irsa' ? 'live' : 'sim'}>{product.meta.source === 'irsa' ? 'Live · SPHEREx' : 'Simulated'}</Badge>}
          <button type="button" className={`round${panelOpen ? ' on' : ''}`} aria-label="Observations, image and tools" aria-pressed={panelOpen} onClick={() => setPanelOpen((o) => !o)}>{ICONS.sparkle}</button>
        </div>
      </header>

      {canCapture && capture && <Suspense fallback={null}><Capture engineRef={view === 'depth' ? depthRef : engineRef} mode={view} key={view} backendUp={backendUp} onClose={closeCapture}
        onSelect={(o) => { const depth = view === 'depth'; if (depth) { closeCapture(); goView('sky') } x.select({ name: o.name, ra: o.ra, dec: o.dec, pmra: o.pmra, pmdec: o.pmdec, otype: o.otype, vmag: o.mag ?? undefined, distance_ly: o.dist, landmark: o.landmark }, { fly: depth, fov: 20 }) }}
        onAskVoyager={voy.show ? (o) => { x.select({ name: o.name, ra: o.ra, dec: o.dec, pmra: o.pmra, pmdec: o.pmdec, otype: o.otype, vmag: o.mag ?? undefined, distance_ly: o.dist, landmark: o.landmark }, { fly: false }); setTimeout(() => voyagerAsk(`What is ${o.name}?`, 'sel'), 60) } : null}
        onOpenImage={(o, row) => { closeCapture(); x.loadProduct({ ra: o.ra, dec: o.dec, obsId: row.obs_id, detector: row.detector }).then((m) => m?.mjd_avg && setEpoch(Date.UTC(2000, 0, 1, 12) + (m.mjd_avg - 51544.5) * 86400000)) }} /></Suspense>}
      {view === 'sky' && !capture && <InfoCard selected={selected} epochMs={epochMs} onClose={() => { x.clearSelection(); setPins([]) }} onZoom={() => x.flyTo(selected.ra, selected.dec, 0.5)} onPreview={x.openBarnardPreview} onAskVoyager={voy.show && selected ? () => voyagerAsk(`Tell me about ${selected.name}.`, 'sel') : null} onObservations={() => { setPanel('obs'); setPanelOpen(true) }} productMeta={product.meta} loadingProduct={product.status === 'loading'} isBarnard={isBarnard} />}

      {!selected && !capture && view === 'sky' && backendUp && !product.meta && (
        <div className="welcome"><button type="button" className="btn primary" onClick={x.openBarnardPreview}>{ICONS.pin} Open the Barnard’s Star SPHEREx preview</button><small>Real infrared image · zoom from the whole sky to single pixels</small></div>
      )}

      {panelOpen && (
        <aside className="panel right" aria-label="Details">
          <header className="panel-head tabs"><div role="tablist">{TABS.map((t) => <button key={t.id} role="tab" type="button" aria-selected={panel === t.id} className={panel === t.id ? 'on' : ''} onClick={() => setPanel(t.id)}>{t.label}</button>)}</div><button type="button" className="icon-btn subtle" aria-label="Hide panel" onClick={() => setPanelOpen(false)}>{Icon.close}</button></header>
          <div className="scroll" role="tabpanel" key={panel + view}>
            {rightPanel && panel === 'image' ? rightPanel : null}
            {!(rightPanel && panel === 'image') && panel === 'obs' && <ObservationsPanel selected={selected} observations={observations} product={product} backendUp={backendUp} now={now} onCancel={x.cancelProduct} onLoad={(o) => x.loadProduct({ ra: selected.ra, dec: selected.dec, obsId: o.obs_id, detector: o.detector }).then((m) => m?.mjd_avg && setEpoch(Date.UTC(2000, 0, 1, 12) + (m.mjd_avg - 51544.5) * 86400000))} />}
            {!(rightPanel && panel === 'image') && panel === 'image' && <ImagePanel product={product} look={look} setLook={setLook} layers={layers} setLayers={setLayers} now={now} onEpoch={x.goToObservationEpoch} onOpenView={goView} />}
            {panel === 'ai' && <AIPanel ctx={{ selected, product: product.meta ? { ...product.meta, image, meshP: product.meta.meshP } : null, epochMs }} onResult={onResult} results={aiResults} setResults={setAiResults} />}
            {panel === 'learn' && <LearnPanel selected={selected} product={product} />}
            {panel === 'ask' && <AskPanel level={level} selected={selected} product={product} />}
          </div>
        </aside>
      )}

      <MenuDrawer open={menuOpen} onClose={() => setMenuOpen(false)} onReplayIntro={() => { setMenuOpen(false); setIntro('first') }} voyager={voy} setVoyager={setVoy} landmarks={sky.landmarks} onLandmark={(l) => x.select({ name: l.label, ra: l.ra, dec: l.dec, pmra: l.pmra, pmdec: l.pmdec, otype: l.otype, distance_ly: l.distance_ly, landmark: true }, { fov: 6 })} settings={{ level, setLevel }} backendUp={backendUp} projection={projection} setProjection={setProjection} layers={layers} setLayers={setLayers} night={night} setNight={setNight} />

      {view === 'sky' && <Toolbar layers={layers} setLayers={setLayers} projection={projection} setProjection={setProjection} night={night} setNight={setNight} onFullscreen={fullscreen} onSettings={() => setMenuOpen(true)} />}
      {view === 'sky' && <ContextPill selected={selected} projection={projection} />}
      {view === 'sky' && !selected && !capture && <FlightLog />}
      {uplink > 0 && <div className="uplink-wipe" key={uplink} aria-hidden="true"><span>UPLINK · D{product.meta?.detector}</span></div>}
      <TimePill epochMs={epochMs} setEpoch={setEpoch} timeRate={timeRate} setTimeRate={setTimeRate} productMjd={product.meta?.mjd_avg} />

      {x.toast && <div className={`toast ${x.toast.tone ?? ''}`} role="status"><span>{x.toast.text}</span><button type="button" className="icon-btn subtle" aria-label="Dismiss" onClick={() => x.setToast(null)}>{Icon.close}</button></div>}
      {voy.show && <Suspense fallback={null}><Voyager ready={!intro && sky.status === 'ready'} humor={voy.humor} detail={voy.detail} getTargets={getTargets} resolveTarget={resolveTarget} getContext={getContext} /></Suspense>}
      {intro && <Suspense fallback={<div className="intro-hold" />}><Intro returning={intro === 'return'} onDone={finishIntro} /></Suspense>}
    </div>
  )
}

