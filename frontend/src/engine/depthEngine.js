// 3D "depth" view: bright stars placed at their true distances (from SIMBAD parallaxes), in the
// galactic frame with the Sun at the origin (x -> galactic centre, y -> direction of rotation, z -> north).
// A single uniform morphs the stars between their real 3D positions and the flat "painted on a
// sphere" arrangement the sky map shows, which is the whole point of the view.
// Only stars with a parallax of at least MIN_PLX_MAS are drawn (distance <= ~650 ly), because
// bright-star parallaxes get unreliable beyond that.

import * as THREE from 'three'
import { CameraRig } from './cameraRig.js'
import { DEG, bvToRGB, clamp, equatorialToGalactic, galacticToEquatorial } from '../lib/astro.js'
import { LY_PER_PARSEC, MIN_PLX_MAS } from '../lib/depth.js'

const SPHERE_R = 420

function galacticXYZ(ra, dec, distance) {
  const { l, b } = equatorialToGalactic(ra, dec)
  const cb = Math.cos(b * DEG)
  const dir = [cb * Math.cos(l * DEG), cb * Math.sin(l * DEG), Math.sin(b * DEG)]
  return { dir, pos: dir.map((c) => c * distance) }
}

// galactic (x,y,z) -> three.js (x, y=up, z) so +z galactic north is "up" on screen
const toScene = ([x, y, z]) => new THREE.Vector3(x, z, -y)

