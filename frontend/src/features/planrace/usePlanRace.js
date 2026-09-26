// The plan race's state for one case (region, size, load level, campus type): its budget bar (fetched when the case is
// shown: a read of the finished study, never a computation), and the race itself, which starts only when asked
// (LAZY: never on its own) and is polled while it runs. The rows are revealed at a steady pace, one per lane per tick,
// so a race the server answers from its saved answers still plays as a race; reduced motion shows everything at once.
import { useCallback, useEffect, useRef, useState } from 'react'

const POLL_MS = 1000
const TICK_MS = 380

export function reducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

export default function usePlanRace({ client, region, mw, loadFactor, mode }) {
  const [knee, setKnee] = useState(null)
  const [kneeError, setKneeError] = useState(null)
  const [race, setRace] = useState(null) // the latest view from the server
  const [phase, setPhase] = useState('idle') // idle | starting | running | done | error
  const [error, setError] = useState(null)
  const [cached, setCached] = useState(false)
  const [tick, setTick] = useState(0) // rows revealed per lane
  const [still] = useState(reducedMotion)
  const gen = useRef(0) // a newer case or start makes every older poll stop

  // the case's budget bar (the panel remounts this hook for a new case, so everything else starts fresh)
  useEffect(() => {
    let live = true
    client
      .knee({ region, mw, loadFactor, mode })
      .then((k) => live && setKnee(k))
      .catch((e) => live && setKneeError(e?.message || String(e)))
    return () => {
      live = false
      gen.current += 1 // unmounted: any poll still running stops
    }
  }, [client, region, mw, loadFactor, mode])

  const start = useCallback(async () => {
    gen.current += 1
    const g = gen.current
    setPhase('starting')
    setError(null)
    setRace(null)
    setTick(0)
    try {
      const s = await client.start({ region, mw, loadFactor, mode })
      if (g !== gen.current) return
      setCached(!!s.cached)
      for (;;) {
        const r = await client.get(s.id)
        if (g !== gen.current) return
        setRace(r)
        if (r.status === 'done') {
          setPhase('done')
          return
        }
        if (r.status === 'error') {
          setPhase('error')
          setError(r.error || 'The race failed. Try again.')
          return
        }
        setPhase('running')
        await new Promise((ok) => setTimeout(ok, POLL_MS))
        if (g !== gen.current) return
      }
    } catch (e) {
      if (g !== gen.current) return
      setPhase('error')
      setError(e?.message || String(e))
    }
  }, [client, region, mw, loadFactor, mode])

  const lanes = race?.lanes || []
  const longest = lanes.reduce((n, l) => Math.max(n, l.trace?.length || 0), 0)
  // the reveal clock: one more row per lane every tick, while any lane has rows not yet shown
  useEffect(() => {
    if (still || tick >= longest) return undefined
    const id = setTimeout(() => setTick((t) => t + 1), tick === 0 ? 150 : TICK_MS)
    return () => clearTimeout(id)
  }, [still, tick, longest])

  const shown = still ? Infinity : tick
  const revealed = phase === 'done' && (still || tick >= longest)
  return { knee, kneeError, race, phase, error, cached, start, shown, revealed, still }
}
