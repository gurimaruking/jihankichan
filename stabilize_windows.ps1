# ジハンキチャン機 安定化設定（キオスク運用向け）
# 実行: powershell -ExecutionPolicy Bypass -File stabilize_windows.ps1
# 管理者権限が必要な項目は FAIL になるので、その場合は管理者PowerShellで再実行する
$ErrorActionPreference = 'Continue'

function Step($name, $sb) {
    try { & $sb; Write-Output "OK   $name" }
    catch { Write-Output "FAIL $name : $($_.Exception.Message)" }
}
function Must($cmdOut) { if ($LASTEXITCODE -ne 0) { throw ("" + $cmdOut) } }

# --- 電源: スリープ/画面OFF/休止/ディスクOFFを全部無効（AC電源時） ---
Step "power: never sleep (AC)"       { Must (powercfg /change standby-timeout-ac 0 2>&1) }
Step "power: monitor never off (AC)" { Must (powercfg /change monitor-timeout-ac 0 2>&1) }
Step "power: never hibernate (AC)"   { Must (powercfg /change hibernate-timeout-ac 0 2>&1) }
Step "power: disk never off (AC)"    { Must (powercfg /change disk-timeout-ac 0 2>&1) }
Step "power: high performance plan"  { Must (powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c 2>&1) }
Step "power: hibernate off [admin]"  { Must (powercfg /h off 2>&1) }

# --- 画面を奪うものを止める ---
Step "screensaver off"               { Must (reg add "HKCU\Control Panel\Desktop" /v ScreenSaveActive /t REG_SZ /d 0 /f 2>&1) }
Step "toast notifications off"       { Must (reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\PushNotifications" /v ToastEnabled /t REG_DWORD /d 0 /f 2>&1) }

# --- Windows Update の自動再起動を営業時間外(0-6時)に寄せる: アクティブ時間 6:00-24:00 ---
Step "WU active hours 06-24 [admin]" {
    Must (reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\UX\Settings" /v ActiveHoursStart /t REG_DWORD /d 6 /f 2>&1)
    Must (reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\UX\Settings" /v ActiveHoursEnd   /t REG_DWORD /d 0 /f 2>&1)
}

# --- sshd が固まっても勝手に復帰するように（サービス失敗時の自動再起動 + 自動起動） ---
Step "sshd auto-restart on failure [admin]" {
    Must (sc.exe failure sshd reset= 86400 actions= restart/5000/restart/5000/restart/5000 2>&1)
    Must (sc.exe config sshd start= auto 2>&1)
}

# --- sshd が「TCPは受けるがバナーを返さない」状態で固まる対策: 毎朝4時に再起動（営業時間外） ---
Step "sshd nightly restart task 04:00 [admin]" {
    Must (schtasks /create /tn sshd_nightly_restart /sc daily /st 04:00 /ru SYSTEM /f /tr "powershell -NoProfile -Command Restart-Service sshd" 2>&1)
}

# --- Wi-Fi の省電力を切る（屋外設置で電波が弱いと省電力モードで切れやすい） ---
Step "wifi power-saving off (power plan)" {
    Must (powercfg /setacvalueindex SCHEME_CURRENT 19cbb8fa-5279-450e-9fac-8a3d5fedd0c1 12bbebe6-58d6-4636-95bb-3217ef867c1a 0 2>&1)
    Must (powercfg /setactive SCHEME_CURRENT 2>&1)
}
Step "wifi adapter power management off [admin]" {
    $ads = Get-NetAdapter | Where-Object { $_.Status -ne 'Disabled' -and ($_.PhysicalMediaType -match '802\.11' -or $_.Name -match 'Wi-?Fi') }
    if (-not $ads) { throw "no Wi-Fi adapter found" }
    foreach ($a in $ads) { Disable-NetAdapterPowerManagement -Name $a.Name -ErrorAction Stop }
}
# --- ゲートウェイ不通が続いたら Wi-Fi アダプタを自動再起動（5分ごと） ---
Step "net_selfheal task every 5 min [admin]" {
    Must (schtasks /create /tn net_selfheal /sc minute /mo 5 /ru SYSTEM /f /tr "powershell -NoProfile -ExecutionPolicy Bypass -File C:\stackchan\net_selfheal.ps1" 2>&1)
}

Write-Output "--- active power scheme ---"
powercfg /getactivescheme
