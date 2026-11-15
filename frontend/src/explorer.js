// Central state for the explorer: selection, epoch, observations, image products, search.
// The sky engine is imperative (WebGL); React only holds what the UI shows.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { COLUMBA, isPigeonQuery } from './eggs.js'
import * as api from './api.js'
import { dedupeCloseStars, parseDec, parseRA } from './lib/astro.js'
import { prepareMesh } from './lib/mesh.js'
import { mjdToMs } from './lib/skyview.js'
import { loadSetting, saveSetting, viewStore } from './store.js'

export const BARNARD = { name: "Barnard's Star", ra: 269.45208, dec: 4.69336, obsId: '2026W34_1A_0007_4', detector: 6 }
const LAYERS_KEY = 'spherex.layers.v2'
const DEFAULT_LAYERS = { stars: true, deep: true, equGrid: false, galGrid: false, ecliptic: true, galPlane: true, labels: true, footprints: true, overlay: true, mask: false, markers: true, survey: 'infrared', surveyOpacity: 0.7, constellations: true, planets: true }

const OTYPES = {
  '*': 'Star', 'BY*': 'Variable star (BY Dra)', BYDraV: 'Variable star (BY Dra)', 'BYDraV*': 'Variable star (BY Dra)', 'V*': 'Variable star', '**': 'Double star', 'SB*': 'Spectroscopic binary',
  'Pl': 'Exoplanet', SolarPlanet: 'Planet (approximate position)', Sun: 'The Sun (approximate position)', 'WD*': 'White dwarf', 'G': 'Galaxy', GiG: 'Galaxy in a group', AGN: 'Active galaxy', Sy1: 'Seyfert galaxy', 'HII': 'HII region', SNR: 'Supernova remnant',
  OpC: 'Open cluster', 'Cl*': 'Star cluster', GlC: 'Globular cluster', 'Psr': 'Pulsar', 'DNe': 'Dark nebula', 'PN': 'Planetary nebula', 'reg': 'Region', 'Rad': 'Radio source', 's*r': 'Red supergiant', 'cC*': 'Cepheid', 'dS*': 'Delta Scuti variable', 'No*': 'Nova', 'IR': 'Infrared source', 'GrG': 'Group of galaxies', 'ClG': 'Galaxy cluster',
}
export const typeLabel = (code) => OTYPES[code] ?? code ?? 'Object'

export function totalPm(o) {
  const a = o.pmra_mas_yr ?? o.pmra
  const d = o.pmdec_mas_yr ?? o.pmdec
  if (a == null || d == null) return null
  return { mas: Math.hypot(a, d), pa: ((Math.atan2(a, d) * 180) / Math.PI + 360) % 360 }
}

