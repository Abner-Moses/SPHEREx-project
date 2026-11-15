import { test } from 'node:test'
import assert from 'node:assert/strict'
import { solarSystem } from './planets.js'
import { healpixCoord } from '../engine/hipsMath.js'

const body = (ms, name) => solarSystem(ms).find((b) => b.name === name)

test('the Sun sits on the ecliptic where it should', () => {
  const j2000 = body(Date.UTC(2000, 0, 1, 12), 'Sun') // RA 18h45m, Dec -23.0
  assert.ok(Math.abs(j2000.ra - 281.3) < 0.3, `RA ${j2000.ra}`)
  assert.ok(Math.abs(j2000.dec + 23.03) < 0.1, `Dec ${j2000.dec}`)
  const solstice = body(Date.UTC(2026, 5, 21, 8, 24), 'Sun')
  assert.ok(Math.abs(solstice.dec - 23.44) < 0.05, `Dec ${solstice.dec}`)
  assert.ok(Math.abs(solstice.au - 1.016) < 0.002)
})

test('planets land near published positions', () => {
  // JPL Horizons, 2026-01-01 00:00 UT (geocentric astrometric, rounded): Jupiter RA 113.6 Dec +22.3, Saturn RA 357.0 Dec -4.4
  const t = Date.UTC(2026, 0, 1)
  const ju = body(t, 'Jupiter')
  const sa = body(t, 'Saturn')
  const sep = (a, b, c, d) => Math.hypot((a - c) * Math.cos((b * Math.PI) / 180), b - d)
  assert.ok(sep(ju.ra, ju.dec, 113.6, 22.3) < 1, `Jupiter ${ju.ra} ${ju.dec}`)
  assert.ok(sep(sa.ra, sa.dec, 357.0, -4.4) < 1, `Saturn ${sa.ra} ${sa.dec}`)
})

test('HEALPix cells tile the sphere: order-0 centres and the poles', () => {
  const [lon, lat] = healpixCoord(0, 4, 0.5, 0.5) // equatorial face 4 is centred on (0, 0)
  assert.ok(Math.abs(lat) < 1e-9 && (Math.abs(lon) < 1e-9 || Math.abs(lon - 2 * Math.PI) < 1e-9))
  assert.ok(Math.abs(healpixCoord(0, 0, 1, 1)[1] - Math.PI / 2) < 1e-9) // north polar corner
  assert.ok(Math.abs(healpixCoord(0, 8, 0, 0)[1] + Math.PI / 2) < 1e-9) // south polar corner
  // children subdivide their parent: child 3's far corner is the parent's far corner
  const a = healpixCoord(1, 4 * 5 + 3, 1, 1)
  const b = healpixCoord(0, 5, 1, 1)
  assert.ok(Math.abs(a[0] - b[0]) < 1e-9 && Math.abs(a[1] - b[1]) < 1e-9)
})
