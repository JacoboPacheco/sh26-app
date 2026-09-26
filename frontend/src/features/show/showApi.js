import { api } from '../../api'

// "Watch the story" (backend/show.py):
//   GET  /api/show/episodes          -> [{id, title, blurb, region, est_minutes}]
//   POST /api/show/{episode}         {region?, lang} -> {id, status: 'pending'|'done'|'error', estimate_s}
//   GET  /api/show/jobs/{id}         -> {status, progress: {step, of, text}, show?, error?}
// A finished show is cached per (episode, region, lang): a repeat POST answers 'done' at once.

// Only while developing, before the backend route exists: the episodes and a fixture show with every layer type.
const DEV_EPISODES = [
  { id: 'collapse', title: 'Five switches', blurb: 'Five 1 GW AI data centers switch on at once and the grid collapses.', region: 'FL', est_minutes: 3 },
  { id: 'hurricane', title: 'The storm', blurb: 'A major hurricane crosses Florida.', region: 'FL', est_minutes: 3 },
  { id: 'boom', title: 'The AI boom', blurb: 'A Gemini siting agent places gigawatts of campuses: where they fit, where they break.', region: 'FL', est_minutes: 3 },
  { id: 'strengthen', title: 'How many can it take?', blurb: 'The capacity meter: upgrades going in, and what they cost.', region: 'FL', est_minutes: 3 },
  { id: 'together', title: 'Build together', blurb: "Two utilities' public plans across the Georgia–South Carolina border.", region: 'GA', est_minutes: 3 },
]
const missingRoute = (e) => /not found|\(404\)|\(405\)/i.test(String(e?.message || ''))

export async function listEpisodes() {
  try {
    const eps = await api('/api/show/episodes')
    return Array.isArray(eps) ? eps : eps?.episodes || []
  } catch (e) {
    if (import.meta.env.DEV && missingRoute(e)) return DEV_EPISODES.map((x) => ({ ...x, fixture: true }))
    throw e
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Start (or fetch the cached) show and poll its job. `onProgress({step, of, text, estimate_s})` reports the
// director's steps; `alive()` returning false stops polling (the viewer closed the player).
export async function loadShow(episode, { region, lang = 'en' } = {}, onProgress, alive = () => true) {
  let job
  try {
    job = await api(`/api/show/${encodeURIComponent(episode)}`, { method: 'POST', body: { ...(region ? { region } : {}), lang } })
  } catch (e) {
    if (import.meta.env.DEV && missingRoute(e)) {
      const fx = (await import('./fixture.json')).default
      onProgress?.({ step: 1, of: 1, text: 'Development fixture (the show route is not on this backend yet)' })
      await sleep(900)
      return { ...fx, episode, fixture: true }
    }
    throw e
  }
  if (job?.show) return job.show
  const estimate = job?.estimate_s
  const jobId = job?.id
  if (!jobId) throw new Error('The episode could not be started. Try again.')
  onProgress?.({ step: 0, of: 0, text: '', estimate_s: estimate })
  let wait = job?.status === 'done' ? 0 : 700
  let fails = 0
  for (;;) {
    if (!alive()) return null
    if (job?.status === 'error') throw new Error(job.error || job.detail || 'The episode could not be made. Try again.')
    await sleep(wait)
    if (!alive()) return null
    try {
      job = await api(`/api/show/jobs/${encodeURIComponent(jobId)}`)
      fails = 0
    } catch (e) {
      if (++fails >= 4) throw e
      continue
    }
    if (job?.progress) onProgress?.({ ...job.progress, estimate_s: estimate })
    if (job?.status === 'done' && job.show) return job.show
    wait = Math.min(1500, wait + 150)
  }
}
