[CmdletBinding()]
param(
    [ValidateSet('Init','Resolve','Build','Start','Status','Stop','Reload','Verify','Backup','Restore','Logs')]
    [string]$Action = 'Status',
    [ValidateSet('Dev','Image')]
    [string]$Mode = 'Dev',
    [string]$BackupBundle,
    [string]$RestoreSite,
    [string]$ExpectedMarker = 'metalmecanica-dev-001'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$workspacePath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $workspacePath

function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker failed with exit code $LASTEXITCODE" }
}

function Test-DockerImage {
    param([Parameter(Mandatory=$true)][string]$Name)
    & docker image inspect $Name *> $null
    return $LASTEXITCODE -eq 0
}

function Ensure-StableImage {
    if (-not (Test-DockerImage 'metalmecanica-erp:dev')) {
        Write-Output 'Image metalmecanica-erp:dev not found; building it before Start.'
        & $PSCommandPath -Action Build -Mode Image
        if ($LASTEXITCODE -ne 0) { throw 'Could not build image metalmecanica-erp:dev.' }
    }
}

function Resolve-GitExecutable {
    $command = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $candidates = @(
        (Join-Path ${env:ProgramFiles} 'Git\cmd\git.exe'),
        (Join-Path ${env:ProgramFiles} 'Git\bin\git.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Git\cmd\git.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Git\bin\git.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw 'Git no está disponible en PowerShell. Agrega Git\cmd al PATH o instala Git for Windows.'
}

function Initialize-LocalConfig {
    $encoding = [Text.UTF8Encoding]::new($false)
    foreach ($directory in @('deployment/.secrets','deployment/backups','deployment/reports')) {
        [IO.Directory]::CreateDirectory((Join-Path $workspacePath $directory)) | Out-Null
    }
    foreach ($name in @('db_root_password','admin_password')) {
        $path = Join-Path $workspacePath ("deployment/.secrets/" + $name)
        if (-not (Test-Path -LiteralPath $path)) {
            $bytes = [byte[]]::new(32)
            $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
            try {
                $generator.GetBytes($bytes)
            }
            finally {
                $generator.Dispose()
            }
            $secret = [BitConverter]::ToString($bytes).Replace('-', '')
            [IO.File]::WriteAllText($path,$secret,$encoding)
        }
    }
    $envPath = Join-Path $workspacePath 'deployment/.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        $images = Get-Content deployment/locks/images.json -Raw | ConvertFrom-Json
        $config = "MARIADB_IMAGE=$($images.images.mariadb)`nREDIS_IMAGE=$($images.images.redis)`nSITE_NAME=metalmecanica.localhost`nCUSTOM_IMAGE=metalmecanica-erp:dev`n"
        [IO.File]::WriteAllText($envPath,$config,$encoding)
    }
    $envText = [IO.File]::ReadAllText($envPath)
    $independentLines = @()
    if ($envText -notmatch '(?m)^INDEPENDENT_ADMIN_USERNAME=') { $independentLines += 'INDEPENDENT_ADMIN_USERNAME=admin' }
    if ($envText -notmatch '(?m)^INDEPENDENT_ADMIN_EMAIL=') { $independentLines += 'INDEPENDENT_ADMIN_EMAIL=admin@example.invalid' }
    if ($envText -notmatch '(?m)^INDEPENDENT_ADMIN_PASSWORD=') {
        $bytes = [byte[]]::new(24)
        $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
        $independentLines += "INDEPENDENT_ADMIN_PASSWORD=$([BitConverter]::ToString($bytes).Replace('-', ''))"
    }
    if ($independentLines.Count -gt 0) {
        [IO.File]::AppendAllText($envPath, "`n# Independent API administrator; keep this file local.`n$($independentLines -join "`n")`n", $encoding)
    }
}

function Confirm-Sources {
    $git = Resolve-GitExecutable
    $manifest = Get-Content docs/architecture/sources.lock.json -Raw | ConvertFrom-Json
    foreach ($source in $manifest.sources) {
        $actual = & $git -C $source.path rev-parse HEAD
        if ($LASTEXITCODE -ne 0 -or $actual -ne $source.commit) { throw "Source commit differs: $($source.name)" }
        $changes = @(& $git -C $source.path status --porcelain)
        if ($LASTEXITCODE -ne 0 -or $changes.Count -gt 0) { throw "Upstream has local changes: $($source.name)" }
    }
}

if ($Action -eq 'Init') { Initialize-LocalConfig; Write-Output 'Local config ready; secrets were not printed.'; return }
$composeArgs = @('compose','--env-file','deployment/.env','-f','deployment/docker-compose.yml')
if ($Mode -eq 'Dev') { $composeArgs += @('-f','deployment/config/compose.dev.yml') }

switch ($Action) {
    'Resolve' {
        Confirm-Sources
        Invoke-Docker build --platform linux/amd64 --target locks --output type=local,dest=deployment/locks -f deployment/Dockerfile .
    }
    'Build' {
        Confirm-Sources
        foreach ($lock in @('deployment/locks/runtime.txt','deployment/locks/tools.txt')) {
            if (-not (Test-Path -LiteralPath $lock)) { throw 'Run -Action Resolve first.' }
        }
        Invoke-Docker build --platform linux/amd64 -f deployment/Dockerfile -t metalmecanica-erp:dev .
    }
    'Start' {
        Initialize-LocalConfig
        Ensure-StableImage
        Invoke-Docker @composeArgs config --quiet
        Invoke-Docker @composeArgs up -d db redis-cache redis-queue
        Invoke-Docker @composeArgs run --rm bootstrap
        Invoke-Docker @composeArgs up -d
    }
    'Status' { Invoke-Docker @composeArgs ps -a }
    'Stop' { Invoke-Docker @composeArgs stop }
    'Reload' { Invoke-Docker @composeArgs restart backend queue-short queue-long scheduler websocket }
    'Verify' {
        $resolvedText = & docker @composeArgs config --format json
        if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve Compose config' }
        $configuredSite = (($resolvedText -join [Environment]::NewLine) | ConvertFrom-Json).services.backend.environment.SITE_NAME
        Invoke-Docker @composeArgs run --rm --no-deps tools
        Invoke-Docker @composeArgs run --rm --no-deps tools env/bin/python /opt/metalmecanica/tests/phase1_http.py --base-url http://frontend:8080 --site $configuredSite --password-file /run/secrets/admin_password --expected-marker $ExpectedMarker
    }
    'Backup' { Invoke-Docker @composeArgs run --rm backup }
    'Restore' {
        if (-not $BackupBundle -or -not $RestoreSite) { throw 'Restore requires -BackupBundle and -RestoreSite; destination must be new.' }
        Invoke-Docker @composeArgs run --rm -e "BACKUP_BUNDLE=$BackupBundle" -e "RESTORE_SITE_NAME=$RestoreSite" restore
    }
    'Logs' { Invoke-Docker @composeArgs logs --tail 80 }
}
