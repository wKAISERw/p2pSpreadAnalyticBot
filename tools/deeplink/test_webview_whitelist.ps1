<#
Експеримент: чи має webview-шлюз Binance білий список доменів?

Гіпотеза. У dex усі без винятку приклади шлюзу містять base64 від адрес на
`www.binance.com`:

    https://www.binance.com/es-AR/my/settings/kyc
    https://www.binance.com/fixedLoan
    https://www.binance.com/{lang}/rewards-hub
    https://www.binance.com/{lang}/square/creatorpad

Хостів `p2p.binance.com` і `c2c.binance.com` у списку https-адрес dex немає
взагалі. Твій успішний тест теж був на www — короткий лінк
`https://www.binance.com/uk-UA/qr/dplk…`.

А бот зараз кладе всередину шлюзу саме `c2c.binance.com/uk-UA/advertiserDetail`.
Якщо білий список існує, застосунок відкине адресу і покаже «Це посилання не
працює» — рівно те, що ти бачиш.

Експеримент розрізняє дві причини:
  A. шлюз відкидає чужий домен          → міняємо хост усередині
  B. шлюз узагалі не приймає довільний url → шлях лише через dplk

Запуск:
    .\test_webview_whitelist.ps1 -MerchantNo <ID_МЕРЧАНТА>
#>

param(
    [string]$MerchantNo = "s40c6bb83ac363d02a2f4cf9bd2f23645",
    [int]$WaitMs = 3500
)

$ErrorActionPreference = "Continue"
$PKG = "com.binance.dev"

function B64([string]$s) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }
function Gate([string]$u) { "https://app.binance.com/webview/webview?type=default&url=" + (B64 $u) }

$cases = @(
    # Контроль: адреса, взята з dex дослівно. Якщо і вона впаде — проблема не
    # в домені, а в самому шлюзі (варіант B).
    @{ label = "control-www-fixedLoan"
       url   = Gate "https://www.binance.com/fixedLoan" },

    # Те, що бот шле зараз.
    @{ label = "current-c2c-advertiserDetail"
       url   = Gate "https://c2c.binance.com/uk-UA/advertiserDetail?advertiserNo=$MerchantNo" },

    # Той самий екран, але на www.
    @{ label = "www-advertiserDetail"
       url   = Gate "https://www.binance.com/uk-UA/advertiserDetail?advertiserNo=$MerchantNo" },

    # p2p-піддомен — щоб знати, чи він теж поза списком.
    @{ label = "p2p-advertiserDetail"
       url   = Gate "https://p2p.binance.com/uk-UA/advertiserDetail?advertiserNo=$MerchantNo" },

    # Варіант шлюзу з needDynamic, який теж зустрічається в dex.
    @{ label = "www-advertiserDetail-dynamic"
       url   = ("https://app.binance.com/webview/webview?type=default&needDynamic=true&url=" +
                (B64 "https://www.binance.com/uk-UA/advertiserDetail?advertiserNo=$MerchantNo")) }
)

if (-not (adb devices | Select-String -Pattern "device$")) {
    Write-Host "adb не бачить пристрій." -ForegroundColor Red; exit 1
}

$shotDir = Join-Path $PSScriptRoot "shots_webview"
New-Item -ItemType Directory -Force -Path $shotDir | Out-Null
$report = Join-Path $PSScriptRoot "webview_report.txt"
"# webview whitelist :: $(Get-Date -Format s)" | Set-Content $report

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

    # Текст на екрані — саме тут буде видно «Це посилання не працює».
    adb shell uiautomator dump /sdcard/__ui.xml 2>&1 | Out-Null
    $ui = adb shell cat /sdcard/__ui.xml 2>&1 | Out-String
    adb shell rm -f /sdcard/__ui.xml 2>&1 | Out-Null
    $texts = ([regex]::Matches($ui, 'text="([^"]{2,80})"') |
              ForEach-Object { $_.Groups[1].Value } |
              Where-Object { $_ -notmatch '^\s*$' } |
              Select-Object -Unique -First 20) -join ' | '
    "SCREEN: $texts" | Add-Content $report

    $bad = ($texts -match "не працює|doesn't work|застаріла|outdated") -or
           ($top -match "NoSupportRouterPath")
    if ($bad) { Write-Host "    ВІДХИЛЕНО" -ForegroundColor Red }
    else      { Write-Host "    прийнято"  -ForegroundColor Green }
    Write-Host "    $texts" -ForegroundColor DarkGray

    adb shell screencap -p /sdcard/__s.png 2>&1 | Out-Null
    adb pull /sdcard/__s.png (Join-Path $shotDir ("{0:d2}_{1}.png" -f $i, $c.label)) 2>&1 | Out-Null
    adb shell rm -f /sdcard/__s.png 2>&1 | Out-Null
}

Write-Host "Report saved to: $report" -ForegroundColor Green
