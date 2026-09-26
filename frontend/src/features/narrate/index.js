// The narrated play-by-play for Strengthen the grid (backend/narrate.py):
//   useBuildNarration({region, mw, loadFactor, mode, budget, lang, playing, fromStart, onStep, onDone}) — fetches the
//     script when playing starts (LAZY), plays it with the deck's narration engine, calls onStep(n) as the voice
//     reaches each campus; `stale` in its return says the script held is for other props (the caption hides)
//   BuildCaption({nb})  — the caption bar for the bottom of the map (the words, the voice, the sound switch)
//   narrationBody(...)  — the script's request body (the play-by-play's reduced-motion path fetches the same script)
export { default as useBuildNarration, narrationBody } from './useBuildNarration'
export { default as BuildCaption } from './BuildCaption'
export { getNarration } from './narrateApi'
