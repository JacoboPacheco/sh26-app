import { useEffect, useRef, useState } from 'react'
import { level, tap } from './audioMeter'

const STEP_MS = 40 // one bar per 40 ms: 120 px holds the last ~1.6 s

// A thin live level meter for the ElevenLabs voice, read from the playing audio element through a WebAudio
// AnalyserNode (audioMeter.js): the bars scroll left as the words are spoken and go flat in the pauses.
// Shown only once an element is really tapped (never for the browser voice or captions-only, and never when
// tapping could silence playback). Reduced motion: a static speaking mark instead of moving bars.
export default function LevelMeter({ audio, reduced }) {
  const canvas = useRef(null)
  const tapRef = useRef(null)
  const [ok, setOk] = useState(false) // the last element tried was tapped (kept through the gaps between segments)
  const el = audio?.el || null
  const cors = !!audio?.meter

  useEffect(() => {
    tapRef.current = null
    if (!el) return undefined
    let live = true
    tap(el, cors).then((t) => {
      if (!live) return
      tapRef.current = t
      setOk(!!t)
    })
    return () => {
      live = false
    }
  }, [el, cors])

  useEffect(() => {
    const cv = canvas.current
    if (!ok || reduced || !cv) return undefined
    const dpr = Math.min(window.devicePixelRatio || 1, 3)
    const w = cv.clientWidth || 120
    const h = cv.clientHeight || 18
    cv.width = Math.round(w * dpr)
    cv.height = Math.round(h * dpr)
    const g = cv.getContext('2d')
    if (!g) return undefined
    g.scale(dpr, dpr)
    const color = getComputedStyle(cv).color
    const n = Math.max(12, Math.floor(w / 3)) // 2 px bars, 1 px gaps, however wide the caption lets it be
    const hist = new Array(n).fill(0)
    const bw = w / n
    let raf = 0
    let last = 0
    const draw = (now) => {
      if (now - last >= STEP_MS) {
        last = now
        hist.shift()
        hist.push(level(tapRef.current))
        g.clearRect(0, 0, w, h)
        g.fillStyle = color
        for (let i = 0; i < n; i++) {
          const bh = Math.max(1, Math.round(hist[i] * (h - 2)))
          g.globalAlpha = 0.35 + 0.65 * (i / (n - 1)) // older bars fade toward the left
          g.fillRect(i * bw, (h - bh) / 2, Math.max(1, bw - 1), bh)
        }
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [ok, reduced])

  if (!ok) return null
  if (reduced)
    return (
      <svg className="rs-meter rs-meter--still" viewBox="0 0 20 14" width="20" height="14" aria-hidden="true">
        {[5, 10, 14, 8, 4].map((bh, i) => (
          <rect key={i} x={i * 4} y={(14 - bh) / 2} width="2.4" height={bh} rx="1" fill="currentColor" />
        ))}
      </svg>
    )
  return <canvas ref={canvas} className="rs-meter" aria-hidden="true" data-meter="live" />
}
