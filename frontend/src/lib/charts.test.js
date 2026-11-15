import { test } from 'node:test'
import assert from 'node:assert/strict'
import { contours, lombScargle, science, stars } from '../charts/builders.js'
import { measuredFrames, starCap } from '../science/client.js'

test('cube spectrum keeps blank channels as gaps instead of connecting across them', () => {
  const wavelength_um = Array.from({ length: 102 }, (_, i) => 0.75 + i * 0.04)
  const surface_brightness = wavelength_um.map((_, i) => i === 50 || i === 51 ? null : i + 1)
  const cube = science({ cube_spectrum: { wavelength_um, surface_brightness } }).find((c) => c.id === 'cube')
  assert.equal(cube.group, 'Spectrum')
  assert.equal(cube.title, 'Local cube spectrum (102 channels)')
  for (const series of cube.spec.series) {
    assert.deepEqual(series.x, wavelength_um)
    assert.deepEqual(series.y, surface_brightness)
    assert.deepEqual(series.y.slice(49, 53), [50, null, null, 53])
  }
})

test('measured frame count uses successful photometry, excluding attempts and cube channels', () => {
  assert.equal(measuredFrames(null), 0)
  assert.equal(measuredFrames({ observations: { used: 12 }, cube_spectrum: { surface_brightness: Array(102).fill(1) } }), 0)
  assert.equal(measuredFrames({ observations: { used: 12 }, spectrophotometry: { points: [{ asset_id: 'a' }] }, exploratory: { points: [{ asset_id: 'b' }, { asset_id: 'c' }] } }), 3)
})

test('Shift-capture cap is proportional to frame area, clamped to 2..250', () => {
  assert.equal(starCap(50, 50), 2) // 2,500 px² -> 1, clamped up to 2
  assert.equal(starCap(300, 220), 26) // 66,000 / 2,500 = 26.4
  assert.equal(starCap(500, 500), 100)
  assert.equal(starCap(2000, 1200), 250) // clamped
  assert.equal(starCap(-300, 220), 26) // drag direction does not matter
})

test('Lomb–Scargle recovers a known period from uneven samples', () => {
  const t = [], y = []
  for (let i = 0; i < 60; i++) { const ti = i * 1.7 + Math.sin(i) * 0.6; t.push(ti); y.push(Math.sin((2 * Math.PI * ti) / 9.3)) }
  const ls = lombScargle(t, y, { minP: 2, maxP: 40, n: 2000 })
  assert.ok(Math.abs(ls.best - 9.3) < 0.15, `best ${ls.best}`)
  assert.equal(lombScargle([1, 2, 3], [1, 2, 3]), null) // too few samples
})

test('contours trace a single level around a peak', () => {
  const w = 9, h = 9, values = []
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) values.push(Math.exp(-((i - 4) ** 2 + (j - 4) ** 2) / 4))
  const segs = contours({ w, h, values }, [0.5])
  assert.ok(segs.length >= 8)
  for (const [x0, y0] of segs) assert.ok(Math.abs(Math.hypot(x0 - 4, y0 - 4) - Math.sqrt(4 * Math.log(2))) < 0.35)
})

test('builders never invent charts without inputs', () => {
  assert.deepEqual(science(null), [])
  assert.deepEqual(stars([{ ra: 1, dec: 1, mag: 5 }]), []) // one star: nothing to compare
  const s = stars([{ name: 'a', ra: 10, dec: 10, mag: 5, bv: 0.6, pmra: 5, pmdec: 1, plx: 20 }, { name: 'b', ra: 10.1, dec: 10.05, mag: 7, bv: 1.2, pmra: -3, pmdec: 2, plx: 5 }])
  const ids = s.map((c) => c.id)
  for (const id of ['cmd', 'hr', 'lf', 'vpd', 'quiver', 'plxmag']) assert.ok(ids.includes(id), id)
  assert.ok(!ids.includes('jhk')) // no J/H/K supplied
})

test('science charts label exploratory and saturated inputs', () => {
  const pts = [0, 1, 2, 3, 4].map((i) => ({ wavelength_um: 1 + i * 0.5, flux_jy: 0.2 + i * 0.01, flux_err_jy: 0.01, mjd: 60900 + i * 3, lower_limit: i === 0, quantity: 'aperture_flux_jy' }))
  const charts = science({ exploratory: { points: pts } })
  const sed = charts.find((c) => c.id === 'sed')
  assert.ok(sed && /Exploratory/.test(sed.note))
  assert.deepEqual(sed.spec.series[0].lower, [true, false, false, false, false])
  const ring = science({ exploratory: { points: pts.map((p) => ({ ...p, quantity: 'ring_surface_brightness_mjy_sr' })) } })
  assert.equal(ring.find((c) => c.id === 'sed').spec.y.unit, 'MJy/sr')
  assert.ok(!ring.some((c) => c.id === 'lfl')) // λFλ needs a flux density, not a surface brightness
})
