import { useEffect, useState } from 'react'
import { useOverload } from '../../store'
import { Button } from '../../ui'
import ReviewStage from './ReviewStage'
import { getDeck, getReport } from './briefingApi'
import { bodyFor } from './stage'
import { getVoiceStatus } from './voiceApi'

// "Present the damage": one click opens the review stage on the short deck — the expected cost and the
// time without power first, then who is hit, why it failed, what could be done. It never opens by itself.
// Sound is whatever this viewer chose (off until they turn it on): the deck runs on captions.
export default function PresentDamage() {
  const { result, cascade, caseBody, cascadeBody, region } = useOverload()
  const [open, setOpen] = useState(false)
  const body = cascade ? bodyFor(cascade, caseBody, cascadeBody) : caseBody
  // Florida (the demo state) warms the briefing ahead of the click; every other state is lazy: nothing is asked
  // of the server until the button is pressed (user, Sat 06:24: only calculate during the cascade, nothing else).
  const key = result && region === 'FL' ? JSON.stringify(body) : ''
  const ran = !!cascade

  // Warm the briefing once the case has held still, so the click opens on data that is already in: the template deck,
  // and once a cascade has run, Gemini's deck too (REVIEW-1 #1: asked for only on the click, it arrived a moment after
  // the template had started playing and was dropped; the stage still holds its autoplay briefly for it)
  useEffect(() => {
    if (!key) return undefined
    const t = setTimeout(() => {
      const b = JSON.parse(key)
      getReport(b).catch(() => {})
      getDeck(b, { ai: false }).catch(() => {})
      if (ran) getDeck(b, { ai: true }).catch(() => {})
      getVoiceStatus() // who narrates (the stage's sound chip and speaker names): a small GET, asked once per page
    }, 1500)
    return () => clearTimeout(t)
  }, [key, ran])

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
