// Claude Code status line: model | context bar | 5-hour limit | branch (dirty, unpushed).
// Reads the session JSON Claude Code sends on stdin; must be fast and never fail.
import { spawnSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

let d = {}
try {
  d = JSON.parse(readFileSync(0, 'utf8') || '{}')
} catch {}

// Snapshot usage for the UserPromptSubmit hook (scripts/hooks/usage-context.mjs),
// which injects it into Claude's context so it can plan around the budget.
try {
  const dir = join(d.workspace?.current_dir || process.cwd(), '.claude', 'tmp')
  mkdirSync(dir, { recursive: true })
  writeFileSync(
    join(dir, 'usage.json'),
    JSON.stringify({
      ts: Date.now(),
      model: d.model?.display_name ?? null,
      context_pct: d.context_window?.used_percentage ?? null,
      five_hour: d.rate_limits?.five_hour ?? null,
      seven_day: d.rate_limits?.seven_day ?? null,
    })
  )
} catch {}

const color = (code, s) => `\x1b[${code}m${s}\x1b[0m`
const parts = []

parts.push(d.model?.display_name || 'Claude')

const pct = Math.round(d.context_window?.used_percentage ?? 0)
const bar = '▓'.repeat(Math.round(pct / 10)).padEnd(10, '░')
const ctx = `ctx ${bar} ${pct}%`
parts.push(pct >= 85 ? color(31, ctx) : pct >= 65 ? color(33, ctx) : ctx)

const five = d.rate_limits?.five_hour?.used_percentage
if (five != null) {
  const s = `5h ${Math.round(five)}%`
  parts.push(five >= 85 ? color(31, s) : s)
}

const cwd = d.workspace?.current_dir || process.cwd()
const git = (...args) => {
  const r = spawnSync('git', args, { cwd, encoding: 'utf8', timeout: 2000 })
  return r.status === 0 ? r.stdout.trim() : null
}
const branch = git('rev-parse', '--abbrev-ref', 'HEAD')
if (branch) {
  const dirty = git('status', '--porcelain') ? '*' : ''
  const ahead = Number(git('rev-list', '--count', '@{u}..HEAD') || 0)
  const noRemote = !git('remote')
  let g = `${branch}${dirty}`
  if (noRemote) g += color(33, ' no-remote')
  else if (ahead > 0) g += color(ahead >= 2 ? 33 : 0, ` ↑${ahead}`)
  parts.push(g)
}

process.stdout.write(parts.join('  |  '))
