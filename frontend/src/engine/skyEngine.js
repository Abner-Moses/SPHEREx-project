// Stellarium-style sky dome. Every object is projected on the CPU in float64 (lib/skyview.js) and
// only clip-space positions reach the GPU, so precision holds at the zoom ceiling (3.6 arcsec of sky
// across the screen). Stars move with proper motion to the chosen epoch. The SPHEREx image is laid
// on the sky through the file's own WCS sample mesh (accurate to ~0.02 arcsec) by an adaptively
// subdivided mesh, so deep zooms never push far-off-screen vertices through float32.

import * as THREE from 'three'
import {
  DEG, bvToRGB, eclipticToEquatorial, equatorialToEcliptic, equatorialToGalactic, galacticToEquatorial, prettyStarName,
} from '../lib/astro.js'
import {
  MIN_FOV, dragCenter, fmtDecStep, fmtRAStep, makeView, maxFovFor, propagate, raStep, decStep, visibleBounds, yearsSinceJ2000, zoomAbout,
} from '../lib/skyview.js'
import { buildLut } from '../lib/colormaps.js'
import { solarSystem } from '../lib/planets.js'
import { HipsLayer } from './hips.js'

// Sky content follows the team's Stellarium-style explorer (spherex-code): 41k HYG stars drawn with a two-Gaussian
// point-spread profile and B−V colour, DSS / 2MASS survey imagery underneath, d3-celestial constellations, and the
// Sun and planets from JPL mean elements. Positions still go through this engine's float64 projection.
const MAX_SEG = 90000
const STAR_CAP = 46000
const KIND = { landmark: 0, 'probe-ok': 1, 'probe-pending': 2, selected: 3, candidate: 4, pin: 5 }
const COLORS = {
  // Endurance Deck roles: blue = selectable, amber = focus, red = alert, cream = neutral
  landmark: [0.29, 0.64, 1], 'probe-ok': [0.29, 0.64, 1], 'probe-mock': [0.61, 0.58, 0.52], 'probe-pending': [0.95, 0.72, 0.02],
  'probe-error': [0.88, 0.22, 0.23], selected: [0.95, 0.72, 0.02], candidate: [0.9, 0.87, 0.8], pin: [0.95, 0.72, 0.02],
}
const STRETCH = { linear: 0, sqrt: 1, log: 2, asinh: 3 }

const pointMaterial = () => new THREE.ShaderMaterial({
  uniforms: { uPx: { value: 1 }, uMaxPoint: { value: 64 }, uTime: { value: 0 } },
  transparent: true, depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending,
  vertexShader: `attribute float aSize; attribute vec3 aColor; attribute float aAlpha; uniform float uPx, uMaxPoint;
    varying vec3 vColor; varying float vAlpha;
    void main(){ gl_Position = vec4(position.xy, 0.0, 1.0); gl_PointSize = min(aSize*uPx, uMaxPoint); vColor = aColor; vAlpha = aAlpha; }`,
  // a sharp core plus a faint wide wing, like a real telescope's point-spread function; aAlpha carries brightness
  fragmentShader: `varying vec3 vColor; varying float vAlpha;
    void main(){ float r = length(gl_PointCoord - 0.5)*2.0; if(r > 1.0) discard;
      float psf = exp(-r*r*13.0) + 0.19*exp(-r*r*3.8); gl_FragColor = vec4(vColor*min(vAlpha, 1.8), psf*min(1.0, vAlpha)); }`,
})

const markerMaterial = () => new THREE.ShaderMaterial({
  uniforms: { uPx: { value: 1 }, uMaxPoint: { value: 64 }, uTime: { value: 0 } },
  transparent: true, depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending,
  vertexShader: `attribute float aKind; attribute vec3 aColor; attribute float aSeed; uniform float uPx, uMaxPoint;
    varying float vKind; varying vec3 vColor; varying float vSeed;
    void main(){ float size = 22.0; if(aKind > 0.5) size = 38.0; if(aKind > 2.5) size = 76.0; if(aKind > 3.5) size = 52.0; if(aKind > 4.5) size = 26.0;
      gl_Position = vec4(position.xy, 0.0, 1.0); gl_PointSize = min(size*uPx, uMaxPoint); vKind = aKind; vColor = aColor; vSeed = aSeed; }`,
  fragmentShader: `uniform float uTime; varying float vKind; varying vec3 vColor; varying float vSeed;
    float ring(float r, float c, float w){ return 1.0 - smoothstep(0.0, w, abs(r - c)); }
    float dotm(float r, float s){ return 1.0 - smoothstep(s*0.6, s, r); }
    void main(){ vec2 c = (gl_PointCoord - 0.5)*2.0; float r = length(c); float ang = atan(c.y, c.x); float t = uTime + vSeed*6.2831; float a = 0.0;
      if(vKind < 0.5){ a = 0.75*ring(r, 0.5, 0.07) + 0.9*dotm(r, 0.1); }
      else if(vKind < 1.5){ a = ring(r, 0.4, 0.07) + dotm(r, 0.12); float ph = fract(t*0.33); a += (1.0 - ph)*0.9*ring(r, 0.4 + 0.55*ph, 0.05); }
      else if(vKind < 2.5){ float arc = smoothstep(-0.2, 1.0, sin(ang - t*4.5)); a = ring(r, 0.5, 0.09)*(0.15 + 0.85*arc) + 0.5*dotm(r, 0.1); }
      else if(vKind < 3.5){ float rot = 0.0; a =ring(r, 0.8, 0.05)*pow(abs(cos(2.0*(ang - rot))), 10.0) + 0.55*ring(r, 0.47, 0.025) + dotm(r, 0.07); }
      else if(vKind < 4.5){ float dash = step(0.0, sin(ang*12.0)); a = ring(r, 0.5, 0.05)*(0.3 + 0.7*dash) + 0.6*(1.0 - smoothstep(0.0, 0.035, min(abs(c.x), abs(c.y))))*step(r, 0.3)*step(0.06, r); }
      else { a = 0.9*ring(r, 0.55, 0.06) + 0.9*(1.0 - smoothstep(0.0, 0.05, abs(abs(c.x) - abs(c.y))))*step(r, 0.5)*step(0.2, r); }
      if(r > 1.0) discard; gl_FragColor = vec4(vColor, clamp(a, 0.0, 1.0)); }`,
})

const lineMaterial = (color, opacity) => new THREE.ShaderMaterial({
  uniforms: { uColor: { value: new THREE.Color(color) }, uOpacity: { value: opacity } },
  transparent: true, depthTest: false, depthWrite: false,
  vertexShader: 'void main(){ gl_Position = vec4(position.xy, 0.0, 1.0); }',
  fragmentShader: 'uniform vec3 uColor; uniform float uOpacity; void main(){ gl_FragColor = vec4(uColor, uOpacity); }',
})

