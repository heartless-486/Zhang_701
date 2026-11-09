param(
    [string]$AdsHome = "",
    [string]$ProjectRoot = "",
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $AdsHome) { $AdsHome = $env:ADS_HOME }
if (-not $AdsHome) { $AdsHome = $env:HPEESOF_DIR }
if (-not $AdsHome) { throw "Pass -AdsHome with the licensed ADS installation directory." }
if (-not (Test-Path -LiteralPath (Join-Path $AdsHome 'bin\ads-mcp.exe'))) {
    throw "Official ADS MCP is missing from the selected installation."
}
if (-not $ProjectRoot) { $ProjectRoot = Join-Path $repo 'projects' }
$projectPath = [IO.Path]::GetFullPath($ProjectRoot)
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv (Join-Path $repo '.venv')
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
Push-Location $repo
try {
    & $venvPython -m pip install '.[test]'
    if ($LASTEXITCODE -ne 0) { throw "Package installation failed" }
    & $venvPython scripts/configure.py --ads-home $AdsHome --project-root $projectPath --keep-existing
    if ($LASTEXITCODE -ne 0) { throw "Configuration failed" }
    & $venvPython -m ads_mcp --config ads-mcp.toml doctor
    if ($LASTEXITCODE -ne 0) { throw "Environment check failed" }
    & $venvPython scripts/check_mcp.py --config ads-mcp.toml
    if ($LASTEXITCODE -ne 0) { throw "MCP protocol check failed" }
    & $venvPython -m ads_mcp --config ads-mcp.toml capabilities
    if ($LASTEXITCODE -ne 0) { throw "Official ADS MCP discovery failed" }
    Write-Host "Ready. Merge .ads-mcp/client-config.json into your AI client configuration."
    Write-Host "No native workspace, circuit simulation or EM solve was launched."
} finally {
    Pop-Location
}
