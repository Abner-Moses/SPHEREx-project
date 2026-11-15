// Sun and planets from JPL's approximate Keplerian elements (Standish, valid 1800–2050; Table 1 of
// https://ssd.jpl.nasa.gov/planets/approx_pos.html). Ported from the team's explorer (Rust solar_system).
// Good to roughly an arcminute for the inner planets: fine for finding them on the sky, not for astrometry.
// No light-time, aberration or nutation. Output: J2000 equatorial RA/Dec in degrees, distance in AU.

const DEG = Math.PI / 180
const TAU = Math.PI * 2
// a, a', e, e', I, I', L, L', long.peri, long.peri', long.node, long.node'  (AU, deg, per century)
const ELEMENTS = {
  Mercury: [0.38709927, 0.00000037, 0.20563593, 0.00001906, 7.00497902, -0.00594749, 252.2503235, 149472.67411175, 77.45779628, 0.16047689, 48.33076593, -0.12534081],
  Venus: [0.72333566, 0.0000039, 0.00677672, -0.00004107, 3.39467605, -0.0007889, 181.9790995, 58517.81538729, 131.60246718, 0.00268329, 76.67984255, -0.27769418],
  EM: [1.00000261, 0.00000562, 0.01671123, -0.00004392, -0.00001531, -0.01294668, 100.46457166, 35999.37244981, 102.93768193, 0.32327364, 0, 0],
  Mars: [1.52371034, 0.00001847, 0.0933941, 0.00007882, 1.84969142, -0.00813131, -4.55343205, 19140.30268499, -23.94362959, 0.44441088, 49.55953891, -0.29257343],
  Jupiter: [5.202887, -0.00011607, 0.04838624, -0.00013253, 1.30439695, -0.00183714, 34.39644051, 3034.74612775, 14.72847983, 0.21252668, 100.47390909, 0.20469106],
  Saturn: [9.53667594, -0.0012506, 0.05386179, -0.00050991, 2.48599187, 0.00193609, 49.95424423, 1222.49362201, 92.59887831, -0.41897216, 113.66242448, -0.28867794],
  Uranus: [19.18916464, -0.00196176, 0.04725744, -0.00004397, 0.77263783, -0.00242939, 313.23810451, 428.48202785, 170.9542763, 0.40805281, 74.01692503, 0.04240589],
  Neptune: [30.06992276, 0.00026291, 0.00859048, 0.00005105, 1.77004347, 0.00035372, -55.12002969, 218.45945325, 44.96476227, -0.32241464, 131.78422574, -0.00508664],
}
// representative brightness for drawing only (not shown as measurements), and a colour
export const BODIES = [
  { name: 'Sun', mag: -3, color: [1, 0.93, 0.72], kind: 'Star (the Sun)' },
  { name: 'Mercury', mag: 0, color: [0.82, 0.78, 0.72] },
  { name: 'Venus', mag: -4, color: [1, 0.96, 0.85] },
  { name: 'Mars', mag: 0.5, color: [1, 0.55, 0.35] },
  { name: 'Jupiter', mag: -2.2, color: [1, 0.92, 0.8] },
  { name: 'Saturn', mag: 0.6, color: [0.98, 0.88, 0.66] },
  { name: 'Uranus', mag: 5.7, color: [0.7, 0.9, 0.95] },
  { name: 'Neptune', mag: 7.8, color: [0.55, 0.7, 1] },
]

function helio(e, T) {
  const a = e[0] + e[1] * T
  const ecc = e[2] + e[3] * T
  const I = (e[4] + e[5] * T) * DEG
  const L = (e[6] + e[7] * T) * DEG
  const peri = (e[8] + e[9] * T) * DEG
  const node = (e[10] + e[11] * T) * DEG
  const M = (((L - peri) % TAU) + TAU) % TAU
  let E = M
  for (let k = 0; k < 10; k++) E -= (E - ecc * Math.sin(E) - M) / (1 - ecc * Math.cos(E))
  const x = a * (Math.cos(E) - ecc)
  const y = a * Math.sqrt(1 - ecc * ecc) * Math.sin(E)
  const w = peri - node
  const cw = Math.cos(w), sw = Math.sin(w), cn = Math.cos(node), sn = Math.sin(node), ci = Math.cos(I), si = Math.sin(I)
  return [(cw * cn - sw * sn * ci) * x + (-sw * cn - cw * sn * ci) * y, (cw * sn + sw * cn * ci) * x + (-sw * sn + cw * cn * ci) * y, sw * si * x + cw * si * y]
}

/** Positions of the Sun and planets as seen from Earth at `ms` (Unix milliseconds). */
export function solarSystem(ms) {
  const jd = ms / 86400000 + 2440587.5
  const T = (jd - 2451545) / 36525
  const earth = helio(ELEMENTS.EM, T)
  const ob = 23.4392911 * DEG
  return BODIES.map((b) => {
    const p = b.name === 'Sun' ? [0, 0, 0] : helio(ELEMENTS[b.name], T)
    const x = p[0] - earth[0], y = p[1] - earth[1], z = p[2] - earth[2]
    const d = Math.hypot(x, y, z)
    const v = [x / d, (y * Math.cos(ob) - z * Math.sin(ob)) / d, (y * Math.sin(ob) + z * Math.cos(ob)) / d]
    const ra = ((Math.atan2(v[1], v[0]) / DEG) % 360 + 360) % 360
    return { ...b, ra, dec: Math.asin(Math.max(-1, Math.min(1, v[2]))) / DEG, au: d, planet: b.name !== 'Sun', body: true, otype: b.name === 'Sun' ? 'Sun' : 'SolarPlanet' }
  })
}
