# ジハンキチャン専用タブレット向け スリム化（不要サービス/スタートアップ/アプリ/視覚効果を止める）
# 実行: powershell -ExecutionPolicy Bypass -File slim_windows.ps1
# 方針: 無効化中心（復元可能）。削除するのは明らかなバンドルアプリのみ。
#       ネットワーク/sshd/音声/カメラ/Windows Update本体には触らない。
# [admin] 印は管理者権限が必要。非昇格だと FAIL になるので管理者PowerShellで再実行する。
$ErrorActionPreference = 'Continue'
$backup = "C:\stackchan\slim_backup_$(Get-Date -Format yyyyMMdd_HHmmss).txt"

function Step($name, $sb) {
    try { & $sb; Write-Output "OK   $name" }
    catch { Write-Output "FAIL $name : $($_.Exception.Message)" }
}

# ---------- 0) 現状バックアップ（何を止めたか後で追えるように） ----------
Step "backup current state -> $backup" {
    "=== services (running) ===" | Out-File $backup -Encoding utf8
    Get-Service | Where-Object Status -eq Running | Select-Object Name,DisplayName,StartType | Format-Table -AutoSize | Out-String -Width 200 | Out-File $backup -Append -Encoding utf8
    "=== startup (HKCU Run) ===" | Out-File $backup -Append -Encoding utf8
    Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue | Out-String | Out-File $backup -Append -Encoding utf8
    "=== startup (HKLM Run) ===" | Out-File $backup -Append -Encoding utf8
    Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue | Out-String | Out-File $backup -Append -Encoding utf8
    "=== appx (user) ===" | Out-File $backup -Append -Encoding utf8
    Get-AppxPackage | Select-Object Name | Out-String | Out-File $backup -Append -Encoding utf8
}

# ---------- 1) スタートアップ整理: stackchan 以外の Run エントリを削除 ----------
Step "startup: remove non-stackchan HKCU Run entries" {
    $k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    if (Test-Path $k) {
        $p = Get-ItemProperty $k
        foreach ($n in ($p.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | Select-Object -ExpandProperty Name)) {
            if ($n -notmatch 'stackchan|jihanki') { Remove-ItemProperty -Path $k -Name $n -ErrorAction SilentlyContinue; Write-Output "   removed HKCU Run: $n" }
        }
    }
}
Step "startup: remove non-stackchan HKLM Run entries [admin]" {
    foreach ($k in 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run') {
        if (Test-Path $k) {
            $p = Get-ItemProperty $k
            foreach ($n in ($p.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | Select-Object -ExpandProperty Name)) {
                if ($n -notmatch 'stackchan|jihanki|SecurityHealth|RtkAudio|Realtek|Intel|Synaptics|ELAN|Wacom|igfx') {
                    Remove-ItemProperty -Path $k -Name $n -ErrorAction Stop; Write-Output "   removed HKLM Run: $n"
                }
            }
        }
    }
}
Step "startup: keep only stackchan.lnk in Startup folder" {
    $sf = [Environment]::GetFolderPath('Startup')
    Get-ChildItem $sf -File | Where-Object { $_.Name -notmatch 'stackchan' } | ForEach-Object { Remove-Item $_.FullName -Force; Write-Output "   removed startup item: $($_.Name)" }
}

# ---------- 2) 不要サービス停止（無効化。ネットワーク/sshd/音声/カメラ/WUは対象外） ----------
$svcOff = @(
    'SysMain',            # Superfetch: 低性能機では逆にディスク/CPUを食う
    'WSearch',            # 検索インデックス
    'DiagTrack',          # テレメトリ
    'dmwappushservice',   # WAP push (テレメトリ関連)
    'XblAuthManager','XblGameSave','XboxNetApiSvc','XboxGipSvc',
    'MapsBroker',         # 地図
    'RetailDemo',
    'WMPNetworkSvc',      # WMP共有
    'Fax',
    'Spooler',            # 印刷スプーラ（プリンタ不要）
    'PhoneSvc',
    'WerSvc',             # エラー報告
    'wisvc',              # Insider
    'edgeupdate','edgeupdatem','MicrosoftEdgeElevationService',
    'GoogleUpdaterService','GoogleUpdaterInternalService','gupdate','gupdatem',
    'AdobeARMservice',
    'OneSyncSvc',
    'lfsvc',              # 位置情報
    'WpnService'          # プッシュ通知
)
Step "services: disable unneeded [admin]" {
    foreach ($s in $svcOff) {
        $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
        if ($svc) {
            try { Stop-Service -Name $s -Force -ErrorAction SilentlyContinue; Set-Service -Name $s -StartupType Disabled -ErrorAction Stop; Write-Output "   disabled: $s" }
            catch { Write-Output "   skip $s : $($_.Exception.Message)" }
        }
    }
}

