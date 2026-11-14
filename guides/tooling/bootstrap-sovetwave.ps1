[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$InstallRoot,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"

$upstreamRepository = "https://github.com/LimiNode/sovetwave.git"
$upstreamTag = "v0.5.0-beta.4"
$resolvedCommit = "2d9534de0168bfd2d2d217de8e3153a7999dd7bf"
$skillBlob = "92beec2728508b1feae39e6b2f0311473bcc4d42"
$licenseBlob = "daf9c006e677b8b01ec043d7563dbfc7ffce2c2f"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
}
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Join-Path $RepoRoot ".codex/skills/sovetwave"
}

$repoRootFull = (Resolve-Path $RepoRoot).Path
$installRootFull = [IO.Path]::GetFullPath($InstallRoot)
$installLeaf = Split-Path $installRootFull -Leaf
if ($installLeaf -ne "sovetwave") {
    throw "InstallRoot must name the project-local sovetwave directory: $installRootFull"
}

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) `
    ("sovetwave-bootstrap-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

try {
    & git clone --filter=blob:none --no-checkout --branch $upstreamTag `
        $upstreamRepository $temporaryRoot | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to clone $upstreamRepository at $upstreamTag"
    }

    & git -C $temporaryRoot checkout --detach $resolvedCommit | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to checkout pinned Sovetwave commit $resolvedCommit"
    }

    $actualCommit = (& git -C $temporaryRoot rev-parse HEAD).Trim()
    if ($actualCommit -ne $resolvedCommit) {
        throw "Pinned commit mismatch: expected $resolvedCommit, got $actualCommit"
    }

    $payloadSource = Join-Path $temporaryRoot "skills/sovetwave"
    $sourceSkill = Join-Path $payloadSource "SKILL.md"
    $sourceLicense = Join-Path $temporaryRoot "LICENSE"
    if (-not (Test-Path $sourceSkill) -or -not (Test-Path $sourceLicense)) {
        throw "Pinned Sovetwave checkout is missing the skill payload or MIT license"
    }

    $actualSkillBlob = (& git -C $temporaryRoot hash-object $sourceSkill).Trim()
    if ($actualSkillBlob -ne $skillBlob) {
        throw "SKILL.md blob mismatch: expected $skillBlob, got $actualSkillBlob"
    }
    $actualLicenseBlob = (& git -C $temporaryRoot hash-object $sourceLicense).Trim()
    if ($actualLicenseBlob -ne $licenseBlob) {
        throw "LICENSE blob mismatch: expected $licenseBlob, got $actualLicenseBlob"
    }

    if ($VerifyOnly) {
        if (-not (Test-Path $InstallRootFull)) {
            throw "Installed skill directory does not exist: $InstallRootFull"
        }
        $installedSkillBlob = (& git hash-object (Join-Path $InstallRootFull "SKILL.md")).Trim()
        if ($installedSkillBlob -ne $skillBlob) {
            throw "Installed SKILL.md blob mismatch: expected $skillBlob, got $installedSkillBlob"
        }
        Write-Output "Sovetwave payload verified at $InstallRootFull"
        exit 0
    }

    $installParent = Split-Path $InstallRootFull -Parent
    New-Item -ItemType Directory -Force -Path $installParent | Out-Null
    if (Test-Path $InstallRootFull) {
        Remove-Item -Recurse -Force $InstallRootFull
    }
    Copy-Item -Recurse -Force $payloadSource $InstallRootFull
    Copy-Item -Force $sourceLicense `
        (Join-Path $installParent "sovetwave-UPSTREAM-LICENSE.txt")

    Write-Output "Installed Sovetwave $upstreamTag ($resolvedCommit) at $InstallRootFull"
    Write-Output "SKILL.md blob: $skillBlob"
    Write-Output "MIT license blob: $licenseBlob"
}
finally {
    if (Test-Path $temporaryRoot) {
        Remove-Item -Recurse -Force $temporaryRoot
    }
}
