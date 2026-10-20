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
function Invoke-Docker { param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args) & docker @Args; if ($LASTEXITCODE -ne 0) { throw "Docker failed with exit code $LASTEXITCODE" } }
function Ensure-Config {
    if (-not (Test-Path $envPath)) { throw 'Run .\deployment\erp.ps1 -Action Init first to create deployment/.env.' }
    if (-not (Test-Path (Join-Path $secretsPath 'db_root_password')) -or -not (Test-Path (Join-Path $secretsPath 'admin_password'))) { throw 'Run the stable Init first to create local secrets.' }
}
switch ($Action) {
    'Init' { & .\deployment\erp.ps1 -Action Init; return }
    'Build' { Ensure-Config; Invoke-Docker build --platform linux/amd64 -f deployment/Dockerfile.independent -t metalmecanica-erp:independent . }
    'Start' { Ensure-Config; Invoke-Docker compose @compose up -d db redis-cache redis-queue; Invoke-Docker compose @compose run --rm bootstrap; Invoke-Docker compose @compose up -d }
    'Status' { Ensure-Config; Invoke-Docker compose @compose ps -a }
    'Stop' { Ensure-Config; Invoke-Docker compose @compose stop }
    'Verify' { Ensure-Config; Invoke-Docker compose @compose run --rm --no-deps tools; Invoke-Docker compose @compose exec -T frontend env/bin/python /opt/metalmecanica/tests/phase1_http.py --base-url http://127.0.0.1:8080 --site metalmecanica-ind.localhost --password-file /run/secrets/admin_password --expected-marker metalmecanica-independent-001 --build-info-path /api/method/metalmecanica_ind.api.get_build_info --expected-app metalmecanica_ind }
}
