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
    
    # Construct remote commands using single-quoted strings to prevent local PowerShell execution
    $Cmds = @()
    $Cmds += "echo '=== Verifying archive integrity on server ==='"
    $Cmds += 'SERVER_MD5=$(md5sum /root/' + $ArchiveName + ' | awk ''{print toupper($1)}'')'
    $Cmds += 'echo "Local MD5: ' + $ArchiveHash + '"'
    $Cmds += 'echo "Server MD5: $SERVER_MD5"'
    $Cmds += 'if [ "$SERVER_MD5" != "' + $ArchiveHash + '" ]; then echo "Error: MD5 hashes do not match!" && exit 1; fi'
    $Cmds += "echo 'MD5 hashes match!'"
    $Cmds += "mkdir -p $TargetDir"
    $Cmds += "echo 'Unpacking archive...'"
    $Cmds += "tar -xzf /root/$ArchiveName -C $TargetDir"
    $Cmds += "rm -f /root/$ArchiveName"
    $Cmds += "echo 'Restarting Docker containers...'"
    $Cmds += "cd $TargetDir"
    $Cmds += "docker compose up --build -d"
    $Cmds += "echo 'Containers updated and started successfully!'"
    $Cmds += "docker compose ps"

    $JoinedCmds = $Cmds -join " && "

    ssh $TargetHost $JoinedCmds
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "Deployment completed successfully!" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green

} catch {
    Write-Host "Deployment failed: $_" -ForegroundColor Red
    Exit 1
}
