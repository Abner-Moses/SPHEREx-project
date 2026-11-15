// All-sky survey imagery (HiPS: hierarchical HEALPix tiles), drawn through the sky engine's own float64 projection.
// Adapted from the team's Stellarium-style explorer (spherex-code, hips.ts + the Rust healpix_coord), in plain JS / three.js.
// Order-0 tiles ship with the app (public/surveys) so the base sky works offline; deeper tiles stream from the public
// mirrors as you zoom, with parent fallback, at most 10 requests in flight and a bounded texture cache.

import * as THREE from 'three'
import { healpixCoord } from './hipsMath.js'

export const SURVEYS = {
  infrared: { label: 'Infrared', name: '2MASS near-infrared', url: 'https://irsa.ipac.caltech.edu/data/hips/CDS/2MASS/Color', credit: '2MASS / UMass / IPAC-Caltech / CDS' },
  dss: { label: 'Visible', name: 'DSS visible light', url: 'https://stpubdata.s3.us-east-1.amazonaws.com/mast/skybackgrounds/DSSColor', credit: 'DSS / STScI / CDS' },
}
const MAX_ORDER = { infrared: 6, dss: 6 } // tiles are 512 px; order 6 is about 6.9 arcsec per pixel
const CACHE = 140
const GRID = 8 // each tile is drawn as an 8 x 8 lattice so its edges follow the sphere

const material = (tex) => new THREE.ShaderMaterial({
  uniforms: { uTex: { value: tex }, uOpacity: { value: 0.75 }, uGain: { value: 1.1 } },
  transparent: true, depthTest: false, depthWrite: false, side: THREE.DoubleSide, // the sky is seen from inside: mirrored winding
  vertexShader: 'attribute vec2 aUv; varying vec2 vUv; void main(){ vUv = aUv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
  // a gentle gamma lift so faint nebulosity reads on a dark screen, then the layer's opacity
  fragmentShader: 'uniform sampler2D uTex; uniform float uOpacity, uGain; varying vec2 vUv; void main(){ vec3 c = texture2D(uTex, vUv).rgb; gl_FragColor = vec4(pow(max(c, vec3(0.0)), vec3(0.8))*uGain, uOpacity); }',
})

export class HipsLayer {
  constructor(scene, renderOrder = 0, onLoad = () => {}) {
    this.scene = scene
    this.renderOrder = renderOrder
    this.onLoad = onLoad
    this.survey = null
    this.tiles = new Map() // key -> { order, pixel, mesh, lon, lat, used }
    this.pending = new Map()
    this.failed = new Map()
    this.generation = 0
    this.loading = 0
  }

  key(o, p) { return `${o}/${p}` }

  clear() {
    this.generation++
    for (const t of this.tiles.values()) this.dispose(t)
    this.tiles.clear()
    for (const c of this.pending.values()) c.abort()
    this.pending.clear()
    this.failed.clear()
  }

  dispose(t) { this.scene.remove(t.mesh); t.mesh.geometry.dispose(); t.mesh.material.uniforms.uTex.value.dispose(); t.mesh.material.dispose() }

  /** Is HEALPix cell (o, p) possibly on screen? A cone test around the view centre, generous at the edges. */
  visible(o, p, c) {
    const [a, b] = healpixCoord(o, p, 0.5, 0.5)
    const cos = Math.sin(b) * Math.sin(c.lat0) + Math.cos(b) * Math.cos(c.lat0) * Math.cos(a - c.lon0)
    return cos > Math.cos(Math.min(Math.PI, c.radius + 1.1 / 2 ** o)) // a cell's corners lie within ~1.03 rad / 2^order of its centre
  }

  /** Choose and draw the tiles for this frame. `view` is the engine's float64 view; returns true while tiles are still arriving. */
  draw(view, state, w, h, survey, opacity) {
    if (survey !== this.survey) { this.clear(); this.survey = survey }
    for (const t of this.tiles.values()) t.mesh.visible = false
    if (!SURVEYS[survey] || opacity <= 0) return false
    const fovR = (Math.min(state.fov, 360) * Math.PI) / 180
    const radius = state.projection === 'map' ? Math.PI : Math.min(Math.PI, fovR * 0.75 * Math.max(1, w / Math.max(h, 1)))
    const c = { lon0: state.lon0, lat0: state.lat0, radius }
    // ~512 px tiles: pick the order whose pixels are about one screen pixel
    const pxPerRad = w / Math.max(1e-6, fovR)
    const want = Math.max(0, Math.min(MAX_ORDER[survey], Math.round(Math.log2((pxPerRad * 1.0233) / 512))))
    const wanted = new Set()
    const show = new Set()
    const mid = Math.max(0, want - 2) // a quick, coarser preview level while the detailed tiles stream in
    // walk down to the wanted order; each missing tile is covered by the deepest ancestor already loaded
    const visit = (o, p, fallback) => {
      if (o > 0 && !this.visible(o, p, c)) return
      const k = this.key(o, p)
      const tile = this.tiles.get(k)
      if (o === 0 || o === want || o === mid) { wanted.add(k); if (!tile) this.load(o, p) }
      const best = tile ?? fallback
      if (o === want) { if (best) show.add(best); return }
      for (let n = 0; n < 4; n++) visit(o + 1, p * 4 + n, best)
    }
    for (let p = 0; p < 12; p++) visit(0, p, null)
    for (const [k, ctl] of this.pending) if (!wanted.has(k)) { ctl.abort(); this.pending.delete(k) }
    const now = performance.now()
    const seam = view.projection === 'map' ? w * 0.35 : Math.max(w, h) * 2
    for (const t of show) { t.used = now; this.place(t, view, w, h, seam, opacity) }
    if (this.tiles.size > CACHE) {
      const old = [...this.tiles.entries()].filter(([, t]) => t.order > 0).sort((a, b) => a[1].used - b[1].used)
      for (const [k, t] of old.slice(0, this.tiles.size - CACHE)) { this.dispose(t); this.tiles.delete(k) }
    }
    return this.pending.size > 0
  }

