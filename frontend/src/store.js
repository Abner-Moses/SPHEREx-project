import { useSyncExternalStore } from 'react'

// Tiny external store for high-frequency values (cursor position) so they don't re-render the whole app.
function createStore(initial) {
  let value = initial
  const subscribers = new Set()
  return {
    get: () => value,
    set(next) {
      value = next
      subscribers.forEach((fn) => fn())
    },
    subscribe(fn) {
      subscribers.add(fn)
      return () => subscribers.delete(fn)
    },
  }
}

export const useStore = (store) => useSyncExternalStore(store.subscribe, store.get)

export const pixelHover = createStore(null) // { x, y, inside } | null
// Live sky view (centre, field of view, projection). Lives outside React state on purpose: it changes every
// frame while moving, and only the small pill that shows it should re-render, never the whole app.
export const viewStore = createStore({ fov: 110, lon0: 0, lat0: 0, projection: 'stereographic' })

export function loadSetting(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? fallback : JSON.parse(raw)
  } catch {
    return fallback
  }
}

export function saveSetting(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage unavailable (private mode / quota): the app simply doesn't remember */
  }
}

// Flight log: a short rolling list of real events (searches, selections, image uplinks, errors) shown on the deck.
export const flightLog = createStore([])
let logId = 0
export function logEvent(kind, text) {
  const d = new Date()
  const t = `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}:${String(d.getUTCSeconds()).padStart(2, '0')}`
  flightLog.set([...flightLog.get(), { id: ++logId, t, kind, text }].slice(-6))
}

// Requests for Voyager from anywhere in the UI (e.g. the target card's "Ask Voyager" button).
export const voyagerBus = createStore(null)
let askId = 0
export function voyagerAsk(question, focus = null) { voyagerBus.set({ id: ++askId, question, focus }) }
