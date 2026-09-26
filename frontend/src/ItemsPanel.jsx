import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { Badge, Button, Card, EmptyState, ErrorBanner, Field } from './ui'

// EXAMPLE FEATURE — template scaffolding, not project code. The frontend half of
// backend/items.py: list, create (with AI-suggested tags that fall back gracefully),
// delete. Copy this shape for the first real feature, then remove it (this file and
// its use in App.jsx; backend/items.py lists the backend pieces).
export default function ItemsPanel() {
  const [items, setItems] = useState(undefined) // undefined = loading
  const [loadError, setLoadError] = useState(null)
  const [formError, setFormError] = useState(null)
  const [title, setTitle] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState(null) // id being deleted
  const [aiOffline, setAiOffline] = useState(false)

  const load = useCallback(
    () =>
      api('/api/items')
        .then((list) => {
          setLoadError(null)
          setItems(list)
        })
        .catch((err) => setLoadError(err)), // keep whatever list we had; the banner says why
    [],
  )

  useEffect(() => {
    load()
  }, [load])

  async function onSubmit(e) {
    e.preventDefault()
    setBusy(true)
    setFormError(null)
    try {
      const created = await api('/api/items', { method: 'POST', body: { title, notes } })
      setAiOffline(created.fallback)
      setItems((list) => [created, ...(list || [])])
      setTitle('')
      setNotes('')
    } catch (err) {
      setFormError(err)
    } finally {
      setBusy(false)
    }
  }

  async function remove(id) {
    setDeleting(id)
    try {
      await api(`/api/items/${id}`, { method: 'DELETE' })
    } catch (err) {
      if (!/not found/i.test(err.message)) {
        setLoadError(err)
        return
      }
      // already gone (double click, or deleted elsewhere) — same outcome
    } finally {
      setDeleting(null)
    }
    setItems((list) => list.filter((it) => it.id !== id))
  }

  return (
    <>
      <Card title="Example: add an item">
        <form onSubmit={onSubmit} className="stack">
          <Field label="Title" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={120} />
          <Field
            label="Notes"
            as="textarea"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            maxLength={2000}
            hint="Tags are suggested by AI when a key is configured; otherwise the item is saved without them."
          />
          <ErrorBanner error={formError} />
          <div className="row">
            <Button type="submit" busy={busy}>
              {busy ? 'Saving…' : 'Add'}
            </Button>
            {aiOffline && <Badge tone="warn">AI offline — tags skipped</Badge>}
          </div>
        </form>
      </Card>

      <Card title="Example: your items">
        <ErrorBanner error={loadError} onRetry={load} />
        {items === undefined ? (
          !loadError && (
            <p className="muted" role="status">
              Loading…
            </p>
          )
        ) : items.length === 0 ? (
          <EmptyState title="Nothing here yet">Add your first item above.</EmptyState>
        ) : (
          <ul className="list">
            {items.map((it) => (
              <li key={it.id}>
                <div>
                  <strong>{it.title}</strong>
                  {it.notes && <p className="muted notes">{it.notes}</p>}
                  {(it.tags.length > 0 || it.fallback) && (
                    <div className="row" style={{ marginTop: '0.25rem' }}>
                      {it.tags.map((t) => (
                        <Badge key={t}>{t}</Badge>
                      ))}
                      {it.fallback && <Badge tone="warn">AI offline</Badge>}
                    </div>
                  )}
                </div>
                <Button variant="danger" busy={deleting === it.id} onClick={() => remove(it.id)} aria-label={`Delete ${it.title}`}>
                  Delete
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}
