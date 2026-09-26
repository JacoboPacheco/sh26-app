import { useEffect, useRef, useState } from 'react'

// Clocks for the play-by-play show. Everything that moves by JS (counters, reveals, the plays) reads one of
// these; the rest of the motion is CSS. When `active` is false (a paused slide, or reduced motion) they return
// the finished state at once, so a still slide is always the complete picture.

export const clamp01 = (v) => Math.min(1, Math.max(0, v))
export const easeOut = (k) => 1 - (1 - k) ** 3
export const easeOutQuart = (k) => 1 - (1 - k) ** 4
export const easeInOut = (k) => (k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2)

// Milliseconds since mount, ~30 times a second, until `max`. Inactive: max (the end of the show).
export function useElapsed(active, max = 120000, fps = 30) {
  const [t, setT] = useState(0)
  useEffect(() => {
    if (!active) return undefined
    const t0 = performance.now()
    let raf = 0
    let last = 0
    const tick = (now) => {
      if (now - last >= 1000 / fps) {
        last = now
        const v = Math.min(max, now - t0)
        setT(v)
        if (v >= max) return
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [active, max, fps])
  return active ? t : max
}

// A number that moves to `target` (from `from`, or from wherever it is when the target changes).
// Inactive: the target at once.
export function useTween(target, { ms = 900, delay = 0, from = 0, active = true, ease = easeOut } = {}) {
  const [v, setV] = useState(from)
  const cur = useRef(from)
  useEffect(() => {
    if (!active) return undefined
    const a = cur.current
    const t0 = performance.now() + delay
    let raf = 0
    const tick = (now) => {
      const k = clamp01((now - t0) / ms)
      const val = a + (target - a) * ease(k)
      cur.current = val
      setV(val)
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms, delay, active, ease])
  return active ? v : target
}
