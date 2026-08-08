<#
Читаємо список дозволених зовнішніх диплінків Binance замість того, щоб його
вгадувати.

Що знайдено в dex. У застосунку є явний механізм фільтрації зовнішніх переходів:

    externalDeeplinkAllows          externalDeeplinkBlocks
    deeplink_allow_index            deeplink_block_index
    isDeeplinkAllowed               deeplink_blocked_by_scene
    supportsExternalLink

Списки керуються віддалено, через їхню систему конфігів nezha:

    android_nezha_enable_external_deeplink_allowed_v2
    android_nezha_enable_external_deeplink_blocked_v2

Тобто самих списків в APK немає — вони приходять з сервера. Саме тому перебір
маршрутів приречений: `/webview/webview`, `/mp/web` і картка оголошення
відхиляються не через неправильне ім'я, а тому що їх немає в дозволених.

Але застосунок ЛОГУЄ своє рішення — у dex є рядки `deeplink allowed ` і
`deeplink denied `. Отже список можна не вгадувати, а прочитати з logcat.

Запуск (телефон підключений, USB-налагодження увімкнене):
    .\dump_binance_allowlist.ps1

Скрипт відкриє кілька диплінків і збере всі рядки логу про рішення роутера.
#>

param(
    [string]$MerchantNo = "s40c6bb83ac363d02a2f4cf9bd2f23645",
    [string]$OrderNo    = "22222222222222222222",
    [int]$WaitMs        = 3000
)

$ErrorActionPreference = "Continue"
$PKG = "com.binance.dev"

function B64([string]$s) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }

$probes = @(
    # Той, що начебто працює — треба нарешті перевірити його чисто.
    "https://app.binance.com/fiat/orderDetails?id=$OrderNo",
    # Відхилені, потрібні для порівняння рядків у логу.
    "https://app.binance.com/mp/web?appId=Bzp9defeaRgNqhgV4wEG5C&startPagePath=" + (B64 "pages/merchant-detail/index"),
    "https://app.binance.com/webview/webview?type=default&url=" + (B64 "https://www.binance.com/fixedLoan"),
    # Свідомо неіснуючий — щоб побачити, як виглядає відмова.
    "https://app.binance.com/definitely/not/a/route",
    # Маршрути з підтвердженого списку 177 — можливо, хтось із них дозволений.
    "https://app.binance.com/main/main?at=index",
    "https://app.binance.com/fiat/hold",
    "https://app.binance.com/p2p/chatList?source=homepage"
)

if (-not (adb devices | Select-String -Pattern "device$")) {
    Write-Host "adb не бачить пристрій." -ForegroundColor Red; exit 1
}

$report = Join-Path $PSScriptRoot "binance_allowlist.txt"
"# binance deeplink allowlist :: $(Get-Date -Format s)" | Set-Content $report

foreach ($url in $probes) {
    Write-Host "`n>>> $url" -ForegroundColor Cyan
    "`n===== $url" | Add-Content $report

    adb shell am force-stop $PKG 2>&1 | Out-Null
    adb logcat -c 2>&1 | Out-Null
    Start-Sleep -Milliseconds 500

    adb shell am start -W -a android.intent.action.VIEW `
        -c android.intent.category.BROWSABLE -d "`"$url`"" 2>&1 | Out-String | Add-Content $report
    Start-Sleep -Milliseconds $WaitMs

    $top = adb shell dumpsys activity activities 2>&1 |
           Select-String -Pattern "topResumedActivity" | Select-Object -First 1
    "TOP: $top" | Add-Content $report

    # Ось найцінніше: рішення роутера словами.
    $log = adb logcat -d 2>&1 |
           Select-String -Pattern "deeplink|deep_link|externalDeeplink|RouterPath|allow|denied" |
           Select-Object -Last 40
    "LOG:" | Add-Content $report
    $log | ForEach-Object { "  $_" } | Add-Content $report

    $verdict = if ($top -match "NoSupportRouterPath") { "ВІДХИЛЕНО" }
               elseif ($top -match [regex]::Escape($PKG)) { "ПРИЙНЯТО" }
               else { "?" }
    Write-Host "    $verdict" -ForegroundColor $(if ($verdict -eq "ПРИЙНЯТО") { "Green" } else { "Red" })
    $hits = $log | Where-Object { $_ -match "allowed|denied|allow_index|block_index" }
    if ($hits) { $hits | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow } }
}

# Окремо: спроба витягнути сам конфіг nezha, якщо він осів у логах.
"`n`n===== ПОШУК КОНФІГУ =====" | Add-Content $report
adb logcat -d 2>&1 |
    Select-String -Pattern "nezha_enable_external_deeplink|externalDeeplinkAllows|externalDeeplinkBlocks" |
    ForEach-Object { "  $_" } | Add-Content $report

Write-Host "`nЗвіт: $report" -ForegroundColor Green
Write-Host @"

Що шукати у звіті:
  рядок 'deeplink allowed <шлях>'  -> шлях у білому списку
  рядок 'deeplink denied <шлях>'   -> у чорному або поза білим
  externalDeeplinkAllows=[...]     -> весь список одразу, найкращий результат

Якщо жоден рядок не з'явився — логування вимкнене у релізному збиранні,
і тоді список зовнішніх диплінків прочитати неможливо.
"@ -ForegroundColor Yellow
