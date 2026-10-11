"""
スタックチャン ウォッチドッグ（改良版 2026-10-03: 多重起動防止）
- 名前付きミューテックスで watchdog の二重起動を防ぐ（スタートアップ+Runキー+手動起動が重なっても1つだけ動く）
- 起動時に孤児の stackchan_full.py を掃除。本体が終了コード75（別インスタンスが稼働中）なら孤児を消して再試行
- メインプロセス(stackchan_full.py)が落ちたら自動再起動。諦めない
- 連続クラッシュ時は待ち時間を 5→10→20→40→60秒 と伸ばし、2分以上安定稼働したらリセット
- 子プロセスの stderr 末尾をログに残す（クラッシュ原因が追える）
- ログはローテーション（2MB x 3）
- 正常終了（終了コード0 = ESCキー等）の時だけ停止する
"""

import subprocess
import sys
import time
import os
import logging
import logging.handlers
import ctypes

LOG_DIR = os.path.join(os.path.expanduser("~"), "stackchan_logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "watchdog.log"),
            maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)

# pythonw.exe ではなく python.exe（pygame の表示に必要）
PYTHON_PATH = os.path.join(os.path.dirname(sys.executable), "python.exe")
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stackchan_full.py")
STABLE_RUN_SEC = 120   # これ以上動いたら「安定起動」とみなしバックオフをリセット
BACKOFF_MIN = 5
BACKOFF_MAX = 60


_MUTEX_HANDLE = None  # プロセス存続中は参照を保持する


def acquire_single_instance(name):
    """Windows 名前付きミューテックス。他に同名の保持者がいれば False"""
    global _MUTEX_HANDLE
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        h = k32.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
        if h and err == 183:   # ERROR_ALREADY_EXISTS
            k32.CloseHandle(ctypes.c_void_p(h))
            return False
        _MUTEX_HANDLE = h
        return True
    except Exception:
        return True   # 判定できない時は起動を妨げない


def kill_orphan_apps():
    """このwatchdogが唯一の見張り役なので、他に動いている stackchan_full.py は全て孤児として終了させる"""
    try:
        import psutil
        me = os.getpid()
        for p in psutil.process_iter(['pid', 'cmdline']):
            cl = p.info.get('cmdline') or []
            if p.info['pid'] != me and any(str(a).lower().endswith('stackchan_full.py') for a in cl):
                logger.warning(f"killing orphan app PID {p.info['pid']}")
                try:
                    p.kill()
                except Exception as e:
                    logger.error(f"kill failed: {e}")
    except Exception as e:
        logger.error(f"orphan cleanup error: {e}")


def main():
    if not acquire_single_instance("Local\\JihankichanWatchdog"):
        logger.warning("another watchdog is already running - exiting this duplicate")
        return
    kill_orphan_apps()
    logger.info("=" * 50)
    logger.info("watchdog start")
    logger.info(f"Python: {PYTHON_PATH}")
    logger.info(f"Script: {SCRIPT_PATH}")
    logger.info("=" * 50)

    backoff = BACKOFF_MIN
    while True:
        started = time.time()
        try:
            proc = subprocess.Popen(
                [PYTHON_PATH, SCRIPT_PATH],
                cwd=os.path.dirname(SCRIPT_PATH),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=0x08000000,   # CREATE_NO_WINDOW: コンソール窓を作らない
            )
            logger.info(f"child started (PID {proc.pid})")
            _, stderr = proc.communicate()
            code = proc.returncode
            ran = time.time() - started
            logger.warning(f"child exited code={code} after {ran:.0f}s")

            if stderr:
                tail = stderr.decode('utf-8', errors='replace').strip().splitlines()[-15:]
                for line in tail:
                    logger.error("child stderr: " + line)

            # キオスク運用: 終了コード0でも再起動する。止めたい時は STOP_WATCHDOG ファイルを置く
            if os.path.exists(os.path.join(os.path.dirname(SCRIPT_PATH), "STOP_WATCHDOG")):
                logger.info("STOP_WATCHDOG flag found - watchdog stops")
                break
            if code == 75:
                # 本体が「別インスタンスが稼働中」で終了。孤児を消して短い待ちで再試行（クラッシュ扱いにしない）
                logger.warning("child refused to start: another app instance holds the lock - cleaning orphans")
                kill_orphan_apps()
                time.sleep(5)
                continue
            if code == 0:
                logger.warning("child exited with code 0 (window closed / ESC?) - restarting anyway")

            if ran >= STABLE_RUN_SEC:
                backoff = BACKOFF_MIN
            else:
                backoff = min(BACKOFF_MAX, backoff * 2)

        except Exception as e:
            logger.error(f"launch error: {e}")
            backoff = min(BACKOFF_MAX, backoff * 2)

        logger.info(f"restart in {backoff}s")
        time.sleep(backoff)


if __name__ == "__main__":
    main()
