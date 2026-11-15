// Camera for the 3D views. One state, two ways of using it:
//
//   orbit — turntable around a pivot (the overview). The camera sits `distance` behind the pivot.
//   look  — Stellarium-style: you stand at the pivot and turn your head (distance 0). Zoom narrows the
//           field of view instead of moving you, so nothing rushes at the viewer.
//
// Comfort rules (the reasons the old orbit camera felt nauseating, each fixed here):
//   * The horizon never rolls: `up` is always world +Y and pitch stops short of straight up/down, so the
//     view can't whirl around the screen centre near the poles or flip upside down.
//   * Look-mode dragging grabs the sky: the point under the pointer stays under it (rate = field of view
//     per pixel). Orbit dragging is gentle (120° per screen height) instead of a fixed 0.34°/px.
//   * Angles follow input through a short (50 ms) critically damped smoothing, which removes mouse jitter
//     but never coasts on after release.
//   * Zoom (field of view or distance) is a critically damped spring on the log: starts from rest, no overshoot.
//   * Keyboard movement (WASD/QE, arrows to turn) eases in and out over ~0.2 s, never jumps.
//   * Programmatic moves (reset, mode switch) take a fixed time with smoothstep ease-in-out.
//   * Nothing moves on its own, and `prefers-reduced-motion` makes every animated move instant.

import * as THREE from 'three'

const DEG = Math.PI / 180
const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x))
const smoothstep = (t) => t * t * (3 - 2 * t)
const wrapPi = (a) => a - 2 * Math.PI * Math.floor((a + Math.PI) / (2 * Math.PI))

/** Viewing direction: yaw turns about +Y (0 looks toward -Z, positive turns left), pitch is up from the horizon. */
export function forwardOf(yaw, pitch, out = new THREE.Vector3()) {
  const cp = Math.cos(pitch)
  return out.set(-Math.sin(yaw) * cp, Math.sin(pitch), -Math.cos(yaw) * cp)
}

const MOVE_KEYS = { KeyW: 'f', KeyS: 'b', KeyA: 'l', KeyD: 'r', KeyE: 'u', KeyQ: 'd', ArrowLeft: 'tl', ArrowRight: 'tr', ArrowUp: 'tu', ArrowDown: 'td' }

export class CameraRig {
  /**
   * @param {HTMLElement} el  element that receives pointer/wheel input
   * @param {object} o
   *   views: { orbit: {pivot,distance,yaw,pitch,fov}, look?: {...} }   home views for each mode
   *   mode: 'orbit' | 'look'
   *   distance: [min,max], fov: [min,max], orbitPitch: [lo,hi] (degrees), lookPitch: [lo,hi] (degrees)
   *   speed: movement speed in world units/s (orbit mode scales it with distance)
   *   clampPivot(v): keep the pivot inside the scene
   */
  constructor(el, o) {
    this.el = el
    this.o = { distance: [1, 10], fov: [10, 90], orbitPitch: [-80, 80], lookPitch: [-88, 88], speed: 1, clampPivot: () => {}, reduced: false, ...o }
    this.mode = o.mode ?? 'orbit'
    const home = this.o.views[this.mode]
    this.cur = this.makeState(home)
    this.goal = this.makeState(home)
    this.zoomV = 0
    this.vel = new THREE.Vector3()
    this.turn = { x: 0, y: 0 }
    this.anim = null
    this.keys = new Set()
    this.shift = false
    this.enabled = true
    this.dirty = true
    this.pointers = new Map()
    this.pinch = null
    this.fwd = new THREE.Vector3()
    this.tmp = new THREE.Vector3()
    this.bind()
  }

  makeState(v) {
    return { pivot: new THREE.Vector3().fromArray(v.pivot ?? [0, 0, 0]), distance: v.distance ?? 0, yaw: v.yaw ?? 0, pitch: v.pitch ?? 0, fov: v.fov ?? 45 }
  }

  get pitchLimits() {
    const [lo, hi] = this.mode === 'look' ? this.o.lookPitch : this.o.orbitPitch
    return [lo * DEG, hi * DEG]
  }

