import { useEffect, useMemo, useRef, useState } from 'react'
import { SurfaceEngine } from '../engine/surfaceEngine.js'
import { downsample } from '../lib/stats.js'
import NavBar3D, { HINTS } from '../components/NavBar3D.jsx'

const CROP = 256

/** Region of the detector shown as a surface: whole image (block-averaged) or a 256 px crop at 1:1. */
function surfaceRect(image, region, center) {
  if (!image) return null
  if (region === 'full') return { x: 0, y: 0, w: image.width, h: image.height }
  const size = Math.min(CROP, image.width, image.height)
  const cx = center?.x ?? image.width / 2
  const cy = center?.y ?? image.height / 2
  const x = Math.max(0, Math.min(image.width - size, Math.round(cx - size / 2)))
  const y = Math.max(0, Math.min(image.height - size, Math.round(cy - size / 2)))
  return { x, y, w: size, h: size }
}

export default function SurfaceView({ active, image, region, center, cmap, exaggeration, contours, replayKey }) {
  const canvasRef = useRef(null)
  const engineRef = useRef(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const engine = new SurfaceEngine(canvasRef.current)
    engineRef.current = engine
    if (import.meta.env.DEV) window.__surface = engine // dev-only handle for checking the camera in the browser
    setReady(true)
    return () => {
      engine.dispose()
      engineRef.current = null
      setReady(false)
    }
  }, [])

  const rect = useMemo(() => surfaceRect(image, region, center), [image, region, center?.x, center?.y]) // eslint-disable-line react-hooks/exhaustive-deps
  const heights = useMemo(() => {
    if (!image || !rect) return null
    const n = region === 'full' ? 192 : Math.min(CROP, rect.w)
    return { n, data: downsample(image.gray, image.width, image.height, rect, n, n) }
  }, [image, rect, region])

  useEffect(() => {
    if (ready && heights) engineRef.current.setHeights(heights.data, heights.n)
    if (ready && heights) {
      engineRef.current.setExaggeration(exaggeration)
      engineRef.current.setContours(contours)
    }
  }, [ready, heights]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { engineRef.current?.setExaggeration(exaggeration) }, [exaggeration, ready])
  useEffect(() => { engineRef.current?.setContours(contours) }, [contours, ready])
  useEffect(() => { engineRef.current?.setColormap(cmap) }, [cmap, ready])
  useEffect(() => { if (replayKey) engineRef.current?.replayRise() }, [replayKey, ready])
  useEffect(() => { engineRef.current?.setActive(active) }, [active, ready])

  return (
    <div className="layer" aria-hidden={!active}>
      <canvas ref={canvasRef} className="gl" role="img" aria-label="3D surface of image brightness. Drag to orbit, scroll to zoom, W A S D to slide across." />
      {active && rect && <NavBar3D onReset={() => engineRef.current?.resetView()} hint={HINTS.surface} />}
      {rect && (
        <div className="surface-caption">
          {region === 'full' ? `Whole detector, ${image.width}×${image.height} px averaged to ${heights?.n}×${heights?.n}` : `${rect.w}×${rect.h} px crop at x ${rect.x}–${rect.x + rect.w}, y ${rect.y}–${rect.y + rect.h}`}
          <span> · height = display brightness (0–255), not physical flux</span>
        </div>
      )}
    </div>
  )
}
