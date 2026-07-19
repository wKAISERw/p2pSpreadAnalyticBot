# deploy.ps1
# Автоматичний скрипт деплою для Arbix Quantum P2P Scanner

Param (
    [string]$TargetHost = "root@167.233.147.232",
    [string]$TargetDir = "/root/app",
    [string]$ArchiveName = "arbix-quantum.tar.gz"
)

$ErrorActionPreference = "Stop"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "🚀 Початок деплою проекту Arbix Quantum" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# Перевірка наявності архіву
if (-not (Test-Path $ArchiveName)) {
    Write-Error "Помилка: Файл архіву '$ArchiveName' не знайдено! Спочатку створіть його."
}

$ArchiveHash = (Get-FileHash $ArchiveName -Algorithm MD5).Hash
$ArchiveSize = [math]::Round((Get-Item $ArchiveName).Length / 1MB, 2)

Write-Host "📦 Локальний архів: $ArchiveName" -ForegroundColor Yellow
Write-Host "📏 Розмір: $ArchiveSize MB" -ForegroundColor Yellow
Write-Host "🔑 MD5 Хеш: $ArchiveHash" -ForegroundColor Yellow
Write-Host "🌐 Сервер призначення: $TargetHost" -ForegroundColor Yellow
Write-Host "📁 Папка призначення: $TargetDir" -ForegroundColor Yellow
Write-Host "----------------------------------------------"

try {
    # 1. Завантаження архіву на сервер
    Write-Host "📤 1/3 Завантаження архіву через SCP..." -ForegroundColor Cyan
    scp $ArchiveName "${TargetHost}:/root/"
    Write-Host "✅ Архів успішно завантажено на сервер." -ForegroundColor Green

    # 2. Виконання команд розпакування та рестарту на сервері через SSH
    Write-Host "⚙️ 2/3 Розпакування архіву та оновлення контейнерів на сервері..." -ForegroundColor Cyan
    
    $SshCommands = @(
        # Перевірка хешу на сервері
        "echo '=== Перевірка цілісності архіву на сервері ==='",
        "SERVER_MD5=`$(md5sum /root/$ArchiveName | awk '{print toupper(`$1)}')`",
        "echo `"Локальний MD5: $ArchiveHash`"",
        "echo `"Серверний  MD5: `$SERVER_MD5`"",
        "if [ `"`$SERVER_MD5`" != `"$ArchiveHash`" ]; then echo '❌ Помилка: Хеш-суми не збігаються!' && exit 1; fi",
        "echo '✅ Хеш-суми збігаються!'",
        
        # Створення папки під додаток, якщо немає
        "mkdir -p $TargetDir",
        
        # Розпакування з перезаписом файлів
        "echo '📦 Розпакування архіву...'",
        "tar -xzf /root/$ArchiveName -C $TargetDir",
        "rm -f /root/$ArchiveName",
        
        # Збірка та запуск контейнерів
        "echo '🐳 Перезапуск Docker-контейнерів...'",
        "cd $TargetDir",
        "docker compose up --build -d",
        "echo '🎉 Контейнери оновлено та запущено!'",
        "docker compose ps"
    ) -join " && "

    ssh $TargetHost $SshCommands
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "🎉 Деплой успішно завершено!" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green

} catch {
    Write-Host "❌ Помилка під час деплою: $_" -ForegroundColor Red
    Exit 1
}
