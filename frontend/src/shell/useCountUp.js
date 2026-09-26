import { useEffect, useRef, useState } from 'react'

// A number that counts up (or down) to its new value instead of jumping.
export default function useCountUp(target, ms = 500) {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    const start = performance.now()
    const a = from.current
    let raf = 0
    const tick = (now) => {
      const t = Math.min(1, (now - start) / ms)
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
