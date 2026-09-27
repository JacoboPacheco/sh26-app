import { useEffect, useState } from 'react'

// Reader C's headline totals ({matches, compared, rate}), read once from the committed report (ReaderProof.jsx shows
// it in full, inside the pipeline page); null while it loads or if it can't be read, so a caller leaves its line out
// (HOW-IT-WORKS.md gap #18: the landing's numbers strip only carried the fault test, not the second reader).
let asked = null

export function useReaderTotals(client) {
  const [totals, setTotals] = useState(null)
  useEffect(() => {
    if (!client?.readerReport) return undefined
    let live = true
    asked = asked || client.readerReport().catch(() => null)
    asked.then((d) => {
      if (!d) asked = null
      const o = d?.agreement?.overall
      if (live) setTotals(o?.compared ? { matches: o.gemini_matches_pipeline, compared: o.compared, rate: o.rate } : null)
    })
    return () => {
      live = false
    }
  }, [client])
  return totals
}
