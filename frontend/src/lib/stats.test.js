import test from 'node:test'
import assert from 'node:assert/strict'
import { histogram, summarize, apertureStats, radialProfile, lineProfile, downsample } from './stats.js'
import { buildLut } from './colormaps.js'

const make = (w, h, fn) => {
  const g = new Uint8Array(w * h)
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) g[y * w + x] = fn(x, y)
  return g
}

test('histogram + summary of a known image', () => {
  const g = new Uint8Array([0, 0, 10, 10, 10, 20, 20, 255])
  const s = summarize(histogram(g))
  assert.equal(s.n, 8)
  assert.equal(s.min, 0)
  assert.equal(s.max, 255)
  assert.equal(s.median, 10)
  assert.ok(Math.abs(s.mean - 325 / 8) < 1e-9)
})

test('aperture stats on a flat plate with one bright pixel', () => {
  const g = make(21, 21, (x, y) => (x === 10 && y === 10 ? 200 : 50))
  const a = apertureStats(g, 21, 21, 10.5, 10.5, 3)
  assert.equal(a.max, 200)
  assert.equal(a.peakX, 10)
  assert.equal(a.peakY, 10)
  assert.equal(a.min, 50)
  assert.ok(a.n >= 24 && a.n <= 36, `circle of r=3 has ~28 px, got ${a.n}`)
})

test('radial profile of a linear radial ramp is monotonic', () => {
  const g = make(101, 101, (x, y) => Math.min(255, Math.round(Math.hypot(x + 0.5 - 50.5, y + 0.5 - 50.5) * 4)))
  const p = radialProfile(g, 101, 101, 50.5, 50.5, 40, 2)
  for (let i = 1; i < p.length; i++) assert.ok(p[i].mean >= p[i - 1].mean, 'monotonic')
  assert.ok(Math.abs(p[10].mean - p[10].r * 4) < 3)
})

test('line profile interpolates along a gradient', () => {
  const g = make(100, 4, (x) => x * 2)
  const p = lineProfile(g, 100, 4, 10.5, 2, 60.5, 2, 6)
  assert.equal(p.length, 6)
  assert.ok(Math.abs(p[0].v - 20) < 1e-9)
  assert.ok(Math.abs(p[5].v - 120) < 1e-9)
  assert.ok(Math.abs(p[5].d - 50) < 1e-9)
})

test('downsample averages blocks and normalises to 0..1', () => {
  const g = make(8, 8, (x, y) => (x < 4 ? 0 : 255))
  const d = downsample(g, 8, 8, { x: 0, y: 0, w: 8, h: 8 }, 2, 2)
  assert.deepEqual(Array.from(d), [0, 1, 0, 1])
})

test('colormap LUT spans first to last stop and is opaque', () => {
  const lut = buildLut('gray')
  assert.deepEqual(Array.from(lut.slice(0, 4)), [0, 0, 0, 255])
  assert.deepEqual(Array.from(lut.slice(255 * 4, 256 * 4)), [255, 255, 255, 255])
  for (const id of ['ember', 'glacier', 'aurora']) assert.equal(buildLut(id).length, 1024)
})
