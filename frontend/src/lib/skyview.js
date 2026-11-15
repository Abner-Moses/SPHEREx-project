// The sky view model: one set of exact, double-precision projections shared by drawing, picking,
// labels and the cursor read-out. Nothing here touches the GPU: the engine projects every object on
// the CPU in float64 and uploads clip-space positions, so accuracy does not degrade when zoomed in
// (a float32 shader working on RA/Dec in radians would lose sub-arcsecond precision).
//
// Conventions: sky seen from INSIDE the celestial sphere, north up, east LEFT (as on a star chart).
// `fov` is the angular size of the screen HEIGHT through the view centre, in degrees.

import { DEG, clamp, wrapPi } from './astro.js'

export const MIN_FOV = 0.001 // degrees (3.6 arcsec across the screen): the zoom ceiling
const MAS_PER_DEG = 3.6e6
const SQRT2 = Math.SQRT2

export const PROJECTIONS = {
  stereographic: { label: 'Stereographic', f: (t) => 2 * Math.tan(t / 2), inv: (r) => 2 * Math.atan(r / 2), maxTheta: 172 * DEG, maxFov: 340 },
  perspective: { label: 'Perspective', f: Math.tan, inv: Math.atan, maxTheta: 87 * DEG, maxFov: 150 },
  orthographic: { label: 'Orthographic', f: Math.sin, inv: (r) => Math.asin(Math.min(1, r)), maxTheta: 90 * DEG, maxFov: 180 },
  equidistant: { label: 'Equidistant', f: (t) => t, inv: (r) => r, maxTheta: 178 * DEG, maxFov: 356 },
  map: { label: 'All-sky map', maxFov: 400 },
}

export const maxFovFor = (projection) => PROJECTIONS[projection]?.maxFov ?? 340

const unit = (lon, lat) => [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)]
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
const toLonLat = (v) => ({ lon: Math.atan2(v[1], v[0]), lat: Math.atan2(v[2], Math.hypot(v[0], v[1])) })

function mollTheta(lat) {
  if (Math.abs(lat) > Math.PI / 2 - 1e-9) return Math.sign(lat) * Math.PI / 2
  const s = Math.PI * Math.sin(lat)
  let t = lat
  for (let i = 0; i < 30; i++) {
    const d = Math.max(2 + 2 * Math.cos(2 * t), 1e-9)
    const step = (2 * t + Math.sin(2 * t) - s) / d
    t -= step
    if (Math.abs(step) < 1e-14) break
  }
  return t
}

/**
 * Build a view. `lon0, lat0` radians (view centre), `fov` degrees, `width, height` CSS pixels.
 * Returns { project(lon, lat) -> {x, y, visible, theta}, unproject(x, y) -> {lon, lat} | null, ... }.
 */
export function makeView({ lon0, lat0, fov, width, height, projection = 'stereographic' }) {
  const cx = width / 2
  const cy = height / 2
  const base = { lon0, lat0, fov, width, height, projection, cx, cy }

  if (projection === 'map') {
    // Mollweide equal-area, centred on lon0; lat0 sets the vertical pan; fov = degrees spanned by the screen height.
    const k = (height / 2) / (SQRT2 * (fov / 180))
    const panY = SQRT2 * Math.sin(mollTheta(lat0))
    return {
      ...base,
      k,
      project(lon, lat) {
        const th = mollTheta(lat)
        const dl = wrapPi(lon - lon0)
        const x = cx - ((2 * SQRT2) / Math.PI) * dl * Math.cos(th) * k
        const y = cy - (SQRT2 * Math.sin(th) - panY) * k
        return { x, y, visible: true, theta: Math.abs(dl) }
      },
      unproject(x, y) {
        const X = -(x - cx) / k
        const Y = -(y - cy) / k + panY
        if (Math.abs(Y) > SQRT2) return null
        const th = Math.asin(Y / SQRT2)
        const c = Math.cos(th)
        if (c < 1e-12) return null
        const dl = (Math.PI * X) / (2 * SQRT2 * c) // X already carries the east-left sign flip
        if (Math.abs(dl) > Math.PI) return null
        return { lon: lon0 + dl, lat: Math.asin(clamp((2 * th + Math.sin(2 * th)) / Math.PI, -1, 1)) }
      },
    }
  }

  const p = PROJECTIONS[projection] ?? PROJECTIONS.stereographic
  const half = Math.min((fov / 2) * DEG, p.maxTheta)
  const k = height / 2 / p.f(half)
  const c = unit(lon0, lat0)
  const east = [-Math.sin(lon0), Math.cos(lon0), 0]
  const north = [-Math.sin(lat0) * Math.cos(lon0), -Math.sin(lat0) * Math.sin(lon0), Math.cos(lat0)]
  return {
    ...base,
    k,
    c,
    east,
    north,
    project(lon, lat) {
      const v = unit(lon, lat)
      const cc = dot(v, c)
      const e = dot(v, east)
      const n = dot(v, north)
      const s = Math.hypot(e, n)
      const theta = Math.atan2(s, cc) // atan2, not acos: exact at arcsecond separations
      if (theta > p.maxTheta) return { x: NaN, y: NaN, visible: false, theta }
      const scale = s > 1e-300 ? p.f(theta) / s : 1
      return { x: cx - k * e * scale, y: cy - k * n * scale, visible: true, theta }
    },
    unproject(x, y) {
      const X = -(x - cx) / k
      const Y = -(y - cy) / k
      const r = Math.hypot(X, Y)
      if (r < 1e-300) return toLonLat(c)
      if (projection === 'orthographic' && r > 1) return null
      const theta = p.inv(r)
      if (!(theta <= p.maxTheta)) return null
      const st = Math.sin(theta)
      const ct = Math.cos(theta)
      const v = [0, 1, 2].map((i) => ct * c[i] + st * ((X / r) * east[i] + (Y / r) * north[i]))
      return toLonLat(v)
    },
  }
}

