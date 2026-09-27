import { useEffect, useState } from 'react'

// The fault test's totals ({caught, injected}), read once from the engine (the same report the pipeline page shows in full);
// null while it loads or if it can't be read, so a caller simply leaves its line out.
let asked = null

export function useFaultTotals(client) {
  const [totals, setTotals] = useState(null)
  useEffect(() => {
    if (!client?.faultTest) return undefined
    let live = true
    asked = asked || client.faultTest().catch(() => null)
    asked.then((d) => {
      if (!d) asked = null
      const s = d?.summary
      if (live) setTotals(s?.injected ? { caught: s.caught, injected: s.injected } : null)
    })
    return () => {
      live = false
    }
  }, [client])
  return totals
}
