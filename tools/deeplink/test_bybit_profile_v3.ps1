<#
Раунд 3 по Bybit-профілю. Відрізняється від v2 не набором параметрів, а
ФОРМОЮ посилання.

Чому v2 не міг спрацювати. Він слав `bybitapp://open/p2p/user/home?<param>=`.
У libapp.so з апки, яка стоїть на пристрої (5.21.0), лежить таблиця з 92
декларованих маршрутів у нотації `://шлях{param?,param?}`. Маршруту
`open/p2p/...` там НЕМАЄ взагалі. Тобто перебір імені параметра йшов усередині
форми, якої роутер не розбирає, і жоден варіант не мав шансу — незалежно від
імені. Саме тому щоразу виходила гола MainActivity.

Справжня форма зовнішнього переходу в міні-апку (є в бінарнику готовим рядком):
    bybitapp://open/route?targetUrl=by-mini://<міні-апка>/<шлях>?<params>

Тому тут перебираються ДВІ осі:
  1. форма обгортки (open/route enc / open/route raw / open/web / inapp?by_dp);
  2. ім'я параметра — лише ті, що реально присутні як рядки в libapp.so:
     maskId, targetUserId, makerUserId, userId, accountId, targetAccountId.

Значення — userMaskId. Це не припущення: публічний ендпоінт
api2.bybit.com/fiat/otc/item/online віддає accountId="0" і userId="0" для всіх
оголошень, тобто інших ідентифікаторів у нас фізично немає.

Контролі. `open/home` — маршрут з бінарника, який точно існує: якщо впаде і він,
проблема в стенді, а не в маршрутах. `fiat_otc_order_detail` — декларований
нативний екран ордера; id свідомо несправжній, тому цікавить не вміст, а чи
роутер узагалі його впізнав (екран помилки ордера ≠ головна).

Запуск:
  .\test_bybit_profile_v3.ps1 -MaskId s455c054d9993455d8bef3e2402d21944
#>

param(
    [string]$MaskId  = "s455c054d9993455d8bef3e2402d21944",
    [string]$OrderId = "",
    [int]$WaitMs     = 5000
)

$ErrorActionPreference = "Continue"
$PKG = "com.bybit.app"

function Enc([string]$s) { [uri]::EscapeDataString($s) }

$cases = New-Object System.Collections.ArrayList
function Add-Case($label, $url) {
    [void]$cases.Add([pscustomobject]@{ Label = $label; Url = $url })
}

# ── контроль: маршрут, який точно існує ──────────────────────────────────────
Add-Case "00-control-home" "bybitapp://open/home?tab=1&initialL1Tab=overview"

# ── вісь 1: open/route + by-mini, ім'я параметра перебираємо ─────────────────
foreach ($p in @("maskId", "targetUserId", "makerUserId", "userId", "accountId", "targetAccountId")) {
    $inner = "by-mini://p2p/user/home?${p}=$MaskId"
    Add-Case "route-enc-$p" ("bybitapp://open/route?targetUrl=" + (Enc $inner))
}

# те саме, але без кодування — у бінарнику приклади лежать саме так
Add-Case "route-raw-maskId"       "bybitapp://open/route?targetUrl=by-mini://p2p/user/home?maskId=$MaskId"
Add-Case "route-raw-targetUserId" "bybitapp://open/route?targetUrl=by-mini://p2p/user/home?targetUserId=$MaskId"

# гола міні-апка без параметра: якщо відкриє P2P-хом, значить форма правильна,
# і питання лише в імені параметра. Це розділяє дві причини провалу.
Add-Case "route-bare-userhome" ("bybitapp://open/route?targetUrl=" + (Enc "by-mini://p2p/user/home"))
Add-Case "route-bare-p2phome"  ("bybitapp://open/route?targetUrl=" + (Enc "by-mini://p2p/home"))

# ── вісь 2: https-шлюз ──────────────────────────────────────────────────────
Add-Case "bydp-maskId" ("https://app.bybit.com/inapp?by_dp=" + (Enc "by-mini://p2p/user/home?maskId=$MaskId"))

# ── вісь 3: універсальний webview хоста ─────────────────────────────────────
$webProfile = "https://www.bybit.com/uk-UA/p2p/profile/$MaskId/USDT/UAH/item"
Add-Case "openweb-profile" ("bybitapp://open/web?url=" + (Enc $webProfile) + "&title=" + (Enc "Profile"))

