import { useEffect, useState } from 'react'
import { useOverload } from '../../store'
import { Button } from '../../ui'
import ReviewStage from './ReviewStage'
import { getDeck, getReport } from './briefingApi'
import { bodyFor } from './stage'

// "Present the damage": one click opens the review stage on the short deck — the expected cost and the
// time without power first, then who is hit, why it failed, what could be done. It never opens by itself.
// Sound is whatever this viewer chose (off until they turn it on): the deck runs on captions.
export default function PresentDamage() {
  const { result, cascade, caseBody, cascadeBody } = useOverload()
  const [open, setOpen] = useState(false)
  const body = cascade ? bodyFor(cascade, caseBody, cascadeBody) : caseBody
  const key = result ? JSON.stringify(body) : ''

  // Warm the briefing once the case has held still, so the click opens on data that is already in
  // (the template deck only: no AI quota is spent until someone presents).
  useEffect(() => {
    if (!key) return undefined
    const t = setTimeout(() => {
      const b = JSON.parse(key)
      getReport(b).catch(() => {})
      getDeck(b, { ai: false }).catch(() => {})
    }, 1500)
    return () => clearTimeout(t)
  }, [key])

  if (!result) return null
  return (
    <>
      <div className="present">
        <Button onClick={() => setOpen(true)}>Present the damage</Button>
      </div>
      {open && <ReviewStage body={body} short autoPlay loadReplay={!cascade} onClose={() => setOpen(false)} />}
    </>
  )
}
