"""
スタックチャン ウォッチドッグ
メインプロセスがクラッシュした場合に自動で再起動する
"""

import subprocess
import sys
import time
import os
import logging
from datetime import datetime

# ログ設定
LOG_DIR = os.path.join(os.path.expanduser("~"), "stackchan_logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "watchdog.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# pythonw.exeではなくpython.exeを使用（pygameのウィンドウを表示するため）
PYTHON_PATH = os.path.join(os.path.dirname(sys.executable), "python.exe")
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stackchan_full.py")
MAX_RESTARTS = 10  # 短時間での最大再起動回数
RESTART_WINDOW = 300  # 秒（この時間内での再起動回数をカウント）
RESTART_DELAY = 5  # 再起動前の待機秒数


def main():
    logger.info("="*50)
    logger.info("スタックチャン ウォッチドッグ 起動")
    logger.info(f"Python: {PYTHON_PATH}")
    logger.info(f"Script: {SCRIPT_PATH}")
    logger.info("="*50)

    restart_times = []

    while True:
        # 古い再起動記録を削除
        current_time = time.time()
        restart_times = [t for t in restart_times if current_time - t < RESTART_WINDOW]

        # 再起動回数チェック
        if len(restart_times) >= MAX_RESTARTS:
            logger.error(f"{RESTART_WINDOW}秒以内に{MAX_RESTARTS}回以上クラッシュしました。")
            logger.error("ウォッチドッグを停止します。手動で確認してください。")
            break

        logger.info("スタックチャンを起動します...")

        try:
            # メインスクリプトを起動
            process = subprocess.Popen(
                [PYTHON_PATH, SCRIPT_PATH],
                cwd=os.path.dirname(SCRIPT_PATH),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            logger.info(f"プロセス起動 (PID: {process.pid})")

            # プロセス終了を待機
            stdout, stderr = process.communicate()

            exit_code = process.returncode
            logger.warning(f"プロセス終了 (終了コード: {exit_code})")

            if stderr:
                logger.error(f"エラー出力: {stderr.decode('utf-8', errors='replace')}")

            # 正常終了（ESCキーなど）の場合は再起動しない
            if exit_code == 0:
                logger.info("正常終了しました。ウォッチドッグを終了します。")
                break

            # クラッシュの場合
            restart_times.append(time.time())
            logger.info(f"{RESTART_DELAY}秒後に再起動します...")
            time.sleep(RESTART_DELAY)

        except Exception as e:
            logger.error(f"例外が発生しました: {e}")
            restart_times.append(time.time())
            time.sleep(RESTART_DELAY)

    logger.info("ウォッチドッグ終了")


if __name__ == "__main__":
    main()