# ---------- 3) バンドルアプリ削除（ユーザー単位、明らかなものだけ） ----------
$appx = @('Microsoft.XboxApp','Microsoft.XboxGamingOverlay','Microsoft.XboxGameOverlay','Microsoft.XboxIdentityProvider','Microsoft.XboxSpeechToTextOverlay','Microsoft.Xbox.TCUI',
          'Microsoft.ZuneMusic','Microsoft.ZuneVideo','Microsoft.BingNews','Microsoft.BingWeather','Microsoft.GetHelp','Microsoft.Getstarted',
          'Microsoft.MicrosoftOfficeHub','Microsoft.MicrosoftSolitaireCollection','Microsoft.People','Microsoft.SkypeApp','Microsoft.YourPhone',
          'Microsoft.Todos','Microsoft.MixedReality.Portal','Clipchamp.Clipchamp','MSTeams','MicrosoftTeams','Microsoft.549981C3F5F10',
          'Microsoft.WindowsFeedbackHub','Microsoft.Microsoft3DViewer','Microsoft.PowerAutomateDesktop','7EE7776C.LinkedInforWindows',
          'Microsoft.OutlookForWindows','Microsoft.Windows.DevHome','Microsoft.GamingApp','Microsoft.MicrosoftStickyNotes','Microsoft.WindowsMaps',
          'Microsoft.WindowsAlarms','Microsoft.WindowsSoundRecorder','Microsoft.BingSearch','Microsoft.Copilot','Microsoft.Windows.Ai.Copilot.Provider',
          'SpotifyAB.SpotifyMusic','Disney.37853A1F4AD8','Microsoft.WindowsCommunicationsApps','Microsoft.MicrosoftJournal','Microsoft.Whiteboard',
          'Microsoft.MicrosoftPCManager')  # PC Manager: 「ブースト」で他プロセスを強制終了するためキオスクでは危険
Step "appx: remove bundled apps (current user)" {
    foreach ($a in $appx) {
        Get-AppxPackage -Name $a -ErrorAction SilentlyContinue | ForEach-Object { Remove-AppxPackage -Package $_.PackageFullName -ErrorAction SilentlyContinue; Write-Output "   removed: $a" }
    }
}

# ---------- 4) 見た目/背景処理を軽く ----------
Step "visual effects: best performance"  { reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f | Out-Null }
Step "transparency off"                   { reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" /v EnableTransparency /t REG_DWORD /d 0 /f | Out-Null }
Step "animations off"                     { reg add "HKCU\Control Panel\Desktop\WindowMetrics" /v MinAnimate /t REG_SZ /d 0 /f | Out-Null }
Step "background apps off (global)"       { reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications" /v GlobalUserDisabled /t REG_DWORD /d 1 /f | Out-Null }
Step "game bar / game DVR off"            { reg add "HKCU\Software\Microsoft\GameBar" /v AutoGameModeEnabled /t REG_DWORD /d 0 /f | Out-Null; reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f | Out-Null; reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 0 /f | Out-Null }
Step "widgets / news & interests off"     { reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Feeds" /v ShellFeedsTaskbarViewMode /t REG_DWORD /d 2 /f | Out-Null; reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" /v TaskbarDa /t REG_DWORD /d 0 /f | Out-Null }
Step "search highlights / cortana off"    { reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\SearchSettings" /v IsDynamicSearchBoxEnabled /t REG_DWORD /d 0 /f | Out-Null; reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Search" /v CortanaConsent /t REG_DWORD /d 0 /f | Out-Null }
Step "start menu suggestions/tips off"    { foreach ($v in 'SubscribedContent-338388Enabled','SubscribedContent-338389Enabled','SubscribedContent-353694Enabled','SubscribedContent-353696Enabled','SystemPaneSuggestionsEnabled','SoftLandingEnabled') { reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" /v $v /t REG_DWORD /d 0 /f | Out-Null } }

# ---------- 5) Defender: 常時書き込む録画/アプリを除外（リアルタイムスキャンのCPUを減らす） ----------
Step "defender exclusions for stackchan paths/process [admin]" {
    Add-MpPreference -ExclusionPath 'C:\stackchan' -ErrorAction Stop
    Add-MpPreference -ExclusionPath 'C:\Users\murat\stackchan_recordings' -ErrorAction Stop
    Add-MpPreference -ExclusionPath 'C:\Users\murat\stackchan_logs' -ErrorAction Stop
    Add-MpPreference -ExclusionProcess 'C:\Users\murat\AppData\Local\Programs\Python\Python312\python.exe' -ErrorAction Stop
}
Step "delivery optimization: LAN/P2P off [admin]" { reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization" /v DODownloadMode /t REG_DWORD /d 0 /f | Out-Null }

# ---------- 6) OneDrive: 専用機には不要（同期でCPU/回線を食う） ----------
Step "onedrive: stop + uninstall (if present)" {
    Get-Process OneDrive -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $od = @("$env:LOCALAPPDATA\Microsoft\OneDrive\OneDrive.exe", "$env:ProgramFiles\Microsoft OneDrive\OneDrive.exe", "${env:ProgramFiles(x86)}\Microsoft OneDrive\OneDrive.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($od) { Start-Process $od -ArgumentList '/uninstall' -Wait; Write-Output "   uninstalled: $od" } else { Write-Output "   not installed" }
}

Write-Output "--- done. backup: $backup ---"
