#Requires -Version 5.1
<#
.SYNOPSIS
  一键启动本机联调栈：Docker Desktop + postgres-test + kelin-server-api。

.PARAMETER WithCron
  额外启动 scheduler / worker（串门、鉴定生成、注销落地需要）。

.PARAMETER Stop
  停止 api / scheduler / worker / postgres-test，不关 Docker Desktop。
#>
[CmdletBinding()]
param(
    [switch]$WithCron,
    [switch]$Stop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $RepoRoot "compose.yaml"
$EnvFile = Join-Path $RepoRoot ".env"
$DockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$UvImage = "ghcr.io/astral-sh/uv:0.8.22-python3.12-bookworm-slim@sha256:28df4bbd896cf66a224f2e0cb22240a9a2b9803a3a13519bcadf2e9fdd68c632"
$Network = "kelin-server_default"
$DbUrl = "postgresql+asyncpg://kelin_test:kelin_test@postgres-test:5432/kelin_test"
$ApiName = "kelin-server-api"
$SchedulerName = "kelin-server-scheduler"
$WorkerName = "kelin-server-worker"
$PostgresName = "kelin-postgres-test"

if (-not (Test-Path $ComposeFile)) {
    throw "找不到 compose.yaml：$ComposeFile"
}

function Test-DockerEngine {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return ($code -eq 0)
}

function Get-ContainerStatus([string]$Name) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $status = docker inspect --format "{{.State.Status}}" $Name 2>$null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -ne 0 -or [string]::IsNullOrWhiteSpace($status)) {
        return $null
    }
    return $status.Trim()
}

function Get-HttpCode([string]$Url) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $code = curl.exe -sS -o NUL -w "%{http_code}" $Url 2>$null
    $ErrorActionPreference = $prev
    return "$code"
}

function Wait-Until([scriptblock]$Condition, [int]$TimeoutSeconds, [string]$WaitingFor) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (& $Condition) {
            return
        }
        if ((Get-Date) -gt $deadline) {
            throw "等待超时：$WaitingFor"
        }
        Start-Sleep -Seconds 2
    } while ($true)
}

function Start-DockerDesktopIfNeeded {
    if (Test-DockerEngine) {
        Write-Host "Docker 引擎已就绪"
        return
    }
    if (-not (Test-Path $DockerDesktop)) {
        throw "未找到 Docker Desktop：$DockerDesktop"
    }
    Write-Host "正在启动 Docker Desktop..."
    Start-Process $DockerDesktop | Out-Null
    Wait-Until { Test-DockerEngine } 120 "Docker 引擎"
    Write-Host "Docker 引擎已就绪"
}

function Invoke-ComposePostgres {
    Write-Host "启动 $PostgresName ..."
    docker compose --project-directory $RepoRoot --profile tools up -d postgres-test
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose 启动 postgres-test 失败"
    }
    Wait-Until {
        $health = docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{end}}" $PostgresName 2>$null
        return ($health -eq "healthy")
    } 60 "$PostgresName healthy"
    Write-Host "$PostgresName 已 healthy"
}

function Ensure-BindMountProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Command
    )
    $status = Get-ContainerStatus $Name
    if ($status -eq "running") {
        Write-Host "$Name 已在运行"
        return
    }
    if ($null -ne $status) {
        Write-Host "启动已有容器 $Name ..."
        docker start $Name | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "docker start $Name 失败"
        }
        return
    }
    if (-not (Test-Path $EnvFile)) {
        throw "缺少 $EnvFile，请先从 .env.example 复制并填写"
    }
    Write-Host "创建容器 $Name ..."
    $dockerArgs = @(
        "run", "-d",
        "--name", $Name,
        "--restart", "unless-stopped",
        "--init",
        "--network", $Network,
        "-w", "/app",
        "-v", "${RepoRoot}:/app",
        "--env-file", $EnvFile,
        "-e", "DATABASE_URL_API=$DbUrl"
    )
    if ($Name -eq $ApiName) {
        $dockerArgs += @("-p", "8000:8000")
    }
    $dockerArgs += @($UvImage) + $Command
    & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker run $Name 失败"
    }
}

function Ensure-Ffmpeg {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker exec $ApiName sh -c "command -v ffmpeg >/dev/null 2>&1"
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -eq 0) {
        return
    }
    Write-Host "API 容器缺少 ffmpeg，正在安装..."
    docker exec $ApiName sh -c 'apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*'
    if ($LASTEXITCODE -ne 0) {
        throw "安装 ffmpeg 失败"
    }
}

function Wait-ApiReady {
    Write-Host "等待 API 健康检查..."
    Wait-Until {
        return ((Get-HttpCode "http://127.0.0.1:8000/health/live") -eq "200")
    } 90 "GET /health/live"
    $readyBody = curl.exe -sS http://127.0.0.1:8000/health/ready
    $readyCode = Get-HttpCode "http://127.0.0.1:8000/health/ready"
    if ($readyCode -ne "200") {
        throw "GET /health/ready 返回 $readyCode $readyBody"
    }
    Write-Host "GET /health/live  200"
    Write-Host "GET /health/ready 200 $readyBody"
}

function Stop-LocalStack {
    Start-DockerDesktopIfNeeded
    foreach ($name in @($ApiName, $SchedulerName, $WorkerName, $PostgresName)) {
        $status = Get-ContainerStatus $name
        if ($null -eq $status) {
            continue
        }
        if ($status -eq "running") {
            Write-Host "停止 $name ..."
            docker stop $name | Out-Null
        }
    }
    Write-Host "本机联调进程已停止（Docker Desktop 仍在运行）"
}

if ($Stop) {
    Stop-LocalStack
    exit 0
}

Start-DockerDesktopIfNeeded
Invoke-ComposePostgres
Ensure-BindMountProcess -Name $ApiName -Command @(
    "uv", "run", "--frozen",
    "uvicorn", "app.main:app",
    "--host", "0.0.0.0", "--port", "8000", "--no-access-log"
)
Ensure-Ffmpeg

if ($WithCron) {
    Ensure-BindMountProcess -Name $SchedulerName -Command @(
        "uv", "run", "--frozen", "python", "-m", "app.scheduler"
    )
    Ensure-BindMountProcess -Name $WorkerName -Command @(
        "uv", "run", "--frozen", "python", "-m", "app.worker"
    )
}

Wait-ApiReady
docker ps --filter "name=kelin-" --format "table {{.Names}}`t{{.Status}}`t{{.Ports}}"

$lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like "192.168.*" } |
    Select-Object -ExpandProperty IPAddress -First 1

Write-Host ""
Write-Host "本机: http://127.0.0.1:8000"
if ($lan) {
    Write-Host "局域网: http://${lan}:8000"
}
Write-Host "改 Python / .env 后执行: docker restart $ApiName"
if (-not $WithCron) {
    Write-Host "串门 / 鉴定 / 注销需要 cron 时加: -WithCron"
}
