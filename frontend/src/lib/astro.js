// Pure astronomy helpers. Frame is ICRS/J2000, angles in degrees unless a name says Rad.
// Constants are the standard J2000 values (IAU galactic pole, mean obliquity); the
// unit tests check them against independent anchor points rather than trusting them.

export const DEG = Math.PI / 180
const TAU = Math.PI * 2

const NGP = { ra: 192.85948 * DEG, dec: 27.12825 * DEG, lNCP: 122.93192 * DEG }
const OBLIQUITY = 23.4392911 * DEG

export const wrapPi = (x) => x - TAU * Math.floor((x + Math.PI) / TAU)
const wrap360 = (d) => ((d % 360) + 360) % 360
export const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x))

export function equatorialToGalactic(raDeg, decDeg) {
  const a = raDeg * DEG
  const d = decDeg * DEG
  const sinB = Math.sin(d) * Math.sin(NGP.dec) + Math.cos(d) * Math.cos(NGP.dec) * Math.cos(a - NGP.ra)
  const y = Math.cos(d) * Math.sin(a - NGP.ra)
  const x = Math.sin(d) * Math.cos(NGP.dec) - Math.cos(d) * Math.sin(NGP.dec) * Math.cos(a - NGP.ra)
  return { l: wrap360((NGP.lNCP - Math.atan2(y, x)) / DEG), b: Math.asin(clamp(sinB, -1, 1)) / DEG }
}

export function galacticToEquatorial(lDeg, bDeg) {
  const L = lDeg * DEG
  const B = bDeg * DEG
  const sinD = Math.sin(B) * Math.sin(NGP.dec) + Math.cos(B) * Math.cos(NGP.dec) * Math.cos(NGP.lNCP - L)
  const y = Math.cos(B) * Math.sin(NGP.lNCP - L)
  const x = Math.sin(B) * Math.cos(NGP.dec) - Math.cos(B) * Math.sin(NGP.dec) * Math.cos(NGP.lNCP - L)
  return { ra: wrap360((NGP.ra + Math.atan2(y, x)) / DEG), dec: Math.asin(clamp(sinD, -1, 1)) / DEG }
}

export function equatorialToEcliptic(raDeg, decDeg) {
  const a = raDeg * DEG
  const d = decDeg * DEG
  const sinB = Math.sin(d) * Math.cos(OBLIQUITY) - Math.cos(d) * Math.sin(OBLIQUITY) * Math.sin(a)
  const lon = Math.atan2(Math.sin(a) * Math.cos(OBLIQUITY) + Math.tan(d) * Math.sin(OBLIQUITY), Math.cos(a))
  return { lon: wrap360(lon / DEG), lat: Math.asin(clamp(sinB, -1, 1)) / DEG }
}

export function eclipticToEquatorial(lonDeg, latDeg) {
  const l = lonDeg * DEG
  const b = latDeg * DEG
  const sinD = Math.sin(b) * Math.cos(OBLIQUITY) + Math.cos(b) * Math.sin(OBLIQUITY) * Math.sin(l)
  const ra = Math.atan2(Math.sin(l) * Math.cos(OBLIQUITY) - Math.tan(b) * Math.sin(OBLIQUITY), Math.cos(l))
  return { ra: wrap360(ra / DEG), dec: Math.asin(clamp(sinD, -1, 1)) / DEG }
}

/** Great-circle separation in degrees (haversine; stable for tiny angles). */
export function separationDeg(ra1, dec1, ra2, dec2) {
  const p1 = dec1 * DEG
  const p2 = dec2 * DEG
  const dp = p2 - p1
  const dl = (ra2 - ra1) * DEG
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) / DEG
}

export function fmtRA(raDeg) {
  const totalSeconds = Math.round((wrap360(raDeg) / 15) * 3600 * 10) / 10
  const wrapped = totalSeconds >= 86400 ? totalSeconds - 86400 : totalSeconds
  const h = Math.floor(wrapped / 3600)
  const m = Math.floor((wrapped - h * 3600) / 60)
  const s = wrapped - h * 3600 - m * 60
  return `${String(h).padStart(2, '0')}h ${String(m).padStart(2, '0')}m ${s.toFixed(1).padStart(4, '0')}s`
}

export function fmtDec(decDeg) {
  const sign = decDeg < 0 ? '−' : '+'
  const total = Math.round(Math.abs(decDeg) * 3600)
  const d = Math.floor(total / 3600)
  const m = Math.floor((total - d * 3600) / 60)
  const s = total - d * 3600 - m * 60
  return `${sign}${String(d).padStart(2, '0')}° ${String(m).padStart(2, '0')}′ ${String(s).padStart(2, '0')}″`
}

const NUMBER = /^[+-]?\d+(?:\.\d+)?$/

