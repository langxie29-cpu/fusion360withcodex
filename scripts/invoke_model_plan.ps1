[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Plan,

    [switch]$Apply,

    [ValidateSet('new_document', 'active_document')]
    [string]$Mode = 'new_document',

    [string]$Server = 'http://127.0.0.1:9100/'
)

$ErrorActionPreference = 'Stop'
$planPath = (Resolve-Path -LiteralPath $Plan).Path
$planJson = Get-Content -Raw -LiteralPath $planPath
$null = $planJson | ConvertFrom-Json

function Invoke-FusionTool {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][hashtable]$Arguments,
        [int]$Id
    )

    $request = @{
        jsonrpc = '2.0'
        id = $Id
        method = 'tools/call'
        params = @{ name = $Name; arguments = $Arguments }
    } | ConvertTo-Json -Depth 12 -Compress
    $response = Invoke-RestMethod -Uri $Server -Method Post -ContentType 'application/json' -Body $request -TimeoutSec 180
    if ($response.error) {
        throw "Fusion MCP error: $($response.error.message)"
    }
    $payload = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError) {
        throw ($payload | ConvertTo-Json -Depth 12)
    }
    return $payload
}

$staged = Invoke-FusionTool -Name 'validate_and_stage_model_plan' -Arguments @{ plan_json = $planJson } -Id 1
$staged | ConvertTo-Json -Depth 12
if (-not $Apply) {
    Write-Output 'Validation passed. Re-run with -Apply to create the Fusion model.'
    return
}

$applied = Invoke-FusionTool -Name 'apply_staged_model_plan' -Arguments @{
    plan_id = $staged.plan_id
    mode = $Mode
} -Id 2
$applied | ConvertTo-Json -Depth 12
