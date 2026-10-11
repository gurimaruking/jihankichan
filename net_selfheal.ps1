# ネットワーク自己修復（5分ごとにスケジュール実行、SYSTEM）
# ゲートウェイに3回連続で届かなければ Wi‑Fi アダプタを再起動する
$log = "C:\Users\murat\stackchan_logs\net_selfheal.log"
function L($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) }

$gw = (Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop
if (-not $gw) { $gw = "192.168.0.1" }

$ok = Test-Connection -ComputerName $gw -Count 3 -Quiet -ErrorAction SilentlyContinue
if ($ok) { exit 0 }

$ad = Get-NetAdapter -ErrorAction SilentlyContinue |
      Where-Object { $_.Status -ne 'Disabled' -and ($_.PhysicalMediaType -match '802\.11' -or $_.Name -match 'Wi-?Fi') } |
      Select-Object -First 1
if ($ad) {
    L ("gateway {0} unreachable -> restarting adapter '{1}'" -f $gw, $ad.Name)
    try { Restart-NetAdapter -Name $ad.Name -Confirm:$false -ErrorAction Stop; L "adapter restarted" }
    catch { L ("restart failed: {0}" -f $_.Exception.Message) }
} else {
    L ("gateway {0} unreachable but no Wi-Fi adapter found" -f $gw)
}
