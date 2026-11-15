// Minimal WebGL2 chart renderer. One hidden WebGL2 canvas is shared by every chart on the page (browsers cap live
// WebGL contexts at ~16), each chart is rendered into it and copied to the chart's own 2D canvas, then axes and text
// are drawn on top in 2D. All marks (points, lines, bars, areas, error bars, vectors, heatmaps, 3D surfaces) are WebGL.

import { buildLut } from '../lib/colormaps.js'

let G = null
const VS_FLAT = `#version 300 es
in vec2 aPos; in vec4 aCol; in float aSize; uniform vec2 uRes; out vec4 vCol;
void main(){ vec2 c = aPos / uRes * 2.0 - 1.0; gl_Position = vec4(c.x, -c.y, 0.0, 1.0); gl_PointSize = aSize; vCol = aCol; }`
const FS_FLAT = `#version 300 es
precision highp float; in vec4 vCol; uniform int uPoint; out vec4 o;
void main(){ if(uPoint == 1){ float r = length(gl_PointCoord - 0.5) * 2.0; if(r > 1.0) discard; o = vec4(vCol.rgb, vCol.a * smoothstep(1.0, 0.75, r)); } else o = vCol; }`
const VS_HEAT = `#version 300 es
in vec2 aPos; in vec2 aUv; uniform vec2 uRes; out vec2 vUv;
void main(){ vec2 c = aPos / uRes * 2.0 - 1.0; gl_Position = vec4(c.x, -c.y, 0.0, 1.0); vUv = aUv; }`
const FS_HEAT = `#version 300 es
precision highp float; in vec2 vUv; uniform sampler2D uData, uLut; uniform float uMin, uMax; uniform int uLog; out vec4 o;
void main(){ float v = texture(uData, vUv).r; if(isnan(v) || v < -1e37){ o = vec4(0.0); return; }
  float t = uLog == 1 ? (log(max(v, 1e-30)) - log(max(uMin, 1e-30))) / max(1e-9, log(max(uMax, 1e-30)) - log(max(uMin, 1e-30))) : (v - uMin) / max(1e-30, uMax - uMin);
  o = vec4(texture(uLut, vec2(clamp(t, 0.0, 1.0) * 0.996 + 0.002, 0.5)).rgb, 1.0); }`
const VS_SURF = `#version 300 es
in vec3 aPos; in vec3 aNrm; in float aT; uniform mat4 uMvp; out float vT; out float vL;
void main(){ gl_Position = uMvp * vec4(aPos, 1.0); vT = aT; vL = 0.35 + 0.65 * abs(dot(normalize(aNrm), normalize(vec3(0.4, 0.9, 0.35)))); }`
const FS_SURF = `#version 300 es
precision highp float; in float vT; in float vL; uniform sampler2D uLut; uniform int uWire; out vec4 o;
void main(){ vec3 c = texture(uLut, vec2(clamp(vT, 0.0, 1.0) * 0.996 + 0.002, 0.5)).rgb; o = uWire == 1 ? vec4(c * 0.35 + 0.1, 0.55) : vec4(c * vL, 1.0); }`

function compile(gl, vs, fs) {
  const p = gl.createProgram()
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type)
    gl.shaderSource(s, src)
    gl.compileShader(s)
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || 'shader')
    gl.attachShader(p, s)
  }
  gl.linkProgram(p)
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) || 'link')
  return p
}

export function webglAvailable() { try { return !!ctx() } catch { return false } }

function ctx() {
  if (G) return G
  const c = document.createElement('canvas')
  const gl = c.getContext('webgl2', { antialias: true, premultipliedAlpha: false, preserveDrawingBuffer: true, alpha: true })
  if (!gl) throw new Error('WebGL2 is not available in this browser')
  G = { c, gl, flat: compile(gl, VS_FLAT, FS_FLAT), heat: compile(gl, VS_HEAT, FS_HEAT), surf: compile(gl, VS_SURF, FS_SURF), buf: gl.createBuffer(), buf2: gl.createBuffer(), buf3: gl.createBuffer(), luts: new Map() }
  c.addEventListener('webglcontextlost', (e) => { e.preventDefault(); G = null })
  return G
}

function lut(name) {
  const g = ctx()
  if (g.luts.has(name)) return g.luts.get(name)
  const gl = g.gl
  const t = gl.createTexture()
  gl.bindTexture(gl.TEXTURE_2D, t)
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, buildLut(name))
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  g.luts.set(name, t)
  return t
}

export const rgba = (hex, a = 1) => {
  const h = hex.replace('#', '')
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h.slice(0, 6), 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255, a]
}

