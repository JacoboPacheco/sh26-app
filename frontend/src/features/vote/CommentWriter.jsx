import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Button, ErrorBanner } from '../../ui'
import AiBadge from '../ai/AiBadge'
import HowAiIsUsed from '../ai/HowAiIsUsed'
import { writeComment } from './voteApi'

// "Write your public comment": the resident picks what matters to them, where they stand, how long they will speak and
// the language; Gemini writes the comment in their voice from this page's facts, and a checker verifies every number,
// name and claim before it is shown (backend/comment.py). Controls only, no chat box. The result is a letter they can
// copy, print, and send to the decision body the page names; every number in it shows its source on hover or focus.
// Mounted with key={proposal id}.

const CONCERNS = [
  ['bill', 'My electric bill'],
  ['blackouts', 'Blackouts and heat waves'],
  ['water', 'Water'],
  ['backup_air', 'Backup generators and air'],
  ['jobs_taxes', 'Jobs and local taxes'],
]
const STANCES = [
  ['questions', 'I have questions'],
  ['support_conditions', 'I support it with conditions'],
  ['oppose', 'I oppose it as proposed'],
]
const MINUTES = [
  [1, 'About 1 minute'],
  [2, 'About 2 minutes'],
  [3, 'About 3 minutes'],
]
const LANGS = [
  ['en', 'English'],
  ['es', 'Español'],
]
const DEFAULTS = { concerns: ['bill', 'blackouts'], stance: 'questions', minutes: 2, lang: 'en' }
const SLOT = /\[[^\]\n]{2,40}\]/g // "[your name]", "[su calle o barrio]": the resident fills these in

