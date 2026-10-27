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
$compose = @('--env-file','deployment/.env','-f','deployment/config/compose.independent.yml')
function Invoke-Docker { & docker @args; if ($LASTEXITCODE -ne 0) { throw "Docker failed with exit code $LASTEXITCODE" } }
function Ensure-Config {
    if (-not (Test-Path $envPath)) { throw 'Run .\deployment\erp-independent.ps1 -Action Init first to create deployment/.env.' }
    if (-not (Test-Path (Join-Path $secretsPath 'db_root_password'))) { throw 'Run .\deployment\erp-independent.ps1 -Action Init first to create local secrets.' }
}
function Initialize-IndependentConfig {
    $encoding = [Text.UTF8Encoding]::new($false)
    [IO.Directory]::CreateDirectory($secretsPath) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $workspacePath 'deployment/backups')) | Out-Null
    $dbSecretPath = Join-Path $secretsPath 'db_root_password'
    if (-not (Test-Path -LiteralPath $dbSecretPath)) {
        $bytes = [byte[]]::new(32)
        $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
        [IO.File]::WriteAllText($dbSecretPath, [BitConverter]::ToString($bytes).Replace('-', ''), $encoding)
    }
    if (-not (Test-Path -LiteralPath $envPath)) {
        $bytes = [byte[]]::new(24)
        $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
        $adminPassword = [BitConverter]::ToString($bytes).Replace('-', '')
        $config = "INDEPENDENT_ADMIN_USERNAME=admin`nINDEPENDENT_ADMIN_EMAIL=admin@example.invalid`nINDEPENDENT_ADMIN_PASSWORD=$adminPassword`nINDEPENDENT_SEED_DUMMY_DATA=true`n"
        [IO.File]::WriteAllText($envPath, $config, $encoding)
    }
}
function Test-DockerImage {
    param([Parameter(Mandatory=$true)][string]$Name)
    & docker image inspect $Name *> $null
    return $LASTEXITCODE -eq 0
}
switch ($Action) {
    'Init' { Initialize-IndependentConfig; Write-Output 'Independent local config ready; secrets were not printed.'; return }
    'Build' { Ensure-Config; Invoke-Docker build --platform linux/amd64 -f deployment/Dockerfile.independent -t metalmecanica-erp:independent . }
    'Start' { Ensure-Config; if (-not (Test-DockerImage 'metalmecanica-erp:independent')) { & $PSCommandPath -Action Build }; Invoke-Docker compose @compose config --quiet; Invoke-Docker compose @compose up -d --force-recreate }
    'Status' { Ensure-Config; Invoke-Docker compose @compose ps -a }
    'Stop' { Ensure-Config; Invoke-Docker compose @compose stop }
    'Verify' { Ensure-Config; Invoke-Docker compose @compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())" }
}