# ── бонус: декларований нативний екран ордера ───────────────────────────────
if (-not $OrderId) { $OrderId = "1234567890123456789" }   # свідомо несправжній
Add-Case "native-order-detail" "bybitapp://open/fiat_otc_order_detail?orderId=$OrderId&sourcePage=deeplink"

# ── прогін ──────────────────────────────────────────────────────────────────
if (-not (adb devices | Select-String -Pattern "device$")) {
    Write-Host "adb не бачить пристрій." -ForegroundColor Red; exit 1
}

$shotDir = Join-Path $PSScriptRoot "shots_bybit_v3"
New-Item -ItemType Directory -Force -Path $shotDir | Out-Null
Get-ChildItem $shotDir -Filter *.png -ErrorAction SilentlyContinue | Remove-Item -Force

$report = Join-Path $PSScriptRoot "bybit_profile_v3.txt"
"# bybit profile v3 :: $(Get-Date -Format s) :: maskId=$MaskId" | Set-Content $report -Encoding utf8

$i = 0
foreach ($c in $cases) {
    $i++
    $name = "{0:d2}_{1}" -f $i, ($c.Label -replace '[^\w-]', '')
    Write-Host "`n[$i/$($cases.Count)] $($c.Label)" -ForegroundColor Cyan
    Write-Host "    $($c.Url)" -ForegroundColor DarkGray

    "`n===== [$i] $($c.Label)" | Add-Content $report -Encoding utf8
    "URL: $($c.Url)"          | Add-Content $report -Encoding utf8

    # холодний старт — інакше зверху лишається екран попереднього кейса
    adb shell am force-stop $PKG 2>&1 | Out-Null
    adb logcat -c 2>&1 | Out-Null
    Start-Sleep -Milliseconds 800

    $raw = adb shell am start -W -a android.intent.action.VIEW `
             -c android.intent.category.BROWSABLE -d "`"$($c.Url)`"" 2>&1 | Out-String
    $raw | Add-Content $report -Encoding utf8

    Start-Sleep -Milliseconds $WaitMs

    # ТІЛЬКИ screencap+pull: exec-out у PowerShell руйнує PNG безповоротно
    $shot = Join-Path $shotDir "$name.png"
    adb shell screencap -p /sdcard/__shot.png 2>&1 | Out-Null
    adb pull /sdcard/__shot.png "$shot" 2>&1 | Out-Null
    adb shell rm -f /sdcard/__shot.png 2>&1 | Out-Null

    $fi = Get-Item $shot -ErrorAction SilentlyContinue
    if ($fi -and $fi.Length -gt 0) {
        $sig = [System.IO.File]::ReadAllBytes($shot)[0..3]
        if ($sig[0] -eq 0x89 -and $sig[1] -eq 0x50) {
            Write-Host "    скріншот ok ($([math]::Round($fi.Length/1kb))kb)" -ForegroundColor Green
        } else { Write-Host "    PNG пошкоджено!" -ForegroundColor Red }
    } else { Write-Host "    скріншот не знявся" -ForegroundColor Red }

    $top = adb shell dumpsys activity activities 2>&1 |
           Select-String -Pattern "topResumedActivity|ResumedActivity" | Select-Object -First 1
    "TOP: $top" | Add-Content $report -Encoding utf8

    # Flutter майже не дає ui-дамп, але деколи семантика є — беремо, якщо є
    adb shell uiautomator dump /sdcard/__ui.xml 2>&1 | Out-Null
    $ui = adb shell cat /sdcard/__ui.xml 2>&1 | Out-String
    adb shell rm -f /sdcard/__ui.xml 2>&1 | Out-Null
    $texts = ([regex]::Matches($ui, 'text="([^"]{2,60})"') |
              ForEach-Object { $_.Groups[1].Value } |
              Where-Object { $_ -notmatch '^\s*$' } |
              Select-Object -Unique -First 20) -join ' | '
    "SCREEN TEXT: $texts" | Add-Content $report -Encoding utf8

    $log = adb logcat -d 2>&1 |
           Select-String -Pattern "by-mini|by_dp|targetUrl|deeplink|DeepLink|route|Router" |
           Select-Object -Last 20
    "ROUTE LOG:" | Add-Content $report -Encoding utf8
    $log | ForEach-Object { "  $_" } | Add-Content $report -Encoding utf8
}

Write-Host "`nГотово." -ForegroundColor Green
Write-Host "Скріншоти: $shotDir"
Write-Host "Звіт:      $report"
