# deploy.ps1
# Automatic deployment script for Arbix Quantum P2P Scanner

Param (
    [string]$TargetHost = "root@167.233.147.232",
    [string]$TargetDir = "/root/app",
    [string]$ArchiveName = "arbix-quantum.tar.gz"
)

$ErrorActionPreference = "Stop"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "Starting deployment for Arbix Quantum" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# Check if archive exists
if (-not (Test-Path $ArchiveName)) {
    Write-Error "Error: Archive file '$ArchiveName' not found! Build it first."
}

$ArchiveHash = (Get-FileHash $ArchiveName -Algorithm MD5).Hash
$ArchiveSize = [math]::Round((Get-Item $ArchiveName).Length / 1MB, 2)

Write-Host "Local archive: $ArchiveName" -ForegroundColor Yellow
Write-Host "Size: $ArchiveSize MB" -ForegroundColor Yellow
Write-Host "MD5 Hash: $ArchiveHash" -ForegroundColor Yellow
Write-Host "Target Host: $TargetHost" -ForegroundColor Yellow
Write-Host "Target Directory: $TargetDir" -ForegroundColor Yellow
Write-Host "----------------------------------------------"

try {
    # 1. Upload archive to server
    Write-Host "1/3 Uploading archive via SCP..." -ForegroundColor Cyan
    scp $ArchiveName "${TargetHost}:/root/"
    Write-Host "Archive uploaded successfully." -ForegroundColor Green

    # 2. Execute unpack and restart commands on server via SSH
    Write-Host "2/3 Unpacking archive and updating containers on server..." -ForegroundColor Cyan
    
    $SshCommands = @(
        # Check hash on server
        "echo '=== Verifying archive integrity on server ==='",
        "SERVER_MD5=`$(md5sum /root/$ArchiveName | awk '{print toupper(`$1)}')`",
        "echo `"Local MD5: $ArchiveHash`"",
        "echo `"Server MD5: `$SERVER_MD5`"",
        "if [ `"`$SERVER_MD5`" != `"$ArchiveHash`" ]; then echo 'Error: MD5 hashes do not match!' && exit 1; fi",
        "echo 'MD5 hashes match!'",
        
        # Create target directory if it doesn't exist
        "mkdir -p $TargetDir",
        
        # Unpack files overriding old ones
        "echo 'Unpacking archive...'",
        "tar -xzf /root/$ArchiveName -C $TargetDir",
        "rm -f /root/$ArchiveName",
        
        # Rebuild and restart containers
        "echo 'Restarting Docker containers...'",
        "cd $TargetDir",
        "docker compose up --build -d",
        "echo 'Containers updated and started successfully!'",
        "docker compose ps"
    ) -join " && "

    ssh $TargetHost $SshCommands
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "Deployment completed successfully!" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green

} catch {
    Write-Host "Deployment failed: $_" -ForegroundColor Red
    Exit 1
}
