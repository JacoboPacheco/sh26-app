import { useEffect, useId, useRef, useState } from 'react'
import { Button } from '../../ui'

// A button that opens a small panel under it (the toolbar's Save and Share). Escape or a click
// outside closes it; focus goes into the panel on open and back to the button on Escape.
export function Popover({ label, variant = 'secondary', disabled, title, panelLabel, children, onOpen }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef(null)
  const btn = useRef(null)
  const panel = useRef(null)
  const id = useId()

  useEffect(() => {
    if (!open) return undefined
    const away = (e) => {
      if (wrap.current && !wrap.current.contains(e.target)) setOpen(false)
    }
    const key = (e) => {
      if (e.key === 'Escape') {
        setOpen(false)
        btn.current?.focus()
      }
    }
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', key)
    // the first field (or button) in the panel takes focus
    const t = setTimeout(() => panel.current?.querySelector('input, button, a')?.focus(), 0)
    return () => {
      clearTimeout(t)
      document.removeEventListener('pointerdown', away)
      document.removeEventListener('keydown', key)
    }
  }, [open])

  const close = () => setOpen(false)
  return (
    <div className="lib-pop" ref={wrap}>
      <Button
        ref={btn}
        variant={variant}
        disabled={disabled}
        title={title}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => {
          if (!open) onOpen?.()
          setOpen((v) => !v)
        }}
      >
        {label}
      </Button>
      {open && (
        <div className="lib-pop__panel" id={id} role="dialog" aria-label={panelLabel} ref={panel}>
          {typeof children === 'function' ? children(close) : children}
        </div>
      )}
    </div>
  )
}
