import { useId } from 'react'

// The UI kit: small, accessible building blocks styled by the tokens in index.css.
// Use these instead of raw <button>/<input> so every screen shares one look and
// the browser check (labels, alt text) stays green. Retheme by editing the tokens.

export function Button({ variant = 'primary', busy = false, disabled = false, type = 'button', children, ...props }) {
  const cls = ['btn', variant !== 'primary' && `btn--${variant}`].filter(Boolean).join(' ')
  // `busy` always wins over a caller's `disabled`, so a click can't double-submit mid-save.
  return (
    <button type={type} className={cls} {...props} disabled={busy || disabled} aria-busy={busy || undefined}>
      {children}
    </button>
  )
}

// Labelled control. `as` is 'input' (default), 'textarea', or 'select' (pass <option>s as children).
// `label` is required: without one the control has no accessible name.
export function Field({ label, hint, as = 'input', id, children, ...props }) {
  const autoId = useId()
  const controlId = id || autoId
  const hintId = hint ? `${controlId}-hint` : undefined
  const Control = as
  if (import.meta.env.DEV && !label) console.warn('Field rendered without a label — screen readers get nothing')
  return (
    <div className="field">
      <label htmlFor={controlId}>{label}</label>
      <Control id={controlId} aria-describedby={hintId} {...props}>
        {as === 'select' ? children : undefined}
      </Control>
      {hint && (
        <span className="hint" id={hintId}>
          {hint}
        </span>
      )}
    </div>
  )
}

export function Card({ title, actions, children }) {
  return (
    <section className="card stack">
      {(title || actions) && (
        <div className="row" style={{ justifyContent: 'space-between' }}>
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}

export function EmptyState({ title, children, action }) {
  return (
    <div className="empty stack">
      <strong>{title}</strong>
      {children && <span>{children}</span>}
      {action}
    </div>
  )
}

export function Loading({ label = 'Loading…' }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      {label}
    </p>
  )
}

// Show an Error (from api.js) with an optional retry. Renders nothing when there's no error.
export function ErrorBanner({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="banner--error" role="alert">
      <span>{error.message || String(error)}</span>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}

// tone: 'neutral' | 'warn'. Use tone="warn" for the AI-offline / fallback badge.
export function Badge({ tone = 'neutral', children }) {
  return <span className={tone === 'warn' ? 'badge badge--warn' : 'badge'}>{children}</span>
}
