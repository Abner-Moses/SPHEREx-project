// Comfort invariants of the 3D camera (engine/cameraRig.js), checked against a real three.js camera.
import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'

globalThis.window ??= { addEventListener() {}, removeEventListener() {} }
const { CameraRig } = await import('../engine/cameraRig.js')

const W = 800
const H = 600
const el = { clientWidth: W, clientHeight: H, style: {}, addEventListener() {}, removeEventListener() {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: W, height: H }) }
const views = {
  orbit: { pivot: [0, 0, 0], distance: 800, yaw: 0.9, pitch: -0.4, fov: 42 },
  look: { pivot: [0, 0, 0], distance: 0, yaw: 0.3, pitch: 0.2, fov: 60 },
}
const make = (mode = 'look') => new CameraRig(el, { mode, reduced: true, views, distance: [100, 3000], fov: [6, 100] })
const cam = () => new THREE.PerspectiveCamera(60, W / H, 0.1, 1e5)
const settle = (rig, c, n = 5) => { for (let i = 0; i < n; i++) rig.update(c, 0.016); c.updateMatrixWorld() }
const toScreen = (c, v) => { const p = v.clone().project(c); return { x: ((p.x + 1) / 2) * W, y: ((1 - p.y) / 2) * H } }
const unproject = (c, x, y) => new THREE.Vector3((x / W) * 2 - 1, 1 - (y / H) * 2, 0.5).unproject(c).sub(c.position).normalize().multiplyScalar(1000).add(c.position)

function dragSteps(rig, a, b, steps = 20) {
  let prev = { x: a[0], y: a[1] }
  for (let i = 1; i <= steps; i++) {
    const p = { x: a[0] + ((b[0] - a[0]) * i) / steps, y: a[1] + ((b[1] - a[1]) * i) / steps }
    rig.drag(prev, p)
    prev = p
  }
}

test('look mode grabs the sky: the point under the pointer stays under it', () => {
  for (const [a, b] of [[[200, 255], [450, 180]], [[100, 100], [700, 500]], [[400, 300], [420, 290]], [[650, 150], [150, 400]]]) {
    const rig = make('look')
    const c = cam()
    settle(rig, c)
    const star = unproject(c, ...a)
    dragSteps(rig, a, b)
    settle(rig, c)
    const s = toScreen(c, star)
    const err = Math.hypot(s.x - b[0], s.y - b[1])
    assert.ok(err < 0.5, `drag ${a} -> ${b} landed at ${s.x.toFixed(1)},${s.y.toFixed(1)} (${err.toFixed(1)} px off)`)
  }
})

test('the horizon never rolls and pitch stops short of the poles', () => {
  for (const mode of ['look', 'orbit']) {
    const rig = make(mode)
    const c = cam()
    for (let i = 0; i < 200; i++) rig.drag({ x: 400, y: 300 }, { x: 400 + (i % 7) * 9, y: 300 - 40 })
    settle(rig, c, 20)
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(c.quaternion)
    assert.ok(Math.abs(right.y) < 1e-9, `${mode}: camera rolled (right.y = ${right.y})`)
    assert.ok(Math.abs(rig.cur.pitch) <= (88 * Math.PI) / 180 + 1e-9, `${mode}: pitch ${rig.cur.pitch} past the limit`)
  }
})

test('look-mode zoom changes the field of view, never the camera position', () => {
  const rig = make('look')
  const c = cam()
  settle(rig, c)
  const before = c.position.clone()
  rig.zoomBy(0.5)
  rig.o.reduced = false
  let last = rig.cur.fov
  for (let i = 0; i < 120; i++) {
    rig.update(c, 1 / 60)
    assert.ok(rig.cur.fov <= last + 1e-9, 'zoom overshot / reversed')
    last = rig.cur.fov
  }
  assert.ok(Math.abs(rig.cur.fov - 30) < 1e-6)
  assert.ok(c.position.distanceTo(before) < 1e-9)
})

test('keyboard movement eases in and out (no jumps)', () => {
  const rig = make('look')
  rig.o.reduced = false
  rig.o.speed = 10
  const c = cam()
  settle(rig, c)
  rig.keys.add('f')
  let prev = 0
  const speeds = []
  for (let i = 0; i < 90; i++) { rig.update(c, 1 / 60); speeds.push(rig.vel.length()) }
  rig.keys.clear()
  for (let i = 0; i < 120; i++) { rig.update(c, 1 / 60); speeds.push(rig.vel.length()) }
  assert.ok(speeds[0] < 1.5, `starts too abruptly: ${speeds[0]}`)
  assert.ok(Math.abs(speeds[89] - 10) < 0.2, `doesn't reach cruise speed: ${speeds[89]}`)
  for (const s of speeds) { assert.ok(Math.abs(s - prev) < 1.5, 'speed jumped between frames'); prev = s }
  assert.equal(speeds.at(-1), 0)
})
