[CmdletBinding()]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Test-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

if (Test-Command "uv") {
    Write-Host "Installing Dairack with uv tool..."
    & uv tool install --force $Root
}
elseif (Test-Command "pipx") {
    Write-Host "Installing Dairack with pipx..."
    & pipx install --force $Root
}
else {
    if (-not $Python) {
        if (Test-Command "py") {
            $Python = "py"
        }
        elseif (Test-Command "python") {
            $Python = "python"
        }
        else {
            throw "Python 3.11 or newer is required. Install Python, uv, or pipx, then rerun this script."
        }
    }

    if ($Python -eq "py") {
        & py -3 -c "import ensurepip, sys, venv; raise SystemExit(sys.version_info < (3, 11))"
    }
    else {
        & $Python -c "import ensurepip, sys, venv; raise SystemExit(sys.version_info < (3, 11))"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$Python cannot create a Python 3.11+ virtual environment with pip. Install venv support or use uv/pipx."
    }

    $DataRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
    $Venv = Join-Path $DataRoot "Dairack\Runtime"
    Write-Host "Installing Dairack into $Venv..."
    if ($Python -eq "py") {
        & py -3 -m venv $Venv
    }
    else {
        & $Python -m venv $Venv
    }
    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    & $VenvPython -m pip install --upgrade $Root

    $Scripts = Join-Path $Venv "Scripts"
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $Entries = @($UserPath -split ";" | Where-Object { $_ })
    if ($Entries -notcontains $Scripts) {
        $UpdatedPath = (@($Entries) + $Scripts) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $UpdatedPath, "User")
        Write-Host "Added $Scripts to your user PATH. New terminals will see the dairack command."
    }
    $env:Path = "$Scripts;$env:Path"
}

Write-Host ""
Write-Host "Installed. Next run:"
Write-Host "  dairack setup"
Write-Host "  dairack"
Write-Host ""
Write-Host "Optional diagnostics:"
Write-Host "  dairack doctor"