/** Begin a frame of size w x h CSS px. Returns a painter whose methods queue WebGL draws in device pixels. */
export function frame(w, h, dpr) {
  const g = ctx()
  const { gl, c } = g
  const W = Math.max(1, Math.round(w * dpr)), H = Math.max(1, Math.round(h * dpr))
  if (c.width !== W || c.height !== H) { c.width = W; c.height = H }
  gl.viewport(0, 0, W, H)
  gl.clearColor(0, 0, 0, 0)
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT)
  gl.enable(gl.BLEND)
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)
  const flat = (mode, pos, col, size) => {
    if (!pos.length) return
    gl.disable(gl.DEPTH_TEST)
    gl.useProgram(g.flat)
    const n = pos.length / 2
    const data = new Float32Array(n * 7)
    for (let i = 0; i < n; i++) { data[i * 7] = pos[i * 2] * dpr; data[i * 7 + 1] = pos[i * 2 + 1] * dpr; data.set(col.subarray(i * 4, i * 4 + 4), i * 7 + 2); data[i * 7 + 6] = (size ? size[i] : 1) * dpr }
    gl.bindBuffer(gl.ARRAY_BUFFER, g.buf)
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STREAM_DRAW)
    const loc = (k) => gl.getAttribLocation(g.flat, k)
    gl.enableVertexAttribArray(loc('aPos')); gl.vertexAttribPointer(loc('aPos'), 2, gl.FLOAT, false, 28, 0)
    gl.enableVertexAttribArray(loc('aCol')); gl.vertexAttribPointer(loc('aCol'), 4, gl.FLOAT, false, 28, 8)
    gl.enableVertexAttribArray(loc('aSize')); gl.vertexAttribPointer(loc('aSize'), 1, gl.FLOAT, false, 28, 24)
    gl.uniform2f(gl.getUniformLocation(g.flat, 'uRes'), W, H)
    gl.uniform1i(gl.getUniformLocation(g.flat, 'uPoint'), mode === gl.POINTS ? 1 : 0)
    gl.drawArrays(mode, 0, n)
  }
  return {
    gl, W, H,
    /** filled triangles: pos = [x,y,...] CSS px, col = Float32Array rgba per vertex */
    tris: (pos, col) => flat(gl.TRIANGLES, pos, col),
    points: (pos, col, size) => flat(gl.POINTS, pos, col, size),
    /** thick polyline segments as quads: segs = [x0,y0,x1,y1,...], one colour per segment */
    segments(segs, colors, width = 1.5) {
      const p = [], c = []
      for (let i = 0, k = 0; i < segs.length; i += 4, k++) {
        const x0 = segs[i], y0 = segs[i + 1], x1 = segs[i + 2], y1 = segs[i + 3]
        const dx = x1 - x0, dy = y1 - y0, L = Math.hypot(dx, dy) || 1, nx = (-dy / L) * width / 2, ny = (dx / L) * width / 2
        p.push(x0 + nx, y0 + ny, x1 + nx, y1 + ny, x1 - nx, y1 - ny, x0 + nx, y0 + ny, x1 - nx, y1 - ny, x0 - nx, y0 - ny)
        const col = colors.length === 4 ? colors : colors.slice(k * 4, k * 4 + 4)
        for (let v = 0; v < 6; v++) c.push(...col)
      }
      flat(gl.TRIANGLES, p, new Float32Array(c))
    },
    /** a scalar grid drawn as a texture through a colour map, into rect [x0,y0,x1,y1] CSS px */
    heat(grid, rect, { min, max, log = false, cmap = 'ember' }) {
      gl.disable(gl.DEPTH_TEST)
      gl.useProgram(g.heat)
      const tex = gl.createTexture()
      gl.activeTexture(gl.TEXTURE0)
      gl.bindTexture(gl.TEXTURE_2D, tex)
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
      const vals = Float32Array.from(grid.values, (v) => (v == null || !Number.isFinite(v) ? -3e38 : v))
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, grid.w, grid.h, 0, gl.RED, gl.FLOAT, vals)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
      gl.activeTexture(gl.TEXTURE1)
      gl.bindTexture(gl.TEXTURE_2D, lut(cmap))
      const [x0, y0, x1, y1] = rect.map((v) => v * dpr)
      const d = new Float32Array([x0, y0, 0, 0, x1, y0, 1, 0, x1, y1, 1, 1, x0, y0, 0, 0, x1, y1, 1, 1, x0, y1, 0, 1])
      gl.bindBuffer(gl.ARRAY_BUFFER, g.buf)
      gl.bufferData(gl.ARRAY_BUFFER, d, gl.STREAM_DRAW)
      const a = gl.getAttribLocation(g.heat, 'aPos'), u = gl.getAttribLocation(g.heat, 'aUv')
      gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 16, 0)
      gl.enableVertexAttribArray(u); gl.vertexAttribPointer(u, 2, gl.FLOAT, false, 16, 8)
      const U = (k) => gl.getUniformLocation(g.heat, k)
      gl.uniform2f(U('uRes'), W, H); gl.uniform1i(U('uData'), 0); gl.uniform1i(U('uLut'), 1)
      gl.uniform1f(U('uMin'), min); gl.uniform1f(U('uMax'), max); gl.uniform1i(U('uLog'), log ? 1 : 0)
      gl.drawArrays(gl.TRIANGLES, 0, 6)
      gl.deleteTexture(tex)
      gl.activeTexture(gl.TEXTURE0)
    },
    /** a height field as a lit 3D surface plus wireframe, rotated by yaw/pitch (radians) */
    surface(grid, { min, max, yaw = 0.7, pitch = 0.55, cmap = 'ember', zoom = 1 }) {
      const { w: nx, h: ny, values } = grid
      const z = (i, j) => { const v = values[j * nx + i]; return Number.isFinite(v) ? (v - min) / Math.max(1e-30, max - min) : 0 }
      const pos = new Float32Array(nx * ny * 3), nrm = new Float32Array(nx * ny * 3), t = new Float32Array(nx * ny)
      for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
        const k = j * nx + i, h = z(i, j)
        pos[k * 3] = (i / (nx - 1)) * 2 - 1; pos[k * 3 + 1] = h * 0.9 - 0.3; pos[k * 3 + 2] = (j / (ny - 1)) * 2 - 1; t[k] = h
        const dx = z(Math.min(nx - 1, i + 1), j) - z(Math.max(0, i - 1), j), dz = z(i, Math.min(ny - 1, j + 1)) - z(i, Math.max(0, j - 1))
        nrm[k * 3] = -dx * 0.9 * (nx - 1) / 4; nrm[k * 3 + 1] = 1; nrm[k * 3 + 2] = -dz * 0.9 * (ny - 1) / 4
      }
      const idx = [], wire = []
      for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) { const a = j * nx + i; idx.push(a, a + 1, a + nx, a + 1, a + nx + 1, a + nx); wire.push(a, a + 1, a, a + nx) }
      const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch), s = 0.62 * zoom, asp = W / H
      // orthographic view: yaw about Y, then pitch about X, viewer on +z (nearer = smaller depth). Column-major mat4.
      // x' = cy x + sy z;  z' = -sy x + cy z;  y'' = cp y - sp z';  depth = -(sp y + cp z') * 0.4
      const m = [cy * s / asp, sp * sy * s, cp * sy * 0.4, 0, 0, cp * s, -sp * 0.4, 0, sy * s / asp, -sp * cy * s, -cp * cy * 0.4, 0, 0, 0.05, 0, 1]
      gl.useProgram(g.surf)
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.clear(gl.DEPTH_BUFFER_BIT)
      const bind = (buf, arr, name, size) => { gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, arr, gl.STREAM_DRAW); const l = gl.getAttribLocation(g.surf, name); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, size, gl.FLOAT, false, 0, 0) }
      bind(g.buf, pos, 'aPos', 3); bind(g.buf2, nrm, 'aNrm', 3); bind(g.buf3, t, 'aT', 1)
      gl.uniformMatrix4fv(gl.getUniformLocation(g.surf, 'uMvp'), false, new Float32Array(m))
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, lut(cmap)); gl.uniform1i(gl.getUniformLocation(g.surf, 'uLut'), 0)
      const ib = gl.createBuffer()
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib)
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint32Array(idx), gl.STREAM_DRAW)
      gl.uniform1i(gl.getUniformLocation(g.surf, 'uWire'), 0)
      gl.drawElements(gl.TRIANGLES, idx.length, gl.UNSIGNED_INT, 0)
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint32Array(wire), gl.STREAM_DRAW)
      gl.uniform1i(gl.getUniformLocation(g.surf, 'uWire'), 1)
      gl.drawElements(gl.LINES, wire.length, gl.UNSIGNED_INT, 0)
      gl.deleteBuffer(ib)
      gl.disable(gl.DEPTH_TEST)
      return { project: (x, y, zz) => [((m[0] * x + m[4] * y + m[8] * zz + m[12]) + 1) / 2, (1 - (m[1] * x + m[5] * y + m[9] * zz + m[13])) / 2] }
    },
    /** copy the finished WebGL image onto a chart's 2D canvas */
    blit(target) { const t = target.getContext('2d'); t.setTransform(1, 0, 0, 1, 0, 0); t.clearRect(0, 0, target.width, target.height); t.drawImage(c, 0, 0) },
  }
}
