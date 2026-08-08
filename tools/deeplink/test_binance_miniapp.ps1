<#
Мініаппа P2P — обхід і dplk, і логіну.

Що знайдено в dex. Усередині застосунку Binance живе мініаппа з
appId = Bzp9defeaRgNqhgV4wEG5C, і в ній є рівно ті сторінки, яких нам бракує:

    pages/merchant-detail/index    профіль мерчанта
    pages/ads/index                оголошення
    pages/order-detail/index       деталі ордера
    pages/order-list/index
    pages/profile/index

Чому це важливо. Маршрут `/mp/web` входить до списку 177 підтверджених шляхів
диспетчера — тобто це нативний перехід, який НЕ потребує ані dplk, ані входу
в особистий акаунт. Якщо він приймає id мерчанта, вся проблема Binance
закривається без 401 і без сесії.

Формат узятий з dex дослівно:

    https://app.binance.com/mp/web
        ?appId=Bzp9defeaRgNqhgV4wEG5C
        &startPagePath=<base64 шляху сторінки>
        &startPageQuery=<base64 рядка запиту>

Приклад з dex: startPageQuery=ZnJvbU5hdGl2ZT10cnVl = "fromNative=true".
Невідоме одне — як зветься параметр з id. Його і перебираємо.

Запуск:
    .\test_binance_miniapp.ps1 -MerchantNo <ID> -AdNo <ID> -OrderNo <ID>
#>

param(
    [string]$MerchantNo = "",
    [string]$AdNo       = "",
    [string]$OrderNo    = "",
    [int]$WaitMs        = 4000
)

$ErrorActionPreference = "Continue"
$PKG   = "com.binance.dev"
$APPID = "Bzp9defeaRgNqhgV4wEG5C"

function B64([string]$s) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }

function MpUrl([string]$page, [string]$query) {
    $u = "https://app.binance.com/mp/web?appId=$APPID&startPagePath=" +
         [uri]::EscapeDataString((B64 $page))
    if ($query) { $u += "&startPageQuery=" + [uri]::EscapeDataString((B64 $query)) }
    return $u
}

$cases = New-Object System.Collections.ArrayList
function Add-Case($label, $url) { [void]$cases.Add(@{ label = $label; url = $url }) }

# УВАГА: цей скрипт історичний. Прогін показав, що /mp/web відхиляється
# серверним білим списком (NoSupportRouterPathActivity), як і /webview/webview.
# Лишений лише як свідчення перевіреної гіпотези. Для Binance робочий шлях —
# dplk через приватний share-ендпоінт, див. bot/binance_share.py.

# Контроль: сторінка без параметрів. Якщо і вона не відкриється — мініаппа
# взагалі не приймає зовнішні інтенти, і далі копати нема сенсу.
Add-Case "control-index" (MpUrl "pages/index" "fromNative=true")

if ($MerchantNo) {
    foreach ($p in @("advertiserNo", "merchantNo", "userNo", "advertiserId", "id")) {
        Add-Case "merchant-$p" (MpUrl "pages/merchant-detail/index" "$p=$MerchantNo&fromNative=true")
    }
}
if ($AdNo) {
    foreach ($p in @("advNo", "adNo", "advertiserNo", "id")) {
        Add-Case "ads-$p" (MpUrl "pages/ads/index" "$p=$AdNo&fromNative=true")
    }
}
if ($OrderNo) {
    foreach ($p in @("orderNo", "orderId", "id")) {
        Add-Case "order-$p" (MpUrl "pages/order-detail/index" "$p=$OrderNo&fromNative=true")
    }
}

if ($cases.Count -le 1) {
    Write-Host "Передай хоча б -MerchantNo. Приклад:" -ForegroundColor Red
    Write-Host "  .\test_binance_miniapp.ps1 -MerchantNo s40c6bb83ac363d02a2f4cf9bd2f23645" -ForegroundColor DarkGray
}

if (-not (adb devices | Select-String -Pattern "device$")) {
    Write-Host "adb не бачить пристрій." -ForegroundColor Red; exit 1
}

$shotDir = Join-Path $PSScriptRoot "shots_miniapp"
New-Item -ItemType Directory -Force -Path $shotDir | Out-Null
$report = Join-Path $PSScriptRoot "miniapp_report.txt"
"# binance miniapp :: $(Get-Date -Format s)" | Set-Content $report

$i = 0
foreach ($c in $cases) {
    $i++
    Write-Host "`n[$i/$($cases.Count)] $($c.label)" -ForegroundColor Cyan
    "`n===== [$i] $($c.label)`nURL: $($c.url)" | Add-Content $report

    adb shell am force-stop $PKG 2>&1 | Out-Null
    Start-Sleep -Milliseconds 600

    $raw = adb shell am start -W -a android.intent.action.VIEW `
             -c android.intent.category.BROWSABLE -d "`"$($c.url)`"" 2>&1 | Out-String
    $raw | Add-Content $report
    Start-Sleep -Milliseconds $WaitMs

    $top = adb shell dumpsys activity activities 2>&1 |
           Select-String -Pattern "topResumedActivity|ResumedActivity" |
           Select-Object -First 1
    "TOP: $top" | Add-Content $report

    adb shell uiautomator dump /sdcard/__ui.xml 2>&1 | Out-Null
    $ui = adb shell cat /sdcard/__ui.xml 2>&1 | Out-String
    adb shell rm -f /sdcard/__ui.xml 2>&1 | Out-Null
    $texts = ([regex]::Matches($ui, 'text="([^"]{2,80})"') |
              ForEach-Object { $_.Groups[1].Value } |
              Where-Object { $_ -notmatch '^\s*$' } |
              Select-Object -Unique -First 20) -join ' | '
    "SCREEN: $texts" | Add-Content $report

    if (($top -match "NoSupportRouterPath") -or ($texts -match "не працює|застаріла")) {
        Write-Host "    роутер відхилив" -ForegroundColor Red
    } elseif ($top -match "com.binance.dev") {
        Write-Host "    відкрилось у застосунку" -ForegroundColor Green
    } else {
        Write-Host "    ???" -ForegroundColor DarkGray
    }
    Write-Host "    $texts" -ForegroundColor DarkGray

    adb shell screencap -p /sdcard/__s.png 2>&1 | Out-Null
    adb pull /sdcard/__s.png (Join-Path $shotDir ("{0:d2}_{1}.png" -f $i, $c.label)) 2>&1 | Out-Null
    adb shell rm -f /sdcard/__s.png 2>&1 | Out-Null
}

Write-Host "`nЗвіт: $report" -ForegroundColor Green
Write-Host @"

Як читати:
  control відхилено                      -> мініаппа не приймає зовнішні
                                            інтенти, шлях закритий.
  control відкрився, merchant-* теж      -> дивись SCREEN: якщо там ім'я
                                            мерчанта, параметр вгадано.
  control відкрився, merchant-* порожні  -> сторінка та, параметр не той;
                                            додай варіантів у список.
"@ -ForegroundColor Yellow
