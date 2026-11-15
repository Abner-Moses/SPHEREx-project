import { useCallback, useEffect, useRef } from 'react'
import { DetectorEngine } from '../engine/detectorEngine.js'
import { pixelHover } from '../store.js'

const ACCENT = '#ff8a3d'

export default function DetectorView({ active, image, look, tool, aperture, line, insets, onAperture, onLine, fitSignal }) {
  const canvasRef = useRef(null)
  const overlayRef = useRef(null)
  const engineRef = useRef(null)
  const live = useRef({})
  live.current = { aperture, line, onAperture, onLine, tool }

  const draw = useCallback(() => {
    const engine = engineRef.current
    const canvas = overlayRef.current
    if (!engine || !canvas) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
    }
    const ctx = canvas.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    const { aperture: ap, line: ln } = live.current
    const zoom = engine.view.zoom

    const hover = pixelHover.get()
    if (hover?.inside && zoom > 6) {
      const a = engine.imageToScreen(Math.floor(hover.x), Math.floor(hover.y))
      ctx.strokeStyle = 'rgba(255,255,255,0.8)'
      ctx.lineWidth = 1
      ctx.strokeRect(a.x + 0.5, a.y + 0.5, zoom - 1, zoom - 1)
    }
    if (ap) {
      const c = engine.imageToScreen(ap.x, ap.y)
      ctx.strokeStyle = ACCENT
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(c.x, c.y, ap.r * zoom, 0, Math.PI * 2)
      ctx.stroke()
      ctx.setLineDash([4, 4])
      ctx.strokeStyle = 'rgba(255,138,61,0.55)'
      ctx.beginPath()
      ctx.arc(c.x, c.y, ap.r * 2 * zoom, 0, Math.PI * 2)
      ctx.arc(c.x, c.y, ap.r * 3 * zoom, 0, Math.PI * 2)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.beginPath()
      ctx.moveTo(c.x - 6, c.y)
      ctx.lineTo(c.x + 6, c.y)
      ctx.moveTo(c.x, c.y - 6)
      ctx.lineTo(c.x, c.y + 6)
      ctx.stroke()
    }
    if (ln) {
      const a = engine.imageToScreen(ln.x0, ln.y0)
      const b = engine.imageToScreen(ln.x1, ln.y1)
      ctx.strokeStyle = '#4de3c2'
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()
      ctx.fillStyle = '#4de3c2'
      for (const p of [a, b]) {
        ctx.beginPath()
        ctx.arc(p.x, p.y, 3.5, 0, Math.PI * 2)
        ctx.fill()
      }
    }
  }, [])

  useEffect(() => {
    const engine = new DetectorEngine(canvasRef.current, {
      onHover: (h) => {
        pixelHover.set(h)
        engine.dirty = engine.view.zoom > 6 || engine.dirty
      },
      onClick: (p) => live.current.tool === 'aperture' && live.current.onAperture?.(p),
      onLine: (phase, p) => live.current.onLine?.(phase, p),
      onFrame: draw,
    })
    engineRef.current = engine
    return () => {
      engine.dispose()
      engineRef.current = null
      pixelHover.set(null)
    }
  }, [draw])

  useEffect(() => { if (image) engineRef.current?.setImage(image) }, [image])
  useEffect(() => { engineRef.current?.setLook(look) }, [look, image])
  useEffect(() => { engineRef.current?.setColormap(look.cmap) }, [look.cmap, image])
  useEffect(() => { engineRef.current?.setTool(tool) }, [tool])
  useEffect(() => { engineRef.current?.setInsets(insets) }, [insets.left, insets.right]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { engineRef.current?.setActive(active); if (active) engineRef.current?.fit() }, [active])
  useEffect(() => { if (fitSignal) engineRef.current?.fit() }, [fitSignal])
  useEffect(() => { draw() }, [aperture, line, draw])

  return (
    <div className="layer" aria-hidden={!active}>
      <canvas ref={canvasRef} className={`gl tool-${tool}`} role="img" aria-label="SPHEREx detector image. Drag to pan, scroll to zoom." />
      <canvas ref={overlayRef} className="overlay" />
    </div>
  )
}
