import { useEffect, useRef, useState } from 'react'

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// A number that eases to its new value (the load climbing as the day heats up); jumps under
// reduced motion. Only the component that calls it re-renders, never the map.
export default function useTween(target, ms = 700) {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    if (!Number.isFinite(target)) return undefined
    const a = Number.isFinite(from.current) ? from.current : target
    if (a === target || reducedMotion()) {
      from.current = target
      setShown(target)
      return undefined
    }
    const start = performance.now()
    let raf = 0
    const tick = (now) => {
      const t = Math.min(1, Math.max(0, (now - start) / ms))
      const v = a + (target - a) * (1 - (1 - t) ** 3)
      from.current = v
      setShown(v)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return shown
}
