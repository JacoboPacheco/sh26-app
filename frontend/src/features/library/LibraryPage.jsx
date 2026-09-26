// The Library (#/next/library): the built-in examples on a shelf, then the saved scenarios, each with
// its versions nested under it. Open loads a scenario's whole case into the workspace; Brief, Share
// and Compare lead to the app's other pages. Every number on a card was computed by the backend when
// the scenario was saved (backend/scenarios.py → result) on a synthetic grid model; people are estimates.
//
// Props (all optional): onOpen(scenario, regionCode) — after a scenario is loaded into the store;
// default: go to that state's workspace (#/next/state/XX).
import { useMemo, useState } from 'react'
import { useOverload } from '../../store'
import { Button, EmptyState, ErrorBanner, Loading } from '../../ui'
import { syncExamples } from './libraryApi'
import { appHref, goTo, openScenario } from './libraryCase'
import { ScenarioCard } from './ScenarioCard'
import './library.css'

// originals in order, each with its versions (oldest first); a version whose original is gone stands alone
function group(list) {
  const byId = new Map(list.map((sc) => [sc.id, sc]))
  const kids = new Map()
  const roots = []
  for (const sc of list) {
    if (sc.parent_id && byId.has(sc.parent_id)) {
      if (!kids.has(sc.parent_id)) kids.set(sc.parent_id, [])
      kids.get(sc.parent_id).push(sc)
    } else roots.push(sc)
  }
  return roots.map((sc) => ({ sc, versions: (kids.get(sc.id) || []).sort((a, b) => (a.version || 0) - (b.version || 0) || a.id - b.id) }))
}

export default function LibraryPage({ onOpen }) {
  const o = useOverload()
  const { scenarios, scenarioError, loadScenarios, user } = o
  const [picked, setPicks] = useState([])
  const [opening, setOpening] = useState(null)
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState(null)

  const groups = useMemo(() => group(scenarios || []), [scenarios])
  // a pick that was deleted (here or by someone else at the demo) drops out, so Compare never gets a dead id
  const picks = useMemo(() => picked.filter((id) => (scenarios || []).some((sc) => sc.id === id)), [picked, scenarios])
  const examples = groups.filter((g) => g.sc.example)
  const mine = groups.filter((g) => !g.sc.example)

  const open = (sc) => {
    setOpening(sc.id)
    const code = openScenario(o, sc)
    if (!code) {
      setOpening(null)
      return
    }
    if (onOpen) onOpen(sc, code)
    else goTo(`/state/${code}`)
  }
  const pick = (id) => setPicks(() => (picks.includes(id) ? picks.filter((x) => x !== id) : [...picks.slice(-1), id]))
  const addExamples = async () => {
    setAdding(true)
    setAddError(null)
    try {
      await syncExamples()
      await loadScenarios()
    } catch (err) {
      setAddError(err)
    } finally {
      setAdding(false)
    }
  }

  if (user === null) return <EmptyState title="Signing in…">The library belongs to the demo account; it signs in on its own.</EmptyState>
  if (scenarioError && scenarios === undefined) return <ErrorBanner error={scenarioError} onRetry={loadScenarios} />
  if (scenarios === undefined) return <Loading label="Loading the library…" />

  const card = ({ sc, versions }) => (
    <li key={sc.id}>
      <ScenarioCard sc={sc} versions={versions} onOpen={open} onChanged={loadScenarios} picks={picks} onPick={pick} />
    </li>
  )
  return (
    <div className="lib" aria-busy={opening ? true : undefined}>
      <ErrorBanner error={scenarioError} onRetry={loadScenarios} />
      <section className="lib-section" aria-labelledby="lib-examples">
        <div className="lib-section__head">
          <h2 id="lib-examples">Examples</h2>
          <p className="lib-fine">
            Built in and locked. Each one was solved on a synthetic grid model, not any utility&apos;s network; people are estimates. Open one, change it,
            and save your own version.
          </p>
        </div>
        {examples.length ? (
          <ul className="lib-shelf">{examples.map(card)}</ul>
        ) : (
          <EmptyState
            title="The examples aren't in this library yet"
            action={
              <Button variant="secondary" busy={adding} onClick={addExamples}>
                Add the examples
              </Button>
            }
          >
            Six measured scenarios: Fort Myers at three sizes and in a heat wave, a Gulf storm, an AI boom, and Abilene, Texas.
          </EmptyState>
        )}
        <ErrorBanner error={addError} onRetry={addExamples} />
      </section>

      <section className="lib-section" aria-labelledby="lib-mine">
        <div className="lib-section__head">
          <h2 id="lib-mine">Saved scenarios{mine.length ? ` (${mine.length})` : ''}</h2>
          <p className="lib-fine">Everyone at the demo shares this library. Versions sit under the scenario they came from.</p>
        </div>
        {mine.length ? (
          <ul className="lib-shelf lib-shelf--mine">{mine.map(card)}</ul>
        ) : (
          <EmptyState
            title="Nothing saved yet"
            action={
              <a className="btn btn--secondary" href={appHref('/state/FL')}>
                Build a scenario
              </a>
            }
          >
            Drop a data center on a state, then press Save. Or open an example and save your version of it.
          </EmptyState>
        )}
      </section>

      {picks.length > 0 && (
        <div className="lib-compare" role="region" aria-label="Compare">
          <span className="lib-fine" role="status" aria-live="polite">
            {picks.length === 2 ? 'Two picked.' : 'Tick Compare on one more to line them up.'}
          </span>
          <div className="row">
            <Button variant="secondary" onClick={() => setPicks([])}>
              Clear
            </Button>
            <Button disabled={picks.length !== 2} onClick={() => goTo(`/compare/${picks[0]}/${picks[1]}`)}>
              Compare the two
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
