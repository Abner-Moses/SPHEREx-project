// WebGL viewer for one detector image. The 8-bit PNG lives in a single-channel texture; stretch,
// black/white points, inversion, colormap and the pixel grid are all applied in the fragment shader,
// so sliders respond instantly even on a 2040x2040 image.
//
// Coordinates: image pixels, x right, y DOWN, (0,0) = top-left corner of the top-left pixel.

import * as THREE from 'three'
import { buildLut } from '../lib/colormaps.js'

const STRETCH = { linear: 0, sqrt: 1, log: 2, asinh: 3 }

const material = (tex, lut, size) => new THREE.ShaderMaterial({
  uniforms: {
    uTex: { value: tex }, uLut: { value: lut }, uSize: { value: new THREE.Vector2(size[0], size[1]) },
    uBlack: { value: 0 }, uWhite: { value: 255 }, uStretch: { value: 0 }, uInvert: { value: 0 }, uGrid: { value: 0 },
  },
  vertexShader: /* glsl */ `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D uTex; uniform sampler2D uLut; uniform vec2 uSize;
    uniform float uBlack, uWhite, uStretch, uInvert, uGrid;
    varying vec2 vUv;
    float stretchFn(float x){
      if(uStretch < 0.5) return x;
      if(uStretch < 1.5) return sqrt(x);
      if(uStretch < 2.5) return log(1.0 + 1000.0*x) / log(1001.0);
      return log(10.0*x + sqrt(100.0*x*x + 1.0)) / log(10.0 + sqrt(101.0));   // asinh(10x)/asinh(10)
    }
    void main(){
      vec2 uv = vec2(vUv.x, 1.0 - vUv.y);                  // texture row 0 is the TOP of the image
      float raw = texture2D(uTex, uv).r * 255.0;
      float x = clamp((raw - uBlack) / max(uWhite - uBlack, 1.0), 0.0, 1.0);
      float y = stretchFn(x);
      if(uInvert > 0.5) y = 1.0 - y;
      vec3 col = texture2D(uLut, vec2(y*0.99609375 + 0.001953125, 0.5)).rgb;
      if(uGrid > 0.0){
        vec2 p = uv * uSize;
        vec2 g = abs(fract(p - 0.5) - 0.5) / fwidth(p);
        float line = 1.0 - min(min(g.x, g.y), 1.0);
        col = mix(col, vec3(0.02, 0.03, 0.06), line * 0.55 * uGrid);
      }
      gl_FragColor = vec4(col, 1.0);
    }`,
})