const spoken = (s) => {
  const m = Math.floor(s / 60)
  const r = Math.round(s - m * 60)
  return m ? `about ${m} min${r ? ` ${r} s` : ''}` : `about ${r} s`
}
const same = (a, b) => a.stance === b.stance && a.minutes === b.minutes && a.lang === b.lang && a.concerns.join() === b.concerns.join()
const joinAnd = (xs) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`)

function jumpTo(section) {
  const el = document.getElementById(`vote-${section}`)
  if (!el) return
  const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' })
}

// ---------------------------------------------------------------- the controls
function Choice({ type, name, value, checked, onChange, children }) {
  return (
    <label className={`cmt-opt${checked ? ' cmt-opt--on' : ''}`}>
      <input type={type} name={name} value={value} checked={checked} onChange={onChange} />
      <span>{children}</span>
    </label>
  )
}

function Controls({ choice, setChoice, busy, onWrite, stale }) {
  const id = useId()
  const toggle = (c) =>
    setChoice((x) => {
      const on = x.concerns.includes(c)
      const next = on ? x.concerns.filter((k) => k !== c) : [...x.concerns, c]
      return { ...x, concerns: CONCERNS.map(([k]) => k).filter((k) => next.includes(k)) }
    })
  return (
    <form
      className="cmt-form"
      onSubmit={(ev) => {
        ev.preventDefault()
        onWrite()
      }}
    >
      <fieldset className="cmt-set cmt-set--wide">
        <legend>What matters to you</legend>
        <div className="cmt-opts">
          {CONCERNS.map(([k, label]) => (
            <Choice key={k} type="checkbox" name={`${id}-c`} value={k} checked={choice.concerns.includes(k)} onChange={() => toggle(k)}>
              {label}
            </Choice>
          ))}
        </div>
      </fieldset>
      <fieldset className="cmt-set cmt-set--wide">
        <legend>Where you stand</legend>
        <div className="cmt-opts">
          {STANCES.map(([k, label]) => (
            <Choice key={k} type="radio" name={`${id}-s`} value={k} checked={choice.stance === k} onChange={() => setChoice((x) => ({ ...x, stance: k }))}>
              {label}
            </Choice>
          ))}
        </div>
      </fieldset>
      <fieldset className="cmt-set">
        <legend>How long you will speak</legend>
        <div className="cmt-opts cmt-opts--seg">
          {MINUTES.map(([k, label]) => (
            <Choice key={k} type="radio" name={`${id}-m`} value={k} checked={choice.minutes === k} onChange={() => setChoice((x) => ({ ...x, minutes: k }))}>
              {label}
            </Choice>
          ))}
        </div>
      </fieldset>
      <fieldset className="cmt-set">
        <legend>Language</legend>
        <div className="cmt-opts cmt-opts--seg">
          {LANGS.map(([k, label]) => (
            <Choice key={k} type="radio" name={`${id}-l`} value={k} checked={choice.lang === k} onChange={() => setChoice((x) => ({ ...x, lang: k }))}>
              <span lang={k}>{label}</span>
            </Choice>
          ))}
        </div>
      </fieldset>
      <div className="cmt-go">
        <Button type="submit" busy={busy}>
          {busy ? 'Writing and checking…' : 'Write my comment'}
        </Button>
        {stale && (
          <span className="vote-note" role="status">
            Your choices changed. Write it again to update the comment.
          </span>
        )}
      </div>
    </form>
  )
}

// ---------------------------------------------------------------- the letter
// The comment with its numbers marked (each shows where it comes from on hover or focus) and the resident's blanks lit.
function Letter({ r }) {
  const base = useId()
  const paras = useMemo(() => {
    const marks = []
    r.numbers.forEach((n, i) => marks.push({ s: n.at[0], e: n.at[1], kind: 'num', i }))
    for (const m of r.text.matchAll(SLOT)) marks.push({ s: m.index, e: m.index + m[0].length, kind: 'slot' })
    marks.sort((a, b) => a.s - b.s)
    const out = []
    let pos = 0
    for (const para of r.text.split('\n\n')) {
      const start = pos
      const end = pos + para.length
      const nodes = []
      let at = start
      for (const m of marks) {
        if (m.s < start || m.e > end || m.s < at) continue
        if (m.s > at) nodes.push(r.text.slice(at, m.s))
        nodes.push(m)
        at = m.e
      }
      if (at < end) nodes.push(r.text.slice(at, end))
      out.push(nodes)
      pos = end + 2
    }
    return out
  }, [r])

  return (
    <article className="cmt-paper" lang={r.lang} aria-label="Your public comment">
      <p className="cmt-paper__subject">
        <span className="cmt-paper__k">{r.lang === 'es' ? 'Asunto' : 'Subject'}:</span> {r.subject}
      </p>
      <div className="cmt-paper__body">
        {paras.map((nodes, pi) => (
          <p key={pi}>
            {nodes.map((n, k) => {
              if (typeof n === 'string') return n
              const text = r.text.slice(n.s, n.e)
              if (n.kind === 'slot')
                return (
                  <mark key={k} className="cmt-slot" title="Fill this in">
                    {text}
                  </mark>
                )
              const num = r.numbers[n.i]
              const tip = `${base}-t${n.i}`
              return (
                <span key={k} className="cmt-num" tabIndex={0} aria-describedby={tip}>
                  {text}
                  <span className="cmt-tip" role="tooltip" id={tip}>
                    <span className="cmt-tip__src">{num.source?.label}</span>
                    {num.fact_text && <span className="cmt-tip__fact">{num.fact_text}</span>}
                  </span>
                </span>
              )
            })}
          </p>
        ))}
      </div>
    </article>
  )
}

function Sources({ r, print = false }) {
  const rows = r.numbers.map((n, i) => (
    <li key={i}>
      <span className="cmt-src__n">{n.text}</span>{' '}
      <span className="cmt-src__from">
        {n.source?.url ? (
          <a href={n.source.url} target="_blank" rel="noopener noreferrer">
            {n.source.label}
          </a>
        ) : (
          n.source?.label
        )}
      </span>
      {!print && n.section && n.section !== 'top' && (
        <>
          {' '}
          <button type="button" className="vote-linkbtn cmt-src__see" onClick={() => jumpTo(n.section)}>
            See it on this page
          </button>
        </>
      )}
    </li>
  ))
  if (print)
    return (
      <section className="cmt-print-src" aria-hidden="true">
        <h2>Where each number comes from</h2>
        <ol>{rows}</ol>
        <p>{r.frame}</p>
      </section>
    )
  return (
    <details className="cmt-src">
      <summary>Where each number comes from ({r.total})</summary>
      <ol>{rows}</ol>
    </details>
  )
}

function Result({ r, busy }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(0)
  useEffect(() => () => clearTimeout(timer.current), [])
  const gemini = r.by === 'gemini'
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(r.text)
      setCopied(true)
    } catch {
      setCopied('no')
    }
    clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setCopied(false), 2400)
  }
  const print = () => {
    const done = () => {
      document.body.classList.remove('cmt-printing')
      window.removeEventListener('afterprint', done)
    }
    document.body.classList.add('cmt-printing')
    window.addEventListener('afterprint', done)
    window.print()
  }
  const allOk = r.ok && r.checked === r.total
  return (
    <div className={`cmt-out${busy ? ' cmt-out--busy' : ''}`} aria-busy={busy || undefined}>
      <div className="cmt-head">
        <AiBadge
          by={gemini ? 'gemini' : 'fallback'}
          why={r.why || undefined}
          title={gemini ? "Written by Gemini from this page's facts; every number, name and claim was checked before it was shown" : undefined}
          className="aib--wrap"
        >
          {gemini ? 'checked' : null}
        </AiBadge>
        <p className="cmt-meta">
          To the {r.addressee}
          {!r.researched && ' (the board that hears it: not researched yet)'} · {spoken(r.seconds)} spoken ({r.words} words)
        </p>
      </div>
      {r.situation?.note && <p className="vote-note cmt-where">{r.situation.note}</p>}

      <Letter r={r} />

      <p className={`cmt-checked${allOk ? ' cmt-checked--ok' : ''}`}>
        <span aria-hidden="true">{allOk ? '✓' : '!'}</span> {r.checked} of {r.total} numbers checked against this page&apos;s sources
      </p>
      <ul className="cmt-checks" aria-label="What the checker verified">
        {r.checks.map((c) => (
          <li key={c.id} className={c.ok ? 'cmt-checks--ok' : 'cmt-checks--no'}>
            <span aria-hidden="true">{c.ok ? '✓' : '×'}</span> {c.label}
            <span className="cmt-sr">{c.ok ? ': passed' : ': did not pass'}</span>
          </li>
        ))}
      </ul>
      {r.draft_checks?.length > 0 && (
        // only what kind of problem the checker caught: a rejected draft's own words are never shown
        <p className="vote-note">
          {gemini ? "The checker sent Gemini's first draft back for " : "The checker turned down Gemini's drafts for "}
          {joinAnd(r.draft_checks.slice(0, 3).map((c) => c.caught))}.{gemini ? ' The rewrite passed every check.' : ' This is the plain version, built from the same facts.'}
        </p>
      )}

      <div className="vote-actions cmt-actions">
        <Button onClick={copy}>{copied === true ? 'Copied' : copied === 'no' ? 'Copy failed: select the text' : 'Copy'}</Button>
        <Button variant="secondary" onClick={print}>
          Print
        </Button>
        {r.send?.url ? (
          <a className="btn btn--secondary" href={r.send.url} target="_blank" rel="noopener noreferrer">
            Where to send it: {r.send.label}
          </a>
        ) : (
          <Button variant="secondary" onClick={() => jumpTo('speak')}>
            Where to send it
          </Button>
        )}
        <span className="cmt-sr" role="status">
          {copied === true ? 'Comment copied' : ''}
        </span>
      </div>
      {r.send?.how && <p className="vote-note">{r.send.how}</p>}
      <Sources r={r} />
      <Sources r={r} print />
      <p className="vote-note vote-note__frame">{r.frame}</p>
      <div className="vote-note">
        <HowAiIsUsed surface="comment" />
      </div>
    </div>
  )
}

export default function CommentWriter({ entry }) {
  const [choice, setChoice] = useState(DEFAULTS)
  const [st, setSt] = useState({ busy: false, result: null, asked: null, error: null })
  const run = useRef(0)
  const outRef = useRef(null)
  useEffect(() => () => void (run.current += 1), [])

  const write = async () => {
    const me = ++run.current
    const asked = choice
    setSt((s) => ({ ...s, busy: true, error: null }))
    try {
      const result = await writeComment(entry.id, asked)
      if (run.current !== me) return
      setSt({ busy: false, result, asked, error: null })
      const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      window.requestAnimationFrame(() => outRef.current?.scrollIntoView?.({ block: 'nearest', behavior: calm ? 'auto' : 'smooth' }))
    } catch (error) {
      if (run.current === me) setSt((s) => ({ ...s, busy: false, error }))
    }
  }

  const stale = Boolean(st.result && st.asked && !same(st.asked, choice))
  return (
    <div className="cmt stack">
      <p className="vote-body">
        Say it in your own words, with this page&apos;s facts behind it. Pick what matters to you and where you stand: Gemini writes a comment you can read at the
        meeting or send, and a checker reads every word before you see it. Every number must be one of the facts above, each grid result is framed as the
        synthetic model, and nothing claims what the real project or utility will do.
      </p>
      <Controls choice={choice} setChoice={setChoice} busy={st.busy} onWrite={write} stale={stale} />
      {st.busy && !st.result && (
        <p className="cmt-working" role="status">
          Gemini is writing from this page&apos;s facts; the checker then reads every number, name and claim.
        </p>
      )}
      {st.error && <ErrorBanner error={st.error} onRetry={write} />}
      <div ref={outRef} className="cmt-result">
        {st.result && <Result r={st.result} busy={st.busy} />}
      </div>
    </div>
  )
}
