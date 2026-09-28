# Tests connectivity to the GoHighLevel (LeadConnector) API using credentials from .env.
# Never prints the raw API token.

$envPath = Join-Path $PSScriptRoot ".env"

if (-not (Test-Path $envPath)) {
    Write-Host "ERROR: .env file not found at $envPath" -ForegroundColor Red
    exit 1
}

$envVars = @{}
Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $idx = $line.IndexOf("=")
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        $envVars[$key] = $value
    }
}

$token = $envVars["GHL_API_TOKEN"]
$locationId = $envVars["GHL_LOCATION_ID"]

if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Host "ERROR: GHL_API_TOKEN is missing from .env" -ForegroundColor Red
    exit 1
}

$maskedToken = $token.Substring(0, [Math]::Min(4, $token.Length)) + "..." + $token.Substring([Math]::Max(0, $token.Length - 4))
Write-Host "Loaded GHL_API_TOKEN (masked): $maskedToken  [length: $($token.Length)]"

if ([string]::IsNullOrWhiteSpace($locationId)) {
    Write-Host "WARNING: GHL_LOCATION_ID is empty in .env." -ForegroundColor Yellow
    Write-Host "Most GoHighLevel v2 API endpoints require a Location ID alongside a Private Integration token."
    Write-Host "Find it in your GHL sub-account under Settings > Business Profile (or in the URL when viewing that location)."
} else {
    Write-Host "Loaded GHL_LOCATION_ID: $locationId"
}

$headers = @{
    "Authorization" = "Bearer $token"
    "Version"       = "2021-07-28"
    "Accept"        = "application/json"
}

$uri = if ([string]::IsNullOrWhiteSpace($locationId)) {
    "https://services.leadconnectorhq.com/locations/"
} else {
    "https://services.leadconnectorhq.com/locations/$locationId"
}

Write-Host "`nCalling GET $uri ..."

try {
    $response = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get -ErrorAction Stop
    Write-Host "`nSUCCESS: GHL API connection verified." -ForegroundColor Green
    if ($response.location) {
        Write-Host "Location name: $($response.location.name)"
        Write-Host "Location ID:   $($response.location.id)"
    } else {
        Write-Host ($response | ConvertTo-Json -Depth 5)
    }
}
catch {
    $statusCode = $null
    $body = $null
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode
        try {
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $body = $reader.ReadToEnd()
        } catch {}
    }

    Write-Host "`nFAILED: GHL API connection could not be verified." -ForegroundColor Red
    if ($statusCode) { Write-Host "HTTP Status: $statusCode" }
    if ($body) { Write-Host "Response body: $body" }

    switch ($statusCode) {
        401 { Write-Host "`nReason: The token was rejected as invalid/unauthorized. Check that GHL_API_TOKEN is correct and active (not revoked/regenerated) in GHL > Settings > Private Integrations." }
        403 { Write-Host "`nReason: The token is valid but lacks the required scope (e.g. locations.readonly). Edit the Private Integration in GHL, add the missing scope, then copy the newly generated token into .env." }
        404 { Write-Host "`nReason: Location not found. GHL_LOCATION_ID is missing, incorrect, or does not match the sub-account the token belongs to." }
        default { Write-Host "`nReason: $($_.Exception.Message)" }
    }
    exit 1
}
