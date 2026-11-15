// Image <-> sky conversion from the backend's WCS sample mesh (pixel centres on an n x n lattice,
// computed with the file's full SIP WCS). Bilinear in the lattice, accurate to ~0.02 arcsec.
// Image coordinates: (0,0) = top-left CORNER of the top-left pixel, so a pixel centre is (i+0.5, j+0.5).

import { DEG } from './astro.js'

/** Prepare a mesh for fast lookup: RA unwrapped so neighbouring nodes never jump across 0/360. */
export function prepareMesh(mesh) {
  const n = mesh.n
  const ra = Float64Array.from(mesh.ra)
  for (let k = 1; k < ra.length; k++) {
    const ref = k % n ? ra[k - 1] : ra[k - n]
    while (ra[k] - ref > 180) ra[k] -= 360
    while (ra[k] - ref < -180) ra[k] += 360
  }
  return { n, x: mesh.x, y: mesh.y, ra, dec: Float64Array.from(mesh.dec) }
}

function cell(axis, v) {
  const n = axis.length
  let i = 0
  while (i < n - 2 && axis[i + 1] <= v) i++
  return [i, (v - axis[i]) / (axis[i + 1] - axis[i])]
}

/** Sky position (ra, dec in degrees) of image coordinates (x, y). */
export function imageToSky(m, x, y) {
  const [i, fu] = cell(m.x, x - 0.5)
  const [j, fv] = cell(m.y, y - 0.5)
  const k = j * m.n + i
  const bl = (a) => (a[k] * (1 - fu) + a[k + 1] * fu) * (1 - fv) + (a[k + m.n] * (1 - fu) + a[k + m.n + 1] * fu) * fv
  return { ra: (((bl(m.ra)) % 360) + 360) % 360, dec: bl(m.dec) }
}

/** Image coordinates of a sky position (degrees), by Newton iteration on the bilinear lattice. Null if clearly outside. */
export function skyToImage(m, ra, dec) {
  const cx = Math.cos(dec * DEG)
  const wrap = (d) => ((d + 540) % 360) - 180
  const mid = imageToSky(m, (m.x[0] + m.x.at(-1)) / 2 + 0.5, (m.y[0] + m.y.at(-1)) / 2 + 0.5)
  let x = (m.x[0] + m.x.at(-1)) / 2 + 0.5
  let y = (m.y[0] + m.y.at(-1)) / 2 + 0.5
  void mid
  const err = (px, py) => {
    const s = imageToSky(m, px, py)
    return [wrap(s.ra - ra) * cx, s.dec - dec]
  }
  for (let it = 0; it < 30; it++) {
    const [e1, e2] = err(x, y)
    if (Math.hypot(e1, e2) < 1e-11) break
    const h = 0.5
    const [a1, a2] = err(x + h, y)
    const [b1, b2] = err(x, y + h)
    const j11 = (a1 - e1) / h, j21 = (a2 - e2) / h, j12 = (b1 - e1) / h, j22 = (b2 - e2) / h
    const det = j11 * j22 - j12 * j21
    if (Math.abs(det) < 1e-300) return null
    x -= (e1 * j22 - e2 * j12) / det
    y -= (-e1 * j21 + e2 * j11) / det
  }
  const W = m.x.at(-1) + 1
  const H = m.y.at(-1) + 1
  return x >= 0 && y >= 0 && x <= W && y <= H ? { x, y } : null
}
