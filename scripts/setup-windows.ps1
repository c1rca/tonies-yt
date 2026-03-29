#requires -Version 5.1
# setup-windows.ps1 v1.2.0
$ScriptVersion = "v1.2.0"
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:TERM = "dumb"
$env:NO_COLOR = "1"

function Step($m){ Write-Host "`n==> $m" -ForegroundColor Yellow }
function Ok($m){ Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [WARN] $m" -ForegroundColor DarkYellow }

# ---- Config ----
$RepoUrl = "https://github.com/c1rca/tonies-yt.git"
$RepoDir = Join-Path $env:USERPROFILE "tonies-yt"
$Branch = "main"

$DenoBin = "C:\Users\$env:USERNAME\.deno\bin"
$PyScriptsLocal = "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\Scripts"
$PyScriptsRoam  = "C:\Users\$env:USERNAME\AppData\Roaming\Python\Python312\Scripts"

Write-Host "=== Tonies-YT Windows Setup ($ScriptVersion) ===" -ForegroundColor Cyan
Write-Host "This script will:" -ForegroundColor Yellow
Write-Host "  1) Ensure Git, FFmpeg, and Deno are installed (via winget if missing)" -ForegroundColor White
Write-Host "  2) Detect a usable Python 3.12 interpreter automatically" -ForegroundColor White
Write-Host "  3) Clone/update repo at: $RepoDir" -ForegroundColor White
Write-Host "  4) Create/recreate .venv (this deletes existing .venv)" -ForegroundColor White
Write-Host "  5) Install Python deps, yt-dlp, Playwright Chromium" -ForegroundColor White
Write-Host "  6) Print verification summary and run command" -ForegroundColor White
Write-Host "" 
$confirm = Read-Host "Continue? (Y/N)"
if ($confirm -notin @('Y','y','Yes','YES')) {
    Write-Host "Setup cancelled by user." -ForegroundColor DarkYellow
    exit 0
}

function AddUserPathIfMissing([string]$PathToAdd) {
    if (-not (Test-Path $PathToAdd)) { return }
    $userPath = [Environment]::GetEnvironmentVariable("Path","User")
    if ([string]::IsNullOrWhiteSpace($userPath)) { $userPath = "" }

    $parts = $userPath.Split(";") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
    if ($parts -contains $PathToAdd) {
        Ok "PATH already has: $PathToAdd"
        return
    }

    [Environment]::SetEnvironmentVariable("Path", (($parts + $PathToAdd) -join ";"), "User")
    Ok "Added to User PATH: $PathToAdd"
}

function Ensure-Command([string]$Name,[string]$WingetId) {
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Ok "$Name available"
        return
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$Name missing and winget not available."
    }
    Step "Installing $Name"
    winget install --id $WingetId -e --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity --silent *> $null
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Warn "$Name may require a new shell PATH refresh."
    } else {
        Ok "$Name installed"
    }
}

function Missing-SystemTools {
    $missing = New-Object System.Collections.Generic.List[string]
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $missing.Add("Git") }
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { $missing.Add("FFmpeg") }
    if (-not (Get-Command deno -ErrorAction SilentlyContinue)) { $missing.Add("Deno") }
    return ,$missing
}

Step "Validating Python 3.12"
$PyCmd = $null
$candidates = New-Object System.Collections.Generic.List[string]

# 1) py launcher candidates (if available)
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        $pyList = (& py -0p 2>$null)
        foreach ($line in $pyList) {
            if ($line -match '3\.12') {
                $m = [regex]::Match($line, '([A-Za-z]:\\[^\r\n]+python\.exe)')
                if ($m.Success) { $candidates.Add($m.Groups[1].Value) }
            }
        }
    } catch {}
}

# 2) where python
try {
    $wherePy = (& where python 2>$null)
    foreach ($w in $wherePy) {
        if ($w) { $candidates.Add($w.Trim()) }
    }
} catch {}

# 3) Filesystem discovery across common roots
$searchRoots = @(
    "$env:LOCALAPPDATA\Programs\Python",
    "$env:LOCALAPPDATA\Python",
    "C:\Program Files",
    "C:\Program Files (x86)"
)
$pyFound = Get-ChildItem -Path $searchRoots -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "Python312|pythoncore-3\.12" } |
    Select-Object -ExpandProperty FullName
foreach ($p in $pyFound) { if ($p) { $candidates.Add($p) } }

# de-dup preserving order
$seen = @{}
$ordered = @()
foreach ($c in $candidates) {
    if (-not $seen.ContainsKey($c)) { $seen[$c] = $true; $ordered += $c }
}

foreach ($c in $ordered) {
    if (-not (Test-Path $c)) { continue }
    try {
        $ver = (& $c -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null).Trim()
        if ($ver -eq '3.12') { $PyCmd = $c; break }
    } catch {}
}

if (-not $PyCmd) {
    Warn "Python 3.12 not detected yet; script will attempt install."
} else {
    & $PyCmd -c "import sys; print(sys.version)"
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12 exists but failed to run."
    }
    Ok "Python 3.12 detected: $PyCmd"
}

Step "Ensuring system tools"
$missingTools = Missing-SystemTools
if ($missingTools.Count -gt 0) {
    $toolList = ($missingTools -join ", ")
    $toolsConfirm = Read-Host "Missing required system tools: $toolList. Install now? (Y/N)"
    if ($toolsConfirm -notin @('Y','y','Yes','YES')) {
        throw "Required tools missing ($toolList). Setup cancelled by user."
    }
}
Ensure-Command "git" "Git.Git"
Ensure-Command "ffmpeg" "Gyan.FFmpeg"
Ensure-Command "deno" "DenoLand.Deno"

