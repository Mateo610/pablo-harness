# Join Pablo from Windows. `pablo invite` prints the exact command:
#   & ([scriptblock]::Create((irm .../scripts/join.ps1))) '<tailscale share link>' '<pablo join link>'
# Installs Tailscale if it's missing, signs you in, has you accept the share, waits until
# Pablo answers, then opens your invite link in the browser.
param(
  [Parameter(Mandatory = $true, Position = 0)][string]$Share,
  [Parameter(Mandatory = $true, Position = 1)][string]$Join
)

function Find-Tailscale {
  $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $exe = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
  if (Test-Path $exe) { return $exe }
  return $null
}

Write-Host "Joining Pablo"

$ts = Find-Tailscale
if (-not $ts) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "- Installing Tailscale"
    winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
  } else {
    Write-Host "- Install Tailscale from the page that just opened. This keeps going once it's installed."
    Start-Process "https://tailscale.com/download/windows"
  }
  while (-not ($ts = Find-Tailscale)) { Start-Sleep -Seconds 3 }
}

& $ts status *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "- Signing in to Tailscale (finish in the browser)"
  & $ts up
}

Write-Host "- Accept the Pablo share in the browser tab that opens"
Start-Process $Share

$hostUrl = ([Uri]$Join).GetLeftPart([UriPartial]::Authority)
Write-Host -NoNewline "- Waiting for Pablo"
$found = $false
for ($i = 0; $i -lt 100; $i++) {
  try {
    Invoke-WebRequest "$hostUrl/api/health" -UseBasicParsing -TimeoutSec 4 | Out-Null
    $found = $true
    break
  } catch {
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 3
  }
}
if (-not $found) {
  Write-Host ""
  Write-Host "Couldn't reach Pablo at $hostUrl after 5 minutes."
  Write-Host "Check that you accepted the share and that Tailscale is connected, then run this again."
  return
}
Write-Host " found it"

Start-Process $Join
Write-Host "You're in. Bookmark $hostUrl"
