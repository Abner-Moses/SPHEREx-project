// Voyager's body: a full-screen, click-through canvas that draws the sprite, flies it between points, lands it with
// a squash, and extends a pixel boom to point at things. Targets are functions returning a screen point (or null),
// so Voyager keeps up when the sky is panned under it.
//
// Comfort rules match the rest of the app: nothing moves unless asked (no idle bobbing, only a blink now and then),
// flights are short eased arcs, and prefers-reduced-motion makes every move instant.

import { drawPigeon } from '../eggs.js'
import { ANCHOR, SIZE, sprite } from './sprite.js'

const S = 2 // screen pixels per sprite pixel
const HALF = (SIZE / 2) * S
const clamp = (x, a, b) => Math.min(b, Math.max(a, x))
const eio = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2)
const snap = (v) => Math.round(v / S) * S

export class VoyagerEngine {
  constructor(canvas, { onMove = () => {}, reduced = false } = {}) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d')
    this.onMove = onMove
    this.reduced = reduced
    this.pos = { x: -100, y: -100 }
    this.face = 'deadpan'
    this.flight = null
    this.land = null
    this.boom = null
    this.anchor = null // target getter Voyager is parked beside
    this.talking = false
    this.visitor = null // the rare pigeon on the dish: { stage: 'in' | 'sit' | 'out', t0, done }
    this.blinkUntil = 0
    this.raf = 0
    this.home = () => ({ x: innerWidth - 74, y: innerHeight - 168 })
    this.resize = () => {
      const dpr = Math.min(devicePixelRatio || 1, 2)
      canvas.width = Math.round(innerWidth * dpr)
      canvas.height = Math.round(innerHeight * dpr)
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      this.ctx.imageSmoothingEnabled = false
      if (!this.flight && !this.anchor && this.pos.x > 0) this.pos = this.clampPos(this.home())
      this.kick()
    }
    addEventListener('resize', this.resize)
    this.resize()
    this.blinker = setInterval(() => { if (!this.flight && this.face === 'deadpan') { this.blinkUntil = performance.now() + 130; this.kick(); setTimeout(() => this.kick(), 140) } }, 4600)
  }

  clampPos(p) { return { x: clamp(p.x, 44, innerWidth - 44), y: clamp(p.y, 96, innerHeight - 96) } }

  /** Where to park next to a target point: beside it, on whichever side has room. */
  parkBeside(p) {
    const left = p.x > 220
    return this.clampPos({ x: p.x + (left ? -(HALF * 2 + 34) : HALF * 2 + 34), y: p.y - 6 })
  }

  place(p) { this.pos = { ...p }; this.kick() }
  setFace(face) { this.face = face; this.kick() }
  setTalking(on) { this.talking = on; this.kick() }

  /** Fly beside a target (getter -> {x,y} | null). Resolves when landed (or immediately if the target is gone). */
  flyTo(getTarget, { face, exact = false } = {}) {
    return new Promise((resolve) => {
      const p0 = getTarget?.()
      if (!p0) { resolve(false); return }
      this.boom = null
      this.anchor = null
      if (this.visitor && this.visitor.stage !== 'out') this.pigeonLeave() // it won't ride along
      const spot = (p) => (exact ? this.clampPos(p) : this.parkBeside(p))
      const end = () => { const p = getTarget(); return p ? spot(p) : this.clampPos(this.home()) }
      const from = { ...this.pos }
      const dist = Math.hypot(end().x - from.x, end().y - from.y)
      if (this.reduced || dist < 4) { this.pos = end(); this.anchor = exact ? null : { get: getTarget }; if (face) this.face = face; this.kick(); resolve(true); return }
      this.flight = { from, end, t0: performance.now(), dur: clamp(dist / 900, 0.45, 1.1) * 1000, lift: Math.min(70, dist * 0.18), face, resolve, getTarget, exact }
      this.kick()
    })
  }

  flyHome() { this.point(null); return this.flyTo(() => this.home(), { exact: true }) }

  /** Extend the boom toward a target getter, or retract it with null. */
  point(getTarget) {
    this.boom = getTarget ? { get: getTarget, t0: performance.now() } : null
    this.kick()
  }

  /** Easter egg: a pigeon lands on the dish. Resolves when it has landed. */
  pigeonVisit() {
    if (this.reduced || this.visitor) return Promise.resolve(false)
    return new Promise((done) => { this.visitor = { stage: 'in', t0: performance.now(), done }; this.kick() })
  }
  pigeonLeave() {
    if (!this.visitor || this.visitor.stage === 'out') return Promise.resolve()
    return new Promise((done) => { this.visitor = { stage: 'out', t0: performance.now(), done, from: this.visitor.at }; this.kick() })
  }

  kick() { if (!this.raf) this.raf = requestAnimationFrame((t) => this.frame(t)) }

  frame(now) {
    this.raf = 0
    let busy = false
    let sx = 1
    let sy = 1
    let flame = 0
    let face = now < this.blinkUntil ? 'blink' : this.face
    if (this.flight) {
      const f = this.flight
      const t = Math.min(1, (now - f.t0) / f.dur)
      const e = eio(t)
      const to = f.end()
      const arc = Math.sin(Math.PI * t)
      this.pos = { x: f.from.x + (to.x - f.from.x) * e, y: f.from.y + (to.y - f.from.y) * e - arc * f.lift }
      sx = 1 + 0.14 * arc
      sy = 1 - 0.11 * arc
      flame = 1 + (Math.floor(now / 70) % 2)
      face = 'deadpan'
      busy = true
      if (t >= 1) {
        this.flight = null
        this.anchor = f.exact ? null : { get: f.getTarget }
        this.land = { t0: now, face: f.face }
        f.resolve(true)
      }
    } else if (this.land) {
      const k = (now - this.land.t0) / 1000
      if (k < 0.12) { const q = k / 0.12; sx = 1.22 - 0.22 * q; sy = 0.8 + 0.2 * q; face = 'surprised' } else if (k < 0.26) { this.bounce = -Math.round(4 * Math.sin(((k - 0.12) / 0.14) * Math.PI)) } else { this.bounce = 0; if (this.land.face) this.face = this.land.face; this.land = null; face = this.face }
      busy = true
    } else if (this.anchor) {
      const p = this.anchor.get()
      if (!p) { this.anchor = null; this.boom = null; this.flyHome() } else { this.pos = this.parkBeside(p) }
      busy = this.boom != null // keep tracking while pointing (the sky may be moving)
    }
    if (this.talking || this.visitor) busy = true
    this.draw(now, sx, sy, flame, face)
    this.onMove(this.pos)
    if (busy || (this.boom && now - this.boom.t0 < 300)) this.kick()
  }

  draw(now, sx, sy, flame, face) {
    const g = this.ctx
    g.clearRect(0, 0, innerWidth, innerHeight)
    const bob = this.talking && !this.reduced ? Math.round(Math.sin(now / 90)) * S : 0
    const x = snap(this.pos.x)
    const y = snap(this.pos.y) + (this.bounce || 0) + bob
    // boom (drawn first so the body sits on top of its root)
    if (this.boom) {
      const p = this.boom.get()
      if (p) {
        const right = p.x >= x
        const [ax, ay] = right ? ANCHOR.boomRight : ANCHOR.boomLeft
        const ox = x - HALF + ax * S
        const oy = y - HALF + ay * S
        const dx = p.x - ox
        const dy = p.y - oy
        const dist = Math.hypot(dx, dy)
        const k = this.reduced ? 1 : eio(clamp((now - this.boom.t0) / 250, 0, 1))
        const len = Math.max(0, dist - 10) * k
        const ux = dx / (dist || 1)
        const uy = dy / (dist || 1)
        for (let s = 0; s <= len; s += S) { g.fillStyle = '#0b0d10'; g.fillRect(snap(ox + ux * s) - 1, snap(oy + uy * s) - 1, S + 2, S + 2) }
        for (let s = 0; s <= len; s += S) { g.fillStyle = '#8f989f'; g.fillRect(snap(ox + ux * s), snap(oy + uy * s), S, S) }
        if (len > 6) {
          const tx = snap(ox + ux * len)
          const ty = snap(oy + uy * len)
          g.fillStyle = '#0b0d10'; g.fillRect(tx - 3, ty - 3, 3 * S + 2, 3 * S + 2)
          g.fillStyle = '#f2b705'; g.fillRect(tx - 2, ty - 2, 3 * S, 3 * S)
        }
        if (k >= 1) { // focus brackets on what's being pointed at
          const r = 12
          g.fillStyle = '#f2b705'
          for (const [cx, cy, hx, hy] of [[-1, -1, 1, 1], [1, -1, -1, 1], [-1, 1, 1, -1], [1, 1, -1, -1]]) {
            const bx = snap(p.x + cx * r)
            const by = snap(p.y + cy * r)
            g.fillRect(bx, by, 6 * hx || S, S); g.fillRect(bx, by, S, 6 * hy || S)
          }
        }
      }
    }
    // thruster flame while flying
    if (flame) {
      for (const [tx, ty] of ANCHOR.thrusters) {
        g.fillStyle = flame === 1 ? '#f2b705' : '#e0393b'
        g.fillRect(x - HALF + tx * S, y - HALF + ty * S, S, S * (1 + (flame % 2)))
      }
    }
    // body, squashed and stretched from the feet so the face stays readable
    g.save()
    g.translate(x, y + HALF)
    g.scale(S * sx, S * sy)
    g.drawImage(sprite(face), -SIZE / 2, -SIZE)
    g.restore()
    if (this.visitor) this.drawVisitor(now, x, y)
  }

  drawVisitor(now, x, y) {
    const v = this.visitor
    const perch = { x: x - HALF + 18 * S, y: y - HALF - 4 * S } // feet on the dish rim
    const k = (now - v.t0) / 1000
    const flap = Math.floor(now / 80) % 2 ? 'up' : 'down'
    if (v.stage === 'in') {
      const q = clamp(k / 1.3, 0, 1)
      const e = 1 - (1 - q) ** 3
      const from = { x: perch.x + 260, y: perch.y - 220 }
      v.at = { x: from.x + (perch.x - from.x) * e, y: from.y + (perch.y - from.y) * e - Math.sin(Math.PI * q) * 30 }
      drawPigeon(this.ctx, q < 0.92 ? flap : 'perched', snap(v.at.x), snap(v.at.y), S)
      if (q >= 1) { v.stage = 'sit'; v.t0 = now; v.done?.(true); v.done = null }
    } else if (v.stage === 'sit') {
      v.at = perch
      drawPigeon(this.ctx, Math.floor(k / 0.45) % 3 === 1 ? 'bob' : 'perched', snap(perch.x), snap(perch.y), S)
    } else {
      const q = clamp(k / 1.1, 0, 1)
      const from = v.from || perch
      drawPigeon(this.ctx, flap, snap(from.x - q * q * 320), snap(from.y - q * 240), S, true)
      if (q >= 1) { const d = v.done; this.visitor = null; d?.() }
    }
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    clearInterval(this.blinker)
    removeEventListener('resize', this.resize)
  }
}
