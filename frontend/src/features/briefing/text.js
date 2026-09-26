import { fmt } from '../../geo'

// The stage's own words in both languages (the slides' words come from the deck), and number formats.
export const T = {
  en: {
    sim: 'SIMULATION',
    presenter: 'PRESENTER',
    analyst: 'ANALYST',
    play: 'Play',
    pause: 'Pause',
    prev: 'Previous slide',
    next: 'Next slide',
    close: 'Close the briefing',
    captions: 'Captions',
    transcript: 'Transcript',
    download: 'Download',
    slides: 'Slides',
    document: 'Full briefing',
    sound: 'Sound',
    ask: 'Ask about it',
    voiceEleven: 'Voice: ElevenLabs',
    voiceBrowser: 'Browser voice',
    voiceNone: 'No voice here, captions only',
    voiceMuted: 'Sound off, captions only',
    byGemini: (n) => `Written by Gemini · ${n} figures checked by the engine`,
    template: 'Template narration',
    fixture: 'Preview data: the briefing engine is not live yet',
    preparing: 'Preparing the briefing…',
    applyBest: 'Apply the best fix',
    apply: 'Apply this fix',
    noAudio: 'Audio needs the voice service; captions and the transcript work without it.',
    mp3: 'Briefing audio (.mp3)',
    vtt: 'Captions (.vtt)',
    txt: 'Transcript (.txt)',
    txtLocal: 'Transcript (.txt, from this page)',
    askSoon: 'Ask about this scenario: coming with the next update.',
    slideOf: (i, n) => `Slide ${i} of ${n}`,
    proof: 'Every family of fixes, re-run on the model',
    split: 'Where the people without power come from (estimate)',
    physical: 'Cut off by the damage',
    cascade: 'Lost to the cascade',
    campus: 'Due to the data center',
    stillOut: 'still out (estimate)',
    waves: 'Rebuild order (people back, estimate)',
    wave: 'Wave',
    lines: 'lines',
    back: 'back',
  },
  es: {
    sim: 'SIMULACRO',
    presenter: 'PRESENTADORA',
    analyst: 'ANALISTA',
    play: 'Reproducir',
    pause: 'Pausa',
    prev: 'Diapositiva anterior',
    next: 'Diapositiva siguiente',
    close: 'Cerrar el informe',
    captions: 'Subtítulos',
    transcript: 'Transcripción',
    download: 'Descargar',
    slides: 'Diapositivas',
    document: 'Informe completo',
    sound: 'Sonido',
    ask: 'Preguntar',
    voiceEleven: 'Voz: ElevenLabs',
    voiceBrowser: 'Voz del navegador',
    voiceNone: 'Sin voz aquí, solo subtítulos',
    voiceMuted: 'Sin sonido, solo subtítulos',
    byGemini: (n) => `Escrito por Gemini · ${n} cifras comprobadas por el motor`,
    template: 'Narración de plantilla',
    fixture: 'Datos de vista previa: el motor del informe aún no está activo',
    preparing: 'Preparando el informe…',
    applyBest: 'Aplicar la mejor solución',
    apply: 'Aplicar esta solución',
    noAudio: 'El audio necesita el servicio de voz; los subtítulos y la transcripción funcionan sin él.',
    mp3: 'Audio del informe (.mp3)',
    vtt: 'Subtítulos (.vtt)',
    txt: 'Transcripción (.txt)',
    txtLocal: 'Transcripción (.txt, de esta página)',
    askSoon: 'Preguntas sobre este escenario: llegan con la próxima actualización.',
    slideOf: (i, n) => `Diapositiva ${i} de ${n}`,
    proof: 'Cada familia de soluciones, simulada de nuevo',
    split: 'De dónde salen las personas sin electricidad (estimación)',
    physical: 'Aisladas por el daño',
    cascade: 'Perdidas en la cascada',
    campus: 'Por el centro de datos',
    stillOut: 'siguen sin luz (estimación)',
    waves: 'Orden de reconstrucción (personas con luz, estimación)',
    wave: 'Fase',
    lines: 'líneas',
    back: 'recuperan la luz',
  },
}

export const VERDICT = {
  en: { holds: 'Holds', partly: 'Partly', fails: 'Fails', not_needed: 'Not needed', not_checked: 'Not checked' },
  es: { holds: 'Funciona', partly: 'En parte', fails: 'Falla', not_needed: 'No hace falta', not_checked: 'Sin comprobar' },
}

export const FAMILY = {
  en: {
    shrink: 'Shrink the campus',
    move: 'Move it',
    flexible: 'Make it flexible',
    time_of_day: 'Another hour',
    upgrade: 'Upgrade lines',
    onsite: 'On-site generation',
    combo: 'Smaller + upgrades',
    remove: 'No data center',
  },
  es: {
    shrink: 'Reducir el centro de datos',
    move: 'Moverlo',
    flexible: 'Hacerlo flexible',
    time_of_day: 'Otra hora',
    upgrade: 'Mejorar líneas',
    onsite: 'Generación propia',
    combo: 'Menor + mejoras',
    remove: 'Sin centro de datos',
  },
}

// A verdict word at the end of a deck line ("Shrink to 550 MW: holds") becomes a chip.
const VERDICT_RE = {
  en: /\s*[:·—–-]\s*(holds|partly|fails|not needed|not checked)\.?$/i,
  es: /\s*[:·—–-]\s*(funciona|se sostiene|aguanta|en parte|parcial(?:mente)?|falla|no hace falta|sin comprobar|no comprobad[ao])\.?$/i,
}
const VERDICT_OF = {
  holds: 'holds',
  partly: 'partly',
  fails: 'fails',
  'not needed': 'not_needed',
  'not checked': 'not_checked',
  funciona: 'holds',
  aguanta: 'holds',
  'se sostiene': 'holds',
  'en parte': 'partly',
  parcial: 'partly',
  parcialmente: 'partly',
  falla: 'fails',
  'no hace falta': 'not_needed',
  'sin comprobar': 'not_checked',
  'no comprobado': 'not_checked',
  'no comprobada': 'not_checked',
}
export function splitVerdict(line, lang) {
  const m = String(line).match(VERDICT_RE[lang] || VERDICT_RE.en)
  if (!m) return { text: line, verdict: null }
  return { text: line.slice(0, m.index), verdict: VERDICT_OF[m[1].toLowerCase()] || null }
}

export const num = (n) => (n == null || !Number.isFinite(Number(n)) ? '–' : fmt(Number(n)))

// 9,520,000 → "9.52 million"; under a million, the full number
export function people(n, lang = 'en') {
  const v = Number(n) || 0
  if (Math.abs(v) >= 1e6) {
    const m = v / 1e6
    const s = m >= 10 ? m.toFixed(1) : m.toFixed(2)
    return `${s.replace(/\.?0+$/, '')} ${lang === 'es' ? 'millones' : 'million'}`
  }
  return fmt(v)
}

export const usd = (n) => {
  const v = Number(n) || 0
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1).replace(/\.0$/, '')} billion`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1).replace(/\.0$/, '')} million`
  return `$${fmt(v)}`
}
