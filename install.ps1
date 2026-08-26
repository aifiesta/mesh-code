# Mesh Code installer  —  Windows
#
#   powershell -ExecutionPolicy ByPass -c "irm https://code.meshapi.ai/install.ps1 | iex"
#
# Downloads the current release and runs the installer. Nothing needs to be
# preinstalled. Re-run any time to upgrade.
$ErrorActionPreference = 'Stop'

$Repo = 'aifiesta/mesh-code'
$Base = if ($env:MESH_CODE_BASE_URL) { $env:MESH_CODE_BASE_URL }
        else { "https://github.com/$Repo/releases/latest/download" }

function Info($m) { Write-Host $m -ForegroundColor Cyan }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Die($m)  { Write-Host "error: $m" -ForegroundColor Red; exit 1 }

$arch = if ([Environment]::Is64BitOperatingSystem) { 'x64' } else { Die 'Mesh Code requires 64-bit Windows.' }
$name = "Mesh-Code-Setup-$arch.exe"
$tmp  = Join-Path $env:TEMP ("mesh-code-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
$exe = Join-Path $tmp $name

try {
  Info "Downloading $name…"
  try { Invoke-WebRequest -Uri "$Base/$name" -OutFile $exe -UseBasicParsing }
  catch { Die "download failed — check your network, or grab it from https://github.com/$Repo/releases/latest" }

  # Verify against the published checksums when they exist. A missing
  # SHA256SUMS warns loudly rather than passing silently.
  try {
    $sums = Invoke-WebRequest -Uri "$Base/SHA256SUMS" -UseBasicParsing
    $line = ($sums.Content -split "`n" | Where-Object { $_ -match [regex]::Escape($name) }) | Select-Object -First 1
    if ($line) {
      $want = ($line -split '\s+')[0]
      $got  = (Get-FileHash -Algorithm SHA256 -Path $exe).Hash.ToLower()
      if ($want.ToLower() -ne $got) { Die "checksum mismatch for $name — refusing to install." }
      Info 'Checksum verified.'
    } else { Warn "No checksum listed for $name — skipping verification." }
  } catch { Warn 'No SHA256SUMS published for this release — skipping verification.' }

  Info 'Running the installer…'
  $p = Start-Process -FilePath $exe -ArgumentList '/S' -PassThru -Wait
  if ($p.ExitCode -ne 0) { Die "the installer exited with code $($p.ExitCode)." }
  Info 'Installed.'
  Info 'Launch Mesh Code and paste your Mesh API key — get one at https://app.meshapi.ai'
}
finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
