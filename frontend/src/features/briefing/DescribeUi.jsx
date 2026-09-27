import { D, descriptionOf } from './describe'

// DESCRIBE THE MAP: the toggle in the presentation's options, and the beat's line ("On the map: ...").

// The toggle: a labeled button in the options block (reachable by keyboard, `aria-pressed` says whether it is on). Turning it
// on or off restarts the beat you are on (ReviewStage), so the line is heard and read from its start; focus stays here.
// `available` is false only when the briefing is loaded and carries no descriptions (an older cached deck).
export function DescribeToggle({ lang, on, onToggle, available = true }) {
  const d = D[lang]
  return (
    <>
      <button type="button" className="rs-tool rs-tool--describe" aria-pressed={on} aria-describedby="rs-describe-hint" onClick={onToggle} disabled={!available}>
        {d.toggle}
      </button>
      <span id="rs-describe-hint" className="sh-sr">
        {available ? d.hint : d.none}
      </span>
    </>
  )
}

// The beat's line: a polite live region (a screen reader reads it when the beat changes, with no audio and no focus move),
// shown in a plain caption style at the top of the slide card. The region stays in the page while the mode is available and
// is empty while the mode is off, so the first line of a beat is announced like every later one. Its text is the whole
// line, set once per beat: no word-by-word highlighting, which would make a reader repeat it.
export function DescribeLine({ slide, lang, on }) {
  const d = on ? descriptionOf(slide, lang) : null
  return (
    <div className="rs-describe" role="status" aria-live="polite" aria-atomic="true" lang={lang} data-describe-line>
      {d?.text ? (
        <p className="rs-describe__p">
          <span className="rs-describe__pre">{D[lang].prefix}</span> {d.text}
        </p>
      ) : null}
    </div>
  )
}
