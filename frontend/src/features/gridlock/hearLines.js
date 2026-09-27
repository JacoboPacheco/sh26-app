// The lines "Hear the negotiation" speaks, from the two responses that carry voice keys (see backend/negotiate.py and
// backend/collab_plans.py: the server registers each line's words with voice.py). Nothing here writes words.

// POST /api/negotiate/<id>: each verified turn, then the closing summary (n: the turn's number, or 'summary')
export function negotiationLines(data) {
  const spoken = (data?.turns || []).filter((x) => x.voice?.key).map((x) => ({ ...x.voice, n: x.n }))
  const sm = data?.voice?.summary?.key ? [{ ...data.voice.summary, n: 'summary' }] : []
  return [...spoken, ...sm]
}

// POST /api/gridlock/plans: the trace's kept agent lines, in order (n: the trace index)
export function planLines(data) {
  return (data?.trace || []).flatMap((s, i) => (s?.voice?.key ? [{ ...s.voice, n: i }] : []))
}