/** The unit vector of a view's pointer position, or null if off the sky. */
export function skyVector(view, x, y) {
  const s = view.unproject(x, y)
  return s ? unit(s.lon, s.lat) : null
}

/** Rodrigues rotation of vector `v` about unit axis by `angle`. */
function rotate(v, axis, angle) {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  const d = dot(axis, v)
  const x = cross(axis, v)
  return [0, 1, 2].map((i) => v[i] * c + x[i] * s + axis[i] * d * (1 - c))
}

/**
 * Stellarium-style drag: move the view centre so the sky point that was under `from` is now under `to`.
 * Exact for azimuthal projections (rotation covariance); on the flat map it is a pan.
 */
export function dragCenter(view, from, to) {
  if (view.projection === 'map') {
    const anchor = view.unproject(from.x, from.y)
    if (!anchor) return { lon0: view.lon0, lat0: view.lat0 }
    return solveMapCenter(view, anchor, to)
  }
  const anchor = view.unproject(from.x, from.y)
  const vTo = skyVector(view, to.x, to.y)
  const px = Math.hypot(to.x - from.x, to.y - from.y)
  const radPerPx = (view.fov * DEG) / Math.max(1, view.width)
  const exact = anchor && vTo ? solveCenter(view, anchor, to, centerAfterMoving(view, vTo, unit(anchor.lon, anchor.lat))) : null
  // The exact "grab" solve is ill-conditioned near the poles and at the edge of wide views: a few pixels can
  // swing the centre by up to 180° (the sky spins). Accept it only if the centre moved about as far as the
  // pointer did; otherwise pan by the pointer delta directly.
  if (exact) {
    const step = Math.acos(Math.max(-1, Math.min(1, dot(unit(view.lon0, view.lat0), unit(exact.lon0, exact.lat0)))))
    if (step <= px * radPerPx * 3 + 1e-9) return exact
  }
  return panByPixels(view, to.x - from.x, to.y - from.y)
}