export class DetectorEngine {
  constructor(canvas, handlers = {}) {
    this.canvas = canvas
    this.handlers = handlers
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance' })
    this.renderer.setClearColor(0x05070d, 1)
    this.scene = new THREE.Scene()
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10)
    this.camera.position.z = 5
    this.image = null
    this.mesh = null
    this.lut = new THREE.DataTexture(buildLut('gray'), 256, 1, THREE.RGBAFormat, THREE.UnsignedByteType)
    this.lut.minFilter = this.lut.magFilter = THREE.LinearFilter
    this.lut.needsUpdate = true
    this.insets = { left: 0, right: 0 }
    this.view = { cx: 0, cy: 0, zoom: 1 }
    this.goal = { ...this.view }
    this.dirty = true
    this.active = true
    this.pointers = new Map()
    this.tool = 'inspect'
    this.reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.bind()
    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(canvas.parentElement ?? canvas)
    this.resize()
    this.raf = requestAnimationFrame(() => this.tick())
  }

  setTool(tool) { this.tool = tool }
  setActive(active) { this.active = active; this.dirty = true }
  setInsets(insets) { this.insets = insets; this.dirty = true }

  setImage({ width, height, gray }) {
    if (this.mesh) {
      this.scene.remove(this.mesh)
      this.mesh.geometry.dispose()
      this.mesh.material.uniforms.uTex.value.dispose()
      this.mesh.material.dispose()
    }
    const tex = new THREE.DataTexture(gray, width, height, THREE.RedFormat, THREE.UnsignedByteType)
    tex.unpackAlignment = 1
    tex.magFilter = THREE.NearestFilter
    tex.minFilter = THREE.LinearFilter
    tex.generateMipmaps = false
    tex.needsUpdate = true
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, height), material(tex, this.lut, [width, height]))
    this.mesh.position.set(width / 2, -height / 2, 0)
    this.scene.add(this.mesh)
    this.image = { width, height, gray }
    this.fit(true)
  }

  setLook({ black, white, stretch, invert, interpolate }) {
    if (!this.mesh) return
    const u = this.mesh.material.uniforms
    u.uBlack.value = black
    u.uWhite.value = white
    u.uStretch.value = STRETCH[stretch] ?? 0
    u.uInvert.value = invert ? 1 : 0
    const tex = u.uTex.value
    const filter = interpolate ? THREE.LinearFilter : THREE.NearestFilter
    if (tex.magFilter !== filter) { tex.magFilter = filter; tex.needsUpdate = true }
    this.dirty = true
  }

  setColormap(id) {
    this.lut.image.data.set(buildLut(id))
    this.lut.needsUpdate = true
    this.dirty = true
  }

  // ---------------------------------------------------------------- geometry of the view
  region() {
    const w = this.canvas.clientWidth
    const h = this.canvas.clientHeight
    const left = Math.min(this.insets.left, w * 0.45)
    const right = Math.min(this.insets.right, w * 0.45)
    return { w, h, cx: left + (w - left - right) / 2, cy: h / 2, rw: w - left - right }
  }

  fit(immediate = false) {
    if (!this.image) return
    const r = this.region()
    const zoom = Math.min(r.rw / this.image.width, r.h / this.image.height) * 0.9
    this.goal = { cx: this.image.width / 2, cy: this.image.height / 2, zoom }
    if (immediate || this.reduced) this.view = { ...this.goal }
    this.dirty = true
  }

  screenToImage(sx, sy, view = this.view) {
    const r = this.region()
    return { x: view.cx + (sx - r.cx) / view.zoom, y: view.cy + (sy - r.cy) / view.zoom }
  }

  imageToScreen(ix, iy, view = this.view) {
    const r = this.region()
    return { x: r.cx + (ix - view.cx) * view.zoom, y: r.cy + (iy - view.cy) * view.zoom }
  }

  zoomAt(factor, sx, sy) {
    if (!this.image) return
    const minZoom = 0.15
    const maxZoom = 80
    const zoom = Math.min(maxZoom, Math.max(minZoom, this.goal.zoom * factor))
    const r = this.region()
    const anchor = this.screenToImage(sx, sy, this.goal)
    this.goal = { zoom, cx: anchor.x - (sx - r.cx) / zoom, cy: anchor.y - (sy - r.cy) / zoom }
    this.dirty = true
  }

  panByPixels(dx, dy) {
    this.goal.cx -= dx / this.goal.zoom
    this.goal.cy -= dy / this.goal.zoom
    this.view.cx = this.goal.cx
    this.view.cy = this.goal.cy
    this.dirty = true
  }

  // ---------------------------------------------------------------- input
  bind() {
    const c = this.canvas
    this.hb = {
      down: (e) => {
        c.setPointerCapture(e.pointerId)
        // In line mode a left-drag draws; any other button (or any other tool) pans.
        const pan = this.tool !== 'line' || e.button !== 0
        this.pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY, moved: 0, button: e.button, pan })
        if (!pan && this.image) this.handlers.onLine?.('start', this.screenToImage(e.offsetX, e.offsetY))
      },
      move: (e) => {
        const p = this.pointers.get(e.pointerId)
        const img = this.image ? this.screenToImage(e.offsetX, e.offsetY) : null
        if (img) this.handlers.onHover?.({ x: img.x, y: img.y, inside: img.x >= 0 && img.y >= 0 && img.x < this.image.width && img.y < this.image.height })
        if (!p) return
        const dx = e.offsetX - p.x
        const dy = e.offsetY - p.y
        p.moved += Math.abs(dx) + Math.abs(dy)
        p.x = e.offsetX
        p.y = e.offsetY
        if (p.pan) this.panByPixels(dx, dy)
        else if (img) this.handlers.onLine?.('move', img)
      },
      up: (e) => {
        const p = this.pointers.get(e.pointerId)
        this.pointers.delete(e.pointerId)
        if (!p || !this.image) return
        const img = this.screenToImage(e.offsetX, e.offsetY)
        if (!p.pan) this.handlers.onLine?.('end', img)
        else if (p.moved < 5 && p.button === 0) this.handlers.onClick?.(img)
      },
      leave: () => this.handlers.onHover?.(null),
      wheel: (e) => {
        e.preventDefault()
        this.zoomAt(Math.exp(-e.deltaY * 0.0018), e.offsetX, e.offsetY)
      },
      menu: (e) => e.preventDefault(),
    }
    const hb = this.hb
    c.addEventListener('pointerdown', hb.down)
    c.addEventListener('pointermove', hb.move)
    c.addEventListener('pointerup', hb.up)
    c.addEventListener('pointercancel', hb.up)
    c.addEventListener('pointerleave', hb.leave)
    c.addEventListener('wheel', hb.wheel, { passive: false })
    c.addEventListener('contextmenu', hb.menu)
    c.style.touchAction = 'none'
  }

  resize() {
    const parent = this.canvas.parentElement ?? this.canvas
    const w = Math.max(1, parent.clientWidth)
    const h = Math.max(1, parent.clientHeight)
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.setSize(w, h, false)
    this.canvas.style.width = `${w}px`
    this.canvas.style.height = `${h}px`
    this.camera.left = -w / 2
    this.camera.right = w / 2
    this.camera.top = h / 2
    this.camera.bottom = -h / 2
    this.camera.updateProjectionMatrix()
    this.fit(false)
    this.dirty = true
  }

  tick() {
    this.raf = requestAnimationFrame(() => this.tick())
    if (!this.active) return
    const v = this.view
    const g = this.goal
    const k = this.reduced ? 1 : 0.22
    const moving = Math.abs(g.zoom - v.zoom) > v.zoom * 0.0005 || Math.abs(g.cx - v.cx) * v.zoom > 0.05 || Math.abs(g.cy - v.cy) * v.zoom > 0.05
    if (moving) {
      v.zoom += (g.zoom - v.zoom) * k
      v.cx += (g.cx - v.cx) * k
      v.cy += (g.cy - v.cy) * k
      this.dirty = true
    } else if (v.zoom !== g.zoom || v.cx !== g.cx || v.cy !== g.cy) {
      Object.assign(v, g)
      this.dirty = true
    }
    if (!this.dirty) return
    this.dirty = false
    const r = this.region()
    const cam = this.camera
    cam.zoom = v.zoom
    cam.position.x = v.cx - (r.cx - r.w / 2) / v.zoom
    cam.position.y = -v.cy
    cam.updateProjectionMatrix()
    if (this.mesh) this.mesh.material.uniforms.uGrid.value = Math.min(1, Math.max(0, (v.zoom - 6) / 8))
    this.renderer.render(this.scene, cam)
    this.handlers.onFrame?.()
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    this.resizeObserver.disconnect()
    const c = this.canvas
    const hb = this.hb
    c.removeEventListener('pointerdown', hb.down)
    c.removeEventListener('pointermove', hb.move)
    c.removeEventListener('pointerup', hb.up)
    c.removeEventListener('pointercancel', hb.up)
    c.removeEventListener('pointerleave', hb.leave)
    c.removeEventListener('wheel', hb.wheel)
    c.removeEventListener('contextmenu', hb.menu)
    this.mesh?.geometry.dispose()
    this.mesh?.material.uniforms.uTex.value.dispose()
    this.mesh?.material.dispose()
    this.lut.dispose()
    this.renderer.dispose()
  }
}
