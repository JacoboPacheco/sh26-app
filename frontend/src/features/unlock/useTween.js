import { useEffect, useRef, useState } from 'react'
import { reducedMotion } from './unlockStore'

// A number that eases to its new value (the Strengthen headline counting during the build-up): a short,
// plain ease-out, no overshoot. Reduced motion, or a first render, shows the value at once.
export default function useTween(value, ms = 420) {
  const [shown, setShown] = useState(value)
  const from = useRef(value)
  const raf = useRef(0)
  useEffect(() => {
    cancelAnimationFrame(raf.current)
    const start = from.current
    if (start === value || reducedMotion()) {
      from.current = value
      raf.current = requestAnimationFrame(() => setShown(value))
      return () => cancelAnimationFrame(raf.current)
    }
    const t0 = performance.now()
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / ms)
      const v = start + (value - start) * (1 - (1 - k) ** 3)
      from.current = v
      setShown(v)
      if (k < 1) raf.current = requestAnimationFrame(tick)
      else from.current = value
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [value, ms])
  return shown
}
