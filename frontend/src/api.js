// Client for the FastAPI backend (see ../../main.py). Vite proxies /api and /health to localhost:8000.

const BACKEND_DOWN = 'Cannot reach the SPHEREx backend. Is `python main.py` running on port 8000?'

class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** FastAPI returns `detail` as a string for HTTPException and as a list of objects for 422 validation errors. */
function describeDetail(detail, fallback) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const text = detail.map((d) => [d?.loc?.slice(1).join('.'), d?.msg].filter(Boolean).join(': ')).join('; ')
    if (text) return text
  }
  return fallback
}

export async function checkHealth(signal) {
  try {
    return (await fetch('/health', { signal })).ok
  } catch {
    return false
  }
}

async function request(path, { method = 'GET', body, signal } = {}) {
  let response
  try {
    response = await fetch(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal,
    })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    // A dropped connection is not the same as a dead backend (long archive downloads can outlast a hop).
    throw new ApiError(
      (await checkHealth())
        ? 'The connection dropped before the backend answered. The download may be slow or interrupted; try again (partial downloads resume).'
        : BACKEND_DOWN,
      0,
    )
  }
  if (!response.ok) {
    let detail
    try {
      detail = (await response.json()).detail
    } catch {
      /* non-JSON error body */
    }
    // The dev proxy answers an empty 502/503/504 when nothing is listening: say that, not "HTTP 502".
    if (detail === undefined && response.status >= 502 && response.status <= 504 && !(await checkHealth())) throw new ApiError(BACKEND_DOWN, 0)
    throw new ApiError(describeDetail(detail, `The backend returned HTTP ${response.status}.`), response.status)
  }
  return response
}

const json = async (path, opts) => (await request(path, opts)).json()
const qs = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null)).toString()

/** Every archive image near a position: any date, any band. */
export const listObservations = (ra, dec, radius = 0.05, limit = 100, signal) => json(`/api/observations?${qs({ ra, dec, radius, limit })}`, { signal })

/** Fetch (or build) one image product and return its metadata (WCS mesh, wavelength, epoch, ...). */
export const observe = ({ ra, dec, mode, obsId, detector }, signal) =>
  json('/api/observe', { method: 'POST', body: { ra, dec, mode, obs_id: obsId, detector }, signal })

export const resolveName = (q, signal) => json(`/api/resolve?${qs({ q })}`, { signal })
export const suggest = async (q, signal) => (await json(`/api/suggest?${qs({ q })}`, { signal })).results
export const coneStars = async (ra, dec, radius, maglim, signal) => (await json(`/api/stars?${qs({ ra, dec, radius, maglim })}`, { signal })).objects
export const photometry = (id, x, y, r, signal) => json(`/api/products/${id}/photometry?${qs({ x, y, r, r_in: r + 4, r_out: r + 10 })}`, { signal })

async function decodeImage(url, signal) {
  const blob = await (await request(url, { signal })).blob()
  const bitmap = await createImageBitmap(blob, { colorSpaceConversion: 'none', premultiplyAlpha: 'none' })
  const { width, height } = bitmap
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d', { willReadFrequently: true, colorSpace: 'srgb' })
  ctx.drawImage(bitmap, 0, 0)
  bitmap.close()
  const rgba = ctx.getImageData(0, 0, width, height).data
  const gray = new Uint8Array(width * height)
  for (let i = 0; i < gray.length; i++) gray[i] = rgba[i * 4]
  return { width, height, gray }
}

/** 8-bit display image of a product (row 0 = top). */
export const loadImage = (meta, signal) => decodeImage(meta.image_url, signal)
/** Defect/artifact mask of a product (255 = flagged). */
export const loadMask = (meta, signal) => decodeImage(meta.mask_url, signal)
