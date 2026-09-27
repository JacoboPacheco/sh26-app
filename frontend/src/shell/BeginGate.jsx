import { useEffect } from 'react'
import { swallowGesture } from './beginRules'
import './begin.css'

// "Click anywhere to begin": one small pill low on the map, on the bare home page, until the viewer begins.
// No timer, never auto-dismissed, no dialog and no dark sheet: the map stays visible and moving behind it (a
// veil over the map, drawn by features/flow/IntroCurtain, only dims it). Any click or tap begins (and is
// swallowed: it does nothing else), and so does any key. The pill is also a real button, so a screen reader
// or the keyboard can find it and press it. Intro.jsx starts the opening when this calls onBegin.
const T = {
  en: { begin: 'Click anywhere to begin' },
  es: { begin: 'Haz clic en cualquier lugar para empezar' },
}

// keys that are not "a key was pressed to begin": modifiers alone, function keys
const NOT_A_KEY = new Set(['Shift', 'Control', 'Alt', 'AltGraph', 'Meta', 'CapsLock', 'NumLock', 'ScrollLock', 'Fn', 'OS', 'Dead', 'Process', 'Unidentified'])

export default function BeginGate({ onBegin, lang = 'en' }) {
  useEffect(() => {
    const down = (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return // a right- or middle-click is not "begin"
      e.stopPropagation()
      e.preventDefault()
      swallowGesture()
      onBegin()
    }
    const key = (e) => {
      if (e.isComposing || e.ctrlKey || e.metaKey || e.altKey || NOT_A_KEY.has(e.key) || /^F\d{1,2}$/.test(e.key)) return
      onBegin() // the key still does its normal job (Tab moves focus)
    }
    // capture, so nothing under the pointer sees the click that begins
    window.addEventListener('pointerdown', down, true)
    window.addEventListener('keydown', key, true)
    return () => {
      window.removeEventListener('pointerdown', down, true)
      window.removeEventListener('keydown', key, true)
    }
  }, [onBegin])

  return (
    <div className="begin">
      <button type="button" className="begin__btn" onClick={onBegin}>
        <span className="begin__dot" aria-hidden="true" />
        {(T[lang] || T.en).begin}
      </button>
    </div>
  )
}
