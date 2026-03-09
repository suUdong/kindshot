param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

function Resolve-Python {
    if (Test-Path ".venv\Scripts\python.exe") {
        return (Resolve-Path ".venv\Scripts\python.exe").Path
    }

    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw "Python executable not found."
}

function Run-Or-Throw([string]$Command, [string[]]$Arguments) {
    $joined = ($Arguments -join " ")
    Write-Host ">> $Command $joined"
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command $joined"
    }
}

$python = Resolve-Python

Run-Or-Throw $python @("-m", "compileall", "src", "tests")

if (-not $SkipTests) {
    & $python "-c" "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest') else 1)" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "pytest is not installed in the active Python environment."
    }
    Run-Or-Throw $python @("-m", "pytest", "-q")
}

Write-Host "Validation completed successfully."
