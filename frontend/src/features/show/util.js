import { useEffect, useState, useSyncExternalStore } from 'react'

const HELD = 1e6

// re-render on the engine's discrete changes (scene, play state, a new word in the captions)
export function useEngine(engine) {
  return useSyncExternalStore(engine.subscribe, engine.getVersion)
}

// how many of `times` (ms into the scene, ascending) have passed; re-renders only when that count changes
export function useTicks(engine, times, held) {
  const [n, setN] = useState(held ? times.length : 0)
  useEffect(() => {
    if (!engine) return undefined
    let cur = -1
    return engine.onFrame((t) => {
      const tt = held ? t + HELD : t
      let k = 0
      while (k < times.length && times[k] <= tt) k++
      if (k !== cur) {
        cur = k
        setN(k)
      }
    })
  }, [engine, times, held])
  return n
}

export const actorName = (a) => (a === 'engine' ? 'Engine' : 'Gemini')

// ------------------------------------------------------------------ scene timing
// A layer with timing 'span' places its items by `at` (0..1) across the scene's own length, so a cascade, a storm or
// a rebuild plays from the first moment to the last whatever the scene lasts (the voice, the pace). sync 'storm':
// `at` is the storm's progress along its track, and the item waits for the storm to get there (the storm travels on
// the same clock with an eased start and end). after_ms: a layer that follows another (a town goes dark after its
// line trips). Everything is ms into the scene.
export const SPAN_LEAD = 900
export const SPAN_TAIL = 2600
const clamp01 = (v) => Math.min(1, Math.max(0, v))
export const spanMs = (est) => Math.max(1200, (est || 0) - SPAN_LEAD - SPAN_TAIL)
export const easeSine = (u) => -(Math.cos(Math.PI * clamp01(u)) - 1) / 2
const invEaseSine = (f) => Math.acos(1 - 2 * clamp01(f)) / Math.PI
export const spanned = (layer) => layer?.timing === 'span'

// when a spanned moment happens, ms into the scene
export function atMs(layer, at, est) {
  const u = layer?.sync === 'storm' ? invEaseSine(at) : clamp01(at)
  return SPAN_LEAD + u * spanMs(est) + (layer?.after_ms || 0)
}

// when item i of a map layer starts: its own moment when the layer is spanned, else base + i x stagger
export function itemStart(layer, it, i, est, defStagger, base) {
  if (spanned(layer) && it?.at != null) return atMs(layer, it.at, est)
  return base + i * (layer.stagger_ms ?? defStagger)
}

// A scene whose map carries something to look at (lines, places, zones, a storm): the before/after then docks in the
// right column as a card instead of splitting the whole stage over the map.
export const mapBusy = (scene) =>
  (scene?.layers || []).some((l) => l.type === 'storm' || (['lines', 'points', 'zones'].includes(l.type) && (l.items || []).length > 0))

// consecutive scenes with the same chapter name make one chapter of the progress bar
export function chaptersOf(scenes) {
  const out = []
  scenes.forEach((s, i) => {
    const name = s.chapter || 'Scene'
    const last = out[out.length - 1]
    if (last && last.name === name) last.count++
    else out.push({ name, start: i, count: 1 })
  })
  return out
}