export function useExplorer() {
  const engineRef = useRef(null)
  const [sky, setSky] = useState({ status: 'loading', stars: [], landmarks: [] })
  const [layers, setLayersState] = useState(() => ({ ...DEFAULT_LAYERS, ...loadSetting(LAYERS_KEY, {}) }))
  const [projection, setProjection] = useState('stereographic')
  const [epochMs, setEpochMs] = useState(() => Date.now())
  const [timeRate, setTimeRateState] = useState(0)
  const [selected, setSelected] = useState(null)
  const [observations, setObservations] = useState({ status: 'idle', rows: [], truncated: false, error: null, key: null })
  const [product, setProduct] = useState({ status: 'idle', meta: null, image: null, error: null, startedAt: 0 })
  const [toast, setToast] = useState(null)
  const [backendUp, setBackendUp] = useState(null)
  const [search, setSearch] = useState({ text: '', suggestions: [], busy: false, error: null })
  const ctl = useRef({})

  // ---- catalogue
  useEffect(() => {
    const c = new AbortController()
    fetch('/data/sky.json', { signal: c.signal })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then((d) => setSky({ status: 'ready', stars: dedupeCloseStars(d.stars), landmarks: d.landmarks, provenance: d.provenance }))
      .catch((e) => { if (e.name !== 'AbortError') setSky({ status: 'error', stars: [], landmarks: [], error: e.message }) })
    return () => c.abort()
  }, [])
  useEffect(() => {
    const c = new AbortController()
    const poll = async () => setBackendUp(await api.checkHealth(c.signal))
    poll()
    const t = setInterval(poll, 15000)
    return () => { c.abort(); clearInterval(t) }
  }, [])

  // ---- layers / epoch plumbing into the engine
  const setLayers = useCallback((patch) => {
    setLayersState((l) => { const next = { ...l, ...patch }; saveSetting(LAYERS_KEY, next); engineRef.current?.setLayers(next); engineRef.current?.applyLook?.(); return next })
  }, [])
  useEffect(() => { engineRef.current?.setLayers(layers) }, [layers])
  const setEpoch = useCallback((ms) => { setEpochMs(ms); engineRef.current?.setEpoch(ms) }, [])
  const setTimeRate = useCallback((r) => { setTimeRateState(r); engineRef.current?.setTimeRate(r) }, [])
  useEffect(() => {
    if (!timeRate) return undefined
    const t = setInterval(() => setEpochMs(engineRef.current?.epochMs ?? Date.now()), 250)
    return () => clearInterval(t)
  }, [timeRate])
  useEffect(() => { engineRef.current?.setProjection(projection) }, [projection])

  const onView = useCallback((s) => viewStore.set(s), [])

  // ---- deep stars for the current field (SIMBAD cone search via the backend). Driven by the view store directly,
  // so panning never re-renders React; a fetch starts only after the view has settled on a new field.
  useEffect(() => {
    if (!layers.deep || !backendUp) { engineRef.current?.setDeepStars([]); return undefined }
    let timer = null
    let ctrl = null
    let lastKey = ''
    const check = () => {
      const v = viewStore.get()
      if (v.fov > 8) { if (lastKey) { lastKey = ''; ctrl?.abort(); engineRef.current?.setDeepStars([]) } return }
      const key = `${Math.round(Math.log2(Math.max(v.fov, 1e-4)) * 2)}|${Math.round(v.lon0 * 400)}|${Math.round(v.lat0 * 400)}`
      if (key === lastKey) return
      lastKey = key
      clearTimeout(timer)
      ctrl?.abort()
      timer = setTimeout(async () => {
        ctrl = new AbortController()
        const maglim = v.fov > 4 ? 10 : v.fov > 1 ? 12.5 : v.fov > 0.3 ? 15 : 17
        try {
          const rows = await api.coneStars((((v.lon0 * 180) / Math.PI) % 360 + 360) % 360, (v.lat0 * 180) / Math.PI, Math.min(2.5, Math.max(0.02, v.fov * 0.8)), maglim, ctrl.signal)
          engineRef.current?.setDeepStars(rows)
        } catch (e) { if (e.name !== 'AbortError') engineRef.current?.setDeepStars([]) }
      }, 450)
    }
    check()
    const unsubscribe = viewStore.subscribe(check)
    return () => { unsubscribe(); clearTimeout(timer); ctrl?.abort() }
  }, [layers.deep, backendUp])

  // ---- selection
  const flyTo = useCallback((ra, dec, fov) => engineRef.current?.flyTo(ra, dec, fov), [])
  const clearSelection = useCallback(() => { setSelected(null); ctl.current.obs?.abort(); setObservations({ status: 'idle', rows: [], truncated: false, error: null, key: null }) }, [])

  const loadObservationList = useCallback(async (ra, dec) => {
    ctl.current.obs?.abort()
    const c = new AbortController()
    ctl.current.obs = c
    const key = `${ra.toFixed(5)}|${dec.toFixed(5)}`
    setObservations({ status: 'loading', rows: [], truncated: false, error: null, key })
    try {
      const r = await api.listObservations(ra, dec, 0.05, 500, c.signal) // same search observe() makes: one archive call serves both
      setObservations({ status: 'ready', rows: r.observations.filter((o) => o.covers), truncated: r.truncated, error: null, key })
    } catch (e) { if (e.name !== 'AbortError') setObservations({ status: 'error', rows: [], truncated: false, error: e.message, key }) }
  }, [])

  // Selecting never touches the network: the archive list is fetched only when something shows it (see App).
  const select = useCallback((obj, { fly = true, fov } = {}) => {
    setSelected({ ...obj })
    if (fly) flyTo(obj.ra, obj.dec, fov ?? Math.min(Math.max(engineRef.current?.getState().fov ?? 30, 0.5), 20))
  }, [flyTo])

  // ---- image products
  const loadProduct = useCallback(async ({ ra, dec, obsId, detector, mode = 'live', fly = true }) => {
    ctl.current.prod?.abort()
    const c = new AbortController()
    ctl.current.prod = c
    setProduct({ status: 'loading', meta: null, image: null, error: null, startedAt: Date.now() })
    try {
      const meta = await api.observe({ ra, dec, mode, obsId, detector }, c.signal)
      const image = await api.loadImage(meta, c.signal)
      // `mesh` is the raw backend mesh (the engine unwraps RA itself); `meshP` is the lookup form used by lib/mesh.js
      const prepared = { ...meta, meshP: prepareMesh(meta.mesh) }
      setProduct({ status: 'ready', meta: prepared, image, error: null, startedAt: 0 })
      engineRef.current?.setProduct(prepared, image)
      loadObservationList(ra, dec)
      if (fly) {
        const f = meta.footprint
        const cRa = f.reduce((s, p) => s + p[0], 0) / 4
        engineRef.current?.flyTo(Math.abs(f[0][0] - f[2][0]) > 180 ? ra : cRa, f.reduce((s, p) => s + p[1], 0) / 4, meta.source === 'mock' ? 0.08 : 4.2)
      }
      return prepared
    } catch (e) {
      if (e.name !== 'AbortError') { setProduct({ status: 'error', meta: null, image: null, error: e.message, startedAt: 0 }); setToast({ text: e.message, tone: 'error' }) }
      else setProduct((p) => (p.status === 'loading' ? { status: 'idle', meta: null, image: null, error: null, startedAt: 0 } : p))
      return null
    }
  }, [loadObservationList])
  const cancelProduct = useCallback(() => ctl.current.prod?.abort(), [])
  const clearProduct = useCallback(() => { ctl.current.prod?.abort(); setProduct({ status: 'idle', meta: null, image: null, error: null, startedAt: 0 }); engineRef.current?.setProduct(null) }, [])
  const goToObservationEpoch = useCallback(() => { if (product.meta?.mjd_avg) setEpoch(mjdToMs(product.meta.mjd_avg)) }, [product.meta, setEpoch])

  // The Barnard's Star preview: select it, load an available covering observation and show its own epoch.
  const openBarnardPreview = useCallback(async () => {
    const lm = sky.landmarks.find((l) => l.label === BARNARD.name)
    select({ name: BARNARD.name, ra: lm?.ra ?? BARNARD.ra, dec: lm?.dec ?? BARNARD.dec, pmra: lm?.pmra ?? -801.551, pmdec: lm?.pmdec ?? 10362.394, otype: 'BY*', distance_ly: 5.96, landmark: true }, { fly: false })
    const meta = await loadProduct({ ra: BARNARD.ra, dec: BARNARD.dec })
    if (meta?.mjd_avg) setEpoch(mjdToMs(meta.mjd_avg))
  }, [loadProduct, select, setEpoch, sky.landmarks])

  // ---- search
  const parseCoords = (text) => {
    const t = text.trim()
    const parts = t.includes(',') ? t.split(',') : t.split(/\s{2,}|;|\s(?=[+\-−])/)
    if (parts.length === 2) { const ra = parseRA(parts[0]); const dec = parseDec(parts[1].replace('−', '-')); if (ra >= 0 && ra <= 360 && dec >= -90 && dec <= 90) return { ra, dec } }
    const m = t.match(/^(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)$/)
    if (m) { const ra = Number(m[1]); const dec = Number(m[2]); if (ra >= 0 && ra <= 360 && Math.abs(dec) <= 90) return { ra, dec } }
    return null
  }
  const runSearch = useCallback(async (text) => {
    const t = (text ?? search.text).trim()
    if (!t) return
    // Los Palomos: the team name, pigeons or doves all lead to Columba, the Dove
    if (isPigeonQuery(t)) { select({ ...COLUMBA }, { fov: 28 }); setSearch((s) => ({ ...s, text: '', suggestions: [], error: null })); return }
    const coords = parseCoords(t)
    if (coords) { select({ name: `${coords.ra.toFixed(4)}°, ${coords.dec.toFixed(4)}°`, ...coords, point: true }, { fov: 2 }); setSearch((s) => ({ ...s, suggestions: [], error: null })); return }
    setSearch((s) => ({ ...s, busy: true, error: null, suggestions: [] }))
    try {
      const o = await api.resolveName(t)
      select({ name: o.name, ra: o.ra, dec: o.dec, pmra: o.pmra_mas_yr, pmdec: o.pmdec_mas_yr, otype: o.otype, vmag: o.vmag, plx: o.parallax_mas, distance_ly: o.distance_ly }, { fov: 2 })
      setSearch((s) => ({ ...s, busy: false, text: '' }))
    } catch (e) { setSearch((s) => ({ ...s, busy: false, error: e.message })) }
  }, [search.text, select])
  useEffect(() => {
    const t = search.text.trim()
    if (t.length < 2 || !backendUp) { setSearch((s) => (s.suggestions.length ? { ...s, suggestions: [] } : s)); return undefined }
    const c = new AbortController()
    const timer = setTimeout(async () => { try { const r = await api.suggest(t, c.signal); setSearch((s) => (s.text.trim() === t ? { ...s, suggestions: r } : s)) } catch { /* suggestions are optional */ } }, 260)
    return () => { clearTimeout(timer); c.abort() }
  }, [search.text, backendUp])

  const footprints = useMemo(() => observations.rows.filter((o) => o.footprint?.length >= 3).map((o) => ({ id: `${o.obs_id}-${o.detector}`, polygon: o.footprint, active: product.meta && o.obs_id === product.meta.obs_id && o.detector === product.meta.detector })), [observations.rows, product.meta])
  useEffect(() => { engineRef.current?.setFootprints(footprints) }, [footprints])

  return { engineRef, sky, layers, setLayers, projection, setProjection, onView, epochMs, setEpoch, timeRate, setTimeRate, selected, select, clearSelection, flyTo, observations, loadObservationList, product, loadProduct, cancelProduct, clearProduct, goToObservationEpoch, openBarnardPreview, toast, setToast, backendUp, search, setSearch, runSearch }
}
