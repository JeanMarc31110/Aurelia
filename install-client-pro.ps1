#Requires -Version 5.0
<#
.SYNOPSIS
Aurelia Professional Windows Installer Deployment Script
Downloads and silently installs the latest Aurelia release.

.PARAMETER Repo
GitHub repository in format owner/repo (default: JeanMarc31110/Aurelia)

.PARAMETER Token
GitHub API token for authenticated requests (optional)

.EXAMPLE
.\install-client-pro.ps1
.\install-client-pro.ps1 -Repo "JeanMarc31110/Aurelia"
.\install-client-pro.ps1 -Token $env:GITHUB_TOKEN
#>

param(
    [string]$Repo = "JeanMarc31110/Aurelia",
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
$VerbosePreference = "Continue"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Aurelia Professional Windows Installer" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Function to get latest release from GitHub API
function Get-LatestRelease {
    param(
        [string]$Repository,
        [string]$GitHubToken
    )
    
    $apiUrl = "https://api.github.com/repos/$Repository/releases/latest"
    
    try {
        $headers = @{
            "Accept" = "application/vnd.github.v3+json"
        }
        
        if ($GitHubToken) {
            $headers["Authorization"] = "token $GitHubToken"
        }
        
        Write-Host "Fetching latest release from GitHub..."
        $release = Invoke-RestMethod -Uri $apiUrl -Headers $headers -UseBasicParsing
        
        if ($release -and $release.assets) {
            return $release
        } else {
            Write-Error "No release found or no assets in release"
            return $null
        }
    } catch {
        Write-Error "Failed to fetch release: $_"
        return $null
    }
}

# Function to download installer
function Download-Installer {
    param(
        [string]$Url,
        [string]$OutputPath
    )
    
    try {
        Write-Host "Downloading installer from: $Url"
        Write-Host "Saving to: $OutputPath"
        
        $parent = Split-Path -Parent $OutputPath
        if (-not (Test-Path $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        
        Invoke-WebRequest -Uri $Url -OutFile $OutputPath -UseBasicParsing -TimeoutSec 300
        
        if (Test-Path $OutputPath) {
            Write-Host "✓ Download completed successfully"
            return $true
        } else {
            Write-Error "Download failed: file not created"
            return $false
        }
    } catch {
        Write-Error "Download failed: $_"
        return $false
    }
}

# Function to install silently
function Install-Silently {
    param(
        [string]$InstallerPath
    )
    
    try {
        if (-not (Test-Path $InstallerPath)) {
            Write-Error "Installer not found: $InstallerPath"
            return $false
        }
        
        Write-Host "Installing Aurelia silently..."
        $args = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-')
        
        $process = Start-Process -FilePath $InstallerPath -ArgumentList $args -Wait -PassThru
        
        if ($process.ExitCode -eq 0) {
            Write-Host "✓ Installation completed successfully (Exit Code: 0)"
            return $true
        } else {
            Write-Error "Installation failed with exit code: $($process.ExitCode)"
            return $false
        }
    } catch {
        Write-Error "Installation error: $_"
        return $false
    }
}

# Main execution
try {
    # Get latest release
    $release = Get-LatestRelease -Repository $Repo -GitHubToken $Token
    
    if (-not $release) {
        Write-Error "Could not retrieve release information"
        exit 1
    }
    
    Write-Host "Found release: $($release.tag_name)" -ForegroundColor Yellow
    
    # Find setup executable or fallback to ZIP
    $setupExe = $release.assets | Where-Object { $_.name -match "Aurelia_Setup.*\.exe$" } | Select-Object -First 1
    
    if (-not $setupExe) {
        Write-Host "No setup EXE found, looking for ZIP package..." -ForegroundColor Yellow
        $zipAsset = $release.assets | Where-Object { $_.name -match "\.zip$" } | Select-Object -First 1
        
        if (-not $zipAsset) {
            Write-Error "No suitable installer found in release"
            exit 1
        }
        
        $downloadUrl = $zipAsset.browser_download_url
        $fileName = $zipAsset.name
    } else {
        $downloadUrl = $setupExe.browser_download_url
        $fileName = $setupExe.name
    }
    
    # Download
    $tempDir = Join-Path $env:TEMP "Aurelia-Installer"
    $installerPath = Join-Path $tempDir $fileName
    
    if (-not (Download-Installer -Url $downloadUrl -OutputPath $installerPath)) {
        exit 1
    }
    
    # Install
    if ($installerPath -match "\.exe$") {
        if (-not (Install-Silently -InstallerPath $installerPath)) {
            exit 1
        }
    } else {
        Write-Host "ZIP installation not yet implemented in this script"
        exit 1
    }
    
    # Cleanup
    Write-Host "Cleaning up temporary files..."
    Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    
    Write-Host ""
    Write-Host "✓ Aurelia installation completed successfully!" -ForegroundColor Green
    Write-Host ""
    exit 0
    
} catch {
    Write-Error "Fatal error: $_"
    exit 1
}
