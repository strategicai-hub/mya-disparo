# build-local.ps1 — Build local + push GHCR + deploy Portainer (mya-disparo)
# Requer: Docker Desktop com buildx, GitHub CLI (gh) autenticado com write:packages
# Credenciais Portainer em ~/.claude/.env

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Carregar env global ────────────────────────────────────────────────────────
$globalEnv = Join-Path $env:USERPROFILE ".claude\.env"
if (Test-Path $globalEnv) {
    Get-Content $globalEnv | ForEach-Object {
        if ($_ -match "^\s*([^#=]+)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
        }
    }
}

$PORTAINER_URL   = $env:PORTAINER_URL
$PORTAINER_TOKEN = $env:PORTAINER_TOKEN

foreach ($var in @("PORTAINER_URL","PORTAINER_TOKEN")) {
    if (-not [System.Environment]::GetEnvironmentVariable($var, "Process")) {
        Write-Error "Variavel $var nao definida em ~/.claude/.env"
    }
}

# ── Constantes ─────────────────────────────────────────────────────────────────
$REGISTRY  = "ghcr.io"
$OWNER     = "strategicai-hub"
$IMAGE     = "mya-disparo"
$IMAGE_TAG = "$REGISTRY/$OWNER/${IMAGE}:latest"
$SVC_NAMES = @("mya-disparo_api", "mya-disparo_worker", "mya-disparo_scheduler")

$projectRoot = $PSScriptRoot

# ── Login GHCR via gh CLI ──────────────────────────────────────────────────────
Write-Host "=== [1/4] Login GHCR (via gh CLI) ===" -ForegroundColor Cyan
$GHCR_USER  = gh api user --jq ".login"
$GHCR_TOKEN = gh auth token
echo $GHCR_TOKEN | docker login ghcr.io -u $GHCR_USER --password-stdin
Write-Host "Logado como $GHCR_USER" -ForegroundColor Green

# ── Build ──────────────────────────────────────────────────────────────────────
Write-Host "=== [2/4] Build + Push ===" -ForegroundColor Cyan
Write-Host "Imagem: $IMAGE_TAG"

$builderName = "mya-disparo-builder"
$buildxList = docker buildx ls
if (-not ($buildxList | Select-String $builderName)) {
    docker buildx create --name $builderName --driver docker-container --use | Out-Null
} else {
    docker buildx use $builderName | Out-Null
}

$metaFile = Join-Path $env:TEMP "buildx-meta-mya.json"

docker buildx build `
    --platform linux/amd64 `
    --push `
    --tag $IMAGE_TAG `
    --metadata-file $metaFile `
    $projectRoot

if ($LASTEXITCODE -ne 0) { Write-Error "Build falhou." }

$meta   = Get-Content $metaFile -Raw | ConvertFrom-Json
$DIGEST = $meta."containerimage.digest"
if (-not $DIGEST) { Write-Error "Nao foi possivel extrair o digest do build." }

$IMAGE_REF = "${IMAGE_TAG}@${DIGEST}"
Write-Host "Digest: $DIGEST" -ForegroundColor Green

# ── Deploy Portainer ───────────────────────────────────────────────────────────
Write-Host "=== [3/4] Deploy via Portainer ===" -ForegroundColor Cyan

Add-Type @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class TrustAllCertsMya : ICertificatePolicy {
    public bool CheckValidationResult(ServicePoint sp, X509Certificate cert, WebRequest req, int error) { return true; }
}
"@ -ErrorAction SilentlyContinue
[System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsMya
[System.Net.ServicePointManager]::SecurityProtocol = "Tls,Tls11,Tls12"

$baseUrl = $PORTAINER_URL.TrimEnd("/")
$headers = @{ "X-API-Key" = $PORTAINER_TOKEN; "Content-Type" = "application/json" }

foreach ($SVC_NAME in $SVC_NAMES) {
    Write-Host "Atualizando $SVC_NAME..."
    $svcUrl  = "$baseUrl/api/endpoints/1/docker/services/$SVC_NAME"
    $svcResp = Invoke-RestMethod -Uri $svcUrl -Headers $headers -Method Get
    $version = $svcResp.Version.Index

    $spec = $svcResp.Spec | ConvertTo-Json -Depth 20 | ConvertFrom-Json
    $spec.TaskTemplate.ContainerSpec.Image = $IMAGE_REF
    $forceUpdate = if ($spec.TaskTemplate.PSObject.Properties["ForceUpdate"]) { $spec.TaskTemplate.ForceUpdate } else { 0 }
    $spec.TaskTemplate.ForceUpdate = $forceUpdate + 1

    $body      = $spec | ConvertTo-Json -Depth 20
    $updateUrl = "$baseUrl/api/endpoints/1/docker/services/$SVC_NAME/update?version=$version&registryAuth="
    Invoke-RestMethod -Uri $updateUrl -Headers $headers -Method Post -Body $body | Out-Null
    Write-Host "  OK: $SVC_NAME -> $IMAGE_REF" -ForegroundColor Green
}

# ── Verificar containers rodando ───────────────────────────────────────────────
Write-Host "=== [4/4] Verificando containers ===" -ForegroundColor Cyan
Start-Sleep -Seconds 15

$containers = Invoke-RestMethod -Uri "$baseUrl/api/endpoints/1/docker/containers/json" -Headers $headers -Method Get
foreach ($SVC_NAME in $SVC_NAMES) {
    $c = $containers | Where-Object {
        $labels = $_.Labels
        $svcLabel = if ($labels.PSObject.Properties["com.docker.swarm.service.name"]) { $labels."com.docker.swarm.service.name" } else { "" }
        $svcLabel -eq $SVC_NAME -and $_.Status -match "^Up"
    }
    if ($c) {
        Write-Host "  OK: $SVC_NAME - $($c.Status)" -ForegroundColor Green
    } else {
        Write-Host "  AVISO: $SVC_NAME nao encontrado em estado Up" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Deploy concluido!" -ForegroundColor Green
Write-Host "  Imagem  : $IMAGE_REF"
$svcList = $SVC_NAMES -join ", "
Write-Host "  Servicos: $svcList"