/** Fallback pan: pointer delta in pixels -> centre shift, with RA scaled by 1/cos(dec) (capped near the poles). */
export function panByPixels(view, dx, dy) {
  const k = (view.fov * DEG) / Math.max(1, view.width)
  const lat0 = Math.max(-89.99 * DEG, Math.min(89.99 * DEG, view.lat0 + dy * k))
  const lon0 = view.lon0 + (dx * k) / Math.max(0.25, Math.cos(view.lat0))
  return { lon0: ((lon0 % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI), lat0 }
}

/**
 * Refine a centre so that sky point `anchor` projects exactly onto pixel `target`. The rotation
 * estimate is exact up to the roll that a north-up lock removes; a few corrective drags erase it.
 */
function solveCenter(view, anchor, target, start) {
  let { lon0, lat0 } = start
  for (let i = 0; i < 6; i++) {
    const v = makeView({ ...view, lon0, lat0 })
    const p = v.project(anchor.lon, anchor.lat)
    if (!p.visible || Math.hypot(p.x - target.x, p.y - target.y) < 1e-7) break
    const vAt = skyVector(v, target.x, target.y)
    if (!vAt) break
    const moved = centerAfterMoving(v, vAt, unit(anchor.lon, anchor.lat))
    lon0 = moved.lon0
    lat0 = moved.lat0
  }
  return { lon0, lat0 }
}

/** Flat map: pan so that sky point `anchor` lands on `target`. Mollweide is not a translation, so iterate to convergence. */
function solveMapCenter(view, anchor, target) {
  let lon0 = view.lon0
  let lat0 = view.lat0
  for (let i = 0; i < 12; i++) {
    const v = makeView({ ...view, lon0, lat0 })
    const under = v.unproject(target.x, target.y)
    if (!under) break
    const dLon = wrapPi(anchor.lon - under.lon)
    const dLat = anchor.lat - under.lat
    if (Math.abs(dLon) < 1e-13 && Math.abs(dLat) < 1e-13) break
    lon0 += dLon
    lat0 = clamp(lat0 + dLat, -89.9 * DEG, 89.9 * DEG)
  }
  return { lon0, lat0 }
}

/** Rotate the view centre by the minimal rotation taking `vFrom` to `vTo` (so `vFrom`'s sky point lands on `vTo`'s screen spot). */
function centerAfterMoving(view, vFrom, vTo) {
  const axis = cross(vFrom, vTo)
  const len = Math.hypot(axis[0], axis[1], axis[2])
  const c = unit(view.lon0, view.lat0)
  if (len < 1e-15) return { lon0: view.lon0, lat0: view.lat0 }
  const angle = Math.atan2(len, dot(vFrom, vTo))
  const out = toLonLat(rotate(c, axis.map((a) => a / len), angle))
  return { lon0: out.lon, lat0: clamp(out.lat, -89.99 * DEG, 89.99 * DEG) }
}

/** Zoom by `factor` (<1 zooms in) keeping the sky point under pixel (x, y) fixed. Returns { lon0, lat0, fov }. */
export function zoomAbout(view, factor, x, y) {
  const fov = clamp(view.fov * factor, MIN_FOV, maxFovFor(view.projection))
  if (fov === view.fov) return { lon0: view.lon0, lat0: view.lat0, fov }
  const before = view.unproject(x, y)
  const next = makeView({ ...view, fov })
  if (!before) return { lon0: view.lon0, lat0: view.lat0, fov }
  if (view.projection === 'map') return { ...solveMapCenter(next, before, { x, y }), fov }
  const vBefore = unit(before.lon, before.lat)
  const vAfter = skyVector(next, x, y)
  if (!vAfter) return { lon0: view.lon0, lat0: view.lat0, fov }
  return { ...solveCenter(next, before, { x, y }, centerAfterMoving(next, vAfter, vBefore)), fov }
}

// ------------------------------------------------------------------ epoch / proper motion

const J2000_MS = Date.UTC(2000, 0, 1, 12, 0, 0)
export const yearsSinceJ2000 = (ms) => (ms - J2000_MS) / (365.25 * 86400000)
export const mjdToMs = (mjd) => J2000_MS + (mjd - 51544.5) * 86400000

/** Position (radians) of an object at `years` after J2000, from linear proper motion in mas/yr (pmra includes cos dec). */
export function propagate(raDeg, decDeg, pmraMasYr, pmdecMasYr, years) {
  const dec = decDeg * DEG
  if (!years || (!pmraMasYr && !pmdecMasYr)) return { lon: raDeg * DEG, lat: dec }
  const dDec = ((pmdecMasYr || 0) * years) / MAS_PER_DEG
  const dRa = ((pmraMasYr || 0) * years) / MAS_PER_DEG / Math.max(Math.cos(dec), 1e-6)
  return { lon: (raDeg + dRa) * DEG, lat: clamp(dec + dDec * DEG, -Math.PI / 2, Math.PI / 2) }
}

// ------------------------------------------------------------------ grid spacing and labels

const DEC_STEPS = [30, 15, 10, 5, 2, 1, 0.5, 0.25, 10 / 60, 5 / 60, 2 / 60, 1 / 60, 30 / 3600, 10 / 3600, 5 / 3600, 2 / 3600, 1 / 3600, 0.5 / 3600, 0.2 / 3600]
// Right-ascension steps in degrees of RA (1 hour = 15 deg, 1 minute = 0.25 deg, 1 second = 1/240 deg)
const RA_STEPS = [30, 15, 7.5, 5, 2.5, 1.25, 0.5, 0.25, 0.125, 5 / 60, 2.5 / 60, 1 / 60, 0.5 / 60, 0.25 / 60, 1 / 240 / 2, 1 / 240 / 5, 1 / 240 / 10, 1 / 240 / 25]

function pickStep(steps, target) {
  let best = steps[0]
  for (const s of steps) if (s <= target * 1.25) { best = s; break }
  return best
}

/** Grid spacing (degrees) giving roughly 5-8 lines across the screen height. */
export const decStep = (fov) => pickStep(DEC_STEPS, fov / 6)
export const raStep = (fov, decDeg) => pickStep(RA_STEPS, fov / 6 / Math.max(Math.cos(decDeg * DEG), 0.05))

export function fmtRAStep(raDeg, stepDeg) {
  const total = ((raDeg % 360) + 360) % 360 / 15
  let h = Math.floor(total + 1e-9)
  let m = Math.floor((total - h) * 60 + 1e-7)
  let s = ((total - h) * 60 - m) * 60
  if (stepDeg >= 15 - 1e-9) return `${h}h`
  if (stepDeg >= 0.25 - 1e-9) return `${h}h ${String(m).padStart(2, '0')}m`
  s = Math.round(s * 1000) / 1000
  if (s >= 60) { s = 0; m += 1 }
  if (m >= 60) { m = 0; h = (h + 1) % 24 }
  const sd = stepDeg < 1 / 240 - 1e-12 ? (stepDeg < 1 / 240 / 5 ? 2 : 1) : 0
  return `${h}h ${String(m).padStart(2, '0')}m ${s.toFixed(sd).padStart(sd ? 3 + sd : 2, '0')}s`
}

export function fmtDecStep(decDeg, stepDeg) {
  const sign = decDeg < 0 ? '−' : '+'
  const a = Math.abs(decDeg)
  const d = Math.floor(a + 1e-9)
  const mFull = (a - d) * 60
  const m = Math.floor(mFull + 1e-7)
  const s = Math.round(((mFull - m) * 60) * 100) / 100
  if (stepDeg >= 1 - 1e-9) return `${sign}${d}°`
  if (stepDeg >= 1 / 60 - 1e-9) return `${sign}${d}° ${String(m).padStart(2, '0')}′`
  const sd = stepDeg < 1 / 3600 - 1e-12 ? 1 : 0
  return `${sign}${d}° ${String(m).padStart(2, '0')}′ ${s.toFixed(sd).padStart(sd ? 4 : 2, '0')}″`
}

export const fmtFov = (fov) => {
  if (fov >= 10) return `${fov.toFixed(0)}°`
  if (fov >= 1) return `${fov.toFixed(1)}°`
  if (fov >= 1 / 6) return `${(fov * 60).toFixed(0)}′`
  if (fov >= 1 / 60) return `${(fov * 60).toFixed(1)}′`
  return `${(fov * 3600).toFixed(fov * 3600 >= 10 ? 0 : 1)}″`
}

/** Screen-edge-aware visible sky bounds for grid generation: { latMin, latMax, lonMin, lonMax (relative to lon0), allLon }. */
export function visibleBounds(view) {
  const lats = []
  const dlons = []
  const N = 8
  for (let i = 0; i <= N; i++) {
    for (let j = 0; j <= N; j++) {
      const s = view.unproject((i / N) * view.width, (j / N) * view.height)
      if (s) { lats.push(s.lat); dlons.push(wrapPi(s.lon - view.lon0)) }
    }
  }
  if (!lats.length) return { latMin: -90 * DEG, latMax: 90 * DEG, lonMin: -Math.PI, lonMax: Math.PI, allLon: true }
  const latMin = Math.min(...lats)
  const latMax = Math.max(...lats)
  const nearPole = latMax > 84 * DEG || latMin < -84 * DEG
  const wide = Math.max(...dlons) - Math.min(...dlons) > Math.PI * 1.3
  return { latMin, latMax, lonMin: Math.min(...dlons), lonMax: Math.max(...dlons), allLon: nearPole || wide || view.fov > 120 }
}

