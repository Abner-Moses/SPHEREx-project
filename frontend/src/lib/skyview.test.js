import test from 'node:test'
import assert from 'node:assert/strict'
import { DEG, separationDeg, wrapPi } from './astro.js'
import {
  MIN_FOV, PROJECTIONS, makeView, dragCenter, zoomAbout, propagate, yearsSinceJ2000, mjdToMs,
  decStep, raStep, fmtRAStep, fmtDecStep, fmtFov, visibleBounds, skyVector,
} from './skyview.js'

const near = (a, b, tol, msg = '') => assert.ok(Math.abs(a - b) <= tol, `${msg} expected ${b} got ${a} (tol ${tol})`)
const base = { lon0: 4.7, lat0: 0.08, width: 1280, height: 800 }
const AZ = ['stereographic', 'perspective', 'orthographic', 'equidistant']
const fovFor = (p, f) => Math.min(f, PROJECTIONS[p].maxFov * 0.9)
const sepRad = (a, b) => separationDeg(a.lon / DEG, a.lat / DEG, b.lon / DEG, b.lat / DEG) * DEG

test('project/unproject round-trips to far below a milliarcsecond in every projection at every zoom', () => {
  for (const projection of [...AZ, 'map']) {
    for (const fov of [120, 30, 2, 0.1, 0.005, MIN_FOV]) {
      const view = makeView({ ...base, projection, fov: projection === 'map' ? Math.max(fov, 0.01) : fovFor(projection, fov) })
      for (const [fx, fy] of [[0.5, 0.5], [0.2, 0.3], [0.8, 0.7], [0.05, 0.9]]) {
        const s = view.unproject(fx * 1280, fy * 800)
        if (!s) continue
        const p = view.project(s.lon, s.lat)
        assert.ok(p.visible)
        near(p.x, fx * 1280, 1e-6, `${projection} fov ${fov} x`)
        near(p.y, fy * 800, 1e-6, `${projection} fov ${fov} y`)
      }
    }
  }
})

test('orientation: centre is the screen centre, east is left, north is up', () => {
  for (const projection of [...AZ, 'map']) {
    const view = makeView({ ...base, projection, fov: projection === 'map' ? 90 : 30 })
    const c = view.project(base.lon0, base.lat0)
    near(c.x, 640, 1e-6)
    near(c.y, 400, 1e-6)
    assert.ok(view.project(base.lon0 + 0.02, base.lat0).x < c.x, `${projection}: east should be left`)
    assert.ok(view.project(base.lon0, base.lat0 + 0.02).y < c.y, `${projection}: north should be up`)
  }
})

test('pixel scale is exact at the zoom ceiling: 1 arcsec offsets map to the right number of pixels', () => {
  const fov = 0.01 // degrees across the 800 px height => 36 arcsec / 800 px
  const view = makeView({ ...base, projection: 'stereographic', fov })
  const arcsec = DEG / 3600
  const p = view.project(base.lon0, base.lat0 + arcsec)
  const expectedPx = (800 / (fov * 3600)) * 1 // px per arcsec
  near(400 - p.y, expectedPx, expectedPx * 1e-6, 'north offset')
  const q = view.project(base.lon0 - arcsec / Math.cos(base.lat0), base.lat0) // 1 arcsec of true sky distance to the east
  near(q.x - 640, expectedPx, expectedPx * 1e-6, 'east offset')
})

test('sub-arcsecond precision survives at the maximum zoom', () => {
  const view = makeView({ ...base, projection: 'perspective', fov: MIN_FOV })
  const dRa = 0.3 / 3600 * DEG / Math.cos(base.lat0) // 0.3 arcsec
  const a = view.project(base.lon0 + dRa, base.lat0)
  const b = view.project(base.lon0 + 2 * dRa, base.lat0)
  near(a.x - b.x, 800 / (MIN_FOV * 3600) * 0.3, 1e-3) // 0.3 arcsec => 66.7 px
})

test('points behind the viewer are not visible; wide fields stay finite', () => {
  const view = makeView({ ...base, projection: 'perspective', fov: 100 })
  assert.equal(view.project(base.lon0 + Math.PI, -base.lat0).visible, false)
  const stereo = makeView({ ...base, projection: 'stereographic', fov: 300 })
  assert.equal(stereo.project(base.lon0 + 3.0, 0).visible, true)
  assert.ok(Number.isFinite(stereo.project(base.lon0 + 3.0, 0).x))
})

test('dragging grabs the sky: the point under the pointer stays under it', () => {
  for (const projection of [...AZ, 'map']) {
    for (const fov of [60, 5, 0.2]) {
      const f = projection === 'map' ? Math.max(fov * 3, 20) : fovFor(projection, fov)
      const view = makeView({ ...base, projection, fov: f })
      const from = { x: 700, y: 380 }
      const to = { x: 790, y: 440 }
      const grabbed = view.unproject(from.x, from.y)
      const center = dragCenter(view, from, to)
      const moved = makeView({ ...view, lon0: center.lon0, lat0: center.lat0 })
      const where = moved.project(grabbed.lon, grabbed.lat)
      // a north-up lock removes roll, so the centre is solved iteratively: the grabbed point lands exactly on the pointer
      near(where.x, to.x, 1e-4, `${projection}/${fov} x`)
      near(where.y, to.y, 1e-4, `${projection}/${fov} y`)
    }
  }
})

