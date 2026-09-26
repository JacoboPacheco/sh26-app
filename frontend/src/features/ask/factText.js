// Cited facts come from the engine with English labels ("People without power in Naples"). In a
// Spanish answer the chips read in Spanish: known label shapes are translated here, anything else
// stays as the engine wrote it. Numbers are never touched.

const FAMILY = {
  shrink: 'reducir',
  move: 'mover',
  flexible: 'flexible',
  time_of_day: 'hora',
  upgrade: 'reforzar',
  onsite: 'generación propia',
  combo: 'combinada',
  remove: 'sin centro de datos',
}

const RULES = [
  [/^People without power at the end$/i, 'Personas sin luz al final'],
  [/^People without power at the worst moment$/i, 'Personas sin luz en el peor momento'],
  [/^Share of the state's residents without power$/i, 'Parte de la población sin luz'],
  [/^Cascade steps.*$/i, 'Pasos de la cascada'],
  [/^Load lost at the end$/i, 'Carga perdida al final'],
  [/^Room at the site before the first overload.*$/i, 'Margen en el sitio'],
  [/^Room at this substation.*$/i, 'Margen en el sitio'],
  [/^Data center size$/i, 'Tamaño del centro de datos'],
  [/^Data center location.*$/i, 'Ubicación del centro de datos'],
  [/^Load level.*$/i, 'Nivel de carga'],
  [/^Time of day$/i, 'Hora del día'],
  [/^First line (?:to fail|over its limit)$/i, 'Primera línea en fallar'],
  [/^Its loading (?:when it failed|with the data center)$/i, 'Su carga con el centro de datos'],
  [/^Its loading without the data center$/i, 'Su carga sin el centro de datos'],
  [/^Data center's share of that line's flow$/i, 'Parte del flujo del centro de datos'],
  [/^People who lose power because of the data center$/i, 'Personas sin luz por el centro de datos'],
  [/^People without power in (.+)$/i, 'Personas sin luz en $1'],
  [/^Step when (.+) first lost power$/i, 'Paso en que $1 se quedó sin luz'],
  [/^Load lost in (.+)$/i, 'Carga perdida en $1'],
  [/^Step (\d+): line that tripped$/i, 'Paso $1: línea desconectada'],
  [/^Step (\d+): its loading when it tripped$/i, 'Paso $1: su carga al desconectarse'],
  [/^Step (\d+): people without power so far$/i, 'Paso $1: personas sin luz'],
  [/^What-if: cascade steps$/i, 'Simulación: pasos'],
  [/^What-if: people without power$/i, 'Simulación: personas sin luz'],
  [/^What-if: more people without power than in this scenario$/i, 'Simulación: más personas sin luz que ahora'],
  [/^What-if: fewer people without power than in this scenario$/i, 'Simulación: menos personas sin luz que ahora'],
  [/^What-if: data center size$/i, 'Simulación: tamaño'],
  [/^What-if: data center site$/i, 'Simulación: sitio'],
  [/^What-if: .*limit.*$/i, 'Simulación: líneas sobre su límite'],
  [/^What-if: load lost$/i, 'Simulación: carga perdida'],
  [/^Cost of the blackout.*$/i, 'Costo del apagón'],
  [/^Cost of the upgrades.*$/i, 'Costo de las mejoras'],
  [/^Assumed outage length.*$/i, 'Duración supuesta del apagón'],
  [/^Hospitals that would be on backup power.*$/i, 'Hospitales con energía de respaldo'],
  [/^Where those hospitals are$/i, 'Dónde están esos hospitales'],
  [/^People cut off even with unlimited lines.*$/i, 'Personas aisladas aun con líneas ilimitadas'],
  [/^Share of the outage no line upgrade can reach$/i, 'Parte del apagón sin solución'],
  [/^Wave (\d+): people back.*$/i, 'Ola $1: personas con luz'],
  [/^Wave (\d+): lines rebuilt.*$/i, 'Ola $1: líneas reconstruidas'],
  [/^Wave (\d+): km of line rebuilt.*$/i, 'Ola $1: km de línea reconstruidos'],
  [/^Restoration check$/i, 'Comprobación de la restauración'],
  [/^Repairs compared.*$/i, 'Reparaciones comparadas'],
  [/^People the plan's order brings back beyond biggest-first$/i, 'Personas de más con el orden del plan'],
  [/^Lines knocked out by the storm$/i, 'Líneas derribadas por la tormenta'],
  [/^Data center's size$/i, 'Tamaño del centro de datos'],
  [/^Repair wave 1: people back on$/i, 'Primera ola: personas con luz'],
  [/^Repair wave 1: lines rebuilt$/i, 'Primera ola: líneas reconstruidas'],
  [/^Verdict$/i, 'Veredicto'],
  [/^Model$/i, 'Modelo'],
  [/^Best verified fix$/i, 'Mejor solución verificada'],
]

export function factLabel(label, lang) {
  const text = String(label || '')
  if (lang !== 'es') return text
  const fix = /^Fix,? \(?([a-z_]+)\)?[:,]?\s*(.*)$/i.exec(text)
  if (fix && FAMILY[fix[1]]) return `Solución (${FAMILY[fix[1]]})${fix[2] ? `: ${esFixField(fix[2])}` : ''}`
  const withFix = /^(.+) with this fix \(([a-z_]+)\)$/i.exec(text)
  if (withFix && FAMILY[withFix[2]]) return `Solución (${FAMILY[withFix[2]]}): ${esFixField(withFix[1])}`
  for (const [rx, out] of RULES) if (rx.test(text)) return text.replace(rx, out)
  return text
}

function esFixField(rest) {
  const r = rest.toLowerCase()
  if (/people/.test(r)) return 'personas sin luz'
  if (/steps/.test(r)) return 'pasos'
  if (/verified result|result/.test(r)) return 'resultado'
  if (/mva/.test(r)) return 'MVA'
  if (/km|length/.test(r)) return 'km'
  if (/lines/.test(r)) return 'líneas'
  if (/towns/.test(r)) return 'ciudades'
  return rest
}

// "783,883 people (estimate)" → "783,883 personas (estimación)"; numbers stay as they are.
export function factValue(text, lang) {
  const t = String(text || '')
  const ordinal = /^(\d+) step$/.exec(t) // a step number, not a count: "5 step" → "step 5"
  if (ordinal) return lang === 'es' ? `paso ${ordinal[1]}` : `step ${ordinal[1]}`
  if (lang !== 'es') return t
  return t
    .replace(/\(estimate\)/g, '(estimación)')
    .replace(/\bpeople\b/g, 'personas')
    .replace(/\bsteps?\b/g, 'pasos')
    .replace(/\blines\b/g, 'líneas')
    .replace(/\bhospitals\b/g, 'hospitales')
}
