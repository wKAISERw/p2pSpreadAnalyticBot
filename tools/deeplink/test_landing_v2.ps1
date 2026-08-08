<#
Раунд 2: перевіряємо не «чи перехопив застосунок», а «на який екран він реально
приземлився».

Навіщо окремий скрипт. У раунді 1 вердикт APP означав лише, що інтент піймав
потрібний пакет. Але і Binance (FirstDispatchRouterActivity), і Bybit
(MainActivity) — це ОДИН диспетчер на всі маршрути. Він відповість «APP» навіть
тоді, коли всередині не зрозуміє параметр і тихо відкриє головну. Саме цей
сценарій і болить. Тому тут після кожного лінка знімається скриншот.

Запуск:
  .\test_landing_v2.ps1 -AdNo <ID> -MerchantNo <ID> -OrderNo <ID> `
                        -OkxShareCode <CODE> -OkxUserId <ID> -BybitUserId <ID>

Результат: папка shots\ зі скриншотами + landing_report.txt.
Обидва треба показати мені — я подивлюсь картинки і скажу, де реально ордер,
а де головна.
#>

param(
    [string]$AdNo         = "",
    [string]$MerchantNo   = "",
    [string]$OrderNo      = "",
    [string]$OkxShareCode = "",
    [string]$OkxUserId    = "",
    [string]$OkxOrderNo   = "",
    [string]$BybitUserId  = "",
    [string]$BybitOrderNo = "",
    [int]$WaitMs          = 4000
)

$ErrorActionPreference = "Continue"

function Enc([string]$s) { [uri]::EscapeDataString($s) }

$cases = New-Object System.Collections.ArrayList

function Add-Case($exchange, $pkg, $label, $url) {
    if ($url -match "=$" -or $url -match "=&") { return }   # порожній id — пропускаємо
    [void]$cases.Add([pscustomobject]@{
        Exchange = $exchange; Pkg = $pkg; Label = $label; Url = $url
    })
}

# ── Binance ──────────────────────────────────────────────────────────────────
# Раунд 2 показав NoSupportRouterPathActivity на /fiat/ads/detail — тобто той
# набір шляхів був не з того роутера. Тут беремо ЛИШЕ маршрути, які лежать у
# dex готовими рядками виду bnc://app.binance.com/... (177 штук), плюс
# універсальний webview-шлюз: url передається як base64 звичайного веб-лінка.
function B64([string]$s) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }

if ($OrderNo) {
    Add-Case "Binance" "com.binance.dev" "order-fiatOrderDetails" "https://app.binance.com/fiat/orderDetails?id=$OrderNo"
}
if ($MerchantNo) {
    $advWeb = "https://p2p.binance.com/en/advertiserDetail?advertiserNo=$MerchantNo"
    Add-Case "Binance" "com.binance.dev" "profile-webview" ("https://app.binance.com/webview/webview?type=default&url=" + (B64 $advWeb))
    Add-Case "Binance" "com.binance.dev" "profile-webview-dyn" ("https://app.binance.com/webview/webview?type=default&needDynamic=true&url=" + (B64 $advWeb))
}
if ($AdNo) {
    $adWeb = "https://p2p.binance.com/en/trade/detail/$AdNo"
    Add-Case "Binance" "com.binance.dev" "ad-webview" ("https://app.binance.com/webview/webview?type=default&url=" + (B64 $adWeb))
}
# контроль: маршрут, який точно існує — якщо і він впаде, проблема в іншому
Add-Case "Binance" "com.binance.dev" "control-main" "https://app.binance.com/main/main?at=index"
Add-Case "Binance" "com.binance.dev" "control-fiatHold" "https://app.binance.com/fiat/hold"

# ── OKX ──────────────────────────────────────────────────────────────────────
# Нове з dex: маршрут профілю мерчанта зветься merchanthome.com і бере shareCode.
# Саме той shareCode, який сканер уже збирає в Order.share_code.
if ($OkxShareCode) {
    Add-Case "OKX" "com.okinc.okex.gp" "merchanthome-shareCode" "okx://exchange/merchanthome.com?shareCode=$OkxShareCode"
    Add-Case "OKX" "com.okinc.okex.gp" "merchanthome-noext"     "okx://exchange/merchanthome?shareCode=$OkxShareCode"
}
if ($OkxUserId) {
    Add-Case "OKX" "com.okinc.okex.gp" "profile-userId"       "okx://exchange/p2p/profile?userId=$OkxUserId"
    Add-Case "OKX" "com.okinc.okex.gp" "profile-publicUserId" "okx://exchange/p2p/profile?publicUserId=$OkxUserId"
}
if ($OkxOrderNo) {
    Add-Case "OKX" "com.okinc.okex.gp" "order-id"      "okx://exchange/p2p/order?id=$OkxOrderNo"
    Add-Case "OKX" "com.okinc.okex.gp" "order-orderId" "okx://exchange/p2p/order?orderId=$OkxOrderNo"
}

# ── Bybit ────────────────────────────────────────────────────────────────────
# Нове з libapp.so: справжній формат — bybitapp://open/<шлях>, а не ?page=.
# Внутрішні маршрути mini-app: by-mini://p2p/home, /p2p/order/detail, /p2p/user/home.
# Https-гейтвей приймає закодований диплінк у параметрі by_dp.
if ($BybitOrderNo) {
    Add-Case "Bybit" "com.bybit.app" "order-path"   "bybitapp://open/p2p/order/detail?orderId=$BybitOrderNo"
    Add-Case "Bybit" "com.bybit.app" "order-bydp"   ("https://app.bybit.com/inapp?by_dp=" + (Enc "by-mini://p2p/order/detail?orderId=$BybitOrderNo"))
    Add-Case "Bybit" "com.bybit.app" "order-inapp"  "https://app.bybit.com/inapp/p2p/order/$BybitOrderNo"
}
if ($BybitUserId) {
    # Порядок не випадковий. У libapp.so поряд існують `targetUserId` і
    # `targetNickName` — характерна пара для «контрагента, якого дивишся».
    # Тому targetUserId перевіряємо першим, далі за спаданням імовірності.
    foreach ($p in @("targetUserId", "makerUserId", "userId", "accountId")) {
        Add-Case "Bybit" "com.bybit.app" "profile-$p"      "bybitapp://open/p2p/user/home?${p}=$BybitUserId"
        Add-Case "Bybit" "com.bybit.app" "profile-bydp-$p" ("https://app.bybit.com/inapp?by_dp=" + (Enc "by-mini://p2p/user/home?${p}=$BybitUserId"))
    }
}

# ── прогін ───────────────────────────────────────────────────────────────────

if ($cases.Count -eq 0) {
    Write-Host "Не передано жодного id. Приклад:" -ForegroundColor Red
    Write-Host '  .\test_landing_v2.ps1 -AdNo 123 -MerchantNo abc -OrderNo 456' -ForegroundColor DarkGray
    exit 1
}

if (-not (adb devices | Select-String -Pattern "device$")) {
    Write-Host "adb не бачить пристрій." -ForegroundColor Red
    exit 1
}

$shotDir = Join-Path $PSScriptRoot "shots"
New-Item -ItemType Directory -Force -Path $shotDir | Out-Null
Get-ChildItem $shotDir -Filter *.png -ErrorAction SilentlyContinue | Remove-Item -Force

$report = Join-Path $PSScriptRoot "landing_report.txt"
"# landing test :: $(Get-Date -Format s)" | Set-Content $report

$i = 0
foreach ($c in $cases) {
    $i++
    $name = "{0:d2}_{1}_{2}" -f $i, $c.Exchange, ($c.Label -replace '[^\w-]', '')
    Write-Host "`n[$i/$($cases.Count)] $($c.Label)" -ForegroundColor Cyan
    Write-Host "    $($c.Url)" -ForegroundColor DarkGray

    "`n===== [$i] $($c.Exchange) / $($c.Label)" | Add-Content $report
    "URL: $($c.Url)"                            | Add-Content $report

    # Холодний старт: інакше застосунок покаже попередній екран і скриншот збреше.
    # Гасимо ВСІ три застосунки — у раунді 2 зверху лишалась чужа активність
    # з попереднього кейса і псувала вимір.
    foreach ($p in @("com.binance.dev", "com.okinc.okex.gp", "com.bybit.app")) {
        adb shell am force-stop $p 2>&1 | Out-Null
    }
    adb logcat -c 2>&1 | Out-Null
    Start-Sleep -Milliseconds 700

    $raw = adb shell am start -W -a android.intent.action.VIEW `
             -c android.intent.category.BROWSABLE -d "`"$($c.Url)`"" 2>&1 | Out-String
    $raw | Add-Content $report

    Start-Sleep -Milliseconds $WaitMs

    # ВАЖЛИВО: `adb exec-out screencap -p > file.png` у PowerShell руйнує PNG —
    # потік пишеться як текст (UTF-16 + нормалізація переводів рядка), і файл
    # уже не відновити. Тільки screencap на пристрій + pull.
    $shot = Join-Path $shotDir "$name.png"
    adb shell screencap -p /sdcard/__shot.png 2>&1 | Out-Null
    adb pull /sdcard/__shot.png "$shot" 2>&1 | Out-Null
    adb shell rm -f /sdcard/__shot.png 2>&1 | Out-Null

    $fi = Get-Item $shot -ErrorAction SilentlyContinue
    if ($fi -and $fi.Length -gt 0) {
        $sig = [System.IO.File]::ReadAllBytes($shot)[0..3]
        if ($sig[0] -eq 0x89 -and $sig[1] -eq 0x50) {
            Write-Host "    скріншот: $name.png ($([math]::Round($fi.Length/1kb))kb)" -ForegroundColor Green
        } else {
            Write-Host "    PNG пошкоджено!" -ForegroundColor Red
        }
    } else {
        Write-Host "    скріншот не знявся" -ForegroundColor Red
    }

    # Який екран зверху.
    $top = adb shell dumpsys activity activities 2>&1 |
           Select-String -Pattern "ResumedActivity|topResumedActivity" |
           Select-Object -First 1
    "TOP: $top" | Add-Content $report

    # Текст, який реально видно на екрані. Для Binance/OKX (нативні View)
    # цього достатньо, щоб зрозуміти екран без картинки.
    adb shell uiautomator dump /sdcard/__ui.xml 2>&1 | Out-Null
    $ui = adb shell cat /sdcard/__ui.xml 2>&1 | Out-String
    adb shell rm -f /sdcard/__ui.xml 2>&1 | Out-Null
    $texts = ([regex]::Matches($ui, 'text="([^"]{2,60})"') |
              ForEach-Object { $_.Groups[1].Value } |
              Where-Object { $_ -notmatch '^\s*$' } |
              Select-Object -Unique -First 25) -join ' | '
    "SCREEN TEXT: $texts" | Add-Content $report

    # Bybit — Flutter, одна Activity і порожній ui-дамп. Але роутер логує перехід.
    $log = adb logcat -d -v brief 2>&1 |
           Select-String -Pattern "route|deeplink|by_dp|by-mini|navigat" |
           Select-Object -Last 15
    "ROUTE LOG:" | Add-Content $report
    $log | ForEach-Object { "  $_" } | Add-Content $report
}

Write-Host "`nDone." -ForegroundColor Green
Write-Host "Screenshots saved to: $shotDir"
Write-Host "Report saved to:      $report"
