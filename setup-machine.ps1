# One-time setup for a machine that has nothing installed yet (a fresh laptop, a
# backup machine). The main laptop already has all of this.
# Fresh Windows blocks .ps1 scripts by default, so run it like this from a normal
# PowerShell window (no Administrator needed):
#   powershell -ExecutionPolicy Bypass -File .\setup-machine.ps1

Write-Host "=== Installing Git ===" -ForegroundColor Cyan
winget install --id Git.Git -e --source winget

Write-Host "=== Installing GitHub CLI ===" -ForegroundColor Cyan
winget install --id GitHub.cli -e --source winget

Write-Host "=== Installing Node.js LTS ===" -ForegroundColor Cyan
winget install --id OpenJS.NodeJS.LTS -e --source winget

Write-Host "=== Installing Python ===" -ForegroundColor Cyan
winget install --id Python.Python.3.13 -e --source winget

Write-Host ""
Write-Host "winget installs done. Close this window and open a NEW PowerShell so PATH updates take effect." -ForegroundColor Yellow
Write-Host ""
Write-Host "Then, in the new window, run:" -ForegroundColor Yellow
Write-Host "  irm https://claude.ai/install.ps1 | iex        # installs Claude Code"
Write-Host "  git --version; gh --version; node --version; python --version; claude --version"
Write-Host "  gh auth login   # sign in to GitHub in the browser"
Write-Host "  python -m pip install playwright; playwright install chromium   # browser check in check.ps1 + Claude's webapp-testing skill (~300MB)"
Write-Host "  claude          # first run: pick your Claude subscription login and sign in in the browser"
Write-Host "  (the repo's .claude/settings.json installs the frontend-design and example-skills plugins when you first open it)"
Write-Host ""
Write-Host "Optional: let this repo's scripts (.\dev.ps1, .\check.ps1) run directly instead of needing" -ForegroundColor Yellow
Write-Host "'powershell -ExecutionPolicy Bypass -File ...' every time. Your choice - it only affects your user account:" -ForegroundColor Yellow
Write-Host "  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"
