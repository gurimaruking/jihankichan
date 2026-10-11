# ジハンキチャン機 ディスク一括掃除（安全な範囲のみ。録画は最新2日分を残す）
# 実行: powershell -ExecutionPolicy Bypass -File cleanup_disk.ps1   （[admin]項目は管理者で）
param([switch]$Deep)  # -Deep で DISM コンポーネント整理も実行（次回再起動時に TiWorker が数十分CPUを占有するので通常は省略）
$ErrorActionPreference = 'Continue'
function Step($name, $sb) { try { & $sb; Write-Output "OK   $name" } catch { Write-Output "FAIL $name : $($_.Exception.Message)" } }
function FreeGB { [math]::Round((Get-PSDrive C).Free / 1GB, 2) }
Write-Output ("free before: {0} GB" -f (FreeGB))

Step "recordings older than 2 days" {
    $cut = (Get-Date).AddDays(-2)
    Get-ChildItem C:\Users\murat\stackchan_recordings -Filter *.mp4 -File -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTime -lt $cut } | Remove-Item -Force
}
Step "rotated logs (*.log.N) and old logs" {
    Get-ChildItem C:\Users\murat\stackchan_logs -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match '\.log\.\d+$' -or $_.Name -like '*.old.log' } | Remove-Item -Force
}
Step "user temp"        { Get-ChildItem $env:TEMP -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
Step "windows temp [admin]" { Get-ChildItem C:\Windows\Temp -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
Step "recycle bin"      { Clear-RecycleBin -DriveLetter C -Force -ErrorAction Stop }
Step "windows update download cache [admin]" {
    Stop-Service wuauserv -Force -ErrorAction SilentlyContinue
    Get-ChildItem C:\Windows\SoftwareDistribution\Download -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Start-Service wuauserv -ErrorAction SilentlyContinue
}
Step "delivery optimization cache [admin]" { Delete-DeliveryOptimizationCache -Force -ErrorAction Stop }
Step "crash dumps / kernel reports [admin]" {
    Remove-Item C:\Windows\Minidump\* -Force -ErrorAction SilentlyContinue
    Remove-Item C:\Windows\LiveKernelReports\*.dmp -Force -ErrorAction SilentlyContinue
    Remove-Item C:\Windows\MEMORY.DMP -Force -ErrorAction SilentlyContinue
}
Step "thumbnail/icon cache" { Remove-Item "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\*cache*.db" -Force -ErrorAction SilentlyContinue }
Step "old windows (Windows.old) [admin]" { if (Test-Path C:\Windows.old) { takeown /F C:\Windows.old /R /A /D Y | Out-Null; icacls C:\Windows.old /grant Administrators:F /T | Out-Null; Remove-Item C:\Windows.old -Recurse -Force -ErrorAction Stop } else { Write-Output "   (none)" } }
if ($Deep) { Step "component store cleanup (DISM, slow) [admin]" { Dism.exe /Online /Cleanup-Image /StartComponentCleanup /Quiet | Out-Null } } else { Write-Output "SKIP component store cleanup (use -Deep)" }

Write-Output ("free after: {0} GB" -f (FreeGB))
