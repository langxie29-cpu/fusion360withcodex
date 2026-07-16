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
    throw "Refusing to update outside AddIns root: $target"
}
if (-not (Test-Path -LiteralPath $target -PathType Container)) {
    throw "FusionAIModeler is not installed at $target; run install_fusion_addin.ps1 first"
}
if (-not $PSCmdlet.ShouldProcess($target, 'Update FusionAIModeler Add-In from the workspace')) {
    return
}

Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse -Force
}

$sourceManifest = Get-Content -Raw -LiteralPath (Join-Path $source 'FusionAIModeler.manifest') | ConvertFrom-Json -AsHashtable
$targetManifest = Get-Content -Raw -LiteralPath (Join-Path $target 'FusionAIModeler.manifest') | ConvertFrom-Json -AsHashtable
if ($sourceManifest['version'] -ne $targetManifest['version']) {
    throw "Update verification failed: source version $($sourceManifest['version']), target version $($targetManifest['version'])"
}
$mismatches = Get-ChildItem -LiteralPath $source -Recurse -File | Where-Object {
    $_.Extension -ne '.pyc'
} | ForEach-Object {
    $relative = $_.FullName.Substring($source.Length + 1)
    $installed = Join-Path $target $relative
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) {
        return $relative
    }
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
    $installedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installed).Hash
    if ($sourceHash -ne $installedHash) {
        return $relative
    }
}
if ($mismatches) {
    throw "Update verification failed for: $($mismatches -join ', ')"
}

Write-Output "Updated FusionAIModeler $($targetManifest['version']) at $target"
Write-Output 'Restart the add-in from Fusion Utilities > Scripts and Add-Ins before testing the new executor.'
