// HEALPix nested-scheme geometry (Gorski et al. 2005), kept free of three.js so it can be unit-tested.

const TAU = Math.PI * 2
const FACE_ROW = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4]
const FACE_COL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7]

/** Continuous HEALPix (nested) face coordinates -> [lon, lat] radians. u, v in 0..1 across the pixel. */
export function healpixCoord(order, pixel, u, v) {
  const n = 2 ** order
  const face = Math.floor(pixel / (n * n))
  const p = pixel % (n * n)
  let ix = 0
  let iy = 0
  for (let k = 0; k < order; k++) {
    ix |= ((p / 4 ** k) & 1) << k
    iy |= ((p / (2 * 4 ** k)) & 1) << k
  }
  const x = ix + u
  const y = iy + v
  const jr = FACE_ROW[face] * n - x - y
  let nr
  let z
  if (jr < n) { nr = jr; z = 1 - (jr * jr) / (3 * n * n) } else if (jr > 3 * n) { nr = 4 * n - jr; z = -1 + (nr * nr) / (3 * n * n) } else { nr = n; z = ((2 * n - jr) * 2) / (3 * n) }
  const phi = Math.abs(nr) < 1e-12 ? 0 : ((FACE_COL[face] + (x - y) / nr) * Math.PI) / 4
  return [((phi % TAU) + TAU) % TAU, Math.asin(Math.max(-1, Math.min(1, z)))]
}
