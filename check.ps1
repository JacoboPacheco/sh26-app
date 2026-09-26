# Runs scripts/check.sh through Git Bash, from a normal PowerShell window.
# (`bash` usually isn't on PowerShell's PATH, and if WSL is installed plain `bash` would be the wrong one.)
# Usage, from the repo root:  .\check.ps1

$candidates = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
)
$bash = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $bash) {
    Write-Host "Git Bash not found. Install Git (setup-machine.ps1 does this), then rerun." -ForegroundColor Red
    exit 1
}

& $bash "$PSScriptRoot/scripts/check.sh"
exit $LASTEXITCODE