function sexagesimalParts(text) {
  const parts = text.replace(/[hdms:°º'"′″]/g, ' ').trim().split(/\s+/).filter(Boolean)
  if (parts.length < 2 || parts.length > 3 || !parts.every((p) => NUMBER.test(p))) return null
  return parts.map(Number)
}

/** Accepts degrees ("83.82"), hours ("5.588h"), or h/m/s ("05h35m17s", "05 35 17", "05:35:17"). Returns degrees or NaN. */
export function parseRA(text) {
  const s = String(text ?? '').trim().toLowerCase()
  if (!s) return NaN
  if (NUMBER.test(s)) return Number(s)
  const hours = s.match(/^([+-]?\d+(?:\.\d+)?)\s*h$/)
  if (hours) return Number(hours[1]) * 15
  const parts = sexagesimalParts(s)
  if (!parts) return NaN
  const [h, m, sec = 0] = parts
  if (h < 0 || m < 0 || m >= 60 || sec < 0 || sec >= 60) return NaN
  return (h + m / 60 + sec / 3600) * 15
}

/** Accepts degrees ("-5.39") or d/m/s ("-05d23m28s", "-05 23 28", "+41:16:09"). Returns degrees or NaN. */
export function parseDec(text) {
  const s = String(text ?? '').trim().toLowerCase()
  if (!s) return NaN
  if (NUMBER.test(s)) return Number(s)
  const negative = s.startsWith('-')
  const parts = sexagesimalParts(s.replace(/^[+-]/, ''))
  if (!parts) return NaN
  const [d, m, sec = 0] = parts
  if (d < 0 || m < 0 || m >= 60 || sec < 0 || sec >= 60) return NaN
  const value = d + m / 60 + sec / 3600
  return negative ? -value : value
}

const GREEK = {
  alf: 'α', bet: 'β', gam: 'γ', del: 'δ', eps: 'ε', zet: 'ζ', eta: 'η', tet: 'θ', iot: 'ι', kap: 'κ',
  lam: 'λ', 'mu.': 'μ', 'nu.': 'ν', ksi: 'ξ', omi: 'ο', 'pi.': 'π', rho: 'ρ', sig: 'σ', tau: 'τ',
  ups: 'υ', phi: 'φ', chi: 'χ', psi: 'ψ', ome: 'ω',
}

/** "* alf CMa" -> "α CMa"; leaves other SIMBAD ids readable (HD/HR/V* prefixes trimmed). */
export function prettyStarName(simbadId) {
  const id = String(simbadId).trim().replace(/^(\*|V\*|NAME)\s+/, '')
  const match = id.match(/^([a-z]{2,3}\.?)(?:\s*(\d+))?\s+([A-Za-z]{3})(?:\s+([A-Z]{1,2}))?$/)
  if (match && GREEK[match[1]]) return `${GREEK[match[1]]}${match[2] ?? ''} ${match[3]}${match[4] ? ` ${match[4]}` : ''}`
  return id.replace(/\s+/g, ' ')
}

/**
 * Drop stars that sit within `thresholdDeg` of a brighter one. SIMBAD lists some multiple systems
 * both combined and per component (e.g. alpha Cen, A and B); drawing all three double-counts them.
 */
export function dedupeCloseStars(stars, thresholdDeg = 0.03) {
  const kept = []
  for (const star of [...stars].sort((a, b) => a.v - b.v)) {
    if (!kept.some((k) => separationDeg(k.ra, k.dec, star.ra, star.dec) < thresholdDeg)) kept.push(star)
  }
  return kept
}

/** Approximate sRGB display colour [0..1]^3 for a B-V index (Ballesteros 2012 temperature + blackbody fit). */
export function bvToRGB(bv) {
  const c = clamp(Number.isFinite(bv) ? bv : 0.6, -0.4, 2.0)
  const kelvin = 4600 * (1 / (0.92 * c + 1.7) + 1 / (0.92 * c + 0.62))
  const t = clamp(kelvin, 1000, 40000) / 100
  const r = t <= 66 ? 255 : 329.698727446 * (t - 60) ** -0.1332047592
  const g = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * (t - 60) ** -0.0755148492
  const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307
  return [clamp(r, 0, 255) / 255, clamp(g, 0, 255) / 255, clamp(b, 0, 255) / 255]
}

const GREEK_NAMES = { α: 'Alpha', β: 'Beta', γ: 'Gamma', δ: 'Delta', ε: 'Epsilon', ζ: 'Zeta', η: 'Eta', θ: 'Theta', ι: 'Iota', κ: 'Kappa', λ: 'Lambda', μ: 'Mu', ν: 'Nu', ξ: 'Xi', ο: 'Omicron', π: 'Pi', ρ: 'Rho', σ: 'Sigma', τ: 'Tau', υ: 'Upsilon', φ: 'Phi', χ: 'Chi', ψ: 'Psi', ω: 'Omega' }
/** Spell out Greek letters for display faces that only have capitals ("α Oph" -> "Alpha Oph"); Α and A look identical there. */
export const spellGreek = (name) => String(name ?? '').replace(/[α-ω]/g, (c) => (GREEK_NAMES[c] ? `${GREEK_NAMES[c]} ` : c)).replace(/\s+/g, ' ').trim()
