// PostToolUse hook: after Claude edits a file, lint JS/JSX or syntax-check
// Python immediately. Exit 2 feeds the error back to Claude; anything else
// (unrelated file, missing tooling, hook bug) exits 0 so it never blocks work.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')

// shell only for npx (a .cmd shim on Windows); a shell would split paths with spaces
function run(cmd, args, cwd, { shell = false } = {}) {
  const r = spawnSync(cmd, args, { cwd, encoding: 'utf8', shell, timeout: 20000 })
  return { status: r.status, output: `${r.stdout || ''}${r.stderr || ''}`.trim() }
}

try {
  const input = JSON.parse(readFileSync(0, 'utf8') || '{}')
  const filePath = input.tool_input?.file_path || input.tool_response?.filePath
  if (!filePath) process.exit(0)

  // Git Bash style /c/Users/... -> C:/Users/... so Windows path math works
  const native = process.platform === 'win32' ? filePath.replace(/^\/([a-zA-Z])\//, '$1:/') : filePath
  const abs = resolve(native)
  const rel = relative(ROOT, abs).replaceAll('\\', '/')

  if (/^frontend\/src\/.*\.(jsx?|mjs)$/.test(rel)) {
    const frontend = join(ROOT, 'frontend')
    if (!existsSync(join(frontend, 'node_modules'))) process.exit(0)
    const target = relative(frontend, abs).replaceAll('\\', '/')
    const win = process.platform === 'win32'
    const r = run('npx', ['--no-install', 'oxlint', win ? `"${target}"` : target], frontend, { shell: win })
    if (r.status === 1) {
      console.error(`oxlint found errors in ${rel}:\n${r.output}`)
      process.exit(2)
    }
  } else if (/^backend\/.*\.py$/.test(rel) && !rel.includes('/venv/')) {
    const venvWin = join(ROOT, 'backend', 'venv', 'Scripts', 'python.exe')
    const venvUnix = join(ROOT, 'backend', 'venv', 'bin', 'python')
    const py = existsSync(venvWin) ? venvWin : existsSync(venvUnix) ? venvUnix : 'python'
    const r = run(py, ['-m', 'py_compile', abs], ROOT)
    if (r.status === 1) {
      console.error(`Python syntax error in ${rel}:\n${r.output}`)
      process.exit(2)
    }
  }
} catch {
  // never let a hook bug block editing
}
process.exit(0)