const overlayMaterial = (tex, lut, mask) => new THREE.ShaderMaterial({
  uniforms: {
    uTex: { value: tex }, uLut: { value: lut }, uMask: { value: mask }, uBlack: { value: 0 }, uWhite: { value: 255 }, uStretch: { value: 0 },
    uInvert: { value: 0 }, uOpacity: { value: 1 }, uShowMask: { value: 0 }, uSize: { value: new THREE.Vector2(1, 1) }, uGrid: { value: 0 },
  },
  transparent: true, depthTest: false, depthWrite: false,
  vertexShader: 'attribute vec2 aUv; varying vec2 vUv; void main(){ vUv = aUv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
  fragmentShader: `uniform sampler2D uTex, uLut, uMask; uniform float uBlack, uWhite, uStretch, uInvert, uOpacity, uShowMask, uGrid; uniform vec2 uSize; varying vec2 vUv;
    float st(float x){ if(uStretch < 0.5) return x; if(uStretch < 1.5) return sqrt(x); if(uStretch < 2.5) return log(1.0 + 1000.0*x)/log(1001.0); return log(10.0*x + sqrt(100.0*x*x + 1.0))/log(10.0 + sqrt(101.0)); }
    void main(){ float raw = texture2D(uTex, vUv).r*255.0; float x = clamp((raw - uBlack)/max(uWhite - uBlack, 1.0), 0.0, 1.0); float y = st(x); if(uInvert > 0.5) y = 1.0 - y;
      vec3 col = texture2D(uLut, vec2(y*0.99609375 + 0.001953125, 0.5)).rgb;
      if(uShowMask > 0.5){ float m = texture2D(uMask, vUv).r; col = mix(col, vec3(1.0, 0.2, 0.7), m*0.6); }
      if(uGrid > 0.0){ vec2 p = vUv*uSize; vec2 g = abs(fract(p - 0.5) - 0.5)/fwidth(p); float line = 1.0 - min(min(g.x, g.y), 1.0); col = mix(col, vec3(0.02, 0.03, 0.06), line*0.5*uGrid); }
      gl_FragColor = vec4(col, uOpacity); }`,
})

/** Point `t` of the way along the great circle between two view centres (radians). */
function slerpLonLat(a, b, t) {
  const va = [Math.cos(a.lat0) * Math.cos(a.lon0), Math.cos(a.lat0) * Math.sin(a.lon0), Math.sin(a.lat0)]
  const vb = [Math.cos(b.lat0) * Math.cos(b.lon0), Math.cos(b.lat0) * Math.sin(b.lon0), Math.sin(b.lat0)]
  const omega = Math.acos(Math.max(-1, Math.min(1, va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2])))
  if (omega < 1e-9) return { lon0: b.lon0, lat0: b.lat0 }
  const sa = Math.sin((1 - t) * omega) / Math.sin(omega)
  const sb = Math.sin(t * omega) / Math.sin(omega)
  const v = [0, 1, 2].map((i) => sa * va[i] + sb * vb[i])
  return { lon0: Math.atan2(v[1], v[0]), lat0: Math.atan2(v[2], Math.hypot(v[0], v[1])) }
}

const hash01 = (i) => { const x = Math.sin(i * 12.9898 + 78.233) * 43758.5453; return x - Math.floor(x) }