  /** Project a tile's lattice and keep only triangles that are fully visible and not torn across a seam. */
  place(t, view, w, h, seam, opacity) {
    const g = t.mesh.geometry
    const pos = g.attributes.position.array
    const ok = t.ok
    const N = GRID + 1
    for (let i = 0; i < N * N; i++) {
      const p = view.project(t.lon[i], t.lat[i])
      ok[i] = p.visible ? 1 : 0
      pos[i * 3] = (p.x / w) * 2 - 1
      pos[i * 3 + 1] = 1 - (p.y / h) * 2
      t.sx[i] = p.x
      t.sy[i] = p.y
    }
    const idx = g.index.array
    let n = 0
    const tri = (a, b, c) => {
      if (!ok[a] || !ok[b] || !ok[c]) return
      const xs = [t.sx[a], t.sx[b], t.sx[c]]
      const ys = [t.sy[a], t.sy[b], t.sy[c]]
      const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
      if (maxX < -20 || minX > w + 20 || maxY < -20 || minY > h + 20) return
      if (maxX - minX > seam || maxY - minY > seam) return
      idx[n++] = a; idx[n++] = b; idx[n++] = c
    }
    for (let j = 0; j < GRID; j++) for (let i = 0; i < GRID; i++) { const a = j * N + i; tri(a, a + 1, a + N + 1); tri(a, a + N + 1, a + N) }
    g.setDrawRange(0, n)
    g.index.needsUpdate = true
    g.attributes.position.needsUpdate = true
    t.mesh.material.uniforms.uOpacity.value = opacity
    t.mesh.visible = n > 0
  }

  async load(order, pixel) {
    const k = this.key(order, pixel)
    if (this.tiles.has(k) || this.pending.has(k) || this.pending.size >= 10 || (this.failed.get(k) || 0) > Date.now()) return
    const ctl = new AbortController()
    const gen = this.generation
    const survey = this.survey
    this.pending.set(k, ctl)
    try {
      const url = order === 0 ? `/surveys/${survey}/Npix${pixel}.jpg` : `${SURVEYS[survey].url}/Norder${order}/Dir${Math.floor(pixel / 10000) * 10000}/Npix${pixel}.jpg`
      const r = await fetch(url, { signal: ctl.signal })
      if (!r.ok) throw new Error(`tile ${r.status}`)
      const bmp = await createImageBitmap(await r.blob())
      if (ctl.signal.aborted || gen !== this.generation) { bmp.close?.(); return }
      const tex = new THREE.Texture(bmp)
      tex.flipY = false
      tex.colorSpace = THREE.NoColorSpace
      tex.minFilter = THREE.LinearFilter
      tex.magFilter = THREE.LinearFilter
      tex.generateMipmaps = false
      tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping
      tex.needsUpdate = true
      const N = GRID + 1
      const lon = new Float64Array(N * N)
      const lat = new Float64Array(N * N)
      const uv = new Float32Array(N * N * 2)
      for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
        const u = i / GRID
        const v = j / GRID
        const [a, b] = healpixCoord(order, pixel, u, v)
        const q = j * N + i
        lon[q] = a; lat[q] = b
        // HiPS tile images are transposed against HEALPix face coordinates: image column follows v, row follows u.
        // Measured in the browser against HYG stars over the Pleiades: this mapping puts survey stars on catalogue
        // stars (4.1x the background at star positions; every other flip/transpose scores about 1.0).
        uv[q * 2] = v; uv[q * 2 + 1] = u
      }
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * N * 3), 3).setUsage(THREE.DynamicDrawUsage))
      geo.setAttribute('aUv', new THREE.BufferAttribute(uv, 2))
      geo.setIndex(new THREE.BufferAttribute(new Uint16Array(GRID * GRID * 6), 1).setUsage(THREE.DynamicDrawUsage))
      const mesh = new THREE.Mesh(geo, material(tex))
      mesh.frustumCulled = false
      mesh.renderOrder = this.renderOrder + order * 0.01
      mesh.visible = false
      this.scene.add(mesh)
      this.tiles.set(k, { order, pixel, mesh, lon, lat, ok: new Uint8Array(N * N), sx: new Float64Array(N * N), sy: new Float64Array(N * N), used: performance.now() })
      this.onLoad()
    } catch {
      if (!ctl.signal.aborted) this.failed.set(k, Date.now() + 60000)
    } finally {
      if (this.pending.get(k) === ctl) this.pending.delete(k)
    }
  }

  disposeAll() { this.clear() }
}
