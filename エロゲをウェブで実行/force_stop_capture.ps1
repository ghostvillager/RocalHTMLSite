$ErrorActionPreference = 'Continue'

# ====== 設定 ======
$SunshineUrl  = "https://localhost:47990"
$SunshineUser = "hasegawa"
$SunshinePass = "9080hake"
# ==================

Write-Host "========================================"
Write-Host " Sunshine Capture Force Stop"
Write-Host " (Stop Current Session)"
Write-Host "========================================"
Write-Host ""
Write-Host "[Info] URL  : $SunshineUrl"
Write-Host "[Info] User : $SunshineUser"
Write-Host ""

# --- 自己証明書を完全に無視 ---
add-type @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class TrustAllCertsPolicy : ICertificatePolicy {
    public bool CheckValidationResult(
        ServicePoint srvPoint, X509Certificate certificate,
        WebRequest request, int certificateProblem) {
        return true;
    }
}
"@
[System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsPolicy
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$pair   = "${SunshineUser}:${SunshinePass}"
$base64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
$headers = @{
    Authorization = "Basic $base64"
    Accept        = "application/json"
}

function Get-CsrfToken($jsonText) {
    $obj = $jsonText | ConvertFrom-Json
    # 両方のキーに対応
    if ($obj.csrfToken)  { return $obj.csrfToken }
    if ($obj.csrf_token) { return $obj.csrf_token }
    return $null
}

$success = $false

# ========== 方法1: Invoke-WebRequest ==========
Write-Host "[Method 1] Invoke-WebRequest ..."
try {
    Write-Host "  [1] Get CSRF token..."
    $r = Invoke-WebRequest -Uri "$SunshineUrl/api/csrf-token" -Headers $headers -UseBasicParsing -TimeoutSec 10
    Write-Host "  Status: $($r.StatusCode)"
    Write-Host "  Body  : $($r.Content)"

    $token = Get-CsrfToken $r.Content
    if (-not $token) { throw "csrf token is empty" }
    Write-Host "  Token OK: $($token.Substring(0, [Math]::Min(12, $token.Length)))..."

    $headers["X-CSRF-Token"] = $token

    Write-Host "  [2] POST /api/apps/close ..."
    $r2 = Invoke-WebRequest -Uri "$SunshineUrl/api/apps/close" -Headers $headers -Method POST -UseBasicParsing -TimeoutSec 10
    Write-Host "  Status: $($r2.StatusCode)"
    Write-Host "  Body  : $($r2.Content)"
    Write-Host ""
    Write-Host "SUCCESS (Method 1)" -ForegroundColor Green
    $success = $true
}
catch {
    Write-Host "  Failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ========== 方法2: curl.exe ==========
if (-not $success) {
    Write-Host ""
    Write-Host "[Method 2] curl.exe ..."
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        try {
            Write-Host "  [1] Get CSRF token..."
            $tokenJson = & curl.exe -s -k -u "${SunshineUser}:${SunshinePass}" "$SunshineUrl/api/csrf-token"
            Write-Host "  Body: $tokenJson"
            $token = Get-CsrfToken $tokenJson
            if (-not $token) { throw "csrf token is empty" }
            Write-Host "  Token OK"

            Write-Host "  [2] POST /api/apps/close ..."
            $result = & curl.exe -s -k -u "${SunshineUser}:${SunshinePass}" -X POST -H "X-CSRF-Token: $token" "$SunshineUrl/api/apps/close"
            Write-Host "  Result: $result"
            Write-Host ""
            Write-Host "SUCCESS (Method 2 - curl)" -ForegroundColor Green
            $success = $true
        }
        catch {
            Write-Host "  Failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "  curl.exe not found"
    }
}

if (-not $success) {
    Write-Host ""
    Write-Host "===== ALL METHODS FAILED =====" -ForegroundColor Red
    Write-Host "Possible causes:"
    Write-Host "  - Sunshine is not running"
    Write-Host "  - Port is not 47990"
    Write-Host "  - Username / Password is wrong"
    Write-Host "  - Firewall is blocking"
}

Write-Host ""
Write-Host "========================================"
Write-Host "Done"
Write-Host "========================================"
