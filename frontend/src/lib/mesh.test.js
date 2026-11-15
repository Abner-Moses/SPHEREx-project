import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { DEG, separationDeg } from './astro.js'
import { prepareMesh, imageToSky, skyToImage } from './mesh.js'
import { mjdToMs, propagate, yearsSinceJ2000 } from './skyview.js'

// Real metadata the backend produced for Barnard's Star, SPHEREx detector 6, obs 2026W34_1A_0007_4 (SIP WCS).
const meta = JSON.parse(readFileSync(new URL('./fixtures/barnard_d6_meta.json', import.meta.url), 'utf8'))
const mesh = prepareMesh(meta.mesh)
const near = (a, b, tol, msg = '') => assert.ok(Math.abs(a - b) <= tol, `${msg} expected ${b} got ${a} (tol ${tol})`)

test('fixture is the real product', () => {
  assert.equal(meta.detector, 6)
  assert.equal(meta.obs_id, '2026W34_1A_0007_4')
  assert.equal(meta.mesh.n, 65)
  assert.equal(meta.mesh.ra.length, 65 * 65)
})

test('sky -> image reproduces the backend WCS answer for the J2000 target (full SIP, astropy)', () => {
  const p = skyToImage(mesh, meta.target.ra, meta.target.dec)
  near(p.x, meta.target.pixel[0], 0.03, 'x')
  near(p.y, meta.target.pixel[1], 0.03, 'y')
})

test('image -> sky -> image round-trips to a millionth of a pixel across the whole detector', () => {
  for (const [x, y] of [[10, 10], [1020, 1020], [2030, 15], [300, 1900], [1500.37, 640.91]]) {
    const s = imageToSky(mesh, x, y)
    const back = skyToImage(mesh, s.ra, s.dec)
    near(back.x, x, 1e-6)
    near(back.y, y, 1e-6)
  }
})

test('the epoch-corrected Barnard\'s Star lands where the real bright star is (astropy: 648.7, 1476.0)', () => {
  const years = yearsSinceJ2000(mjdToMs(meta.mjd_avg))
  const { lon, lat } = propagate(269.452077, 4.693365, -801.551, 10362.394, years)
  const p = skyToImage(mesh, lon / DEG, lat / DEG)
  near(p.x, 648.7, 0.15, 'x')
  near(p.y, 1476.0, 0.15, 'y')
  // ...and the J2000 position is ~45 px away, which is why the epoch matters
  const j2000 = skyToImage(mesh, 269.452077, 4.693365)
  assert.ok(Math.hypot(p.x - j2000.x, p.y - j2000.y) > 43)
})

test('the footprint corners map to the image corners', () => {
  const [a, b, c, d] = meta.footprint
  const corners = [[0, 0], [2040, 0], [2040, 2040], [0, 2040]].map(([x, y]) => imageToSky(mesh, x, y))
  // mesh covers pixel centres, so the extrapolated corner is within a few arcsec of the backend footprint
  ;[a, b, c, d].forEach((f, i) => assert.ok(separationDeg(f[0], f[1], corners[i].ra, corners[i].dec) < 0.01, `corner ${i}`))
})
