[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$AddInsRoot = "$env:APPDATA\Autodesk\Autodesk Fusion 360\API\AddIns"
)

$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$source = (Resolve-Path -LiteralPath (Join-Path $workspace 'fusion-addin\FusionAIModeler')).Path
$root = [System.IO.Path]::GetFullPath($AddInsRoot)
$target = [System.IO.Path]::GetFullPath((Join-Path $root 'FusionAIModeler'))

if (-not $target.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install outside AddIns root: $target"
}
if (Test-Path -LiteralPath $target) {
    throw "Add-in already exists; remove or back it up explicitly before reinstalling: $target"
}

if (-not $PSCmdlet.ShouldProcess($target, "Install FusionAIModeler Add-In")) {
    return
}

New-Item -ItemType Directory -Path $root -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Recurse

$manifest = Join-Path $target 'FusionAIModeler.manifest'
if (-not (Test-Path -LiteralPath $manifest)) {
    throw "Installation verification failed: $manifest"
}

Write-Output "Installed FusionAIModeler to $target"