  // ------------------------------------------------------------------ input
  bind() {
    const el = this.el
    this.h = {
      down: (e) => {
        if (e.button !== 0 && e.pointerType === 'mouse') return
        el.setPointerCapture(e.pointerId)
        this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
        this.anim = null
        this.grab = null
        if (this.pointers.size === 2) { const [a, b] = [...this.pointers.values()]; this.pinch = Math.hypot(a.x - b.x, a.y - b.y) }
      },
      move: (e) => {
        const p = this.pointers.get(e.pointerId)
        if (!p) return
        const from = { x: p.x, y: p.y }
        p.x = e.clientX
        p.y = e.clientY
        if (this.pointers.size === 1) this.drag(from, p)
        else if (this.pointers.size === 2 && this.pinch) {
          const [a, b] = [...this.pointers.values()]
          const d = Math.hypot(a.x - b.x, a.y - b.y)
          this.zoomBy(this.pinch / Math.max(d, 1))
          this.pinch = d
        }
      },
      up: (e) => {
        this.pointers.delete(e.pointerId)
        if (this.pointers.size < 2) this.pinch = null
        this.grab = null
      },
      wheel: (e) => {
        e.preventDefault()
        // one notch (~100 px or 3 lines) ≈ 17%; trackpads send many small deltas
        const px = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY
        this.zoomBy(Math.exp(clamp(px, -250, 250) * 0.0016))
      },
      keydown: (e) => {
        if (!this.enabled || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return
        this.shift = e.shiftKey
        if (e.code === 'Home' || (e.code === 'KeyR' && !e.repeat)) { e.preventDefault(); this.reset(); return }
        if (MOVE_KEYS[e.code]) {
          e.preventDefault()
          this.keys.add(MOVE_KEYS[e.code])
          this.anim = null
          this.dirty = true
        }
      },
      keyup: (e) => {
        this.shift = e.shiftKey
        if (MOVE_KEYS[e.code]) this.keys.delete(MOVE_KEYS[e.code])
      },
      blur: () => { this.keys.clear(); this.shift = false },
    }
    el.style.touchAction = 'none'
    el.addEventListener('pointerdown', this.h.down)
    el.addEventListener('pointermove', this.h.move)
    el.addEventListener('pointerup', this.h.up)
    el.addEventListener('pointercancel', this.h.up)
    el.addEventListener('wheel', this.h.wheel, { passive: false })
    window.addEventListener('keydown', this.h.keydown)
    window.addEventListener('keyup', this.h.keyup)
    window.addEventListener('blur', this.h.blur)
  }

  /** Pointer moved from `a` to `b` (client coordinates). */
  drag(a, b) {
    if (this.mode !== 'look') { this.rotateBy(b.x - a.x, b.y - a.y); return }
    // Grab the sky exactly, as Stellarium does: remember the world direction first grabbed, then solve for the
    // yaw and pitch that put that direction back under the pointer (closed form, no roll).
    if (!this.grab) this.grab = forwardRay(this.localRay(a), this.goal.yaw, this.goal.pitch)
    const D = this.grab
    const l = this.localRay(b)
    // pitch: rotating l about X by p must give D.y  ->  l.y cos p - l.z sin p = D.y
    const A = l.y
    const B = -l.z
    const R = Math.hypot(A, B)
    const phi = Math.atan2(B, A)
    const c = Math.acos(clamp(D.y / R, -1, 1))
    const p1 = wrapPi(phi + c)
    const p2 = wrapPi(phi - c)
    const [lo, hi] = this.pitchLimits
    let p = Math.abs(p1 - this.goal.pitch) < Math.abs(p2 - this.goal.pitch) ? p1 : p2
    p = clamp(p, lo, hi)
    // yaw: turn the pitched ray about Y until it points along D horizontally
    const zp = l.y * Math.sin(p) + l.z * Math.cos(p)
    const yaw = Math.atan2(D.x, D.z) - Math.atan2(l.x, zp)
    this.goal.yaw = this.goal.yaw + wrapPi(yaw - this.goal.yaw)
    this.goal.pitch = p
    this.anim = null
    this.dirty = true
  }

  /** Unit ray through a client point, in camera coordinates (x right, y up, -z forward). */
  localRay(pt) {
    const r = this.el.getBoundingClientRect()
    const f = r.height / 2 / Math.tan((this.cur.fov * DEG) / 2)
    return new THREE.Vector3(pt.x - r.left - r.width / 2, r.top + r.height / 2 - pt.y, -f).normalize()
  }

  /** Orbit-style drag by (dx, dy) CSS pixels. */
  rotateBy(dx, dy) {
    const h = Math.max(1, this.el.clientHeight)
    let k
    let sign
    if (this.mode === 'look') {
      // grab the sky: the angle one pixel spans at the centre of the view
      k = (2 * Math.tan((this.cur.fov * DEG) / 2)) / h
      sign = 1
    } else {
      k = (120 * DEG) / h
      sign = -1
    }
    // horizontal drags near the top/bottom of the sky cover less yaw per pixel; compensate (capped)
    const yawK = this.mode === 'look' ? k / Math.max(Math.cos(this.goal.pitch), 0.35) : k
    const [lo, hi] = this.pitchLimits
    this.goal.yaw += sign * dx * yawK
    this.goal.pitch = clamp(this.goal.pitch + sign * dy * k, lo, hi)
    this.anim = null
    this.dirty = true
  }

  zoomBy(factor) {
    this.anim = null
    if (this.mode === 'look') this.goal.fov = clamp(this.goal.fov * factor, ...this.o.fov)
    else this.goal.distance = clamp(this.goal.distance * factor, ...this.o.distance)
    this.dirty = true
  }

  // ------------------------------------------------------------------ programmatic moves
  /** Ease to a view over `seconds` (smoothstep). Missing fields keep their current value. */
  flyTo(view, seconds = 1) {
    const to = {
      pivot: view.pivot ? new THREE.Vector3().fromArray(view.pivot) : this.cur.pivot.clone(),
      distance: view.distance ?? this.cur.distance,
      yaw: view.yaw ?? this.cur.yaw,
      pitch: view.pitch ?? this.cur.pitch,
      fov: view.fov ?? this.cur.fov,
    }
    to.yaw = this.cur.yaw + wrapPi(to.yaw - this.cur.yaw) // take the short way round
    const from = { ...this.cur, pivot: this.cur.pivot.clone() }
    this.vel.set(0, 0, 0)
    this.turn = { x: 0, y: 0 }
    this.zoomV = 0
    if (this.o.reduced || seconds <= 0) { this.setBoth(to); this.anim = null } else this.anim = { from, to, t: 0, seconds }
    this.dirty = true
  }

  setBoth(s) {
    for (const st of [this.cur, this.goal]) {
      st.pivot.copy(s.pivot)
      st.distance = s.distance
      st.yaw = s.yaw
      st.pitch = s.pitch
      st.fov = s.fov
    }
  }

  setMode(mode, seconds = 1.2) {
    if (mode === this.mode || !this.o.views[mode]) return
    this.mode = mode
    const v = this.o.views[mode]
    // keep facing the same way where that makes sense, so the switch reads as one continuous move
    this.flyTo({ ...v, yaw: mode === 'look' ? this.cur.yaw : v.yaw }, seconds)
  }

  reset() { this.flyTo(this.o.views[this.mode], 0.9) }

  // ------------------------------------------------------------------ per frame
  /** Advance and write the camera. Returns true while anything is still changing. */
  update(camera, dtRaw) {
    const dt = Math.min(dtRaw, 1 / 20)
    let moving = this.dirty
    this.dirty = false
    const c = this.cur
    const g = this.goal

    if (this.anim) {
      const a = this.anim
      a.t = Math.min(1, a.t + dt / a.seconds)
      const s = smoothstep(a.t)
      c.pivot.lerpVectors(a.from.pivot, a.to.pivot, s)
      // distance and fov change in log space so the apparent speed stays even (distance 0 handled linearly)
      c.distance = a.from.distance > 0 && a.to.distance > 0 ? Math.exp(lerp(Math.log(a.from.distance), Math.log(a.to.distance), s)) : lerp(a.from.distance, a.to.distance, s)
      c.yaw = lerp(a.from.yaw, a.to.yaw, s)
      c.pitch = lerp(a.from.pitch, a.to.pitch, s)
      c.fov = Math.exp(lerp(Math.log(a.from.fov), Math.log(a.to.fov), s))
      this.setBoth(c)
      if (a.t >= 1) this.anim = null
      moving = true
    } else {
      moving = this.stepKeys(dt) || moving
      // angles: 50 ms critically damped follow (no coasting past where the pointer left them)
      const ka = this.o.reduced ? 1 : 1 - Math.exp(-dt / 0.05)
      const dy = g.yaw - c.yaw
      const dp = g.pitch - c.pitch
      if (Math.abs(dy) > 1e-6 || Math.abs(dp) > 1e-6) { c.yaw += dy * ka; c.pitch += dp * ka; moving = true } else { c.yaw = g.yaw; c.pitch = g.pitch }
      // zoom: critically damped spring on log(fov) in look mode, log(distance) in orbit mode
      const key = this.mode === 'look' ? 'fov' : 'distance'
      if (c[key] !== g[key] && c[key] > 0) {
        const w = this.o.reduced ? 400 : 12
        const x = Math.log(c[key])
        const goal = Math.log(g[key])
        this.zoomV += (w * w * (goal - x) - 2 * w * this.zoomV) * dt
        const nx = x + this.zoomV * dt
        c[key] = Math.exp(nx)
        if (Math.abs(goal - nx) < 2e-4 && Math.abs(this.zoomV) < 2e-3) { c[key] = g[key]; this.zoomV = 0 }
        moving = true
      }
      if (!c.pivot.equals(g.pivot)) { c.pivot.copy(g.pivot); moving = true }
    }

    // write the camera: eye sits `distance` behind the pivot along the view direction; horizon always level
    forwardOf(c.yaw, c.pitch, this.fwd)
    camera.position.copy(c.pivot).addScaledVector(this.fwd, -c.distance)
    camera.up.set(0, 1, 0)
    camera.lookAt(this.tmp.copy(camera.position).add(this.fwd))
    if (camera.fov !== c.fov) { camera.fov = c.fov; camera.updateProjectionMatrix() }
    return moving
  }

  stepKeys(dt) {
    const k = this.keys
    const boost = this.shift ? 3.5 : 1
    // movement in the camera's frame. Orbit: W/S/A/D slide the pivot over the ground plane. Look: fly where you face.
    const f = this.mode === 'look' ? forwardOf(this.cur.yaw, this.cur.pitch, new THREE.Vector3()) : forwardOf(this.cur.yaw, 0, new THREE.Vector3())
    const r = new THREE.Vector3(Math.cos(this.cur.yaw), 0, -Math.sin(this.cur.yaw))
    const want = new THREE.Vector3()
    if (k.has('f')) want.add(f)
    if (k.has('b')) want.sub(f)
    if (k.has('r')) want.add(r)
    if (k.has('l')) want.sub(r)
    if (k.has('u')) want.y += 1
    if (k.has('d')) want.y -= 1
    const speed = this.o.speed * boost * (this.mode === 'orbit' ? this.cur.distance / this.o.views.orbit.distance : 1)
    if (want.lengthSq() > 0) want.normalize().multiplyScalar(speed)
    const km = this.o.reduced ? 1 : 1 - Math.exp(-dt / 0.18) // ~0.2 s ease in and out
    this.vel.lerp(want, km)
    if (want.lengthSq() === 0 && this.vel.lengthSq() < (speed * 0.002) ** 2) this.vel.set(0, 0, 0)

    // arrows turn the view (left/right = look left/right, up/down = look up/down), slower when zoomed in
    const rate = 55 * DEG * boost * (this.mode === 'look' ? this.cur.fov / 60 : 1)
    const tx = ((k.has('tl') ? 1 : 0) - (k.has('tr') ? 1 : 0)) * rate
    const ty = ((k.has('tu') ? 1 : 0) - (k.has('td') ? 1 : 0)) * rate
    this.turn.x += (tx - this.turn.x) * km
    this.turn.y += (ty - this.turn.y) * km
    if (!tx && Math.abs(this.turn.x) < 1e-4) this.turn.x = 0
    if (!ty && Math.abs(this.turn.y) < 1e-4) this.turn.y = 0

    if (this.vel.lengthSq() === 0 && !this.turn.x && !this.turn.y) return false
    this.goal.pivot.addScaledVector(this.vel, dt)
    this.o.clampPivot(this.goal.pivot)
    const [lo, hi] = this.pitchLimits
    this.goal.yaw += this.turn.x * dt
    this.goal.pitch = clamp(this.goal.pitch + this.turn.y * dt, lo, hi)
    return true
  }

  setEnabled(on) {
    this.enabled = on
    if (!on) { this.keys.clear(); this.pointers.clear(); this.pinch = null }
  }

  dispose() {
    const el = this.el
    el.removeEventListener('pointerdown', this.h.down)
    el.removeEventListener('pointermove', this.h.move)
    el.removeEventListener('pointerup', this.h.up)
    el.removeEventListener('pointercancel', this.h.up)
    el.removeEventListener('wheel', this.h.wheel)
    window.removeEventListener('keydown', this.h.keydown)
    window.removeEventListener('keyup', this.h.keyup)
    window.removeEventListener('blur', this.h.blur)
  }
}

const lerp = (a, b, t) => a + (b - a) * t

/** Camera-frame ray -> world direction for a camera at (yaw, pitch) with a level horizon. */
function forwardRay(l, yaw, pitch) {
  const y = l.y * Math.cos(pitch) - l.z * Math.sin(pitch)
  const z = l.y * Math.sin(pitch) + l.z * Math.cos(pitch)
  return new THREE.Vector3(l.x * Math.cos(yaw) + z * Math.sin(yaw), y, -l.x * Math.sin(yaw) + z * Math.cos(yaw))
}

function isTyping(t) {
  if (!t) return false
  const tag = t.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable
}
