<#
Прогонка кандидатів-диплінків через adb.

Телефон має бути підключений, USB-налагодження увімкнене, застосунки встановлені.

Приклад:
  .\test_deeplinks.ps1 -Exchange binance -AdNo 13123456789012345678 -MerchantNo abc123 -OrderNo 22334455667788
  .\test_deeplinks.ps1 -Exchange okx -OrderNo 1234567890

Результат пишеться в results_<exchange>.txt поруч зі скриптом — цей файл я потім прочитаю.
#>

param(
    [ValidateSet("binance", "okx", "bybit")]
    [string]$Exchange = "binance",
    [string]$AdNo      = "PLACEHOLDER_AD",
    [string]$MerchantNo= "PLACEHOLDER_MERCHANT",
    [string]$OrderNo   = "PLACEHOLDER_ORDER"
)

$ErrorActionPreference = "Continue"

# --- кандидати, витягнуті з AndroidManifest + dex ------------------------------

$binance = @(
    # https-хост app.binance.com — єдиний, що реально зареєстрований в манифесті
    "https://app.binance.com/fiat/ads/detail?adNo=$AdNo",
    "https://app.binance.com/fiat/ads/detail?advNo=$AdNo",
    "https://app.binance.com/fiat/ads/detail?advOrderNumber=$AdNo",
    "https://app.binance.com/p2p/advertiserProfile?advertiserNo=$MerchantNo",
    "https://app.binance.com/p2p/userProfile?userNo=$MerchantNo",
    "https://app.binance.com/fiat/merchant/details?merchantNo=$MerchantNo",
    "https://app.binance.com/fiat/merchant/store?merchantNo=$MerchantNo",
    "https://app.binance.com/p2p/orderDetail?orderNo=$OrderNo",
    "https://app.binance.com/fiat/orderDetails?id=$OrderNo",
    "https://app.binance.com/c2c/orderDetails?orderNo=$OrderNo",
    # ті самі маршрути через custom scheme (обхід App Link верифікації)
    "bnc://app.binance.com/fiat/ads/detail?adNo=$AdNo",
    "bnc://app.binance.com/p2p/advertiserProfile?advertiserNo=$MerchantNo",
    "bnc://app.binance.com/p2p/orderDetail?orderNo=$OrderNo",
    # контроль: те, що використовується зараз і НЕ працює
    "https://p2p.binance.com/en/trade/detail/$AdNo"
)

$okx = @(
    "okx://exchange/p2p/order?orderId=$OrderNo",
    "okx://exchange/p2p/order?id=$OrderNo",
    "okx://exchange/p2p/order?orderNo=$OrderNo",
    "okx://exchange/p2p/orders",
    "okx://exchange/p2p/profile?userId=$MerchantNo",
    "okx://exchange/p2p/profile?publicUserId=$MerchantNo",
    "okx://exchange/p2p/trading?type=buy&crypto=USDT",
    # контроль
    "https://www.okx.com/p2p/order/$OrderNo"
)

$bybit = @(
    "bybitapp://open?page=p2pOrderDetail&orderId=$OrderNo",
    "bybitapp://open?page=/p2p/order/$OrderNo",
    "https://app.bybit.com/inapp/p2p/order/$OrderNo",
    "https://app.bybit.com/inapp?page=p2p&orderId=$OrderNo",
    # контроль
    "https://www.bybit.com/uk-UA/p2p/order/$OrderNo"
)

$targets = switch ($Exchange) {
    "binance" { $binance }
    "okx"     { $okx }
    "bybit"   { $bybit }
}

$expectedPkg = switch ($Exchange) {
    "binance" { "com.binance.dev" }
    "okx"     { "com.okinc.okex" }
    "bybit"   { "com.bybit.app" }
}

$outFile = Join-Path $PSScriptRoot "results_$Exchange.txt"
"# deeplink test :: $Exchange :: $(Get-Date -Format s)" | Set-Content $outFile

$devices = adb devices | Select-String -Pattern "device$"
if (-not $devices) {
    Write-Host "adb не бачить пристрій. Перевір USB-налагодження і 'adb devices'." -ForegroundColor Red
    exit 1
}

foreach ($url in $targets) {
    Write-Host "`n>>> $url" -ForegroundColor Cyan
    "`n>>> $url" | Add-Content $outFile

    # закрити застосунок, щоб кожен тест був чистим
    if ($Exchange -eq "okx") {
        adb shell am force-stop com.okinc.okex 2>&1 | Out-Null
        adb shell am force-stop com.okinc.okex.gp 2>&1 | Out-Null
    } else {
        adb shell am force-stop $expectedPkg 2>&1 | Out-Null
    }

    $raw = adb shell am start -W -a android.intent.action.VIEW -c android.intent.category.BROWSABLE -d "`"$url`"" 2>&1 | Out-String
    $raw | Add-Content $outFile

    $comp = ($raw | Select-String -Pattern "cmp=([^\s}]+)").Matches.Value
    if (-not $comp) { $comp = ($raw | Select-String -Pattern "ComponentInfo\{([^}]+)\}").Matches.Value }

    if ($raw -match [regex]::Escape($expectedPkg)) {
        Write-Host "    APP  $comp" -ForegroundColor Green
        "    VERDICT: APP  $comp" | Add-Content $outFile
    } elseif ($raw -match "chrome|browser|resolver|ResolverActivity") {
        Write-Host "    BROWSER/CHOOSER  $comp" -ForegroundColor Yellow
        "    VERDICT: BROWSER-OR-CHOOSER  $comp" | Add-Content $outFile
    } else {
        Write-Host "    ??? $comp" -ForegroundColor DarkGray
        "    VERDICT: UNKNOWN  $comp" | Add-Content $outFile
    }
    Start-Sleep -Milliseconds 900
}

# стан App Link верифікації домену
"`n`n=== pm get-app-links $expectedPkg ===" | Add-Content $outFile
adb shell pm get-app-links $expectedPkg 2>&1 | Out-String | Add-Content $outFile

Write-Host "Done. Result saved to: $outFile" -ForegroundColor Green
