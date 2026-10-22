[CmdletBinding()]
param(
    [ValidateSet('Init','Build','Start','Status','Stop','Verify')]
    [string]$Action = 'Status'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$workspacePath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $workspacePath
$envPath = Join-Path $workspacePath 'deployment/.env'
$secretsPath = Join-Path $workspacePath 'deployment/.secrets'
$compose = @('-p','metalmecanica-independent-compare','--env-file','deployment/.env','-f','deployment/docker-compose.yml','-f','deployment/config/compose.independent.yml')
function Invoke-Docker { & docker @args; if ($LASTEXITCODE -ne 0) { throw "Docker failed with exit code $LASTEXITCODE" } }
function Ensure-Config {
    if (-not (Test-Path $envPath)) { throw 'Run .\deployment\erp.ps1 -Action Init first to create deployment/.env.' }
    if (-not (Test-Path (Join-Path $secretsPath 'db_root_password')) -or -not (Test-Path (Join-Path $secretsPath 'admin_password'))) { throw 'Run the stable Init first to create local secrets.' }
}
function Test-DockerImage {
    param([Parameter(Mandatory=$true)][string]$Name)
    & docker image inspect $Name *> $null
    return $LASTEXITCODE -eq 0
}
function Ensure-BaseImage {
    if (-not (Test-DockerImage 'metalmecanica-erp:dev')) {
        Write-Output 'Stable image metalmecanica-erp:dev not found; building it first.'
        & .\deployment\erp.ps1 -Action Build -Mode Image
        if ($LASTEXITCODE -ne 0) { throw 'Could not build the stable base image.' }
    }
}
switch ($Action) {
    'Init' { & .\deployment\erp.ps1 -Action Init; return }
    'Build' { Ensure-Config; Ensure-BaseImage; Invoke-Docker build --platform linux/amd64 -f deployment/Dockerfile.independent -t metalmecanica-erp:independent . }
    'Start' { Ensure-Config; if (-not (Test-DockerImage 'metalmecanica-erp:independent')) { & $PSCommandPath -Action Build }; Invoke-Docker compose @compose config --quiet; Invoke-Docker compose @compose up -d db redis-cache redis-queue; Invoke-Docker compose @compose run --rm bootstrap; Invoke-Docker compose @compose up -d --force-recreate }
    'Status' { Ensure-Config; Invoke-Docker compose @compose ps -a }
    'Stop' { Ensure-Config; Invoke-Docker compose @compose stop }
    'Verify' { Ensure-Config; Invoke-Docker compose @compose run --rm --no-deps tools; Invoke-Docker compose @compose exec -T ind-api env/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health').read().decode())" }
}
