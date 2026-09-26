import { useEffect, useRef, useState } from 'react'

// A number that moves to its new value instead of jumping. `linear` keeps it moving at a steady
// pace (for a cascade that plays step by step, the number never stops between steps).
export default function useCountUp(target, ms = 500, { linear = false } = {}) {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    const start = performance.now()
    const a = from.current
    let raf = 0
    const tick = (now) => {
      const t = Math.min(1, (now - start) / ms)
      const k = linear ? t : 1 - (1 - t) ** 3
      const v = a + (target - a) * k
      from.current = v
      setShown(v)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms, linear])
  return shown
}
