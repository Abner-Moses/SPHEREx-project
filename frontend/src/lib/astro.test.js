import test from 'node:test'
import assert from 'node:assert/strict'
import {
  equatorialToGalactic, galacticToEquatorial, equatorialToEcliptic, eclipticToEquatorial,
  separationDeg, fmtRA, fmtDec, parseRA, parseDec, prettyStarName, dedupeCloseStars, wrapPi,
} from './astro.js'

const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg ?? ''} expected ${b} got ${a} (tol ${tol})`)

test('galactic origin (l=0,b=0) matches the repo validator constant (RA 266.40510, Dec -28.93618)', () => {
  const { ra, dec } = galacticToEquatorial(0, 0)
  near(ra, 266.4051, 0.01, 'ra')
  near(dec, -28.93618, 0.01, 'dec')
})

test('M31 galactic coordinates agree with SIMBAD-derived values (l~121.17, b~-21.57)', () => {
  const { l, b } = equatorialToGalactic(10.684708, 41.26875)
  near(l, 121.17, 0.05, 'l')
  near(b, -21.57, 0.05, 'b')
})

test('galactic <-> equatorial round trip', () => {
  for (const [ra, dec] of [[0, 0], [123.4, -56.7], [359.9, 89], [10, -89.5]]) {
    const g = equatorialToGalactic(ra, dec)
    const back = galacticToEquatorial(g.l, g.b)
    near(separationDeg(ra, dec, back.ra, back.dec), 0, 1e-9, `${ra},${dec}`)
  }
})

test('ecliptic: lambda=90 beta=0 is RA 90, Dec = obliquity; round trip', () => {
  const eq = eclipticToEquatorial(90, 0)
  near(eq.ra, 90, 1e-9)
  near(eq.dec, 23.4392911, 1e-6)
  for (const [ra, dec] of [[10, 5], [200, -40], [300, 60]]) {
    const e = equatorialToEcliptic(ra, dec)
    const back = eclipticToEquatorial(e.lon, e.lat)
    near(separationDeg(ra, dec, back.ra, back.dec), 0, 1e-9)
  }
})

test('separation: pole to pole is 180, tiny angle is stable', () => {
  near(separationDeg(0, 90, 123, -90), 180, 1e-9)
  near(separationDeg(10, 10, 10, 10.0001), 0.0001, 1e-8)
})

test('formatting and parsing agree', () => {
  assert.equal(fmtRA(83.8201), '05h 35m 16.8s')
  assert.equal(fmtDec(-5.3876), '−05° 23′ 15″')
  near(parseRA('05h35m16.8s'), 83.82, 1e-6)
  near(parseRA('05 35 16.8'), 83.82, 1e-6)
  near(parseRA('5.5h'), 82.5, 1e-9)
  near(parseRA('269.452'), 269.452, 1e-12)
  near(parseDec('-05d23m15s'), -5.3875, 1e-6)
  near(parseDec('+41:16:08'), 41.26888889, 1e-6)
  near(parseDec('-00 30 00'), -0.5, 1e-9)
  assert.ok(Number.isNaN(parseRA('abc')))
  assert.ok(Number.isNaN(parseDec('12 75 00')))
  assert.equal(fmtRA(359.99999), '00h 00m 00.0s')
})

test('star names', () => {
  assert.equal(prettyStarName('* alf CMa'), 'α CMa')
  assert.equal(prettyStarName('* bet01 Cyg'), 'β01 Cyg')
  assert.equal(prettyStarName('HD 12345'), 'HD 12345')
  assert.equal(prettyStarName('* alf Cen A'), 'α Cen A')
})

test('dedupe keeps the brightest of a close group and leaves distant stars alone', () => {
  const stars = [
    { name: 'alf Cen', ra: 219.9, dec: -60.83, v: -0.27 },
    { name: 'alf Cen A', ra: 219.9005, dec: -60.8340, v: -0.01 },
    { name: 'alf Cen B', ra: 219.8961, dec: -60.8395, v: 1.33 },
    { name: 'Sirius', ra: 101.287, dec: -16.716, v: -1.46 },
  ]
  const out = dedupeCloseStars(stars, 0.03).map((s) => s.name)
  assert.deepEqual(out, ['Sirius', 'alf Cen'])
})