test('zoom keeps the sky point under the cursor fixed, and respects both limits', () => {
  for (const projection of [...AZ, 'map']) {
    const fov = projection === 'map' ? 90 : 40
    const view = makeView({ ...base, projection, fov })
    const spot = { x: 900, y: 300 }
    const anchor = view.unproject(spot.x, spot.y)
    const z = zoomAbout(view, 0.5, spot.x, spot.y)
    near(z.fov, fov / 2, 1e-9)
    const after = makeView({ ...view, ...{ lon0: z.lon0, lat0: z.lat0, fov: z.fov } })
    const where = after.project(anchor.lon, anchor.lat)
    near(where.x, spot.x, 1e-4, `${projection} x`)
    near(where.y, spot.y, 1e-4, `${projection} y`)
  }
  const deep = makeView({ ...base, projection: 'stereographic', fov: MIN_FOV * 1.2 })
  assert.equal(zoomAbout(deep, 0.001, 600, 400).fov, MIN_FOV, 'cannot zoom past the ceiling')
  assert.equal(zoomAbout(makeView({ ...base, projection: 'perspective', fov: 140 }), 10, 600, 400).fov, 150, 'perspective stops at its limit')
})

test('proper motion: Barnard\'s Star has moved ~4.6 arcmin north since J2000 by August 2026', () => {
  const years = yearsSinceJ2000(mjdToMs(61269.038561386))
  near(years, 26.624, 0.002)
  const p = propagate(269.452077, 4.693365, -801.551, 10362.394, years)
  const moved = sepRad({ lon: 269.452077 * DEG, lat: 4.693365 * DEG }, p) / DEG * 3600
  const north = (10362.394 * years) / 1000 // arcsec, from the proper-motion vector
  const west = (801.551 * years) / 1000 // pmra already includes cos(dec)
  near(moved, Math.hypot(north, west), 0.05, 'arcsec moved') // ~275.9" north + 21.3" west = 276.7"
  assert.ok(p.lat > 4.693365 * DEG)
  assert.deepEqual(propagate(10, 20, 0, 0, 50), { lon: 10 * DEG, lat: 20 * DEG })
})

test('grid steps shrink as you zoom in and labels carry the right precision', () => {
  const fovs = [300, 120, 60, 20, 5, 1, 0.1, 0.01, 0.002]
  const steps = fovs.map(decStep)
  for (let i = 1; i < steps.length; i++) assert.ok(steps[i] <= steps[i - 1])
  assert.ok(steps[0] >= 15 && steps.at(-1) < 1 / 600)
  assert.ok(raStep(60, 70) > raStep(60, 0), 'RA lines are spaced wider near the pole to stay ~equally dense on the sky')
  assert.equal(fmtRAStep(269.45, 15), '17h')
  assert.equal(fmtRAStep(269.5, 0.25), '17h 58m')
  assert.equal(fmtRAStep(269.45208, 1 / 240), '17h 57m 48s') // 48.5 s: 17h 57m 48.499 s
  assert.equal(fmtDecStep(-5.5, 1), '−5°')
  assert.equal(fmtDecStep(4.6934, 1 / 60), '+4° 41′')
  assert.equal(fmtDecStep(4.69336, 1 / 3600), '+4° 41′ 36″')
  assert.equal(fmtFov(60), '60°')
  assert.equal(fmtFov(0.5), '30′')
  assert.equal(fmtFov(0.001), '3.6″')
})

test('visible bounds cover the screen and flag pole-crossing views', () => {
  const normal = visibleBounds(makeView({ ...base, projection: 'stereographic', fov: 10 }))
  assert.equal(normal.allLon, false)
  assert.ok(normal.latMin < base.lat0 && normal.latMax > base.lat0)
  assert.equal(visibleBounds(makeView({ ...base, lat0: 89 * DEG, projection: 'stereographic', fov: 20 })).allLon, true)
  assert.equal(visibleBounds(makeView({ ...base, projection: 'stereographic', fov: 200 })).allLon, true)
})

test('skyVector returns unit vectors', () => {
  const v = skyVector(makeView({ ...base, projection: 'stereographic', fov: 30 }), 500, 300)
  near(Math.hypot(...v), 1, 1e-12)
  assert.equal(skyVector(makeView({ ...base, projection: 'orthographic', fov: 180 }), -5000, 0), null)
  assert.ok(Math.abs(wrapPi(3 * Math.PI)) <= Math.PI + 1e-12)
})
