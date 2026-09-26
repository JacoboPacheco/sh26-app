// Stop hook: when Claude finishes a turn, nudge if work is piling up unpushed.
// Pushes are the tamper-proof timestamps that show the project was built during
// the event, and the backup if the machine dies. Never blocks; silent unless
// there are >= THRESHOLD unpushed commits.
import { spawnSync } from 'node:child_process'

// low on purpose: if the build machine dies, unpushed commits are the only thing lost
const THRESHOLD = 2

function git(...args) {
  const r = spawnSync('git', args, { encoding: 'utf8', timeout: 5000 })
  return r.status === 0 ? r.stdout.trim() : null
}

try {
  const upstream = git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
  let message = null

  if (upstream) {
    const ahead = Number(git('rev-list', '--count', '@{u}..HEAD') || 0)
    if (ahead >= THRESHOLD) {
      message = `${ahead} commits not pushed yet — run: git push`
    }
  } else if (git('rev-parse', '--verify', 'HEAD') && !git('remote')) {
    const total = Number(git('rev-list', '--count', 'HEAD') || 0)
    if (total >= THRESHOLD) message = `This repo has no GitHub remote — nothing is backed up. Create one: gh repo create`
  }

  if (message) process.stdout.write(JSON.stringify({ systemMessage: message }))
} catch {
  // a hook bug must never interfere with the session
}
process.exit(0)
