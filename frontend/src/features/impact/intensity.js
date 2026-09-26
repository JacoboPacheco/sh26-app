// How hard an incident hits the screen, relative to its own size. Pure: no React, no DOM.
//
// Until now every effect (counter shake, flash, map shake, call-outs) scaled with the ABSOLUTE
// number of people in a leap, so a cascade of a few thousand people played weak beside the Florida
// hero (1.27 million). Intensity is 0..1 and combines two things:
//   - how big the whole incident is (log10 of the people hit by the end), and
//   - how much of the incident this leap is (its share of the final total).
// The Florida hero's biggest leap (329,740 of 1.27M, a quarter of it) is 1; the smallest real incident (a few thousand
// people, one transformer) is about 0.35-0.5, never lower than FLOOR, so a real hit always gets the
// full vocabulary (snap, ring, sparks, call-out, fire, shake, flash) at a smaller size.
// Anything that already scaled with the absolute numbers keeps doing so and takes the larger of the
// two (max(today, relative)): the biggest incident is never bigger than before, a small one is lifted.
// The REF_* values are what the Florida hero's biggest leap (329,740 people, Naples) got before this
// existed, so intensity 1 reproduces it exactly.

const clamp01 = (v) => Math.min(1, Math.max(0, v))
export const FLOOR = 0.3
export const REF_SHAKE_PX = 9.4 // the counter's shake
export const REF_POP = 0.33 // the counter's pop: scale 1 + this
export const REF_FLASH = 0.7 // the counter's corner flash (opacity)
export const REF_QUAKE_PX = 12.9 // the map's shake
const X0 = 3 // log10(1,000 people): where an incident starts to count
const X_SPAN = 3.1 // …to log10(1.27 million): the Florida hero, intensity 1

/** 0.36..1 — how big the whole incident is, by the people it hits in the end. */
export function incidentScale(total) {
  const x = Math.log10(Math.max(total || 0, 1))
  const s = clamp01((x - X0) / X_SPAN)
  return 0.36 + 0.64 * Math.pow(s, 1.3)
}

/** FLOOR..1 — how hard one leap (delta people, of `total` by the end) should hit. */
export function leapIntensity(delta, total) {
  const inc = incidentScale(total)
  const share = total > 0 ? clamp01(delta / total) : 1
  const rel = clamp01(Math.sqrt(share / 0.25)) // a quarter of the incident in one leap is a full-size blow
  return Math.max(FLOOR, inc * (0.55 + 0.45 * rel))
}

/** The number's look for a running total, given where the incident will end: {size, heat} are the
 *  log10 values the counter's font size and color are read from. Never below the absolute look. */
export function lookX(total, final) {
  const x = Math.log10(Math.max(total || 0, 1))
  if (!(final > 0) || x <= X0) return { size: x, heat: x }
  const xf = Math.max(3.3, Math.log10(final))
  const inc = incidentScale(final)
  const p = (x - X0) / (xf - X0) // how far along this incident's own scale the number is
  return {
    size: Math.max(x, X0 + X_SPAN * inc * p),
    heat: Math.max(x, X0 + X_SPAN * Math.min(1, inc * 1.25) * p), // color runs a little hotter than size
  }
}

/** lo..hi by intensity. */
export const byIntensity = (I, lo, hi) => lo + (hi - lo) * clamp01(I)
