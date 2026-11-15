// 3D brightness surface: the detector (or a crop of it) as a height field. Height = display brightness
// (0..255 after the backend's percentile stretch), NOT physical flux. Shading comes from screen-space
// derivatives so the mesh needs only one height attribute; contour lines are drawn in the shader.

import * as THREE from 'three'
import { CameraRig } from './cameraRig.js'
import { buildLut } from '../lib/colormaps.js'

const material = (lut) => new THREE.ShaderMaterial({
  uniforms: { uScale: { value: 0.5 }, uRise: { value: 0 }, uLut: { value: lut }, uContours: { value: 1 } },
  side: THREE.DoubleSide,
  vertexShader: /* glsl */ `
    attribute float aH; uniform float uScale, uRise;
    varying float vH; varying vec3 vPos;
    void main(){
      vH = aH;
      vec3 p = vec3(position.x, aH * uScale * uRise, position.z);
      vPos = p;
      gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.0);
    }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D uLut; uniform float uContours;
    varying float vH; varying vec3 vPos;
    void main(){
      vec3 n = normalize(cross(dFdx(vPos), dFdy(vPos)));
      if(n.y < 0.0) n = -n;
      vec3 light = normalize(vec3(-0.5, 0.9, 0.4));
      float lit = 0.35 + 0.65 * max(dot(n, light), 0.0);
      vec3 col = texture2D(uLut, vec2(clamp(vH, 0.0, 1.0)*0.99609375 + 0.001953125, 0.5)).rgb * lit;
      float levels = 16.0;
      float f = vH * levels;
      float line = 1.0 - min(abs(fract(f - 0.5) - 0.5) / max(fwidth(f), 1e-4), 1.0);
      col = mix(col, vec3(1.0), line * 0.22 * uContours);
      gl_FragColor = vec4(col, 1.0);
    }`,
})

export class SurfaceEngine {
  constructor(canvas, handlers = {}) {
    this.canvas = canvas
    this.handlers = handlers
    this.reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' })
    this.renderer.setClearColor(0x05070d, 1)
    this.scene = new THREE.Scene()
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.05, 50)
    // Turntable only. Pitch stays between 6° and 85° looking down, so the camera can never dip under the floor
    // or reach straight overhead (where the view would whirl). WASD slides the focus point across the surface.
    this.rig = new CameraRig(canvas, {
      reduced: this.reduced,
      views: { orbit: { pivot: [0, 0.12, 0], distance: 3.3, yaw: 0.7, pitch: -0.57, fov: 40 } },
      distance: [1.2, 9],
      orbitPitch: [-85, -6],
      speed: 0.9,
      clampPivot: (v) => { v.x = Math.max(-1, Math.min(1, v.x)); v.z = Math.max(-1, Math.min(1, v.z)); v.y = Math.max(0, Math.min(0.8, v.y)) },
    })
    this.dirty = true
    this.lut = new THREE.DataTexture(buildLut('gray'), 256, 1, THREE.RGBAFormat, THREE.UnsignedByteType)
    this.lut.minFilter = this.lut.magFilter = THREE.LinearFilter
    this.lut.needsUpdate = true
    this.mesh = null
    this.rise = 0
    this.riseTarget = 1
    this.active = true
    this.addFloor()
    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(canvas.parentElement ?? canvas)
    this.resize()
    this.last = performance.now()
    this.raf = requestAnimationFrame((t) => this.tick(t))
  }

  addFloor() {
    const pts = []
    for (let i = -4; i <= 4; i++) {
      const t = i / 4
      pts.push(-1, 0, t, 1, 0, t, t, 0, -1, t, 0, 1)
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
    this.floor = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0x5b7099, transparent: true, opacity: 0.25 }))
    this.scene.add(this.floor)
  }

  setActive(active) {
    this.active = active
    this.rig.setEnabled(active)
    if (active) { this.last = performance.now(); this.dirty = true }
  }

  resetView() { this.rig.reset() }

  /** heights: Float32Array n*n in 0..1 (row-major, row 0 = top of the image region). */
  setHeights(heights, n) {
    if (this.mesh) {
      this.scene.remove(this.mesh)
      this.mesh.geometry.dispose()
      this.mesh.material.dispose()
    }
    const pos = new Float32Array(n * n * 3)
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        pos.set([(i / (n - 1)) * 2 - 1, 0, (j / (n - 1)) * 2 - 1], (j * n + i) * 3)
      }
    }
    const index = []
    for (let j = 0; j < n - 1; j++) {
      for (let i = 0; i < n - 1; i++) {
        const a = j * n + i
        index.push(a, a + n, a + 1, a + 1, a + n, a + n + 1)
      }
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    g.setAttribute('aH', new THREE.BufferAttribute(heights, 1))
    g.setIndex(index)
    this.mesh = new THREE.Mesh(g, material(this.lut))
    this.mesh.frustumCulled = false
    this.scene.add(this.mesh)
    this.rise = this.reduced ? 1 : 0
    this.exaggeration && this.setExaggeration(this.exaggeration)
    this.dirty = true
  }

  setExaggeration(value) {
    this.exaggeration = value
    if (this.mesh) this.mesh.material.uniforms.uScale.value = value
    this.dirty = true
  }

  setContours(on) { if (this.mesh) this.mesh.material.uniforms.uContours.value = on ? 1 : 0; this.dirty = true }

  setColormap(id) {
    this.lut.image.data.set(buildLut(id))
    this.lut.needsUpdate = true
    this.dirty = true
  }

  replayRise() { this.rise = this.reduced ? 1 : 0; this.dirty = true }

  resize() {
    const parent = this.canvas.parentElement ?? this.canvas
    const w = Math.max(1, parent.clientWidth)
    const h = Math.max(1, parent.clientHeight)
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.setSize(w, h, false)
    this.canvas.style.width = `${w}px`
    this.canvas.style.height = `${h}px`
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.dirty = true
  }

  tick(now) {
    this.raf = requestAnimationFrame((t) => this.tick(t))
    if (!this.active) return
    const dt = Math.min(0.05, (now - this.last) / 1000)
    this.last = now
    let changed = this.rig.update(this.camera, dt) || this.dirty
    this.dirty = false
    if (this.rise < 1) { this.rise = Math.min(1, this.rise + dt / 1.5); changed = true }
    if (!changed) return // idle: no redraw
    if (this.mesh) this.mesh.material.uniforms.uRise.value = 1 - (1 - this.rise) ** 3
    this.renderer.render(this.scene, this.camera)
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    this.resizeObserver.disconnect()
    this.rig.dispose()
    this.mesh?.geometry.dispose()
    this.mesh?.material.dispose()
    this.floor.geometry.dispose()
    this.floor.material.dispose()
    this.lut.dispose()
    this.renderer.dispose()
  }
}