export class DepthEngine {
  constructor(canvas, labelRoot) {
    this.canvas = canvas
    this.labelRoot = labelRoot
    this.reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' })
    this.renderer.setClearColor(0x000000, 0)
    const gl = this.renderer.getContext()
    this.maxPoint = gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)?.[1] ?? 64
    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(42, 1, 1, 8000)
    // Overview orbits the Sun from outside; "look around" stands at the Sun (exactly the sky map's point of view)
    // and lets you fly off with WASD. The pivot is kept inside a 2000 ly bubble so you can't get lost.
    this.rig = new CameraRig(canvas, {
      reduced: this.reduced,
      mode: 'orbit', // open on the overview; "Look around" (standing at the Sun) is one click away
      views: {
        orbit: { pivot: [0, 0, 0], distance: 820, yaw: 0.9, pitch: -0.42, fov: 42 },
        look: { pivot: [0, 0, 0], distance: 0, yaw: 0.9, pitch: 0, fov: 60 },
      },
      distance: [120, 3200],
      fov: [6, 100],
      orbitPitch: [-80, 80],
      speed: 70,
      clampPivot: (v) => { if (v.length() > 2000) v.setLength(2000) },
    })
    this.dirty = true
    this.depth = 0 // eased
    this.depthTarget = 1
    this.active = true
    this.labels = []
    this.count = 0
    this.addGuides()
    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(canvas.parentElement ?? canvas)
    this.resize()
    this.last = performance.now()
    this.raf = requestAnimationFrame((t) => this.tick(t))
  }

  addGuides() {
    const rings = new THREE.Group()
    for (const r of [100, 200, 300, 400, 500, 600]) {
      const pts = []
      for (let i = 0; i <= 128; i++) pts.push(Math.cos((i / 128) * Math.PI * 2) * r, 0, Math.sin((i / 128) * Math.PI * 2) * r)
      const g = new THREE.BufferGeometry()
      g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
      rings.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0x7f97c8, transparent: true, opacity: r % 200 === 0 ? 0.22 : 0.11 })))
      this.ringLabels ??= []
      this.ringLabels.push({ r, pos: new THREE.Vector3(r, 0, 0) })
    }
    const axis = new THREE.BufferGeometry()
    axis.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 640, 0, 0], 3))
    rings.add(new THREE.Line(axis, new THREE.LineBasicMaterial({ color: 0xa58bff, transparent: true, opacity: 0.55 })))
    this.scene.add(rings)
    this.guides = rings
    // the Sun
    // A fixed-size glowing point, not a 3D sphere: a sphere balloons to fill the screen as the camera flies in.
    const sunGeo = new THREE.BufferGeometry()
    sunGeo.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0], 3))
    const sun = new THREE.Points(sunGeo, new THREE.ShaderMaterial({
      uniforms: { uPx: { value: 1 } }, transparent: true, depthWrite: false,
      vertexShader: 'uniform float uPx; void main(){ gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); gl_PointSize = 14.0 * uPx; }',
      fragmentShader: 'void main(){ float d = length(gl_PointCoord - 0.5) * 2.0; if (d > 1.0) discard; gl_FragColor = vec4(1.0, 0.82, 0.48, smoothstep(1.0, 0.55, d)); }',
    }))
    sun.frustumCulled = false
    this.scene.add(sun)
    this.sun = sun
    this.addLabel('Sun', new THREE.Vector3(0, 0, 0), 'marker selected')
    this.addLabel('toward Galactic centre', new THREE.Vector3(640, 0, 0), 'grid')
    this.ringLabels.forEach((r) => { if (r.r % 200 === 0) this.addLabel(`${r.r} ly`, r.pos.clone().setZ(0), 'grid', r) })
  }

  addLabel(text, pos, className, ref) {
    const el = document.createElement('div')
    el.className = `sky-label ${className}`
    el.textContent = text
    this.labelRoot.appendChild(el)
    this.labels.push({ el, pos, ref, sphere: null })
  }

  /** stars: [{name,ra,dec,v,bv,plx}], landmarks: [{label,ra,dec,distance_ly}] */
  setData(stars, landmarks) {
    const usable = stars.filter((s) => s.plx && s.plx >= MIN_PLX_MAS)
    this.count = usable.length
    const n = usable.length
    const real = new Float32Array(n * 3)
    const flat = new Float32Array(n * 3)
    const col = new Float32Array(n * 3)
    const mag = new Float32Array(n)
    this.objects = [] // for capture: each star with its real and on-the-sphere scene positions
    usable.forEach((s, i) => {
      const distance = LY_PER_PARSEC / s.plx
      const { dir, pos } = galacticXYZ(s.ra, s.dec, distance)
      const r = toScene(pos)
      const f = toScene(dir.map((c) => c * SPHERE_R))
      real.set([r.x, r.y, r.z], i * 3)
      flat.set([f.x, f.y, f.z], i * 3)
      col.set(bvToRGB(s.bv), i * 3)
      mag[i] = s.v
      this.objects.push({ name: s.name, ra: s.ra, dec: s.dec, v: s.v, plx: s.plx, pmra: s.pmra, pmdec: s.pmdec, real: r, flat: f })
    })
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(real, 3))
    g.setAttribute('aFlat', new THREE.BufferAttribute(flat, 3))
    g.setAttribute('aColor', new THREE.BufferAttribute(col, 3))
    g.setAttribute('aMag', new THREE.BufferAttribute(mag, 1))
    this.points = new THREE.Points(g, new THREE.ShaderMaterial({
      uniforms: { uDepth: { value: 0 }, uPx: { value: 1 }, uMaxPoint: { value: this.maxPoint }, uZoom: { value: 1 } },
      transparent: true, depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: /* glsl */ `
        attribute vec3 aFlat; attribute vec3 aColor; attribute float aMag;
        uniform float uDepth, uPx, uMaxPoint, uZoom; varying vec3 vColor; varying float vA;
        void main(){
          vec3 p = mix(aFlat, position, uDepth);
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          float dist = length(mv.xyz);
          float bright = clamp((5.0 - aMag)/5.5, 0.0, 1.0);
          // Gentle, bounded depth cue: nearer stars draw a little larger (at most 2x), farther ones smaller
          // (at least 0.5x). Unbounded 1/z sizing made stars balloon into screen-filling blobs as the camera passed.
          float atten = clamp(pow(400.0 / max(dist, 1.0), 0.6), 0.5, 2.0);
          gl_PointSize = min((3.5 + 9.0*pow(bright, 1.4)) * atten * uZoom * uPx, min(30.0 * uPx, uMaxPoint));
          gl_Position = projectionMatrix * mv;
          // fade a star out as the camera gets within a few light-years, so you pass by it instead of through a flash
          vColor = aColor; vA = (0.55 + 0.45*bright) * smoothstep(2.0, 12.0, dist);
        }`,
      fragmentShader: /* glsl */ `
        varying vec3 vColor; varying float vA;
        void main(){
          float d = length(gl_PointCoord - 0.5) * 2.0; if(d > 1.0) discard;
          gl_FragColor = vec4(vColor, (pow(1.0 - d, 2.0) + 0.3*exp(-d*d*5.0)) * vA);
        }`,
    }))
    this.points.frustumCulled = false
    this.scene.add(this.points)
    this.dirty = true

    landmarks.filter((l) => l.distance_ly && l.distance_ly <= 700).forEach((l) => {
      const { dir, pos } = galacticXYZ(l.ra, l.dec, l.distance_ly)
      this.addLabel(l.label, toScene(pos), 'marker landmark', { sphere: toScene(dir.map((c) => c * SPHERE_R)) })
      this.objects.push({ marker: true, label: l.label, ra: l.ra, dec: l.dec, pmra: l.pmra, pmdec: l.pmdec, otype: l.otype, distance_ly: l.distance_ly, real: toScene(pos), flat: toScene(dir.map((c) => c * SPHERE_R)) })
    })
  }

  // ---------------------------------------------------------------- capture hooks (same shape as SkyEngine's)
  /** Stars and landmarks drawn inside a canvas-pixel rectangle, where they are right now (true or flattened). */
  objectsIn({ x0, y0, x1, y1 }) {
    const w = this.canvas.clientWidth
    const h = this.canvas.clientHeight
    const v = new THREE.Vector3()
    const eye = this.camera.position
    this.camera.updateMatrixWorld()
    const out = []
    for (const o of this.objects ?? []) {
      v.lerpVectors(o.flat, o.real, this.depth)
      const d = v.distanceTo(eye)
      v.project(this.camera)
      if (v.z >= 1 || d < 2) continue
      const x = ((v.x + 1) / 2) * w
      const y = ((1 - v.y) / 2) * h
      if (x >= x0 && x <= x1 && y >= y0 && y <= y1) out.push({ ...o, x, y, mag: o.v, fromCamera_ly: d })
    }
    return out
  }

  /** A PNG of the view now (renders first: the drawing buffer isn't kept between frames). */
  snapshot() { this.renderer.render(this.scene, this.camera); return this.canvas.toDataURL('image/png') }

  /** Direction through a canvas pixel, as RA/Dec. It's the real sky direction only when the camera is at the Sun. */
  skyAt(x, y) {
    const w = this.canvas.clientWidth
    const h = this.canvas.clientHeight
    this.camera.updateMatrixWorld()
    const d = new THREE.Vector3((x / w) * 2 - 1, 1 - (y / h) * 2, 0.5).unproject(this.camera).sub(this.camera.position).normalize()
    // scene (x, y=up, z) -> galactic (x, -z, y), the inverse of toScene
    const gl = Math.atan2(-d.z, d.x) / DEG
    const gb = Math.asin(clamp(d.y, -1, 1)) / DEG
    const { ra, dec } = galacticToEquatorial(((gl % 360) + 360) % 360, gb)
    return { ra, dec }
  }

  /** True when the camera is standing at the Sun, so screen directions are real sky directions. */
  atSun() { return this.camera.position.length() < 1 }

  getState() { return { fov: this.camera.fov } }

  /** project(lon, lat) in radians for a direction seen from the Sun, like SkyEngine's view. */
  getView() {
    const cam = this.camera
    const w = this.canvas.clientWidth
    const h = this.canvas.clientHeight
    return {
      project(lon, lat) {
        const { dir } = galacticXYZ(lon / DEG, lat / DEG, 1)
        const p = toScene(dir).multiplyScalar(1000).add(cam.position).project(cam)
        return { x: ((p.x + 1) / 2) * w, y: ((1 - p.y) / 2) * h, visible: p.z < 1 }
      },
    }
  }

  /** Stop camera input while framing a capture. */
  freeze(on) { this.rig.setEnabled(!on && this.active) }

  setFlattened(flat) { this.depthTarget = flat ? 0 : 1; this.dirty = true }
  setActive(active) {
    this.active = active
    this.rig.setEnabled(active)
    if (active) { this.last = performance.now(); this.dirty = true }
  }
  setMode(mode) { this.rig.setMode(mode) }
  resetView() { this.rig.reset() }

  resize() {
    const parent = this.canvas.parentElement ?? this.canvas
    const w = Math.max(1, parent.clientWidth)
    const h = Math.max(1, parent.clientHeight)
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    this.renderer.setPixelRatio(dpr)
    this.renderer.setSize(w, h, false)
    this.canvas.style.width = `${w}px`
    this.canvas.style.height = `${h}px`
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    if (this.points) this.points.material.uniforms.uPx.value = dpr
    this.sun.material.uniforms.uPx.value = dpr
    this.dirty = true
  }

  updateLabels() {
    const w = this.canvas.clientWidth
    const h = this.canvas.clientHeight
    const t = this.depth
    const v = new THREE.Vector3()
    const eye = this.camera.position
    for (const l of this.labels) {
      v.copy(l.pos)
      if (l.ref?.sphere) v.lerpVectors(l.ref.sphere, l.pos, t)
      const hideRing = l.ref?.r && t < 0.5
      const tooClose = v.distanceTo(eye) < 10 // e.g. the Sun's label while standing at the Sun
      v.project(this.camera)
      const visible = v.z < 1 && Math.abs(v.x) < 1.05 && Math.abs(v.y) < 1.05 && !hideRing && !tooClose
      l.el.style.opacity = visible ? '' : '0'
      if (visible) l.el.style.transform = `translate(${((v.x + 1) / 2) * w}px, ${((1 - v.y) / 2) * h}px)`
    }
  }

  tick(now) {
    this.raf = requestAnimationFrame((t) => this.tick(t))
    if (!this.active) return
    const dt = Math.min(0.05, (now - this.last) / 1000)
    this.last = now
    let changed = this.rig.update(this.camera, dt) || this.dirty
    this.dirty = false
    if (this.depth !== this.depthTarget) {
      const k = 1 - Math.exp(-dt * (this.reduced ? 40 : 2.4))
      this.depth += (this.depthTarget - this.depth) * k
      if (Math.abs(this.depthTarget - this.depth) < 1e-3) this.depth = this.depthTarget
      changed = true
    }
    if (!changed) return // idle: no redraw
    if (this.points) {
      const u = this.points.material.uniforms
      u.uDepth.value = this.depth
      // a narrower field of view magnifies a little, like a telescope (sqrt keeps it subtle)
      u.uZoom.value = clamp(Math.sqrt(Math.tan(21 * DEG) / Math.tan((this.camera.fov / 2) * DEG)), 0.8, 2)
    }
    this.guides.visible = this.depth > 0.35
    this.guides.children.forEach((c) => { c.material.opacity = (c.material.userData.base ??= c.material.opacity) * Math.min(1, this.depth * 1.4) })
    // the Sun is a small sphere at the origin: hide it while the camera is standing inside it
    this.sun.visible = this.camera.position.length() > 12
    this.renderer.render(this.scene, this.camera)
    this.updateLabels()
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    this.resizeObserver.disconnect()
    this.rig.dispose()
    this.labels.forEach((l) => l.el.remove())
    this.scene.traverse((o) => {
      o.geometry?.dispose()
      o.material?.dispose()
    })
    this.renderer.dispose()
  }
}
