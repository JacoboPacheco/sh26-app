// Share: a read-only public link to a saved scenario (POST /api/scenarios/{id}/share). The link opens
// the app's brief page, which anyone can read without the demo account.
//   <ShareButton />             the workspace toolbar: shares the case on screen (saves it first if needed)
//   <ShareLink scenario={sc} /> one saved scenario (the Library's cards)
import { useState } from 'react'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Field } from '../../ui'
import { createScenario, makeVersion, shareScenario, unshareScenario } from './libraryApi'
import { caseFromStore, caseKey, caseOf, markOpened, shareUrl, suggestName, useOpened } from './libraryCase'
import { Popover } from './Popover'
import './library.css'

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false // no clipboard permission (or not a secure page): the link stays selectable
  }
}

// The link once it exists: selectable, with a copy button and what it shows.
function LinkBox({ slug, copied, onCopy }) {
  const url = shareUrl(slug)
  return (
    <div className="lib-linkbox">
      <Field label="Link to this scenario" value={url} readOnly onFocus={(e) => e.target.select()} />
      <div className="row">
        <Button variant="secondary" onClick={onCopy}>
          {copied === true ? 'Copied' : 'Copy link'}
        </Button>
        <a className="lib-a" href={url} target="_blank" rel="noreferrer">
          Open it
        </a>
      </div>
      <p className="lib-fine" role="status" aria-live="polite">
        {copied === false ? 'Copy it from the box above. ' : ''}Anyone with the link sees this scenario and its result, read-only. Nothing else in the library.
      </p>
    </div>
  )
}

// Share one saved scenario (a Library card).
export function ShareLink({ scenario, onChanged }) {
  const [slug, setSlug] = useState(scenario.share_slug || null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(null)
  const make = async () => {
    setError(null)
    if (slug) return
    setBusy(true)
    try {
      const r = await shareScenario(scenario.id)
      setSlug(r.slug)
      setCopied(await copy(shareUrl(r.slug)))
      onChanged?.()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }
  const off = async () => {
    setBusy(true)
    setError(null)
    try {
      await unshareScenario(scenario.id)
      setSlug(null)
      setCopied(null)
      onChanged?.()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Popover label={slug ? 'Shared' : 'Share'} panelLabel={`Share ${scenario.name}`} onOpen={make}>
      <div className="stack">
        <p className="lib-pop__title">Share “{scenario.name}”</p>
        <ErrorBanner error={error} onRetry={make} />
        {busy && !slug && <p className="muted">Making a link…</p>}
        {slug && <LinkBox slug={slug} copied={copied} onCopy={async () => setCopied(await copy(shareUrl(slug)))} />}
        {slug && !scenario.example && (
          <Button variant="danger" busy={busy} onClick={off}>
            Turn the link off
          </Button>
        )}
      </div>
    </Popover>
  )
}

// The workspace toolbar's Share: the case on screen. Already saved → its link; otherwise save it
// (as a version of the open scenario when it came from one) and then share.
export default function ShareButton() {
  const o = useOverload()
  const opened = useOpened(o)
  const current = caseFromStore(o)
  const key = caseKey(current)
  const saved = current ? (o.scenarios || []).find((sc) => caseKey(caseOf(sc)) === key) : null
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null) // {slug, name}
  const [copied, setCopied] = useState(null)

  const shareSaved = async (sc) => {
    setBusy(true)
    setError(null)
    try {
      const r = await shareScenario(sc.id)
      setDone({ slug: r.slug, name: sc.name })
      setCopied(await copy(shareUrl(r.slug)))
      o.loadScenarios?.()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }
  const saveAndShare = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const body = { name: name.trim() || suggestName(o) || 'My scenario', case: current }
      const sc = opened && opened.key !== key ? await makeVersion(opened.id, body) : await createScenario(body)
      markOpened(sc, o.resetCount, opened && opened.key !== key ? opened.rootName : null)
      const r = await shareScenario(sc.id)
      setDone({ slug: r.slug, name: sc.name })
      setCopied(await copy(shareUrl(r.slug)))
      o.loadScenarios?.()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const onOpen = () => {
    setError(null)
    setCopied(null)
    setDone(null)
    setName(suggestName(o))
    if (saved) shareSaved(saved)
  }
  const disabled = !current || !o.user
  return (
    <Popover
      label="Share"
      panelLabel="Share this scenario"
      disabled={disabled}
      title={disabled ? 'Add a data center, a storm or a load level first' : undefined}
      onOpen={onOpen}
    >
      <div className="stack">
        <ErrorBanner error={error} />
        {done ? (
          <>
            <p className="lib-pop__title">Share “{done.name}”</p>
            <LinkBox slug={done.slug} copied={copied} onCopy={async () => setCopied(await copy(shareUrl(done.slug)))} />
          </>
        ) : saved || busy ? (
          <p className="muted" role="status">
            Making a link…
          </p>
        ) : (
          <form className="stack" onSubmit={saveAndShare}>
            <p className="lib-pop__title">Save it to share it</p>
            <p className="lib-fine">
              {opened && opened.key !== key
                ? `It will be saved as a new version of “${opened.rootName}”, then shared.`
                : 'It will be saved to the library, then shared.'}
            </p>
            <Field label="Name" value={name} maxLength={80} required onChange={(e) => setName(e.target.value)} />
            <Button type="submit" busy={busy} disabled={!name.trim()}>
              Save and share
            </Button>
          </form>
        )}
      </div>
    </Popover>
  )
}
