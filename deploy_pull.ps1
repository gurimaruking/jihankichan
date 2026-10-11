# ジハンキチャン機で実行する「引き取り型」デプロイ（SSHが使えない時用）
# 使い方（機体の通常PowerShellで）:
#   powershell -ExecutionPolicy Bypass -File $env:TEMP\deploy_pull.ps1 -Base http://192.168.0.47:8765
param([string]$Base = "http://192.168.0.47:8765")
$ErrorActionPreference = 'Stop'
$py = "C:\Users\murat\AppData\Local\Programs\Python\Python312\python.exe"
Set-Location C:\stackchan

foreach ($f in 'stackchan_full.py', 'stackchan_watchdog.py', 'stabilize_windows.ps1', 'net_selfheal.ps1', 'slim_windows.ps1', 'cleanup_disk.ps1') {
    Invoke-WebRequest "$Base/$f" -OutFile "$f.new" -UseBasicParsing
    Move-Item -Force "$f.new" $f
    Write-Output ("downloaded {0} ({1} bytes)" -f $f, (Get-Item $f).Length)
}

& $py -m py_compile C:\stackchan\stackchan_full.py
if ($LASTEXITCODE -ne 0) { throw "stackchan_full.py syntax error - abort" }
Write-Output "syntax OK"

Write-Output "=== stabilize (non-admin parts; [admin] items may FAIL here) ==="
powershell -ExecutionPolicy Bypass -File C:\stackchan\stabilize_windows.ps1

Write-Output "=== slim (non-admin parts) ==="
powershell -ExecutionPolicy Bypass -File C:\stackchan\slim_windows.ps1

Write-Output "=== restart via watchdog ==="
taskkill /im python.exe /f 2>$null | Out-Null
Start-Sleep 2
Start-Process -FilePath $py -ArgumentList "C:\stackchan\stackchan_watchdog.py" -WorkingDirectory "C:\stackchan" -WindowStyle Minimized
Start-Sleep 35

Write-Output "=== /health ==="
try { Invoke-RestMethod http://localhost:8080/health | ConvertTo-Json -Compress } catch { Write-Output "health not ready yet: $($_.Exception.Message)" }
Write-Output "=== last log lines ==="
Get-Content C:\Users\murat\stackchan_logs\stackchan.log -Tail 12
