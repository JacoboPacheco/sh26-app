// Save: the case on screen goes into the Library (POST /api/scenarios), with its result computed by
// the backend. When the case came from a saved scenario and was changed, the first choice is to save
// it as a new version of that scenario (POST /api/scenarios/{id}/version) — the original stays as it
// was, and Compare can line them up. The workspace toolbar renders it with no props.
import { useState } from 'react'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Field } from '../../ui'
import { createScenario, makeVersion } from './libraryApi'
import { appHref, caseFromStore, caseKey, caseOf, markOpened, suggestName, useOpened } from './libraryCase'
import { Popover } from './Popover'
import './library.css'

export default function SaveButton() {
  const o = useOverload()
  const opened = useOpened(o)
  const current = caseFromStore(o)
  const key = caseKey(current)
  const saved = current ? (o.scenarios || []).find((sc) => caseKey(caseOf(sc)) === key) : null
  const changedFrom = opened && opened.key !== key ? opened : null // a change to an open scenario
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(null) // 'version' | 'new' | null
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null) // the scenario just saved

  const save = async (how) => {
    setBusy(how)
    setError(null)
    try {
      // a version is named for what it is now ("Fort Myers · 1,200 MW"); the library labels it v2, v3…
      const body = { case: current, name: name.trim() || suggestName(o) || 'My scenario' }
      const sc = how === 'version' ? await makeVersion(changedFrom.id, body) : await createScenario(body)
      // what's on screen is now this scenario: a further change saves as its next version
      markOpened(sc, o.resetCount, how === 'version' ? changedFrom.rootName : sc.name)
      setDone(sc)
      o.loadScenarios?.()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  const onOpen = () => {
    setError(null)
    setDone(null)
    setName(changedFrom ? '' : suggestName(o))
  }
  const disabled = !current || !o.user
  const label = saved && !done ? 'Saved' : 'Save'
  return (
    <Popover
      label={label}
      panelLabel="Save this scenario"
      disabled={disabled}
      title={disabled ? 'Add a data center, a storm or a load level first' : saved ? `In the library as “${saved.name}”` : undefined}
      onOpen={onOpen}
    >
      {(close) => (
        <div className="stack">
          <ErrorBanner error={error} />
          {done ? (
            <>
              <p className="lib-pop__title" role="status">
                Saved as “{done.name}”{done.version > 1 ? ` (version ${done.version})` : ''}
              </p>
              <p className="lib-fine">
                {done.result ? `Measured on the synthetic model: ${verdictLine(done.result)}` : 'The numbers will show in the library.'}
              </p>
              <div className="row">
                <a className="lib-a" href={appHref('/library')}>
                  Open the library
                </a>
                <Button variant="secondary" onClick={close}>
                  Done
                </Button>
              </div>
            </>
          ) : saved ? (
            <>
              <p className="lib-pop__title">Already in the library</p>
              <p className="lib-fine">This exact scenario is saved as “{saved.name}”. Change something to save a new version.</p>
              <div className="row">
                <a className="lib-a" href={appHref('/library')}>
                  Open the library
                </a>
                <Button variant="secondary" onClick={close}>
                  Close
                </Button>
              </div>
            </>
          ) : (
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault()
                save(changedFrom && !name.trim() ? 'version' : 'new')
              }}
            >
              {changedFrom && (
                <>
                  <p className="lib-pop__title">You changed “{changedFrom.name}”</p>
                  <Button busy={busy === 'version'} disabled={!!busy} onClick={() => save('version')}>
                    Save as a new version
                  </Button>
                  <p className="lib-fine">The original stays as it was; the version sits under it in the library, ready to compare.</p>
                  <p className="lib-or">or save it on its own</p>
                </>
              )}
              <Field
                label="Name"
                value={name}
                maxLength={80}
                placeholder={suggestName(o)}
                onChange={(e) => setName(e.target.value)}
              />
              <Button type={changedFrom ? 'button' : 'submit'} variant={changedFrom ? 'secondary' : 'primary'} busy={busy === 'new'} disabled={!!busy} onClick={changedFrom ? () => save('new') : undefined}>
                {changedFrom ? 'Save as a new scenario' : 'Save to the library'}
              </Button>
              <p className="lib-fine">Everyone at the demo shares this library.</p>
            </form>
          )}
        </div>
      )}
    </Popover>
  )
}

function verdictLine(r) {
  if (r.verdict === 'holds') return 'it holds, nobody loses power.'
  if (r.verdict === 'trips') return `lines trip over ${r.steps} steps, but every light stays on.`
  return `about ${Math.round(r.people_peak ?? r.people).toLocaleString('en-US')} people lose power at the worst (estimate).`
}