Step "Installing Python 3.12 if missing"
if (-not $PyCmd) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.12 missing and winget not available to install it."
    }

    $pyInstallConfirm = Read-Host "Python 3.12 was not detected. Install Python 3.12 now? (Y/N)"
    if ($pyInstallConfirm -notin @('Y','y','Yes','YES')) {
        throw "Python 3.12 is required. Setup cancelled by user."
    }

    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity --silent *> $null

    # re-run Python 3.12 discovery after install
    $candidates = New-Object System.Collections.Generic.List[string]
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $pyList = (& py -0p 2>$null)
            foreach ($line in $pyList) {
                if ($line -match '3\.12') {
                    $m = [regex]::Match($line, '([A-Za-z]:\\[^\r\n]+python\.exe)')
                    if ($m.Success) { $candidates.Add($m.Groups[1].Value) }
                }
            }
        } catch {}
    }
    try {
        $wherePy = (& where python 2>$null)
        foreach ($w in $wherePy) { if ($w) { $candidates.Add($w.Trim()) } }
    } catch {}
    $searchRoots = @("$env:LOCALAPPDATA\Programs\Python", "$env:LOCALAPPDATA\Python", "C:\Program Files", "C:\Program Files (x86)")
    $pyFound = Get-ChildItem -Path $searchRoots -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "Python312|pythoncore-3\.12" } |
        Select-Object -ExpandProperty FullName
    foreach ($p in $pyFound) { if ($p) { $candidates.Add($p) } }
    $seen = @{}
    $ordered = @()
    foreach ($c in $candidates) { if (-not $seen.ContainsKey($c)) { $seen[$c] = $true; $ordered += $c } }
    foreach ($c in $ordered) {
        if (-not (Test-Path $c)) { continue }
        try {
            $ver = (& $c -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null).Trim()
            if ($ver -eq '3.12') { $PyCmd = $c; break }
        } catch {}
    }
    if (-not $PyCmd) {
        throw "Python 3.12 install command ran, but Python 3.12 still not found."
    }
    Ok "Python 3.12 installed and detected: $PyCmd"
}

Step "Ensuring PATH entries"
AddUserPathIfMissing $DenoBin
AddUserPathIfMissing $PyScriptsLocal
AddUserPathIfMissing $PyScriptsRoam
$DetectedPyScripts = Join-Path (Split-Path -Parent $PyCmd) "Scripts"
AddUserPathIfMissing $DetectedPyScripts

# refresh shell PATH
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

Step "Clone or update repo"
if (Test-Path (Join-Path $RepoDir ".git")) {
    Push-Location $RepoDir
    git remote set-url origin $RepoUrl | Out-Null
    git fetch origin
    git checkout $Branch
    git reset --hard "origin/$Branch"
    Ok "Updated existing repo"
} else {
    git clone $RepoUrl $RepoDir
    Push-Location $RepoDir
    git checkout $Branch
    Ok "Cloned repo"
}

Step "Prepare .env"
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env" -Force
    Ok "Created .env from .env.example"
} else {
    Ok ".env already present (or .env.example missing)"
}

Step "Recreate .venv with Python 3.12"
if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
& $PyCmd -m venv .venv
$VenvPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { throw "Failed creating venv" }
Ok "Venv created"

Step "Install Python dependencies"
& $VenvPy -m pip install --upgrade pip setuptools wheel
# Install cryptography explicitly first to avoid occasional Windows resolver/runtime mismatches.
& $VenvPy -m pip install --upgrade cryptography
& $VenvPy -m pip install -r requirements.txt
& $VenvPy -m pip install --upgrade yt-dlp
& $VenvPy -m playwright install chromium
Ok "Python dependencies + Playwright installed"

Step "Verification"
$summary = [ordered]@{}
$summary["Repo"]      = (Get-Location).Path
$summary["Git HEAD"]  = (git rev-parse --short HEAD)
$summary["Python"]    = (& $VenvPy -V)
$summary["pip"]       = (& $VenvPy -m pip --version)
$summary["yt-dlp"]    = (& $VenvPy -m yt_dlp --version)
$summary["cryptography"] = (& $VenvPy -c "import cryptography; print(cryptography.__version__)" )
$summary["ffmpeg"]    = ((ffmpeg -version 2>$null | Select-Object -First 1) -join "")
$summary["deno"]      = ((deno --version 2>$null | Select-Object -First 1) -join "")
$downloaderText = Get-Content .\app\downloader.py -Raw
$summary["yt_dlp patch"] = ($(if ($downloaderText.Contains('"-m", "yt_dlp"')) { "OK (python -m yt_dlp)" } else { "MISSING (old yt-dlp PATH mode)" }))

Write-Host "`n=== Setup Summary ===" -ForegroundColor Cyan
$summary.GetEnumerator() | ForEach-Object {
    Write-Host ("{0,-12}: {1}" -f $_.Key, $_.Value)
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Next step: start the Tonies-YT web server." -ForegroundColor Yellow
Write-Host "This command runs Uvicorn for app.main:app on port 8090:" -ForegroundColor White
Write-Host "  cd $RepoDir" -ForegroundColor Cyan
Write-Host "  .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8090" -ForegroundColor Cyan
Write-Host "Then open: http://localhost:8090" -ForegroundColor White

Pop-Location
