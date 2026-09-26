// FEATURE: the one small label every AI-made result carries. Says who made it and, when the engine
// re-checked it, that too — so a viewer never has to guess what is measured and what is proposed.
// by: 'gemini' (proposed or written by Gemini) | 'engine' (computed, no AI) | 'fallback' (the plain version ran)
// verified: Gemini's output was re-run and checked by the power-flow engine (only with by="gemini")
// why: for a fallback, the reason in its parenthesis (default "Gemini unavailable"), e.g. "Gemini too slow"
// compact: the fallback reads just "Plain version" (the full text stays in the tooltip), for narrow columns
// lang: 'en' (default) | 'es', for a surface that answers in Spanish
// className: extra classes (aib--wrap lets a long label wrap in a narrow spot)
// children: a short note after the label ("Gemini · engine-verified · earlier run")
import './ai.css'

const T = {
  en: {
    gemini: 'Gemini',
    engine: 'Engine',
    fallback: 'Plain version',
    unavailable: 'Gemini unavailable',
    verified: 'engine-verified',
    tipGemini: 'Written or proposed by Gemini from the computed facts',
    tipVerified: 'Proposed by Gemini, re-run and checked by the power-flow engine',
    tipEngine: 'Computed by the power-flow engine on the synthetic grid model. No AI.',
    tipFallback: (why) => `${why}: the plain rule-based version ran instead`,
  },
  es: {
    gemini: 'Gemini',
    engine: 'Motor',
    fallback: 'Versión simple',
    unavailable: 'Gemini no disponible',
    verified: 'verificado por el motor',
    tipGemini: 'Escrito o propuesto por Gemini a partir de los datos calculados',
    tipVerified: 'Propuesto por Gemini, recalculado y comprobado por el motor de flujo de potencia',
    tipEngine: 'Calculado por el motor de flujo de potencia sobre el modelo sintético de la red. Sin IA.',
    tipFallback: (why) => `${why}: respondió la versión simple basada en reglas`,
  },
}

export default function AiBadge({ by = 'gemini', verified = false, why, compact = false, lang = 'en', title, className, children }) {
  const t = T[lang] || T.en
  const kind = by === 'engine' || by === 'fallback' ? by : 'gemini'
  const reason = why || t.unavailable
  const label = kind === 'fallback' && !compact ? `${t.fallback} (${reason})` : t[kind]
  const check = verified && kind === 'gemini'
  const tip = title || (check ? t.tipVerified : kind === 'fallback' ? t.tipFallback(reason) : kind === 'engine' ? t.tipEngine : t.tipGemini)
  return (
    <span className={`aib aib--${kind}${className ? ` ${className}` : ''}`} title={tip}>
      <span className="aib__dot" aria-hidden="true" />
      {label}
      {check && <span className="aib__check"> · {t.verified}</span>}
      {children != null && children !== false && <span className="aib__note"> · {children}</span>}
    </span>
  )
}
