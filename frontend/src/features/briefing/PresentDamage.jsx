import { useEffect, useState } from 'react'
import { useOverload } from '../../store'
import { Button } from '../../ui'
import ReviewStage from './ReviewStage'
import { getDeck, getReport, prefetchBriefing } from './briefingApi'
import { bodyFor } from './stage'
import { getVoiceStatus } from './voiceApi'

// "Present the damage": one click opens the review stage on the short deck — the expected cost and the
// time without power first, then who is hit, why it failed, what could be done. It never opens by itself.
// Sound is whatever this viewer chose (off until they turn it on): the deck runs on captions.
export default function PresentDamage() {
  const { result, cascade, caseBody, cascadeBody, region } = useOverload()
  const [open, setOpen] = useState(false)
  const body = cascade ? bodyFor(cascade, caseBody, cascadeBody) : caseBody
  const ran = !!cascade
  // Florida (the demo state) warms the briefing ahead of the click, once the dropped case has held still. Every state
  // asks for it the moment the cascade result arrives (BRIEFING SPEED, Sat 20:16: the replay's time hides the work);
  // before that, outside Florida nothing is asked of the server (LAZY, Sat 06:24). Outside Florida the prefetch is the
  // engine's (the template decks): Gemini's deck is asked for on the click (the stage swaps it into the slides not yet
  // played) and the AI proposer starts then too, so a cascade nobody presents spends none of the public site's daily
  // Gemini calls.
  const key = result && (region === 'FL' || ran) ? JSON.stringify(body) : ''

  // The click then opens on data that is already in: the template deck, the report and (Florida) Gemini's deck
  // (REVIEW-1 #1: Gemini's deck asked for only on the click arrived after the template had started playing; the stage
  // still holds its autoplay briefly for it)
  useEffect(() => {
    if (!key) return undefined
    const t = setTimeout(
      () => {
        const b = JSON.parse(key)
        if (ran) prefetchBriefing(b, { ai: region === 'FL' })
        else {
          getReport(b).catch(() => {})
          getDeck(b, { ai: false }).catch(() => {})
        }
        getVoiceStatus() // who narrates (the stage's sound chip and speaker names): a small GET, asked once per page
      },
      ran ? 0 : 1500,
    )
    return () => clearTimeout(t)
  }, [key, ran, region])

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