export class SkyEngine {
  constructor(canvas, labelRoot, handlers = {}) {
    this.canvas = canvas
    this.labelRoot = labelRoot
    this.handlers = handlers
    this.reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' })
    this.renderer.setClearColor(0x000000, 0)
    const gl = this.renderer.getContext()
    this.maxPoint = gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)?.[1] ?? 64
    this.scene = new THREE.Scene()
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)

    this.state = { lon0: 4.7, lat0: 0.08, fov: 110, projection: 'stereographic' }
    // Motion model (see tick): programmatic moves are fixed-duration ease-in-out; wheel zoom is a critically
    // damped spring on log(fov) that starts from rest; dragging tracks the pointer 1:1 with no coasting.
    this.anim = null
    this.zoom = { target: null, v: 0 }
    this.layers = { stars: true, deep: true, equGrid: false, galGrid: false, ecliptic: true, galPlane: true, labels: true, footprints: true, overlay: true, mask: false, markers: true, survey: 'infrared', surveyOpacity: 0.7, constellations: true, planets: true }
    this.cat = null // HYG catalogue (setCatalogue); until it arrives the bright SIMBAD list is drawn
    this.catRefs = []
    this.cons = null
    this.bodies = []
    this.bodiesAt = -Infinity
    this.look = { black: 0, white: 255, stretch: 'asinh', invert: false, cmap: 'ember', opacity: 1, interpolate: false }
    this.epochMs = Date.now()
    this.timeRate = 0
    this.time = 0
    this.dirty = true
    this.active = true
    this.dpr = Math.min(window.devicePixelRatio || 1, 2)
    this.bright = []
    this.deep = []
    this.markers = []
    this.footprints = []
    this.product = null
    this.pointers = new Map()
    this.pinch = null
    this.labelPool = []
    // Pick list rebuilt each drawn frame without allocating: parallel arrays + the source object, resolved on demand.
    this.pick = { x: new Float32Array(STAR_CAP + 1024), y: new Float32Array(STAR_CAP + 1024), lon: new Float64Array(STAR_CAP + 1024), lat: new Float64Array(STAR_CAP + 1024), marker: new Uint8Array(STAR_CAP + 1024), ref: new Array(STAR_CAP + 1024), n: 0 }

    const mk = (cap, material, Ctor) => {
      const g = new THREE.BufferGeometry()
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(cap * 3), 3).setUsage(THREE.DynamicDrawUsage))
      const obj = new Ctor(g, material)
      obj.frustumCulled = false
      g.setDrawRange(0, 0)
      this.scene.add(obj)
      return obj
    }
    const addAttr = (obj, name, size, cap) => obj.geometry.setAttribute(name, new THREE.BufferAttribute(new Float32Array(cap * size), size).setUsage(THREE.DynamicDrawUsage))

    this.overlayMesh = mk(40000, new THREE.MeshBasicMaterial(), THREE.Mesh)
    addAttr(this.overlayMesh, 'aUv', 2, 40000)
    this.overlayMesh.geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(60000), 1).setUsage(THREE.DynamicDrawUsage))
    this.overlayMesh.renderOrder = 1
    this.lutTex = new THREE.DataTexture(buildLut('ember'), 256, 1, THREE.RGBAFormat, THREE.UnsignedByteType)
    this.lutTex.minFilter = this.lutTex.magFilter = THREE.LinearFilter
    this.lutTex.needsUpdate = true
    this.blank = new THREE.DataTexture(new Uint8Array([0]), 1, 1, THREE.RedFormat, THREE.UnsignedByteType)
    this.blank.needsUpdate = true

    this.lines = {
      equ: mk(MAX_SEG, lineMaterial(0x4aa3ff, 0.2), THREE.LineSegments),
      gal: mk(MAX_SEG, lineMaterial(0x9b9484, 0.24), THREE.LineSegments),
      ecl: mk(8000, lineMaterial(0xb08a3a, 0.65), THREE.LineSegments),
      plane: mk(8000, lineMaterial(0x9b9484, 0.45), THREE.LineSegments),
      foot: mk(MAX_SEG, lineMaterial(0x4aa3ff, 0.24), THREE.LineSegments),
      footActive: mk(2000, lineMaterial(0xf2b705, 0.95), THREE.LineSegments),
      trail: mk(4000, lineMaterial(0xf2b705, 0.85), THREE.LineSegments), // selected star's motion history
      cons: mk(20000, lineMaterial(0x8fb4c8, 0.42), THREE.LineSegments),
    }
    Object.values(this.lines).forEach((l, i) => { l.renderOrder = 2 + (i % 3) })
    this.hips = new HipsLayer(this.scene, 0, () => { this.dirty = true })
    this.starPts = mk(STAR_CAP, pointMaterial(), THREE.Points)
    addAttr(this.starPts, 'aSize', 1, STAR_CAP); addAttr(this.starPts, 'aColor', 3, STAR_CAP); addAttr(this.starPts, 'aAlpha', 1, STAR_CAP)
    this.starPts.renderOrder = 6
    this.markerPts = mk(512, markerMaterial(), THREE.Points)
    addAttr(this.markerPts, 'aKind', 1, 512); addAttr(this.markerPts, 'aColor', 3, 512); addAttr(this.markerPts, 'aSeed', 1, 512)
    this.markerPts.renderOrder = 7

    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(canvas.parentElement ?? canvas)
    canvas.style.touchAction = 'none'
    this.bind()
    this.resize()
    this.last = performance.now()
    this.lastEmit = 0
    this.raf = requestAnimationFrame((t) => this.tick(t))
  }

  // ------------------------------------------------------------------ data
  setBrightStars(stars) { this.bright = stars.map((s, i) => ({ ...s, color: bvToRGB(s.bv), seed: hash01(i + 1), mag: s.v })); this.dirty = true }
  setDeepStars(list) {
    this.deep = (list ?? []).map((o) => ({ name: o.name, ra: o.ra, dec: o.dec, mag: o.vmag ?? 15, pmra: o.pmra_mas_yr, pmdec: o.pmdec_mas_yr, otype: o.otype, deep: true }))
    this.dirty = true
  }
  /** The HYG catalogue in column form (public/data/hyg.json), brightest first. */
  setCatalogue(h) {
    if (!h) { this.cat = null; this.dirty = true; return }
    const n = h.count
    const ux = new Float32Array(n), uy = new Float32Array(n), uz = new Float32Array(n), rgb = new Float32Array(n * 3)
    for (let i = 0; i < n; i++) {
      const a = h.ra[i] * DEG, b = h.dec[i] * DEG
      ux[i] = Math.cos(b) * Math.cos(a); uy[i] = Math.cos(b) * Math.sin(a); uz[i] = Math.sin(b)
      const c = bvToRGB(h.bv[i]); rgb[i * 3] = c[0]; rgb[i * 3 + 1] = c[1]; rgb[i * 3 + 2] = c[2]
    }
    this.cat = { ...h, n, ux, uy, uz, rgb }
    this.catRefs = new Array(n)
    this.dirty = true
  }
  /** One catalogue star as the plain object the rest of the app expects (built on demand, then cached). */
  catStar(i) {
    const c = this.cat
    let r = this.catRefs[i]
    if (!r) {
      const pc = c.dist[i]
      r = this.catRefs[i] = { name: c.names[i] ?? (c.hip[i] ? `HIP ${c.hip[i]}` : `HYG ${i}`), ra: c.ra[i], dec: c.dec[i], mag: c.mag[i], v: c.mag[i], bv: c.bv[i], pmra: c.pmra[i], pmdec: c.pmdec[i], plx: pc > 0 ? 1000 / pc : undefined, distance_ly: pc > 0 ? Math.round(pc * 326.156) / 100 : undefined, hip: c.hip[i] || undefined, otype: '*', catalogue: 'HYG v4.1' }
    }
    return r
  }
  setConstellations(d) {
    if (!d) { this.cons = null; this.dirty = true; return }
    // split long segments so the lines follow great circles under every projection
    const lines = d.lines.map((line) => {
      const out = []
      for (let i = 0; i < line.length; i++) {
        if (i === 0) { out.push([line[0][0] * DEG, line[0][1] * DEG]); continue }
        const [a0, b0] = line[i - 1], [a1, b1] = line[i]
        const da = ((a1 - a0 + 540) % 360) - 180
        const steps = Math.max(1, Math.ceil(Math.hypot(da, b1 - b0) / 2))
        for (let k = 1; k <= steps; k++) out.push([(a0 + (da * k) / steps) * DEG, (b0 + ((b1 - b0) * k) / steps) * DEG])
      }
      return out
    })
    this.cons = { lines, names: d.names }
    this.dirty = true
  }
  setMarkers(markers) { this.markers = markers; this.dirty = true }
  setFootprints(list) { this.footprints = list ?? []; this.dirty = true }
  setLayers(patch) { this.layers = { ...this.layers, ...patch }; this.dirty = true }
  setProjection(name) {
    this.state.projection = name
    this.state.fov = Math.min(this.state.fov, maxFovFor(name))
    if (name === 'map') this.state.fov = Math.max(this.state.fov, 150)
    this.dirty = true
  }
  setEpoch(ms) { this.epochMs = ms; this.dirty = true }
  setTimeRate(r) { this.timeRate = r }
  setActive(a) { this.active = a; if (a) { this.last = performance.now(); this.dirty = true } }

  setProduct(product, image) {
    const mat = this.overlayMesh.material
    if (mat.uniforms?.uTex) { mat.uniforms.uTex.value.dispose?.(); mat.uniforms.uMask.value.dispose?.() }
    this.product = null
    if (product && image) {
      const tex = new THREE.DataTexture(image.gray, image.width, image.height, THREE.RedFormat, THREE.UnsignedByteType)
      tex.unpackAlignment = 1
      tex.generateMipmaps = true
      tex.minFilter = THREE.LinearMipmapLinearFilter
      tex.magFilter = THREE.NearestFilter
      tex.needsUpdate = true
      this.overlayMesh.material = overlayMaterial(tex, this.lutTex, this.blank)
      this.overlayMesh.material.uniforms.uSize.value.set(image.width, image.height)
      const m = product.mesh
      const n = m.n
      const ra = m.ra.slice()
      for (let j = 0; j < n; j++) {
        for (let i = 0; i < n; i++) {
          const k = j * n + i
          const ref = i > 0 ? ra[k - 1] : j > 0 ? ra[k - n] : ra[0]
          if (k > 0) { while (ra[k] - ref > 180) ra[k] -= 360; while (ra[k] - ref < -180) ra[k] += 360 }
        }
      }
      const uv = new Float32Array(n * n * 2)
      for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) { uv[(j * n + i) * 2] = (m.x[i] + 0.5) / image.width; uv[(j * n + i) * 2 + 1] = (m.y[j] + 0.5) / image.height }
      this.product = { ...product, image, raU: Float64Array.from(ra, (v) => v * DEG), decR: Float64Array.from(m.dec, (v) => v * DEG), n, W: image.width, H: image.height, nodes: new Float64Array(n * n * 3), uv }
      this.applyLook()
    }
    this.dirty = true
  }

  setMaskImage(maskImage) {
    if (!this.product || !maskImage) return
    const tex = new THREE.DataTexture(maskImage.gray, maskImage.width, maskImage.height, THREE.RedFormat, THREE.UnsignedByteType)
    tex.unpackAlignment = 1
    tex.minFilter = tex.magFilter = THREE.NearestFilter
    tex.needsUpdate = true
    this.overlayMesh.material.uniforms.uMask.value = tex
    this.dirty = true
  }

  setLook(patch) { this.look = { ...this.look, ...patch }; this.applyLook() }
  applyLook() {
    const u = this.overlayMesh.material.uniforms
    if (!u?.uBlack) return
    const l = this.look
    u.uBlack.value = l.black; u.uWhite.value = l.white; u.uStretch.value = STRETCH[l.stretch] ?? 0; u.uInvert.value = l.invert ? 1 : 0; u.uOpacity.value = l.opacity
    u.uShowMask.value = this.layers.mask ? 1 : 0
    this.lutTex.image.data.set(buildLut(l.cmap)); this.lutTex.needsUpdate = true
    const tex = u.uTex.value
    const mag = l.interpolate ? THREE.LinearFilter : THREE.NearestFilter
    if (tex.magFilter !== mag) { tex.magFilter = mag; tex.needsUpdate = true }
    this.dirty = true
  }

  // ------------------------------------------------------------------ view control
  getView() { const w = this.canvas.clientWidth || 1; const h = this.canvas.clientHeight || 1; return makeView({ ...this.state, width: w, height: h }) }
  getState() { return { ...this.state, epochMs: this.epochMs } }
  /** Fixed-duration (1 s) ease-in-out move: the centre travels the great circle while log(fov) interpolates, in lock-step. */
  flyTo(raDeg, decDeg, fov, seconds = 1) {
    const s = this.state
    const to = { lon0: raDeg * DEG, lat0: decDeg * DEG, fov: Math.max(MIN_FOV, Math.min(fov ?? s.fov, maxFovFor(s.projection))) }
    this.zoom.target = null
    if (this.reduced) { Object.assign(s, to); this.anim = null; this.dirty = true; return }
    this.anim = { t: 0, dur: seconds, from: { lon0: s.lon0, lat0: s.lat0, fov: s.fov }, to }
  }
  /** Eased zoom by a factor (the same spring as the mouse wheel). */
  zoomBy(f) { const base = this.zoom.target ?? this.state.fov; this.anim = null; this.zoom.target = Math.max(MIN_FOV, Math.min(maxFovFor(this.state.projection), base * f)) }
  /** Eased zoom to a field of view, keeping the centre. */
  zoomTo(fov) { this.anim = null; this.zoom.target = Math.max(MIN_FOV, Math.min(maxFovFor(this.state.projection), fov)) }
  /** Nudge the view by a fraction of the screen (arrow keys, pan buttons): a short eased move. */
  panBy(fx, fy) {
    const w = this.canvas.clientWidth || 1, h = this.canvas.clientHeight || 1
    const to = this.skyAt(w / 2 + fx * w, h / 2 + fy * h)
    if (to) this.flyTo(to.ra, to.dec, this.zoom.target ?? this.state.fov, 0.3)
  }
  /** Horizontal x vertical sky coverage of the screen, in degrees. */
  coverage() {
    const v = this.getView(), w = this.canvas.clientWidth || 1, h = this.canvas.clientHeight || 1
    const sep = (a, b) => { if (!a || !b) return null; const r = Math.PI / 180; const c = Math.sin(a.dec * r) * Math.sin(b.dec * r) + Math.cos(a.dec * r) * Math.cos(b.dec * r) * Math.cos((a.ra - b.ra) * r); return Math.acos(Math.max(-1, Math.min(1, c))) / r }
    const hz = sep(this.skyAt(0, h / 2), this.skyAt(w / 2, h / 2)) ?? this.state.fov / 2
    const vt = sep(this.skyAt(w / 2, 0), this.skyAt(w / 2, h / 2)) ?? (this.state.fov * h) / w / 2
    return { w: Math.min(360, hz * 2), h: Math.min(180, vt * 2) }
  }
  jumpTo(raDeg, decDeg, fov) { this.anim = null; this.state.lon0 = raDeg * DEG; this.state.lat0 = decDeg * DEG; if (fov) this.state.fov = fov; this.dirty = true }

  // ------------------------------------------------------------------ input
  bind() {
    const c = this.canvas
    const pos = (e) => ({ x: e.offsetX, y: e.offsetY })
    this.hb = {
      down: (e) => {
        c.setPointerCapture(e.pointerId)
        this.pointers.set(e.pointerId, { ...pos(e), sx: e.offsetX, sy: e.offsetY, moved: 0, t: e.timeStamp })
        this.anim = null; this.zoom.target = null
        if (this.pointers.size === 2) { const [a, b] = [...this.pointers.values()]; this.pinch = Math.hypot(a.x - b.x, a.y - b.y) }
      },
      move: (e) => {
        const p = this.pointers.get(e.pointerId)
        if (!p) { if (e.pointerType === 'mouse' && this.handlers.onHover) { this.hoverAt = pos(e); if (!this.hoverRaf) this.hoverRaf = requestAnimationFrame(() => { this.hoverRaf = 0; const q = this.hoverAt; this.handlers.onHover(q ? this.pickAt(q.x, q.y, 10) : null, q) }) } return }
        if (this.hoverAt) { this.hoverAt = null; this.handlers.onHover?.(null, null) }
        const to = pos(e)
        p.moved += Math.abs(to.x - p.x) + Math.abs(to.y - p.y)
        if (this.pointers.size === 1) {
          const next = dragCenter(this.getView(), { x: p.x, y: p.y }, to)
          this.state.lon0 = next.lon0; this.state.lat0 = next.lat0
          this.dirty = true
        } else if (this.pointers.size === 2 && this.pinch) {
          const [a, b] = [...this.pointers.values()]
          const d = Math.hypot(a.x - b.x, a.y - b.y)
          this.applyZoom(this.pinch / d, (a.x + b.x) / 2, (a.y + b.y) / 2)
          this.pinch = d
        }
        p.x = to.x; p.y = to.y; p.t = e.timeStamp
      },
      up: (e) => {
        const p = this.pointers.get(e.pointerId)
        this.pointers.delete(e.pointerId)
        if (this.pointers.size < 2) this.pinch = null
        if (p && p.moved < 6 && this.pointers.size === 0) this.handlers.onPick?.(this.pickAt(e.offsetX, e.offsetY), this.skyAt(e.offsetX, e.offsetY), e)
      },
      wheel: (e) => {
        e.preventDefault()
        // Normalise: one mouse-wheel notch (~100 px or 3 lines) is ~15% of the field; trackpads send many small deltas.
        const px = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY
        const factor = Math.exp(Math.max(-250, Math.min(250, px)) * (e.ctrlKey ? 0.01 : 0.0016))
        const base = this.zoom.target ?? this.state.fov
        this.anim = null
        this.zoom.target = Math.max(MIN_FOV, Math.min(maxFovFor(this.state.projection), base * factor))
      },
      dbl: (e) => {
        const hit = this.pickAt(e.offsetX, e.offsetY)
        const sky = hit ? { ra: hit.ra, dec: hit.dec } : this.skyAt(e.offsetX, e.offsetY)
        if (sky) this.flyTo(sky.ra, sky.dec, Math.max(MIN_FOV, this.state.fov * 0.35))
      },
      lost: (e) => e.preventDefault(),
      leave: () => { this.hoverAt = null; this.handlers.onHover?.(null, null) },
    }
    const h = this.hb
    c.addEventListener('pointerdown', h.down); c.addEventListener('pointermove', h.move); c.addEventListener('pointerup', h.up)
    c.addEventListener('pointercancel', h.up); c.addEventListener('wheel', h.wheel, { passive: false })
    c.addEventListener('dblclick', h.dbl); c.addEventListener('webglcontextlost', h.lost); c.addEventListener('pointerleave', h.leave)
  }

  applyZoom(factor, x, y) {
    const z = zoomAbout(this.getView(), factor, x, y)
    this.state.lon0 = z.lon0; this.state.lat0 = z.lat0; this.state.fov = z.fov
    this.dirty = true
  }

  skyAt(x, y) { const s = this.getView().unproject(x, y); return s ? { ra: (((s.lon / DEG) % 360) + 360) % 360, dec: s.lat / DEG } : null }

  pickAt(x, y, radius = 14) {
    const { pick } = this
    let best = -1
    let bestScore = Infinity
    for (let i = 0; i < pick.n; i++) {
      const d = Math.hypot(pick.x[i] - x, pick.y[i] - y)
      if (d >= radius) continue
      const r = pick.ref[i]
      const mag = typeof r === 'number' ? this.cat.mag[r] : r.mag ?? 0
      const score = d - (pick.marker[i] ? 6 : 0) + mag * 0.15 // prefer markers, then brighter stars
      if (score < bestScore) { bestScore = score; best = i }
    }
    if (best < 0) return null
    const ref = this.refAt(best)
    const ra = (((pick.lon[best] / DEG) % 360) + 360) % 360
    const dec = pick.lat[best] / DEG
    // `ra/dec` = where it is drawn now (epoch-shifted); `ra0/dec0` = the J2000 catalogue position selection starts from
    return pick.marker[best] ? { kind: 1, ...ref } : { kind: 0, star: true, ...ref, ra0: ref.ra, dec0: ref.dec, ra, dec }
  }

  /** Pick-buffer entry i as an object (catalogue stars are stored by index). */
  refAt(i) { const r = this.pick.ref[i]; return typeof r === 'number' ? this.catStar(r) : r }

  /** Everything drawn inside a screen rectangle (canvas px), from the same pick buffer clicks use. Positions are as drawn (epoch-shifted). */
  objectsIn({ x0, y0, x1, y1 }) {
    const { pick } = this
    const out = []
    for (let i = 0; i < pick.n; i++) {
      const x = pick.x[i]
      const y = pick.y[i]
      if (x < x0 || x > x1 || y < y0 || y > y1) continue
      const ref = this.refAt(i)
      out.push({ ...ref, marker: !!pick.marker[i], x, y, raNow: (((pick.lon[i] / DEG) % 360) + 360) % 360, decNow: pick.lat[i] / DEG })
    }
    return out
  }

  /** A PNG of the sky as it is now. Renders synchronously first, since the WebGL buffer isn't preserved between frames. */
  snapshot() { this.draw(); return this.canvas.toDataURL('image/png') }

  // ------------------------------------------------------------------ labels
  label(i, text, x, y, cls) {
    let el = this.labelPool[i]
    if (!el) { el = document.createElement('div'); this.labelRoot.appendChild(el); this.labelPool[i] = el }
    if (el.textContent !== text) el.textContent = text
    const className = `sky-label ${cls}`
    if (el.className !== className) el.className = className
    el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`
    el.style.display = ''
  }

  // ------------------------------------------------------------------ frame
  resize() {
    const parent = this.canvas.parentElement ?? this.canvas
    const w = Math.max(1, parent.clientWidth)
    const h = Math.max(1, parent.clientHeight)
    this.dpr = Math.min(window.devicePixelRatio || 1, 2)
    this.renderer.setPixelRatio(this.dpr)
    this.renderer.setSize(w, h, false)
    this.canvas.style.width = `${w}px`
    this.canvas.style.height = `${h}px`
    this.dirty = true
  }

  tick(now) {
    this.raf = requestAnimationFrame((t) => this.tick(t))
    if (!this.active) return
    const dt = Math.min(0.05, (now - this.last) / 1000)
    this.last = now
    this.time += dt
    const s = this.state
    if (this.timeRate) { this.epochMs += this.timeRate * dt * 1000; this.dirty = true }
    if (this.zoom.target !== null) {
      // Critically damped spring on log(fov): starts from rest, never overshoots, settles in ~0.35 s.
      const w = this.reduced ? 400 : 12
      const x = Math.log(s.fov)
      const goal = Math.log(this.zoom.target)
      const h = Math.min(dt, 1 / 30)
      this.zoom.v += (w * w * (goal - x) - 2 * w * this.zoom.v) * h
      const nx = x + this.zoom.v * h
      s.fov = Math.exp(nx)
      if (Math.abs(goal - nx) < 2e-4 && Math.abs(this.zoom.v) < 2e-3) { s.fov = this.zoom.target; this.zoom.target = null; this.zoom.v = 0 }
      this.dirty = true
    }
    if (this.anim) {
      const a = this.anim
      a.t += dt / a.dur
      const u = Math.min(1, a.t)
      const e = u * u * (3 - 2 * u) // smoothstep: gentle start, gentle stop
      const c = slerpLonLat(a.from, a.to, e)
      s.lon0 = c.lon0
      s.lat0 = c.lat0
      s.fov = Math.exp(Math.log(a.from.fov) + (Math.log(a.to.fov) - Math.log(a.from.fov)) * e)
      if (u >= 1) { Object.assign(s, a.to); this.anim = null }
      this.dirty = true
    }
    if (!this.dirty) return
    this.dirty = false
    this.draw()
    if (now - this.lastEmit > 90) { this.lastEmit = now; this.handlers.onView?.(this.getState()) }
  }

  draw() {
    const w = this.canvas.clientWidth
    const h = this.canvas.clientHeight
    const view = makeView({ ...this.state, width: w, height: h })
    const years = yearsSinceJ2000(this.epochMs)
    const ndc = (x, y, out, o) => { out[o] = (x / w) * 2 - 1; out[o + 1] = 1 - (y / h) * 2 }
    const L = this.layers
    let labelN = 0
    const hideLabels = () => { for (let i = labelN; i < this.labelPool.length; i++) if (this.labelPool[i]) this.labelPool[i].style.display = 'none' }
    this.pick.n = 0
    const starLabels = [] // [labelIndex, x, y] so a landmark label can replace a star label drawn on the same spot
    const addPick = (x, y, ref, lon, lat, isMarker) => { const p = this.pick; if (p.n < p.x.length) { p.x[p.n] = x; p.y[p.n] = y; p.lon[p.n] = lon; p.lat[p.n] = lat; p.marker[p.n] = isMarker; p.ref[p.n] = ref; p.n++ } }
    const onScreen = (x, y, m = 40) => x > -m && x < w + m && y > -m && y < h + m

    // ---- stars
    const sp = this.starPts.geometry
    const pos = sp.attributes.position.array, sz = sp.attributes.aSize.array, col = sp.attributes.aColor.array, al = sp.attributes.aAlpha.array
    let n = 0
    const fov = this.state.fov
    // the naked-eye sky when wide, every catalogue star (V 8) once zoomed in; stars fade in rather than pop
    const limit = Math.max(5.6, Math.min(8, 6 + 1.6 * Math.log2(60 / fov)))
    const exposure = 1.45
    const taken = [] // label rectangles already used, so names never pile up
    const freeSpot = (x, y, wpx) => { if (x < 16 || x > w - 70 || y < 70 || y > h - 100) return false; if (taken.some((r) => Math.abs(r[0] - x) < Math.max(wpx, r[2]) && Math.abs(r[1] - y) < 16)) return false; taken.push([x, y, wpx]); return true }
    const nameLimit = Math.min(6, 2.4 + 1.3 * Math.log2(Math.max(1, 60 / fov)))
    const putStar = (lon, lat, mag, r, g, b, ref, isDeep) => {
      const p = view.project(lon, lat)
      if (!p.visible || !onScreen(p.x, p.y, 20) || n >= STAR_CAP - 16) return null
      ndc(p.x, p.y, pos, n * 3)
      const flux = Math.pow(2.512, -mag)
      sz[n] = Math.min(46, 3.2 + Math.pow(Math.max(flux, 0.001), 0.24) * 9)
      const fade = isDeep ? 0.75 : Math.min(1, Math.max(0.15, (limit - mag) / 0.7 + 0.15))
      col[n * 3] = r; col[n * 3 + 1] = g; col[n * 3 + 2] = b
      al[n] = Math.min(1.8, (0.68 + Math.sqrt(flux) * 1.8) * exposure) * fade
      n++
      addPick(p.x, p.y, ref, lon, lat, 0)
      return p
    }
    pos.fill(0, 0, 0)
    const C = this.cat
    if (L.stars && C) {
      // cone cull around the view centre on the J2000 unit vectors (proper motion moves nothing more than a degree)
      const cl = Math.cos(this.state.lat0)
      const cx = cl * Math.cos(this.state.lon0), cy = cl * Math.sin(this.state.lon0), cz = Math.sin(this.state.lat0)
      const aspect = Math.max(1, w / Math.max(h, 1))
      const coneR = view.projection === 'map' ? Math.PI : Math.min(Math.PI, fov * DEG * 0.75 * aspect + 0.03)
      const cosR = Math.cos(coneR)
      for (let i = 0; i < C.n; i++) {
        const mag = C.mag[i]
        if (mag > limit) break // the catalogue is sorted brightest first
        if (C.ux[i] * cx + C.uy[i] * cy + C.uz[i] * cz < cosR) continue
        const { lon, lat } = propagate(C.ra[i], C.dec[i], C.pmra[i], C.pmdec[i], years)
        const p = putStar(lon, lat, mag, C.rgb[i * 3], C.rgb[i * 3 + 1], C.rgb[i * 3 + 2], i, false)
        if (p && L.labels && mag < nameLimit && C.names[i] && labelN < 60) {
          const text = prettyStarName(C.names[i])
          if (freeSpot(p.x + 9, p.y - 7, text.length * 6.5 + 10)) { starLabels.push([labelN, p.x, p.y]); this.label(labelN++, text, p.x + 9, p.y - 9, mag < 1.5 ? 'star' : 'star dim') }
        }
      }
    } else if (L.stars) {
      for (const s of this.bright) {
        const { lon, lat } = propagate(s.ra, s.dec, s.pmra, s.pmdec, years)
        const c = s.color ?? [0.85, 0.88, 1]
        const p = putStar(lon, lat, s.mag, c[0], c[1], c[2], s, false)
        if (p && L.labels && s.mag < 2.6 && labelN < 40 && freeSpot(p.x + 9, p.y - 7, 80)) { starLabels.push([labelN, p.x, p.y]); this.label(labelN++, prettyStarName(s.name), p.x + 8, p.y - 8, 'star') }
      }
    }
    if (L.stars && L.deep && this.deep.length) {
      // SIMBAD's fainter stars for the zoomed-in field; skip any the catalogue already drew at the same spot
      for (const s of this.deep) {
        if (C && s.mag <= 8 && fov > 0.5) continue
        const { lon, lat } = propagate(s.ra, s.dec, s.pmra, s.pmdec, years)
        const p = putStar(lon, lat, Math.max(s.mag, 6), 0.85, 0.88, 1, s, true)
        if (p && L.labels && fov < 0.8 && s.mag < 11 && labelN < 70) { starLabels.push([labelN, p.x, p.y]); this.label(labelN++, s.name.replace(/^(\*|V\*)\s+/, ''), p.x + 7, p.y - 7, 'star dim') }
      }
    }
    // ---- Sun and planets, where they are on the chosen date
    if (L.planets) {
      if (Math.abs(this.epochMs - this.bodiesAt) > 3600000) { this.bodies = solarSystem(this.epochMs); this.bodiesAt = this.epochMs }
      for (const b of this.bodies) {
        const p = putStar(b.ra * DEG, b.dec * DEG, b.name === 'Sun' ? -3 : Math.min(b.mag, 3), b.color[0], b.color[1], b.color[2], b, false)
        if (!p) continue
        sz[n - 1] = b.name === 'Sun' ? 30 : Math.max(sz[n - 1], 10)
        al[n - 1] = b.name === 'Sun' ? 1.8 : Math.max(al[n - 1], 1.2)
        if (L.labels && labelN < 80) { taken.push([p.x + 12, p.y - 9, 70]); this.label(labelN++, b.name, p.x + 12, p.y - 11, 'body') }
      }
    }
    sp.setDrawRange(0, n)
    ;['position', 'aSize', 'aColor', 'aAlpha'].forEach((k) => { sp.attributes[k].needsUpdate = true })

    // ---- lines
    const seg = (obj, build) => {
      const arr = obj.geometry.attributes.position.array
      let count = 0
      const cap = arr.length / 3 - 2
      const push = (a, b) => { if (count + 2 > cap) return; ndc(a.x, a.y, arr, count * 3); ndc(b.x, b.y, arr, (count + 1) * 3); count += 2 }
      build(push)
      obj.geometry.setDrawRange(0, count)
      obj.geometry.attributes.position.needsUpdate = true
    }
    const maxPx = view.projection === 'map' ? w * 0.45 : Math.max(w, h) * 1.2
    const polyline = (pts, push) => {
      let prev = null
      for (const [lon, lat] of pts) {
        const p = view.project(lon, lat)
        if (p.visible && prev && Math.hypot(p.x - prev.x, p.y - prev.y) < maxPx && (onScreen(p.x, p.y, 600) || onScreen(prev.x, prev.y, 600))) push(prev, p)
        prev = p.visible ? p : null
      }
    }
    const fovR = this.state.fov * DEG
    const gridLines = (frame, push) => {
      const bounds = visibleBounds(view) // 81 unprojections: only paid when a grid is actually shown
      const decS = decStep(this.state.fov)
      const cDec = this.state.lat0 / DEG
      const raS = raStep(this.state.fov, Math.max(-85, Math.min(85, cDec)))
      const toEq = frame === 'gal' ? (a, b) => { const e = galacticToEquatorial(a, b); return [e.ra * DEG, e.dec * DEG] } : (a, b) => [a * DEG, b * DEG]
      let latLo, latHi, lonLo, lonHi
      if (frame === 'gal') { latLo = -90; latHi = 90; lonLo = 0; lonHi = 360 } else {
        latLo = Math.max(-90, Math.floor((bounds.latMin / DEG - decS) / decS) * decS); latHi = Math.min(90, Math.ceil((bounds.latMax / DEG + decS) / decS) * decS)
        const c0 = this.state.lon0 / DEG
        lonLo = bounds.allLon ? 0 : Math.floor((c0 + bounds.lonMin / DEG - raS) / raS) * raS; lonHi = bounds.allLon ? 360 : Math.ceil((c0 + bounds.lonMax / DEG + raS) / raS) * raS
      }
      const ds = frame === 'gal' ? Math.max(decS, 5) : decS
      const rs = frame === 'gal' ? Math.max(raS * 6, 15) : raS
      const nPts = (span) => Math.max(12, Math.min(260, Math.ceil(span / Math.max(fovR / DEG / 40, 0.0002))))
      const lat0s = []
      for (let b = Math.ceil(latLo / ds) * ds; b <= latHi + 1e-9 && lat0s.length < 120; b += ds) if (Math.abs(b) < 89.999) lat0s.push(b)
      for (const b of lat0s) {
        const np = nPts(lonHi - lonLo); const pts = []
        for (let i = 0; i <= np; i++) { const l = lonLo + ((lonHi - lonLo) * i) / np; pts.push(toEq(l, b)) }
        polyline(pts, push)
        if (L.labels && frame === 'equ') {
          let best = null
          for (const [lo, la] of pts) { const p = view.project(lo, la); if (p.visible && p.y > 60 && p.y < h - 70 && (!best || Math.abs(p.x - w * 0.07) < Math.abs(best.x - w * 0.07)) && p.x > 4 && p.x < w * 0.5) best = p }
          if (best && labelN < 230) this.label(labelN++, fmtDecStep(b, decS), best.x + 4, best.y - 14, 'grid')
        }
      }
      for (let a = Math.ceil(lonLo / rs) * rs, k = 0; a <= lonHi + 1e-9 && k < 200; a += rs, k++) {
        const la0 = frame === 'gal' ? -90 : Math.max(-90, bounds.latMin / DEG - decS), la1 = frame === 'gal' ? 90 : Math.min(90, bounds.latMax / DEG + decS)
        const np = nPts(la1 - la0); const pts = []
        for (let i = 0; i <= np; i++) pts.push(toEq(a, la0 + ((la1 - la0) * i) / np))
        polyline(pts, push)
        if (L.labels && frame === 'equ') {
          let best = null
          for (const [lo, la] of pts) { const p = view.project(lo, la); if (p.visible && p.x > 60 && p.x < w - 60 && (!best || Math.abs(p.y - h * 0.93) < Math.abs(best.y - h * 0.93)) && p.y < h - 8 && p.y > h * 0.5) best = p }
          if (best && labelN < 230) this.label(labelN++, fmtRAStep(((a % 360) + 360) % 360, rs), best.x + 4, best.y - 20, 'grid')
        }
      }
    }
    seg(this.lines.equ, (push) => { if (L.equGrid) gridLines('equ', push) })
    seg(this.lines.gal, (push) => { if (L.galGrid) gridLines('gal', push) })
    const circle = (toEq, param) => (push) => {
      const span = this.state.fov >= 120 ? 180 : Math.min(180, this.state.fov * 0.9 + 1)
      const np = Math.min(900, Math.max(60, Math.ceil(span * 2 / Math.max(this.state.fov / 80, 0.0002))))
      const pts = []
      for (let i = 0; i <= np; i++) { const q = toEq(param - span + (2 * span * i) / np); pts.push([q.ra * DEG, q.dec * DEG]) }
      polyline(pts, push)
    }
    const cEcl = equatorialToEcliptic(this.state.lon0 / DEG, this.state.lat0 / DEG).lon
    const cGal = equatorialToGalactic(this.state.lon0 / DEG, this.state.lat0 / DEG).l
    seg(this.lines.ecl, (push) => { if (L.ecliptic) circle((l) => eclipticToEquatorial(l, 0), cEcl)(push) })
    seg(this.lines.plane, (push) => { if (L.galPlane) circle((l) => galacticToEquatorial(l, 0), cGal)(push) })
    // ---- motion history of the selected star: its path from 1800 to the epoch shown, ticks every 50 years
    const sel = this.markers.find((m) => m.selected && (m.pmra || m.pmdec))
    seg(this.lines.trail, (push) => {
      if (!sel) return
      const from = 1800 - 2000, to = years, n = 120, pts = []
      for (let i = 0; i <= n; i++) { const q = propagate(sel.ra, sel.dec, sel.pmra || 0, sel.pmdec || 0, from + ((to - from) * i) / n); pts.push([q.lon, q.lat]) }
      const a = view.project(pts[0][0], pts[0][1]), b = view.project(pts[n][0], pts[n][1])
      if (!a.visible || !b.visible || Math.hypot(a.x - b.x, a.y - b.y) < 6) return // too short to see at this zoom
      polyline(pts, push)
      for (let yr = 1800; yr <= 2000 + to; yr += 50) {
        const q = propagate(sel.ra, sel.dec, sel.pmra || 0, sel.pmdec || 0, yr - 2000), p = view.project(q.lon, q.lat)
        if (!p.visible || !onScreen(p.x, p.y)) continue
        push({ x: p.x - 4, y: p.y }, { x: p.x + 4, y: p.y }); push({ x: p.x, y: p.y - 4 }, { x: p.x, y: p.y + 4 })
        if (L.labels && labelN < 260) this.label(labelN++, String(yr), p.x + 6, p.y + 4, 'trail')
      }
    })
    // ---- constellations: stick figures always (when on), names only at "constellation scale"
    seg(this.lines.cons, (push) => { if (L.constellations && this.cons) for (const line of this.cons.lines) polyline(line, push) })
    if (L.constellations && L.labels && this.cons && fov > 6 && fov < 260) {
      for (const c of this.cons.names) {
        if (fov > 140 && c.rank > 1) continue
        const p = view.project(c.ra * DEG, c.dec * DEG)
        if (!p.visible || labelN > 150) continue
        const text = c.name.toUpperCase()
        if (freeSpot(p.x, p.y, text.length * 7.5)) this.label(labelN++, text, p.x - text.length * 3.6, p.y - 6, 'const')
      }
    }

    // ---- footprints
    // outlines of every overlapping observation are only useful from afar; up close they bury the image
    const showAllFootprints = L.footprints && !(this.product && this.state.fov < 8)
    seg(this.lines.foot, (push) => { if (showAllFootprints) for (const f of this.footprints) if (!f.active) polyline([...f.polygon.map(([a, b]) => [a * DEG, b * DEG]), [f.polygon[0][0] * DEG, f.polygon[0][1] * DEG]], push) })
    seg(this.lines.footActive, (push) => { for (const f of this.footprints) if (f.active) polyline([...f.polygon.map(([a, b]) => [a * DEG, b * DEG]), [f.polygon[0][0] * DEG, f.polygon[0][1] * DEG]], push) })

    // ---- markers
    const mp = this.markerPts.geometry
    const mpos = mp.attributes.position.array, mk = mp.attributes.aKind.array, mc = mp.attributes.aColor.array, ms = mp.attributes.aSeed.array
    let m = 0
    const draw = []
    for (const mkr of this.markers) {
      if (mkr.selected) draw.push({ mkr, kind: KIND.selected, color: COLORS.selected })
      let kind = KIND.landmark, key = 'landmark'
      if (mkr.kind === 'probe-ok' || mkr.kind === 'probe-mock' || mkr.kind === 'probe-error') { kind = KIND['probe-ok']; key = mkr.kind }
      else if (mkr.kind === 'probe-pending') { kind = KIND['probe-pending']; key = 'probe-pending' }
      else if (mkr.kind === 'candidate') { kind = KIND.candidate; key = 'candidate' }
      else if (mkr.kind === 'pin') { kind = KIND.pin; key = 'pin' }
      draw.push({ mkr, kind, color: COLORS[key] })
    }
    for (const d of draw) {
      if (!L.markers && d.mkr.kind === 'landmark') continue
      const { lon, lat } = propagate(d.mkr.ra, d.mkr.dec, d.mkr.pmra, d.mkr.pmdec, d.mkr.moves ? years : 0)
      const p = view.project(lon, lat)
      if (!p.visible || !onScreen(p.x, p.y, 60) || m >= 500) continue
      ndc(p.x, p.y, mpos, m * 3); mk[m] = d.kind; mc[m * 3] = d.color[0]; mc[m * 3 + 1] = d.color[1]; mc[m * 3 + 2] = d.color[2]; ms[m] = hash01(m * 7 + 3); m++
      addPick(p.x, p.y, d.mkr, lon, lat, 1)
      if (L.labels && d.mkr.label && labelN < 290) {
        this.label(labelN++, d.mkr.label, p.x + 16, p.y - 8, `marker ${d.mkr.kind}${d.mkr.selected ? ' selected' : ''}`)
        // the landmark's name wins over the catalogue label of the star under it (e.g. Polaris over α UMi)
        for (const [i, sx, sy] of starLabels) if (Math.abs(sx - p.x) < 12 && Math.abs(sy - p.y) < 12 && this.labelPool[i]) this.labelPool[i].style.display = 'none'
      }
    }
    mp.setDrawRange(0, m)
    ;['position', 'aKind', 'aColor', 'aSeed'].forEach((k) => { mp.attributes[k].needsUpdate = true })
    hideLabels()

    // ---- survey imagery underneath everything, then the SPHEREx image overlay
    this.hips.draw(view, this.state, w, h, L.survey, L.surveyOpacity)
    this.drawOverlay(view, w, h, ndc)

    for (const o of [this.starPts, this.markerPts]) { o.material.uniforms.uPx.value = this.dpr; o.material.uniforms.uMaxPoint.value = this.maxPoint; o.material.uniforms.uTime.value = this.time }
    const mat = this.overlayMesh.material
    if (mat.uniforms?.uGrid) mat.uniforms.uGrid.value = Math.min(1, Math.max(0, (Math.log10(8 / Math.max(this.state.fov, 1e-4) ) - 1.2) / 1.2))
    this.renderer.render(this.scene, this.camera)
  }

  drawOverlay(view, w, h, ndc) {
    const geo = this.overlayMesh.geometry
    const P = this.product
    if (!P || !this.layers.overlay) { geo.setDrawRange(0, 0); return }
    const posA = geo.attributes.position.array
    const uvA = geo.attributes.aUv.array
    const idx = geo.index.array
    const { n, nodes, uv } = P
    const VCAP = 39000
    const ICAP = 58000
    let vc = 0
    let ic = 0

    // 1) project the whole lattice ONCE per frame; cells share corners.
    for (let k = 0; k < n * n; k++) {
      const p = view.project(P.raU[k], P.decR[k])
      nodes[k * 3] = p.x
      nodes[k * 3 + 1] = p.y
      nodes[k * 3 + 2] = p.visible ? 1 : 0
    }
    const M = 8
    const seam = view.projection === 'map' ? w * 0.4 : Infinity

    // 2) deep zoom: a lattice cell can be thousands of screens wide, where float32 clip coordinates would lose
    //    precision. Such cells are split in (u, v) until each piece is screen-sized, and culled when off-screen.
    const skyAt = (u, v) => {
      const i = Math.min(n - 2, Math.max(0, Math.floor(u)))
      const j = Math.min(n - 2, Math.max(0, Math.floor(v)))
      const fu = u - i
      const fv = v - j
      const k = j * n + i
      const bl = (a) => (a[k] * (1 - fu) + a[k + 1] * fu) * (1 - fv) + (a[k + n] * (1 - fu) + a[k + n + 1] * fu) * fv
      return [bl(P.raU), bl(P.decR)]
    }
    const uvAt = (u, v) => {
      const i = Math.min(n - 2, Math.max(0, Math.floor(u)))
      const j = Math.min(n - 2, Math.max(0, Math.floor(v)))
      const x = P.mesh.x[i] + (P.mesh.x[i + 1] - P.mesh.x[i]) * (u - i)
      const y = P.mesh.y[j] + (P.mesh.y[j + 1] - P.mesh.y[j]) * (v - j)
      return [(x + 0.5) / P.W, (y + 0.5) / P.H]
    }
    const split = (u0, u1, v0, v1, depth) => {
      if (vc > VCAP || ic > ICAP) return
      const c = [[u0, v0], [u1, v0], [u1, v1], [u0, v1]].map(([u, v]) => { const [lon, lat] = skyAt(u, v); return { p: view.project(lon, lat), t: uvAt(u, v) } })
      if (!c.every((q) => q.p.visible)) return
      const xs = c.map((q) => q.p.x)
      const ys = c.map((q) => q.p.y)
      const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
      if (maxX < -M || minX > w + M || maxY < -M || minY > h + M) return
      if (depth < 14 && (maxX - minX > w * 2.5 || maxY - minY > h * 2.5)) {
        const um = (u0 + u1) / 2
        const vm = (v0 + v1) / 2
        split(u0, um, v0, vm, depth + 1); split(um, u1, v0, vm, depth + 1); split(u0, um, vm, v1, depth + 1); split(um, u1, vm, v1, depth + 1)
        return
      }
      const base = vc
      for (const q of c) { ndc(q.p.x, q.p.y, posA, vc * 3); uvA[vc * 2] = q.t[0]; uvA[vc * 2 + 1] = q.t[1]; vc++ }
      idx[ic++] = base; idx[ic++] = base + 1; idx[ic++] = base + 2; idx[ic++] = base; idx[ic++] = base + 2; idx[ic++] = base + 3
    }

    // 3) ordinary cells straight from the shared node buffer
    for (let j = 0; j < n - 1; j++) {
      for (let i = 0; i < n - 1; i++) {
        const a = j * n + i
        const b = a + 1
        const c = a + n
        const d = c + 1
        if (!(nodes[a * 3 + 2] && nodes[b * 3 + 2] && nodes[c * 3 + 2] && nodes[d * 3 + 2])) continue
        const x0 = nodes[a * 3], x1 = nodes[b * 3], x2 = nodes[c * 3], x3 = nodes[d * 3]
        const y0 = nodes[a * 3 + 1], y1 = nodes[b * 3 + 1], y2 = nodes[c * 3 + 1], y3 = nodes[d * 3 + 1]
        const minX = Math.min(x0, x1, x2, x3), maxX = Math.max(x0, x1, x2, x3)
        const minY = Math.min(y0, y1, y2, y3), maxY = Math.max(y0, y1, y2, y3)
        if (maxX < -M || minX > w + M || maxY < -M || minY > h + M) continue
        if (maxX - minX > seam) continue
        if (maxX - minX > w * 2.5 || maxY - minY > h * 2.5) { split(i, i + 1, j, j + 1, 0); continue }
        if (vc + 4 > VCAP || ic + 6 > ICAP) break
        const base = vc
        for (const k of [a, b, d, c]) { posA[vc * 3] = (nodes[k * 3] / w) * 2 - 1; posA[vc * 3 + 1] = 1 - (nodes[k * 3 + 1] / h) * 2; uvA[vc * 2] = uv[k * 2]; uvA[vc * 2 + 1] = uv[k * 2 + 1]; vc++ }
        idx[ic++] = base; idx[ic++] = base + 1; idx[ic++] = base + 2; idx[ic++] = base; idx[ic++] = base + 2; idx[ic++] = base + 3
      }
    }
    geo.setDrawRange(0, ic)
    geo.attributes.position.needsUpdate = true
    geo.attributes.aUv.needsUpdate = true
    geo.index.needsUpdate = true
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    this.resizeObserver.disconnect()
    const c = this.canvas
    const h = this.hb
    c.removeEventListener('pointerdown', h.down); c.removeEventListener('pointermove', h.move); c.removeEventListener('pointerup', h.up)
    c.removeEventListener('pointercancel', h.up); c.removeEventListener('wheel', h.wheel)
    c.removeEventListener('dblclick', h.dbl); c.removeEventListener('webglcontextlost', h.lost); c.removeEventListener('pointerleave', h.leave)
    cancelAnimationFrame(this.hoverRaf)
    this.labelPool.forEach((el) => el?.remove())
    this.hips.disposeAll()
    this.scene.traverse((o) => { o.geometry?.dispose(); o.material?.dispose?.() })
    this.lutTex.dispose(); this.blank.dispose()
    this.renderer.dispose()
  }
}
