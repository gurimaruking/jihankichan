"""
ジハンキチャン - UIAPduino Pro Micro自販機用スクリプト
- オリジナルstack-chanに近い顔アニメーション
- カメラ映像をWebサーバーで配信（http://localhost:8080）
- 3日分の録画をローカル保存
- 顔認識で「いらっしゃいませー」と購入案内
- 待機時は製品紹介スライドショー
- 10分に1回秋葉原ニュースを喋る
"""

import os as _os_early
# 全画面ウィンドウは既定でフォーカスを失うと最小化される(SDL)。キオスク用途では他の窓が前に出ても最小化させない
_os_early.environ.setdefault("SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", "0")
import pygame
import math
import random
import threading
import cv2
import numpy as np
import os
import sys
import time
from datetime import datetime, timedelta
from flask import Flask, Response, jsonify
import logging
import logging.handlers
import ctypes

# Windowsタスクバー非表示用 & pygame最前面強制用
try:
    user32 = ctypes.windll.user32
    def hide_taskbar():
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
    def show_taskbar():
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW

    # Windows のフォアグラウンドロック対策のため Altキートリックを使う
    def _send_alt_key():
        """Altキーを瞬間的に押下→離す。これでフォアグラウンドロックが一時解除される"""
        try:
            VK_MENU = 0x12  # Alt
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

    def _disable_foreground_lock():
        """ForegroundLockTimeout を 0 にして、フォアグラウンドロックを無効化"""
        try:
            SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
            SPIF_SENDCHANGE = 0x0002
            user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, 0, SPIF_SENDCHANGE)
        except Exception:
            pass

    def bring_pygame_to_front():
        """pygame ウィンドウをデスクトップ最前面に強制する（キオスク用、強化版）"""
        try:
            import pygame as _pg
            info = _pg.display.get_wm_info()
            hwnd = info.get('window')
            if not hwnd:
                return False

            HWND_TOPMOST = -1
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_SHOWWINDOW = 0x0040
            SW_SHOW = 5
            SW_RESTORE = 9

            # 1. フォアグラウンドロックを無効化
            _disable_foreground_lock()

            # 2. 最小化されてたら復元 + 表示
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_SHOW)

            # 3. Altキートリックでフォアグラウンドロックを解除
            _send_alt_key()

            # 4. TOPMOSTで最前面に固定
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)

            # 5. AllowSetForegroundWindow → SetForegroundWindow（フォーカス奪取）
            try:
                ASFW_ANY = 0xFFFFFFFF
                user32.AllowSetForegroundWindow(ASFW_ANY)
            except Exception:
                pass
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    def start_window_keeper(hwnd, interval=3.0):
        """ゲーム画面が最小化/非表示になったら数秒以内に復元する専用スレッド。pygame には触れず user32 のみ使う"""
        def _loop():
            last_log = 0.0
            while True:
                time.sleep(interval)
                try:
                    if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
                        if time.time() - last_log > 60:
                            logger.warning("Game window minimized/hidden - restoring")
                            last_log = time.time()
                        _disable_foreground_lock()
                        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                        user32.ShowWindow(hwnd, 5)   # SW_SHOW
                        _send_alt_key()
                        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                        user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
        threading.Thread(target=_loop, daemon=True, name="window-keeper").start()
except Exception:
    def hide_taskbar(): pass
    def show_taskbar(): pass
    def bring_pygame_to_front(): return False
    def start_window_keeper(hwnd, interval=3.0): pass

# Claude API
try:
    import anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False

# TTS（Windows SAPI）
try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False

# Discord通知用
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ログ設定
LOG_DIR = os.path.join(os.path.expanduser("~"), "stackchan_logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(os.path.join(LOG_DIR, "stackchan.log"),
                                             maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)

# Flask設定
app = Flask(__name__)
flask_log = logging.getLogger('werkzeug')
flask_log.setLevel(logging.ERROR)

# グローバル変数
current_frame = None
frame_lock = threading.Lock()
# /health で返す稼働状態
HEALTH = {"start": time.time(), "last_frame": 0.0, "frames": 0, "last_det_ms": 0.0,
          "detections": 0, "camera_reopens": 0, "discord_sent": 0, "last_face": 0.0,
          "passersby_today": 0, "visitors_today": 0, "tracks": 0, "motion_cells": 0}
camera_index = 0

# 録画設定
RECORDING_DIR = os.path.join(os.path.expanduser("~"), "stackchan_recordings")
RETENTION_DAYS = int(os.environ.get("JIHANKICHAN_RETENTION_DAYS", "1"))   # 常時録画の保持日数(防犯用: 1日分)
# 空き容量がこれを下回ったら古い録画から消す（GB）。復帰目標は MIN_FREE_GB + 2
MIN_FREE_GB = float(os.environ.get("JIHANKICHAN_MIN_FREE_GB", "4"))
SEGMENT_MINUTES = 10

# 基本サイズ
BASE_WIDTH = 320
BASE_HEIGHT = 240

# 色定義
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (128, 128, 128)
YELLOW = (255, 220, 50)
ORANGE = (255, 165, 0)
CYAN = (100, 220, 255)
RED = (255, 50, 50)
DARK_RED = (180, 30, 30)

# Claude API設定
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("JIHANKICHAN_CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# ニュース間隔（秒）
NEWS_INTERVAL = 600  # 10分

# 製品情報（複数商品を交互にメイン表示）
# image_dir: images/ 下のサブディレクトリ名（商品ごとに画像を分けて混同を防ぐ）
PRODUCTS = [
    {"name": "UIAPduino Pro Micro CH32V003", "price": "300円", "image_dir": "uiapduino"},
    {"name": "Suittyo-On CH32X035", "price": "500円", "image_dir": "suittyo-on"},
]
# スライドショー時の商品切替間隔（秒）
PRODUCT_SWITCH_INTERVAL = 30
# 後方互換のためのエイリアス（古いコードが参照する場合に備えて）
PRODUCT_NAME = PRODUCTS[0]["name"]
PRODUCT_PRICE = PRODUCTS[0]["price"]
PRODUCT_IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")

# 売り切れモード（True: 売り切れ表示、False: 通常販売）
SOLD_OUT = False

# スライドショー設定
SLIDESHOW_INTERVAL = 5  # 5秒ごとに切り替え

# 夜間ミュート設定（23時〜8時は音声を出さない）
NIGHT_START_HOUR = 23  # 夜間開始（23時）
NIGHT_END_HOUR = 8     # 夜間終了（8時）

# Discord Webhook設定 (公開リポジトリのため URL はコードに書かない)
#  優先順: 環境変数 JIHANKICHAN_DISCORD_WEBHOOK → スクリプトと同じフォルダの discord_webhook.txt (git管理外)
def _load_discord_webhook():
    url = os.environ.get("JIHANKICHAN_DISCORD_WEBHOOK", "").strip()
    if url:
        return url
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_webhook.txt")
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


DISCORD_WEBHOOK_URL = _load_discord_webhook()
logger.info("Discord webhook: " + ("configured" if DISCORD_WEBHOOK_URL else "NOT configured (notifications disabled)"))

# Discord通知用の顔サイズ閾値（pixel、検出された顔の短辺で判定）
# 静止物検知ロジックで誤検知を弾けるようになったので、しきい値はあまり厳しくしないでOK
# (2026-05-15 遠い人の通知が多めだったため 150→200 へ調整)
DISCORD_FACE_MIN_SIZE = int(os.environ.get("JIHANKICHAN_DISCORD_FACE_MIN_SIZE", "200"))

# 静止物検知: 直近 STATIC_WINDOW_SEC 秒以内に同じ座標(±STATIC_TOL_PX)で検出された顔は
# 朝の光線パターンや看板等の静止物として扱い、Discord通知をスキップ
# (2026-05-22: 30分→24時間に拡張。固定誤検知パターンが30分ごとに再発火する症状を防ぐため)
STATIC_TOL_PX = int(os.environ.get("JIHANKICHAN_STATIC_TOL_PX", "30"))
STATIC_WINDOW_SEC = int(os.environ.get("JIHANKICHAN_STATIC_WINDOW_SEC", "86400"))

# 挨拶を発火する最小顔サイズ(px)。YuNetは9pxのような極小ブロブも拾うので足切り
GREETING_FACE_MIN_SIZE = int(os.environ.get("JIHANKICHAN_GREETING_FACE_MIN_SIZE", "40"))
# 録画は N フレームに1回だけ書く（15fps化でディスクが倍増するのを防ぐ。writer fpsも連動）
RECORD_EVERY_N_FRAMES = max(1, int(os.environ.get("JIHANKICHAN_RECORD_EVERY_N", "4")))  # 2026-09-28: 実効25fps化で録画が倍増したため 2→4（約6fps、~4.5GB/3日）

# ---- 来訪者クリップ(前後N秒を動画化して Discord へ) ----
CLIP_ENABLED = os.environ.get("JIHANKICHAN_CLIP_ENABLED", "1") not in ("0", "false", "False")
CLIP_PRE_SEC = float(os.environ.get("JIHANKICHAN_CLIP_PRE_SEC", "30"))
CLIP_POST_SEC = float(os.environ.get("JIHANKICHAN_CLIP_POST_SEC", "30"))
CLIP_MAX_SEC = float(os.environ.get("JIHANKICHAN_CLIP_MAX_SEC", "150"))      # 重なる来訪を1本にまとめる最長(検知起点から)
CLIP_FPS = float(os.environ.get("JIHANKICHAN_CLIP_FPS", "5"))
CLIP_JPEG_Q = int(os.environ.get("JIHANKICHAN_CLIP_JPEG_Q", "72"))
CLIP_MAX_UPLOAD_BYTES = int(float(os.environ.get("JIHANKICHAN_CLIP_MAX_UPLOAD_MB", "9.5")) * 1e6)   # Discord の上限(10MB)未満
CLIP_KEEP_DAYS = int(os.environ.get("JIHANKICHAN_CLIP_KEEP_DAYS", "3"))
CLIP_DIR = os.path.join(os.path.expanduser("~"), "stackchan_clips")
CLIP_TEST_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clip_test.flag")

# 顔検出に使う縮小率（0.5 = 320x240で検出 → 約4倍速。座標は元解像度に戻す）
DETECT_SCALE = float(os.environ.get("JIHANKICHAN_DETECT_SCALE", "0.4"))
# OpenCV(DNN)のスレッド数を制限（全コアを食い潰して sshd/Flask が飢えるのを防ぐ）
try:
    cv2.setNumThreads(int(os.environ.get("JIHANKICHAN_CV_THREADS", "2")))
except Exception:
    pass

# 顔検出を何フレームに1回走らせるか（小さいほど反応が速い。YuNetは軽いので2で十分）
FACE_CHECK_EVERY_N_FRAMES = max(1, int(os.environ.get("JIHANKICHAN_FACE_CHECK_EVERY_N", "4")))
# ---- CPU負荷の調整 (低性能 Atom x7-Z8750 向け。2026-10-03 のスレッド別プロファイルに基づく) ----
DECODE_FPS = float(os.environ.get("JIHANKICHAN_DECODE_FPS", "12"))        # カメラからデコードして使うコマ数/秒 (他は grab のみ)
RECORD_FPS = float(os.environ.get("JIHANKICHAN_RECORD_FPS", "5"))         # 録画fps。mp4vエンコードは6fpsで約60%/コア
DETECT_RATE_HZ = float(os.environ.get("JIHANKICHAN_DETECT_RATE_HZ", "6"))  # 検出器へ渡すコマ数/秒の上限
UI_FPS_IDLE = int(os.environ.get("JIHANKICHAN_UI_FPS_IDLE", "10"))        # 待機(スライドショー)時の描画fps
UI_FPS_SPEAKING = int(os.environ.get("JIHANKICHAN_UI_FPS_SPEAKING", "24"))  # 発話/字幕表示中の描画fps
STREAM_FPS = float(os.environ.get("JIHANKICHAN_STREAM_FPS", "10"))        # MJPEG配信の上限fps

# Discord通知は「短時間に同じ場所で N 回連続検出」で確定（1フレームだけの誤検知を排除）
DISCORD_CONFIRM_HITS = max(1, int(os.environ.get("JIHANKICHAN_DISCORD_CONFIRM_HITS", "2")))
DISCORD_CONFIRM_WINDOW_SEC = float(os.environ.get("JIHANKICHAN_DISCORD_CONFIRM_WINDOW_SEC", "2.5"))
DISCORD_CONFIRM_TOL_PX = int(os.environ.get("JIHANKICHAN_DISCORD_CONFIRM_TOL_PX", "80"))

# TTSの出力先: この文字列を含む再生デバイスがあればそれを使う（Bluetoothスピーカー優先）。空=Windows既定
TTS_OUTPUT_MATCH = os.environ.get("JIHANKICHAN_TTS_OUTPUT_MATCH", "A36S")

# ---- お客さん検出 v4 (動き検出ゲート + トラッキング) ----
DET_W, DET_H = 320, 240                      # 検出器の入力は常にこのサイズ(動き周辺を等倍切り出し or 全体0.5倍)
MOTION_THRESH = int(os.environ.get("JIHANKICHAN_MOTION_THRESH", "16"))        # 背景差分のしきい値(0-255)
MOTION_MIN_CELLS = int(os.environ.get("JIHANKICHAN_MOTION_MIN_CELLS", "25"))  # 160x120上で動き画素がこれ未満なら無視
MOTION_BG_ALPHA = float(os.environ.get("JIHANKICHAN_MOTION_BG_ALPHA", "0.04"))  # 背景の追従速度(大きいほど静止物が早く背景化)
MOTION_HOLD_SEC = float(os.environ.get("JIHANKICHAN_MOTION_HOLD_SEC", "2.5"))  # 動きが止まってもこの間は検出を続ける
MOTION_ELIGIBLE_FRAC = float(os.environ.get("JIHANKICHAN_MOTION_ELIGIBLE_FRAC", "0.04"))  # 顔枠のうち動き領域が占める最小割合
TRACK_TTL_SEC = float(os.environ.get("JIHANKICHAN_TRACK_TTL_SEC", "6.0"))      # 検出が途切れても追跡を保つ秒数
RECENT_MEMORY_SEC = float(os.environ.get("JIHANKICHAN_RECENT_MEMORY_SEC", "60"))   # 消えた追跡を覚えておく秒数
RECENT_MATCH_PX = float(os.environ.get("JIHANKICHAN_RECENT_MATCH_PX", "170"))      # 再出現とみなす最大距離(px)
GREET_COOLDOWN_SEC = float(os.environ.get("JIHANKICHAN_GREET_COOLDOWN_SEC", "90"))  # 続けて挨拶しない秒数
WALK_MATCH_PX = float(os.environ.get("JIHANKICHAN_WALK_MATCH_PX", "260"))          # 歩いている人を同じ追跡に当てる最大距離(px)
COUNT_SUPPRESS_SEC = float(os.environ.get("JIHANKICHAN_COUNT_SUPPRESS_SEC", "12")) # この秒数以内に近くで数えたら二重加算しない
COUNT_SUPPRESS_PX = float(os.environ.get("JIHANKICHAN_COUNT_SUPPRESS_PX", "230"))
GREET_MIN_HITS = int(os.environ.get("JIHANKICHAN_GREET_MIN_HITS", "2"))       # 挨拶に必要な検出回数
TRACK_CONFIRM_HITS = int(os.environ.get("JIHANKICHAN_TRACK_CONFIRM_HITS", "3"))  # 通行人として数えるのに必要な検出回数
TRACK_MIN_SPAN_SEC = float(os.environ.get("JIHANKICHAN_TRACK_MIN_SPAN_SEC", "0.4"))
TRACK_MIN_MEAN_CONF = float(os.environ.get("JIHANKICHAN_TRACK_MIN_MEAN_CONF", "0.62"))
ENGAGE_SIZE = int(os.environ.get("JIHANKICHAN_ENGAGE_SIZE", "170"))            # 顔がこのpx以上(カメラのすぐ前)=来訪者
ENGAGE_DWELL_SEC = float(os.environ.get("JIHANKICHAN_ENGAGE_DWELL_SEC", "3.0"))  # この秒数以上、同じ場所にとどまった人=来訪者
DISCORD_MIN_INTERVAL_SEC = float(os.environ.get("JIHANKICHAN_DISCORD_MIN_INTERVAL_SEC", "20"))
VISIT_SNAPSHOT_KEEP = int(os.environ.get("JIHANKICHAN_VISIT_SNAPSHOT_KEEP", "300"))

# YuNet 信頼度スコア最小値（Discord通知時に要求する確度。0.0–1.0、高いほど厳格）
DISCORD_CONF_MIN = float(os.environ.get("JIHANKICHAN_DISCORD_CONF_MIN", "0.85"))
# 顔検出器のscore閾値（これ以上の確度のみ「検出」とみなす）
DETECT_SCORE_THRESHOLD = float(os.environ.get("JIHANKICHAN_DETECT_SCORE_THRESHOLD", "0.6"))
# YuNet モデル所在
YUNET_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "models", "face_detection_yunet_2023mar.onnx"
)

# カメラ露出・明るさ設定（env varで上書き可、空文字列ならカメラ任せ）
# 注: 現状の内蔵カメラ(2026-05-12調査)はOpenCV経由のプロパティ設定を一切無視するため
# デフォルトは空文字列(=設定スキップ)。外付けカメラに変えた場合は値を指定すれば効くかも
# AUTO_EXPOSURE: 0.25=手動 / 0.75=自動 (DSHOW backend、カメラ依存)
# EXPOSURE: log2 seconds. -6=1/64s, -7=1/128s, -8=1/256s（小さいほど暗い）
CAMERA_AUTO_EXPOSURE = os.environ.get("JIHANKICHAN_CAMERA_AUTO_EXPOSURE", "")
CAMERA_EXPOSURE = os.environ.get("JIHANKICHAN_CAMERA_EXPOSURE", "")
CAMERA_BRIGHTNESS = os.environ.get("JIHANKICHAN_CAMERA_BRIGHTNESS", "")
CAMERA_GAIN = os.environ.get("JIHANKICHAN_CAMERA_GAIN", "")


_APP_MUTEX_HANDLE = None  # プロセス存続中は参照を保持する


def acquire_single_instance(name):
    """Windows 名前付きミューテックスで多重起動を防ぐ。取得できたら True
    (2026-10-03: 9/29にスタートアップとRunキーが重なり3系統が同時稼働→録画破損/CPU飽和した対策)"""
    global _APP_MUTEX_HANDLE
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        h = k32.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
        if h and err == 183:   # ERROR_ALREADY_EXISTS
            k32.CloseHandle(ctypes.c_void_p(h))
            return False
        _APP_MUTEX_HANDLE = h
        return True
    except Exception:
        return True


def set_system_volume_max():
    """Windowsシステム音量を100%にセット + ミュート解除（pycaw経由）"""
    try:
        from pycaw.pycaw import AudioUtilities
        speakers = AudioUtilities.GetSpeakers()
        # 新版pycaw (20251023+) は EndpointVolume プロパティを直接持つ
        # 旧版互換のため両対応
        if hasattr(speakers, "EndpointVolume"):
            vol = speakers.EndpointVolume
        else:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = cast(interface, POINTER(IAudioEndpointVolume))
        vol.SetMasterVolumeLevelScalar(1.0, None)
        vol.SetMute(0, None)
        logger.info("System volume: set to 100% and unmuted")
        return True
    except ImportError:
        logger.warning("pycaw not installed - skipping system volume control (pip install pycaw)")
        return False
    except Exception as e:
        logger.error(f"Failed to set system volume: {e}")
        return False


def is_night_time():
    """現在が夜間（ミュート時間）かどうかを判定"""
    current_hour = datetime.now().hour
    if NIGHT_START_HOUR > NIGHT_END_HOUR:
        # 23時〜8時のように日をまたぐ場合
        return current_hour >= NIGHT_START_HOUR or current_hour < NIGHT_END_HOUR
    else:
        # 同じ日の場合
        return NIGHT_START_HOUR <= current_hour < NIGHT_END_HOUR


def send_discord_notification(frame, face_count=1, box=None, conf=None):
    """Discord Webhookに顔検出通知を画像付きで送信（別スレッドで呼ぶこと）
    白飛び対策でCLAHEをかけ、検出枠と信頼度を描いて「本当に人か」を判断しやすくする"""
    if not REQUESTS_AVAILABLE or not DISCORD_WEBHOOK_URL:
        return
    try:
        img = frame
        try:
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
            img = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        except Exception:
            pass
        if box is not None:
            x, y, w, h = [int(v) for v in box]
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            label = f"{conf:.2f}" if conf is not None else "face"
            cv2.putText(img, label, (x, max(15, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        ret, buffer = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ret:
            logger.error("Discord: failed to encode frame")
            return

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        filename = f'visitor_{now.strftime("%Y%m%d_%H%M%S")}.jpg'
        face_label = f"（顔 {face_count} 件）" if face_count > 1 else ""
        conf_label = f" 確度{conf:.2f}" if conf is not None else ""
        content = f"👀 お客さんが来ました！{face_label}{conf_label}\n🕐 {timestamp}"

        files = {'file': (filename, buffer.tobytes(), 'image/jpeg')}
        data = {'content': content}
        response = requests.post(DISCORD_WEBHOOK_URL, data=data, files=files, timeout=10)
        if response.status_code in (200, 204):
            HEALTH["discord_sent"] += 1
            logger.info(f"Discord notification sent ({face_count} faces)")
        else:
            logger.warning(f"Discord notification failed: HTTP {response.status_code} {response.text[:200]}")
    except Exception as e:
        logger.error(f"Discord notification error: {e}")


class Emotion:
    NEUTRAL = 'NEUTRAL'
    HAPPY = 'HAPPY'
    SAD = 'SAD'
    ANGRY = 'ANGRY'
    SLEEPY = 'SLEEPY'
    SURPRISED = 'SURPRISED'


def norm_rand(mean=0, std=1):
    a = 1 - random.random()
    b = 1 - random.random()
    c = math.sqrt(-2 * math.log(a))
    if random.random() > 0.5:
        return c * math.sin(math.pi * 2 * b) * std + mean
    return c * math.cos(math.pi * 2 * b) * std + mean


def linear_in_ease_out(fraction):
    if fraction < 0.25:
        return 1 - fraction * 4
    return ((fraction - 0.25) ** 2 * 16) / 9


def quantize(value, steps):
    return round(value * steps) / steps


class StackchanFace:
    def __init__(self, screen_width, screen_height):
        self.update_size(screen_width, screen_height)
        self.left_eye_open = 1.0
        self.right_eye_open = 1.0
        self.left_gaze_x = 0.0
        self.left_gaze_y = 0.0
        self.right_gaze_x = 0.0
        self.right_gaze_y = 0.0
        self.mouth_open = 0.0
        self.breath = 0.0
        self.breath_time = 0
        self.emotion = Emotion.NEUTRAL
        self.is_blinking = False
        self.blink_count = 0
        self.next_blink_toggle = random.uniform(0.4, 5.0)
        self.saccade_time = 0
        self.next_saccade = random.uniform(0.3, 2.0)
        self.target_gaze_x = 0
        self.target_gaze_y = 0
        self.is_speaking = False
        self.status_text = ""

        # スライドショー用
        self.show_slideshow = True
        # 商品ごとの画像リスト（PRODUCTS と同じ並び）
        self.product_images = [[] for _ in PRODUCTS]
        # 後方互換: 現在表示中商品の画像（_draw_slideshow が参照）
        self.slideshow_images = []
        self.current_slide = 0
        self.slideshow_timer = 0

        # メイン表示商品（複数商品を時間で切替）
        self.current_product_idx = 0
        self.product_switch_timer = 0

        # フォントを一元管理（BIZ UDゴシック優先）
        self._init_fonts()
        self._load_slideshow_images()

    def _find_font(self, size, bold=False):
        """読みやすい日本語フォントを優先順位で探す"""
        font_candidates = [
            'bizudgothic',
            'bizudpgothic',
            'meiryo',
            'yugothicmedium',
            'yugothic',
            'msgothic',
        ]
        for name in font_candidates:
            try:
                f = pygame.font.SysFont(name, size, bold=bold)
                if f:
                    return f
            except Exception:
                continue
        return pygame.font.Font(None, size)

    def _init_fonts(self):
        """全フォントを画面高さベースで初期化（大型表示版）"""
        h = self.screen_height
        self.font_product_name = self._find_font(max(36, int(h * 0.055)), bold=True)
        self.font_price = self._find_font(max(80, int(h * 0.16)), bold=True)
        self.font_guide = self._find_font(max(30, int(h * 0.045)))
        self.font_case_return = self._find_font(max(28, int(h * 0.04)), bold=True)
        self.font_subtitle = self._find_font(max(30, int(h * 0.05)))
        self.font_slide_indicator = self._find_font(max(12, int(h * 0.015)))
        # 売り切れ表示用
        self.font_sold_out = self._find_font(max(80, int(h * 0.14)), bold=True)
        self.font_sold_out_sub = self._find_font(max(36, int(h * 0.05)), bold=True)

    def _load_slideshow_images(self):
        """商品ごとに images/<image_dir>/ から画像を読み込む。
        サブディレクトリが無く images/ 直下に画像がある旧構造の場合は、
        その全画像を1つ目の商品(UIAPduino)に割り当てる後方互換動作。"""
        try:
            from PIL import Image

            valid_ext = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
            max_w = int(self.screen_width * 0.80)
            max_h = int(self.screen_height * 0.30)

            def load_dir(dirpath):
                images = []
                if not os.path.isdir(dirpath):
                    return images
                filenames = sorted([
                    f for f in os.listdir(dirpath)
                    if f.lower().endswith(valid_ext)
                    and os.path.isfile(os.path.join(dirpath, f))
                ])
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        pil_img = Image.open(filepath)
                        pil_img = pil_img.convert('RGB')
                        pil_img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                        pygame_img = pygame.image.fromstring(
                            pil_img.tobytes(), pil_img.size, pil_img.mode)
                        images.append(pygame_img)
                        logger.info(f"Loaded image: {os.path.basename(dirpath)}/{filename} "
                                    f"({pil_img.size[0]}x{pil_img.size[1]})")
                    except Exception as e:
                        logger.error(f"Failed to load {filename}: {e}")
                return images

            # 商品ごとにサブディレクトリから読み込み
            for idx, product in enumerate(PRODUCTS):
                sub = product.get("image_dir", "")
                subdir = os.path.join(PRODUCT_IMAGES_DIR, sub) if sub else None
                if subdir and os.path.isdir(subdir):
                    self.product_images[idx] = load_dir(subdir)
                else:
                    self.product_images[idx] = []
                logger.info(f"Product '{product['name']}': {len(self.product_images[idx])} images")

            # 後方互換: サブディレクトリが空 かつ images/直下に画像があるなら、
            # それをUIAPduino(idx=0)用と見なして割り当てる（移行期間のための救済処理）
            if (self.product_images and not self.product_images[0]
                    and os.path.isdir(PRODUCT_IMAGES_DIR)):
                root_images = load_dir(PRODUCT_IMAGES_DIR)
                if root_images:
                    self.product_images[0] = root_images
                    logger.info(f"Legacy fallback: assigned {len(root_images)} root images to product 0")

            # 起動時は最初の商品の画像をアクティブに
            self.slideshow_images = self.product_images[self.current_product_idx]
            total = sum(len(imgs) for imgs in self.product_images)
            logger.info(f"Slideshow ready: {total} images across {len(PRODUCTS)} products")

        except Exception as e:
            logger.error(f"Failed to load slideshow images: {e}")

    def update_size(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.scale = min(screen_width / BASE_WIDTH, screen_height / BASE_HEIGHT)
        self.center_x = screen_width // 2
        self.center_y = screen_height // 2
        self.eye_radius = int(8 * self.scale)
        self.eye_spacing = int(140 * self.scale)
        self.left_eye_x = self.center_x - int(70 * self.scale)
        self.right_eye_x = self.center_x + int(70 * self.scale)
        self.eye_y = self.center_y - int(15 * self.scale)
        self.eyelid_width = int(30 * self.scale)
        self.eyelid_height = int(25 * self.scale)
        self.mouth_min_width = int(50 * self.scale)
        self.mouth_max_width = int(90 * self.scale)
        self.mouth_min_height = int(8 * self.scale)
        self.mouth_max_height = int(58 * self.scale)
        self.mouth_y = self.center_y + int(40 * self.scale)
        self.gaze_max = int(2 * self.scale)

    def update(self, dt):
        tick_ms = dt * 1000

        # 呼吸アニメーション
        self.breath_time += tick_ms
        breath_duration = 6000
        self.breath_time %= breath_duration
        raw_breath = math.sin((2 * math.pi * self.breath_time) / breath_duration)
        self.breath = quantize(raw_breath, 8)

        # まばたき
        self.blink_count += tick_ms / 1000
        if self.blink_count >= self.next_blink_toggle:
            self.is_blinking = not self.is_blinking
            self.blink_count = 0
            if self.is_blinking:
                self.next_blink_toggle = random.uniform(0.2, 0.4)
            else:
                self.next_blink_toggle = random.uniform(0.4, 5.0)

        if self.is_blinking:
            fraction = linear_in_ease_out(min(self.blink_count / self.next_blink_toggle, 1.0))
            eye_open = 0.2 + fraction * 0.8
        else:
            eye_open = 1.0

        self.left_eye_open = eye_open
        self.right_eye_open = eye_open

        # 視線移動
        self.saccade_time += tick_ms / 1000
        if self.saccade_time >= self.next_saccade:
            self.target_gaze_x = norm_rand(0, 0.2)
            self.target_gaze_y = norm_rand(0, 0.2)
            self.target_gaze_x = max(-1, min(1, self.target_gaze_x))
            self.target_gaze_y = max(-1, min(1, self.target_gaze_y))
            self.saccade_time = 0
            self.next_saccade = random.uniform(0.3, 2.0)

        self.left_gaze_x += (self.target_gaze_x - self.left_gaze_x) * 0.1
        self.left_gaze_y += (self.target_gaze_y - self.left_gaze_y) * 0.1
        self.right_gaze_x = self.left_gaze_x
        self.right_gaze_y = self.left_gaze_y

        # 話している時の口
        if self.is_speaking:
            self.mouth_open = 0.3 + 0.4 * abs(math.sin(time.time() * 12))
        else:
            self.mouth_open = max(0, self.mouth_open - dt * 5)

        # スライドショータイマー更新
        self.slideshow_timer += dt
        if self.slideshow_timer >= SLIDESHOW_INTERVAL:
            self.slideshow_timer = 0
            if self.slideshow_images:
                self.current_slide = (self.current_slide + 1) % len(self.slideshow_images)

        # メイン表示商品の切替タイマー更新
        self.product_switch_timer += dt
        if self.product_switch_timer >= PRODUCT_SWITCH_INTERVAL:
            self.product_switch_timer = 0
            if len(PRODUCTS) > 1:
                self.current_product_idx = (self.current_product_idx + 1) % len(PRODUCTS)
                # 商品切替時にスライドショー画像も切り替え（混同防止）
                self.slideshow_images = self.product_images[self.current_product_idx]
                self.current_slide = 0
                self.slideshow_timer = 0
                logger.debug(f"Switched to product: {PRODUCTS[self.current_product_idx]['name']} "
                            f"({len(self.slideshow_images)} images)")

    def draw(self, surface):
        surface.fill(BLACK)

        if SOLD_OUT and not self.is_speaking and self.slideshow_images:
            # 売り切れモード
            self._draw_sold_out(surface)
        elif self.show_slideshow and not self.is_speaking and self.slideshow_images:
            # 通常スライドショー
            self._draw_slideshow(surface)
        else:
            # 顔表示（話している時 or スライドショー画像なし時）
            breath_offset = self.breath * 3 * self.scale

            self._draw_eye(surface, self.left_eye_x, self.eye_y + breath_offset,
                           self.left_eye_open, self.left_gaze_x, self.left_gaze_y, 'left')
            self._draw_eye(surface, self.right_eye_x, self.eye_y + breath_offset,
                           self.right_eye_open, self.right_gaze_x, self.right_gaze_y, 'right')
            self._draw_mouth(surface, self.center_x, self.mouth_y + breath_offset)

        # 字幕テキスト表示（画面最下部に背景帯付き）
        if self.status_text:
            self._draw_subtitle(surface)

    def _draw_subtitle(self, surface):
        """字幕テキストを画面最下部に背景帯付きで描画"""
        try:
            font = self.font_subtitle
            max_width = int(self.screen_width * 0.92)

            # テキストを行に分割
            lines = []
            current_line = ""
            for char in self.status_text:
                test_line = current_line + char
                test_surface = font.render(test_line, True, WHITE)
                if test_surface.get_width() <= max_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = char
            if current_line:
                lines.append(current_line)

            lines = lines[:3]  # 最大3行

            line_height = int(36 * self.scale)
            padding = int(12 * self.scale)
            total_height = len(lines) * line_height + padding * 2

            # 半透明の背景帯
            bg_rect = pygame.Rect(0, self.screen_height - total_height,
                                  self.screen_width, total_height)
            bg_surface = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
            bg_surface.fill((0, 0, 0, 180))
            surface.blit(bg_surface, bg_rect.topleft)

            # 各行を中央揃えで描画
            for i, line in enumerate(lines):
                text = font.render(line, True, WHITE)
                y = self.screen_height - total_height + padding + i * line_height
                text_rect = text.get_rect(center=(self.center_x, y + line_height // 2))
                surface.blit(text, text_rect)
        except Exception as e:
            logger.error(f"Subtitle draw error: {e}")

    def _draw_slideshow(self, surface):
        """製品紹介スライドショーを描画（大型テキスト版）

        画面レイアウト（縦を100%として）:
          0% ～ 30%  : 製品画像（中央配置）
         33%         : 製品名（大きく太字CYAN）
         42% ～ 58%  : 価格（超大型YELLOW太字）★メイン
         62%         : 購入案内（日本語）
         68%         : 購入案内（英語）
         76%         : 両替案内（CYAN太字）
         82%         : 店内案内（CYAN太字）
         89%         : ケース返却お願い（ORANGE太字）
         95%         : スライドドット
        """
        try:
            sw = self.screen_width
            sh = self.screen_height
            cx = self.center_x

            # --- 製品画像（上部30%に配置） ---
            if self.slideshow_images:
                img = self.slideshow_images[self.current_slide]
                img_rect = img.get_rect(center=(cx, int(sh * 0.16)))
                surface.blit(img, img_rect)

            # 現在のメイン表示商品
            product = PRODUCTS[self.current_product_idx]

            # --- 製品名（大きく太字） ---
            name_text = self.font_product_name.render(product["name"], True, CYAN)
            name_rect = name_text.get_rect(center=(cx, int(sh * 0.33)))
            surface.blit(name_text, name_rect)

            # --- 価格（超大型、画面の中心に） ---
            price_text = self.font_price.render(product["price"], True, YELLOW)
            price_rect = price_text.get_rect(center=(cx, int(sh * 0.48)))
            surface.blit(price_text, price_rect)

            # --- 購入案内（日本語） ---
            guide_ja = self.font_guide.render(
                "コイン入れてボタンを押してね！", True, WHITE)
            guide_ja_rect = guide_ja.get_rect(center=(cx, int(sh * 0.62)))
            surface.blit(guide_ja, guide_ja_rect)

            # --- 購入案内（英語） ---
            guide_en = self.font_guide.render(
                "Insert coins & push button!", True, WHITE)
            guide_en_rect = guide_en.get_rect(center=(cx, int(sh * 0.68)))
            surface.blit(guide_en, guide_en_rect)

            # --- 両替案内（重要情報） ---
            change_text = self.font_case_return.render(
                "お店が開いている時は両替対応します", True, CYAN)
            change_rect = change_text.get_rect(center=(cx, int(sh * 0.76)))
            surface.blit(change_text, change_rect)

            # --- 店内案内（重要情報） ---
            instore_text = self.font_case_return.render(
                "店内でも同じマイコンボード扱ってます", True, CYAN)
            instore_rect = instore_text.get_rect(center=(cx, int(sh * 0.82)))
            surface.blit(instore_text, instore_rect)

            # --- ケース返却のお願い（オレンジ、目立つ） ---
            case_text = self.font_case_return.render(
                "★ ケースが要らなかったら右横のかごに入れてね ★", True, ORANGE)
            case_rect = case_text.get_rect(center=(cx, int(sh * 0.89)))
            surface.blit(case_text, case_rect)

            # --- スライドインジケータ（ドット表示） ---
            if len(self.slideshow_images) > 1:
                dot_y = int(sh * 0.95)
                dot_r = max(5, int(sh * 0.005))
                dot_gap = max(18, int(sh * 0.018))
                total_w = (len(self.slideshow_images) - 1) * dot_gap
                start_x = cx - total_w // 2
                for i in range(len(self.slideshow_images)):
                    color = WHITE if i == self.current_slide else GRAY
                    pygame.draw.circle(surface, color,
                                       (start_x + i * dot_gap, dot_y), dot_r)

        except Exception as e:
            logger.error(f"Slideshow draw error: {e}")

    def _draw_sold_out(self, surface):
        """売り切れ画面を描画

        画面レイアウト（縦を100%として）:
          0% ～ 30%  : 製品画像（中央配置）
         33%         : 製品名（CYAN太字）
         48%         : 「売り切れ」（超大型RED太字）
         60%         : 「SOLD OUT」（大型RED太字）
         72%         : 「次の入荷をお楽しみに！」
         80%         : 「Stay tuned for restock!」
         95%         : スライドドット
        """
        try:
            sw = self.screen_width
            sh = self.screen_height
            cx = self.center_x

            # --- 製品画像（上部30%に配置） ---
            if self.slideshow_images:
                img = self.slideshow_images[self.current_slide]
                img_rect = img.get_rect(center=(cx, int(sh * 0.16)))
                surface.blit(img, img_rect)

            # --- 製品名（現在のメイン商品） ---
            product = PRODUCTS[self.current_product_idx]
            name_text = self.font_product_name.render(product["name"], True, CYAN)
            name_rect = name_text.get_rect(center=(cx, int(sh * 0.34)))
            surface.blit(name_text, name_rect)

            # --- 「売り切れ」（超大型、RED） ---
            sold_ja = self.font_sold_out.render("売り切れ", True, RED)
            sold_ja_rect = sold_ja.get_rect(center=(cx, int(sh * 0.48)))
            surface.blit(sold_ja, sold_ja_rect)

            # --- 「SOLD OUT」（大型、RED） ---
            sold_en = self.font_sold_out.render("SOLD OUT", True, RED)
            sold_en_rect = sold_en.get_rect(center=(cx, int(sh * 0.60)))
            surface.blit(sold_en, sold_en_rect)

            # --- 「次の入荷をお楽しみに！」 ---
            restock_ja = self.font_sold_out_sub.render(
                "次の入荷をお楽しみに！", True, YELLOW)
            restock_ja_rect = restock_ja.get_rect(center=(cx, int(sh * 0.74)))
            surface.blit(restock_ja, restock_ja_rect)

            # --- 「Stay tuned for restock!」 ---
            restock_en = self.font_sold_out_sub.render(
                "Stay tuned for restock!", True, YELLOW)
            restock_en_rect = restock_en.get_rect(center=(cx, int(sh * 0.82)))
            surface.blit(restock_en, restock_en_rect)

            # --- スライドインジケータ ---
            if len(self.slideshow_images) > 1:
                dot_y = int(sh * 0.95)
                dot_r = max(5, int(sh * 0.005))
                dot_gap = max(18, int(sh * 0.018))
                total_w = (len(self.slideshow_images) - 1) * dot_gap
                start_x = cx - total_w // 2
                for i in range(len(self.slideshow_images)):
                    color = WHITE if i == self.current_slide else GRAY
                    pygame.draw.circle(surface, color,
                                       (start_x + i * dot_gap, dot_y), dot_r)

        except Exception as e:
            logger.error(f"Sold out draw error: {e}")

    def _draw_eye(self, surface, cx, cy, eye_open, gaze_x, gaze_y, side):
        offset_x = gaze_x * self.gaze_max
        offset_y = gaze_y * self.gaze_max

        if eye_open > 0.3:
            radius = int(self.eye_radius * min(eye_open, 1.2))
            pygame.draw.circle(surface, WHITE,
                               (int(cx + offset_x), int(cy + offset_y)), radius)
        else:
            line_width = self.eye_radius * 2
            pygame.draw.line(surface, WHITE,
                             (int(cx - line_width/2), int(cy)),
                             (int(cx + line_width/2), int(cy)),
                             max(2, int(2 * self.scale)))

        if self.emotion == Emotion.ANGRY:
            self._draw_angry_eyelid(surface, cx, cy, side)
        elif self.emotion == Emotion.SAD:
            self._draw_sad_eyelid(surface, cx, cy, side)
        elif self.emotion == Emotion.HAPPY:
            self._draw_happy_eyelid(surface, cx, cy, eye_open, side)

    def _draw_angry_eyelid(self, surface, cx, cy, side):
        w = self.eyelid_width
        h = self.eyelid_height * 0.4
        if side == 'left':
            points = [(cx - w/2, cy - h), (cx + w/2, cy - h*0.3),
                      (cx + w/2, cy - h - 10*self.scale), (cx - w/2, cy - h - 10*self.scale)]
        else:
            points = [(cx - w/2, cy - h*0.3), (cx + w/2, cy - h),
                      (cx + w/2, cy - h - 10*self.scale), (cx - w/2, cy - h - 10*self.scale)]
        pygame.draw.polygon(surface, BLACK, points)

    def _draw_sad_eyelid(self, surface, cx, cy, side):
        w = self.eyelid_width
        h = self.eyelid_height * 0.3
        if side == 'left':
            points = [(cx - w/2, cy - h*0.3), (cx + w/2, cy - h),
                      (cx + w/2, cy - h - 10*self.scale), (cx - w/2, cy - h - 10*self.scale)]
        else:
            points = [(cx - w/2, cy - h), (cx + w/2, cy - h*0.3),
                      (cx + w/2, cy - h - 10*self.scale), (cx - w/2, cy - h - 10*self.scale)]
        pygame.draw.polygon(surface, BLACK, points)

    def _draw_happy_eyelid(self, surface, cx, cy, eye_open, side):
        if eye_open > 0.5:
            w = self.eyelid_width * 1.2
            h = self.eyelid_height * 0.5
            rect = pygame.Rect(cx - w/2, cy + self.eye_radius * 0.3, w, h)
            pygame.draw.ellipse(surface, BLACK, rect)

    def _draw_mouth(self, surface, cx, cy):
        open_ratio = self.mouth_open
        h = self.mouth_min_height + (self.mouth_max_height - self.mouth_min_height) * open_ratio
        w = self.mouth_max_width - (self.mouth_max_width - self.mouth_min_width) * open_ratio

        if open_ratio > 0.1:
            rect = pygame.Rect(cx - w/2, cy - h/2, w, h)
            pygame.draw.rect(surface, WHITE, rect, border_radius=int(min(w, h) * 0.3))
        else:
            pygame.draw.line(surface, WHITE,
                             (int(cx - w/2), int(cy)),
                             (int(cx + w/2), int(cy)),
                             max(3, int(4 * self.scale)))

    def set_emotion(self, emotion):
        self.emotion = emotion

    def start_speaking(self):
        self.is_speaking = True

    def stop_speaking(self):
        self.is_speaking = False


class Speaker:
    """TTS スピーカー（専用ワーカースレッド + 常駐SAPI + 優先度付きキュー）

    低遅延化のポイント:
    - SAPI.SpVoice をワーカースレッドで1回だけ生成して使い回す
      （発話ごとの CreateObject / 音声列挙 = 数百ms を排除）
    - 挨拶(PRIORITY_HIGH)はニュース等(PRIORITY_NORMAL)を割り込んで即発話
    """

    PRIORITY_NORMAL = 0   # ニュース・宣伝
    PRIORITY_HIGH = 1     # お客さんへの挨拶

    # SAPI フラグ
    _SVSF_ASYNC = 1
    _SVSF_PURGE = 2

    def __init__(self, face):
        self.face = face
        self.tts_ready = False
        self.tts_voice_name = ""
        self.is_speaking = False
        self.current_priority = self.PRIORITY_NORMAL
        self._queue = []                 # [(priority, text), ...] 先頭が次に喋る
        self._cv = threading.Condition()
        self._interrupt = False
        self._worker = threading.Thread(target=self._tts_worker, daemon=True, name="tts-worker")
        self._worker.start()

    # ---- 外部API ----
    def queue_speak(self, text, priority=None):
        """キューに追加（任意スレッドから呼べる）"""
        if priority is None:
            priority = self.PRIORITY_NORMAL
        with self._cv:
            if priority == self.PRIORITY_HIGH:
                # 挨拶は先頭へ。再生中がニュースなら割り込み
                self._queue.insert(0, (priority, text))
                if self.is_speaking and self.current_priority < self.PRIORITY_HIGH:
                    self._interrupt = True
            else:
                self._queue.append((priority, text))
            self._cv.notify()
        logger.info(f"Queued speech{' [HIGH]' if priority == self.PRIORITY_HIGH else ''}: {text[:30]}...")

    def queue_greeting(self, text):
        """お客さん挨拶用（高優先・割り込みあり）"""
        self.queue_speak(text, priority=self.PRIORITY_HIGH)

    def process_queue(self):
        """互換用（ワーカースレッド方式になったので何もしない）"""
        return

    # ---- 内部 ----
    def _init_sapi(self):
        """ワーカースレッド内で SAPI を1回だけ初期化"""
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        sapi = comtypes.client.CreateObject("SAPI.SpVoice")
        sapi.Volume = 100
        sapi.Rate = 1
        try:
            voices = sapi.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                desc = (v.GetDescription() or "")
                if any(k in desc.lower() for k in ("japan", "japanese", "haruka", "ayumi", "ichiro", "nanami")):
                    sapi.Voice = v
                    self.tts_voice_name = desc
                    break
            if not self.tts_voice_name and voices.Count > 0:
                self.tts_voice_name = voices.Item(0).GetDescription() or ""
        except Exception:
            pass
        self._select_output(sapi)
        return sapi

    def _select_output(self, sapi):
        """TTS_OUTPUT_MATCH を含む再生デバイスがあればそれへ、無ければ既定へ。変化があった時だけログ"""
        try:
            outs = sapi.GetAudioOutputs()
            chosen = None
            names = []
            for i in range(outs.Count):
                tok = outs.Item(i)
                d = tok.GetDescription() or ""
                names.append(d)
                if TTS_OUTPUT_MATCH and TTS_OUTPUT_MATCH.lower() in d.lower() and chosen is None:
                    chosen = tok
            want = chosen.GetDescription() if chosen is not None else "(default)"
            if want != getattr(self, "_tts_output_name", None):
                if chosen is not None:
                    sapi.AudioOutput = chosen
                else:
                    sapi.AudioOutput = None  # 既定に戻す
                self._tts_output_name = want
                logger.info(f"TTS output -> {want} (available: {names})")
        except Exception as e:
            logger.debug(f"TTS output select skipped: {e}")

    def _speak_fallback_vbs(self, text):
        """comtypes が使えない時の保険（cscript 経由、遅い）"""
        try:
            import subprocess
            escaped = text.replace('"', '""')
            vbs = ('Set sapi = CreateObject("SAPI.SpVoice")\r\n'
                   'sapi.Rate = 1\r\nsapi.Volume = 100\r\n'
                   f'sapi.Speak "{escaped}"\r\n')
            vbs_path = os.path.join(os.environ.get('TEMP', '.'), 'jihankichan_tts.vbs')
            with open(vbs_path, 'w', encoding='shift_jis', errors='replace') as f:
                f.write(vbs)
            subprocess.run(["cscript", "//nologo", vbs_path], capture_output=True, timeout=30)
        except Exception as e:
            logger.error(f"TTS fallback error: {e}")

    def _tts_worker(self):
        sapi = None
        try:
            sapi = self._init_sapi()
            self.tts_ready = True
            logger.info(f"TTS worker ready (persistent SAPI, voice='{self.tts_voice_name}')")
        except Exception as e:
            logger.error(f"TTS worker init failed, using VBScript fallback: {e}")

        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                priority, text = self._queue.pop(0)
                self._interrupt = False
                self.current_priority = priority
                self.is_speaking = True

            interrupted = False
            try:
                # 夜間はミュート（字幕のみ）
                if is_night_time():
                    logger.info(f"Night mode - muted: {text}")
                    self.face.status_text = text
                    self.face.set_emotion(Emotion.HAPPY)
                    # 4秒表示（割り込みが来たら即切り上げ）
                    for _ in range(40):
                        if self._interrupt:
                            interrupted = True
                            break
                        time.sleep(0.1)
                    continue

                self.face.start_speaking()
                self.face.set_emotion(Emotion.HAPPY)
                self.face.status_text = text
                logger.info(f"Speaking: {text}")

                if sapi is not None:
                    try:
                        # 発話前に出力先を再確認（Bluetoothスピーカーの接続/切断に追従）
                        self._select_output(sapi)
                        # 非同期で喋らせて、100ms刻みで完了/割り込みを監視
                        sapi.Speak(text, self._SVSF_ASYNC | self._SVSF_PURGE)
                        while not sapi.WaitUntilDone(100):
                            if self._interrupt:
                                sapi.Speak("", self._SVSF_ASYNC | self._SVSF_PURGE)  # 即停止
                                interrupted = True
                                break
                    except Exception as e:
                        logger.error(f"TTS error: {e}")
                        self._speak_fallback_vbs(text)
                else:
                    self._speak_fallback_vbs(text)

                # 余韻: 字幕を2秒残す（割り込み時はスキップ）
                if not interrupted:
                    for _ in range(20):
                        if self._interrupt:
                            break
                        time.sleep(0.1)
            finally:
                self.face.stop_speaking()
                self.face.set_emotion(Emotion.NEUTRAL)
                self.face.status_text = ""
                with self._cv:
                    self.is_speaking = False
                    self.current_priority = self.PRIORITY_NORMAL
                    if interrupted:
                        logger.info("Speech interrupted by greeting")


# ======================================================================
# お客さん検出 v4 (2026-10-03)
#  1) MotionGate  : 160x120 背景差分。動きの無い所の「顔」(看板/ポスター/ドア等)を原理的に除外
#  2) 動き周辺を常に 320x240 で検出 (動き中心を等倍で切り出し / 大きい動きは全体を0.5倍)
#  3) 簡易トラッキングで「1人=1回」。2ヒットで挨拶、3ヒットで通行人、立ち止まる/近づくで「来訪」
#  4) VisitCounter : 日別・時間別カウントを JSON/CSV に保存、/health と /stats で確認
# ======================================================================
class MotionGate:
    """軽量な動き検出 (約1ms)。背景は指数移動平均で緩やかに追従"""
    W, H = 160, 120

    def __init__(self):
        self.bg = None
        self.mask = None
        self.cells = 0
        self.sx = 4.0
        self.sy = 4.0

    def update(self, frame):
        """動きのバウンディングボックス (x0,y0,x1,y1; 元画像座標) を返す。無ければ None"""
        self.sx = frame.shape[1] / float(self.W)
        self.sy = frame.shape[0] / float(self.H)
        small = cv2.resize(frame, (self.W, self.H), interpolation=cv2.INTER_AREA)
        g = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        if self.bg is None:
            self.bg = g.astype(np.float32)
            self.mask = np.zeros_like(g)
            self.cells = 0
            return None
        diff = cv2.absdiff(g, cv2.convertScaleAbs(self.bg))
        _, m = cv2.threshold(diff, MOTION_THRESH, 255, cv2.THRESH_BINARY)
        m = cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=2)
        cells = int(cv2.countNonZero(m))
        # 照明の急変(ほぼ全面が変化)は背景を即リセットして「動き無し」とみなす
        if cells > 0.9 * self.W * self.H:
            self.bg = g.astype(np.float32)
            self.mask = np.zeros_like(g)
            self.cells = 0
            return None
        cv2.accumulateWeighted(g, self.bg, MOTION_BG_ALPHA)
        self.mask = m
        self.cells = cells
        if cells < MOTION_MIN_CELLS:
            return None
        ys, xs = np.nonzero(m)
        return (int(xs.min() * self.sx), int(ys.min() * self.sy),
                int((xs.max() + 1) * self.sx), int((ys.max() + 1) * self.sy))

    def eligible(self, box):
        """顔枠が『動いている領域』に重なっているか (静止物の誤検出を弾く)"""
        if self.mask is None:
            return False
        x, y, w, h = box
        x0 = max(0, int(x / self.sx)); y0 = max(0, int(y / self.sy))
        x1 = min(self.W, int((x + w) / self.sx) + 1); y1 = min(self.H, int((y + h) / self.sy) + 1)
        if x1 <= x0 or y1 <= y0:
            return False
        region = self.mask[y0:y1, x0:x1]
        return int(cv2.countNonZero(region)) >= max(3, MOTION_ELIGIBLE_FRAC * region.size)


class VisitCounter:
    """通行人/来訪者の日別・時間別カウント。再起動しても継続"""
    PATH = os.path.join(os.path.expanduser("~"), "stackchan_logs", "visitors.json")
    CSV = os.path.join(os.path.expanduser("~"), "stackchan_logs", "visits.csv")

    def __init__(self):
        self._lock = threading.Lock()
        self.data = {}
        try:
            import json
            if os.path.isfile(self.PATH):
                with open(self.PATH, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
        except Exception as e:
            logger.error(f"VisitCounter load failed: {e}")

    def _slot(self):
        now = datetime.now()
        day = self.data.setdefault(now.strftime("%Y-%m-%d"), {"passersby": 0, "visitors": 0, "by_hour": {}})
        hour = day["by_hour"].setdefault(now.strftime("%H"), [0, 0])
        return day, hour

    def _save(self):
        try:
            import json
            keep = sorted(self.data.keys())[-60:]
            self.data = {k: self.data[k] for k in keep}
            with open(self.PATH, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"VisitCounter save failed: {e}")

    def add(self, kind, size, conf, dwell):
        with self._lock:
            day, hour = self._slot()
            if kind == "visitor":   # visitors は passersby の内数 (立ち止まった/近づいた人)
                day["visitors"] += 1; hour[1] += 1
            else:
                day["passersby"] += 1; hour[0] += 1
            self._save()
            try:
                new = not os.path.isfile(self.CSV)
                with open(self.CSV, 'a', encoding='utf-8') as f:
                    if new:
                        f.write("time,kind,face_px,conf,dwell_sec\n")
                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},{kind},{size},{conf:.2f},{dwell:.1f}\n")
            except Exception:
                pass

    def today(self):
        with self._lock:
            d = self.data.get(datetime.now().strftime("%Y-%m-%d"), {})
            return int(d.get("passersby", 0)), int(d.get("visitors", 0))

    def snapshot(self):
        with self._lock:
            days = sorted(self.data.keys())[-14:]
            return {k: self.data[k] for k in days}


VISITS = VisitCounter()


def _save_visit_snapshot(frame, track, kind):
    """来訪の証拠写真を保存 (最新 VISIT_SNAPSHOT_KEEP 枚だけ残す)。後から目視で精度を確認できる"""
    try:
        d = os.path.join(os.path.expanduser("~"), "stackchan_logs", "visits")
        os.makedirs(d, exist_ok=True)
        img = frame.copy()
        x, y, w, h = track['box']
        color = (0, 255, 0) if kind == "visitor" else (0, 200, 255)
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        cv2.putText(img, f"{kind} {track['conf']:.2f} {min(w, h)}px", (x, max(14, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{kind}.jpg"
        cv2.imwrite(os.path.join(d, name), img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        files = sorted(f for f in os.listdir(d) if f.endswith(".jpg"))
        for old in files[:-VISIT_SNAPSHOT_KEEP]:
            try:
                os.remove(os.path.join(d, old))
            except OSError:
                pass
    except Exception as e:
        logger.debug(f"visit snapshot failed: {e}")


import collections as _collections
import queue as _queue
import subprocess as _subprocess


class ClipRecorder:
    """来訪者の検知を中心に、前 CLIP_PRE_SEC 秒 + 後 CLIP_POST_SEC 秒を1本の動画にして Discord へ送る。

    - 直近フレームを JPEG でメモリ(約6MB)に保持 → 録画ファイルのシークに頼らず前30秒を確保
    - 後30秒の間に別の来訪があれば、後ろを延長して1本にまとめる (最長 CLIP_MAX_SEC)
    - 変換(ffmpeg/H.264)と送信は専用スレッド・低優先度。検出や挨拶は止めない
    """

    def __init__(self):
        self.ring = _collections.deque()          # (time, jpeg_bytes)
        self.active = None                        # 進行中のイベント
        self._lock = threading.Lock()
        self._q = _queue.Queue()
        self._last_push = 0.0
        self._last_flag = 0.0
        self._ffmpeg_exe = False                  # False=未探索 / None=無し
        HEALTH.setdefault("clips_sent", 0)
        HEALTH.setdefault("clips_failed", 0)
        if CLIP_ENABLED:
            threading.Thread(target=self._worker, daemon=True, name="clip-worker").start()

    # ---- カメラスレッドから (毎フレーム呼んでよい。内部で CLIP_FPS に間引く) ----
    def push(self, frame):
        if not CLIP_ENABLED:
            return
        now = time.time()
        if now - self._last_push < 0.85 / CLIP_FPS:    # デコード間隔(約83ms)に合わせ、目標fpsに近づける
            return
        self._last_push = now
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, CLIP_JPEG_Q])
        if not ok:
            return
        item = (now, buf.tobytes())
        done = None
        with self._lock:
            self.ring.append(item)
            while self.ring and now - self.ring[0][0] > CLIP_PRE_SEC:
                self.ring.popleft()
            if self.active is not None:
                self.active['frames'].append(item)
                if now >= self.active['end'] or now - self.active['t0'] >= CLIP_MAX_SEC:
                    done, self.active = self.active, None
        if done is not None:
            self._q.put(done)
        if now - self._last_flag > 5.0:        # 動作確認用: C:\stackchan\clip_test.flag を置くと1本作る
            self._last_flag = now
            self._check_flag()

    def _check_flag(self):
        try:
            if os.path.isfile(CLIP_TEST_FLAG):
                with open(CLIP_TEST_FLAG, 'r', encoding='utf-8', errors='replace') as f:
                    mode = f.read().strip().lower()
                os.remove(CLIP_TEST_FLAG)
                logger.info(f"Clip test flag found (mode={mode or 'upload'})")
                self.trigger("manual test", upload=(mode != "noupload"))
        except Exception as e:
            logger.warning(f"clip test flag error: {e}")

    # ---- 来訪の検知から ----
    def trigger(self, note, upload=True):
        if not CLIP_ENABLED:
            return None
        now = time.time()
        with self._lock:
            if self.active is not None:
                self.active['end'] = now + CLIP_POST_SEC       # 重なる検知は1本にまとめる
                self.active['notes'].append(note)
                self.active['upload'] = self.active['upload'] or upload
                return "extended"
            self.active = {'t0': now, 'end': now + CLIP_POST_SEC, 'frames': list(self.ring),
                           'notes': [note], 'upload': upload}
            return "started"

    # ---- 変換・送信 (専用スレッド) ----
    def _worker(self):
        while True:
            ev = self._q.get()
            try:
                self._make_and_send(ev)
            except Exception as e:
                HEALTH["clips_failed"] = HEALTH.get("clips_failed", 0) + 1
                logger.error(f"clip error: {e}")

    def _find_ffmpeg(self):
        if self._ffmpeg_exe is not False:
            return self._ffmpeg_exe
        exe = None
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            import shutil
            exe = shutil.which("ffmpeg")
        self._ffmpeg_exe = exe
        logger.info(f"Clip encoder: {'ffmpeg ' + str(exe) if exe else 'NOT found (fallback mp4v)'}")
        return exe

    def _encode_ffmpeg(self, frames, fps, path, crf, width=None):
        exe = self._find_ffmpeg()
        if not exe:
            return False
        cmd = [exe, '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', f'{fps:.3f}', '-c:v', 'mjpeg', '-i', '-']
        if width:
            cmd += ['-vf', f'scale={width}:-2']
        cmd += ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', str(crf), '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart', path]
        # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS: 検出/TTS を邪魔しない
        p = _subprocess.Popen(cmd, stdin=_subprocess.PIPE, stdout=_subprocess.DEVNULL,
                              stderr=_subprocess.DEVNULL, creationflags=0x08000000 | 0x00004000)
        try:
            for _, jpg in frames:
                p.stdin.write(jpg)
            p.stdin.close()
            p.wait(timeout=600)
        except Exception as e:
            logger.warning(f"ffmpeg pipe error: {e}")
            try:
                p.kill()
            except Exception:
                pass
            return False
        return p.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 1000

    def _encode_cv2(self, frames, fps, path):
        """ffmpeg が無い時の保険 (mp4v: Discord上でプレビューできない場合がある)"""
        first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        h, w = first.shape[:2]
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
        for _, jpg in frames:
            vw.write(cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR))
        vw.release()
        return os.path.isfile(path) and os.path.getsize(path) > 1000

    def _make_and_send(self, ev):
        frames = ev['frames']
        if len(frames) < 5:
            logger.info("clip skipped: too few frames")
            return
        dur = max(1.0, frames[-1][0] - frames[0][0])
        fps = min(max(len(frames) / dur, 1.0), 15.0)
        os.makedirs(CLIP_DIR, exist_ok=True)
        stamp = datetime.fromtimestamp(ev['t0'])
        path = os.path.join(CLIP_DIR, stamp.strftime("clip_%Y%m%d_%H%M%S.mp4"))
        t0 = time.time()
        ok = self._encode_ffmpeg(frames, fps, path, crf=30)
        if ok and os.path.getsize(path) > CLIP_MAX_UPLOAD_BYTES:
            logger.info(f"clip too large ({os.path.getsize(path) / 1e6:.1f}MB) - re-encoding smaller")
            ok = self._encode_ffmpeg(frames, fps, path, crf=36, width=480)
        if not ok:
            ok = self._encode_cv2(frames, fps, path)
        if not ok:
            HEALTH["clips_failed"] = HEALTH.get("clips_failed", 0) + 1
            logger.error("clip encode failed")
            return
        size = os.path.getsize(path)
        logger.info(f"Clip saved: {os.path.basename(path)} {size / 1e6:.1f}MB, {dur:.0f}s, {len(frames)} frames, "
                    f"encode {time.time() - t0:.0f}s, events={len(ev['notes'])}, upload={ev['upload']}")
        self._cleanup()
        if not ev['upload']:
            return
        if size > CLIP_MAX_UPLOAD_BYTES:
            logger.warning(f"clip not uploaded: {size / 1e6:.1f}MB exceeds {CLIP_MAX_UPLOAD_BYTES / 1e6:.1f}MB")
            HEALTH["clips_failed"] = HEALTH.get("clips_failed", 0) + 1
            return
        self._upload(path, stamp, dur, len(ev['notes']))

    def _upload(self, path, stamp, dur, n_events):
        if not REQUESTS_AVAILABLE or not DISCORD_WEBHOOK_URL:
            return
        extra = f"（{n_events}件の来訪を1本にまとめました）" if n_events > 1 else ""
        content = f"🎥 来訪者クリップ 前後{CLIP_PRE_SEC}秒{extra}\n🕐 {stamp.strftime('%Y-%m-%d %H:%M:%S')}（{dur:.0f}秒）"
        try:
            with open(path, 'rb') as f:
                r = requests.post(DISCORD_WEBHOOK_URL, data={'content': content},
                                  files={'file': (os.path.basename(path), f, 'video/mp4')}, timeout=180)
            if r.status_code in (200, 204):
                HEALTH["clips_sent"] = HEALTH.get("clips_sent", 0) + 1
                logger.info(f"Clip uploaded to Discord (HTTP {r.status_code})")
            else:
                HEALTH["clips_failed"] = HEALTH.get("clips_failed", 0) + 1
                logger.warning(f"Clip upload failed: HTTP {r.status_code} {r.text[:160]}")
        except Exception as e:
            HEALTH["clips_failed"] = HEALTH.get("clips_failed", 0) + 1
            logger.error(f"Clip upload error: {e}")

    def _cleanup(self):
        try:
            cutoff = time.time() - CLIP_KEEP_DAYS * 86400
            for n in os.listdir(CLIP_DIR):
                p = os.path.join(CLIP_DIR, n)
                if n.endswith('.mp4') and os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except Exception:
            pass


CLIPS = ClipRecorder()


class FaceDetector:
    """お客さん検出: 動き検出ゲート → 動き周辺の高解像度顔検出 → トラッキング → 挨拶/カウント/通知"""

    def __init__(self, speaker):
        self.speaker = speaker
        self.last_greeting_time = 0.0
        self.greeting_cooldown = GREET_COOLDOWN_SEC  # この秒数は続けて挨拶しない
        self.recent = []   # 追跡が消えた人の記憶 (同一人物の再出現を引き継ぐ)
        self._recent_counts = []   # 直近にカウントした位置 [(t, cx, cy)] 二重加算の抑制用
        self.last_discord_time = 0.0
        self.motion = MotionGate()
        self._motion_until = 0.0
        self._last_roi = None
        self.tracks = []
        self._next_track_id = 1

        # CLAHE 前処理 (白飛び画像のコントラストを引き出す)
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        # 顔検出器: YuNet (DNN-based) を優先、なければ Haar Cascade にフォールバック
        self.detector_yunet = None
        self.face_cascade = None
        if hasattr(cv2, "FaceDetectorYN") and os.path.isfile(YUNET_MODEL_PATH):
            try:
                self.detector_yunet = cv2.FaceDetectorYN.create(
                    YUNET_MODEL_PATH, "", (DET_W, DET_H), DETECT_SCORE_THRESHOLD, 0.3, 500)
                logger.info(f"YuNet face detector loaded ({YUNET_MODEL_PATH})")
            except Exception as e:
                logger.error(f"YuNet init failed: {e}, falling back to Haar")
                self.detector_yunet = None
        if self.detector_yunet is None:
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            self.face_cascade = cv2.CascadeClassifier(cascade_path)
            logger.info("Haar cascade face detector loaded (fallback)")
        logger.info("Face detector initialized (motion-gated, tracked)")

        # 検出専用ワーカー（カメラ読取をブロックしない。常に最新フレームだけ処理）
        self._latest_frame = None
        self._det_cv = threading.Condition()
        threading.Thread(target=self._detect_worker, daemon=True, name="face-detect").start()

    # ---------------- ワーカー ----------------
    def submit(self, frame):
        """カメラスレッドから呼ぶ。処理中なら古いフレームは捨てて最新に差し替え"""
        with self._det_cv:
            self._latest_frame = frame
            self._det_cv.notify()

    def _detect_worker(self):
        while True:
            with self._det_cv:
                while self._latest_frame is None:
                    self._det_cv.wait()
                frame = self._latest_frame
                self._latest_frame = None
            try:
                self.check_frame(frame)
            except Exception as e:
                logger.error(f"detect worker error: {e}")

    # ---------------- 検出 ----------------
    def _preprocess(self, frame):
        """白飛び画像対策: LAB空間のLチャネルにCLAHEを適用してコントラスト改善"""
        try:
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            l = self.clahe.apply(l)
            return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        except Exception:
            return frame

    def _detect_faces(self, img):
        """img は常に DET_W x DET_H。[(x, y, w, h, conf), ...] を img 座標で返す"""
        if self.detector_yunet is not None:
            try:
                _, faces = self.detector_yunet.detect(img)
                if faces is None:
                    return []
                h, w = img.shape[:2]
                out = []
                for f in faces:
                    x, y = max(0, int(f[0])), max(0, int(f[1]))
                    out.append((x, y, max(1, min(int(f[2]), w - x)), max(1, min(int(f[3]), h - y)), float(f[-1])))
                return out
            except Exception as e:
                logger.error(f"YuNet detect error: {e}")
                return []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=4, minSize=(40, 40))
        return [(int(x), int(y), int(w), int(h), 0.7) for (x, y, w, h) in faces]

    def _run_detector(self, frame, roi, track_hint):
        """動き周辺で顔検出して元画像座標 [(x,y,w,h,conf)] を返す。入力は常に DET_W x DET_H。
        - 小〜中サイズの動き: 動き領域の『上端』(=頭の位置)に合わせた等倍の窓 (最大2窓)。遠くの小さい顔が潰れない
        - 画面の大半を占める動き(カメラのすぐ前の人): 全体を縮小して1回
        - 動きが止まっても追跡中の人がいれば、その人の周りの窓だけを見る"""
        H, W = frame.shape[:2]
        out = []
        if roi is not None:
            x0, y0, x1, y1 = roi
            if (x1 - x0) * (y1 - y0) > 0.35 * W * H:
                small = cv2.resize(frame, (DET_W, DET_H), interpolation=cv2.INTER_AREA)
                res = self._detect_faces(self._preprocess(small))
                fx, fy = W / float(DET_W), H / float(DET_H)
                return [(int(x * fx), int(y * fy), int(w * fx), int(h * fy), c) for (x, y, w, h, c) in res]
            wy = int(min(max(0, y0 - 24), H - DET_H))
            if x1 - x0 <= DET_W - 24:
                wins = [(int(min(max(0, (x0 + x1) / 2.0 - DET_W / 2), W - DET_W)), wy)]
            else:   # 横に広い動き(複数人/車): 左右2窓
                wl = int(min(max(0, x0 - 12), W - DET_W))
                wr = int(min(max(0, x1 + 12 - DET_W), W - DET_W))
                wins = [(wl, wy)] + ([(wr, wy)] if wr - wl > DET_W // 2 else [])
        elif track_hint is not None:
            cx, cy = track_hint
            wins = [(int(min(max(0, cx - DET_W / 2), W - DET_W)), int(min(max(0, cy - DET_H / 2), H - DET_H)))]
        else:
            return []
        for (wx, wy) in wins:
            res = self._detect_faces(self._preprocess(frame[wy:wy + DET_H, wx:wx + DET_W]))
            out.extend((x + wx, y + wy, w, h, c) for (x, y, w, h, c) in res)
        return out

    # ---------------- メイン処理 ----------------
    def check_frame(self, frame):
        if frame is None:
            return
        now = time.time()
        roi = self.motion.update(frame)
        HEALTH["motion_cells"] = self.motion.cells
        if roi is not None:
            self._motion_until = now + MOTION_HOLD_SEC
            self._last_roi = roi
        tracks_alive = [t for t in self.tracks if now - t['last'] <= TRACK_TTL_SEC]
        if roi is None and now >= self._motion_until and not tracks_alive:
            HEALTH["tracks"] = 0
            return  # 動きも追跡対象も無い: 何もしない (CPU節約 + 静止物の誤検出を防ぐ)

        hint = None
        if roi is None:
            if tracks_alive:
                t = max(tracks_alive, key=lambda t: t['last'])
                hint = (t['cx'], t['cy'])
            elif self._last_roi is not None:
                r = self._last_roi
                hint = ((r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0)
        t0 = time.perf_counter()
        dets = self._run_detector(frame, roi, hint)
        det_ms = (time.perf_counter() - t0) * 1000.0
        HEALTH["last_det_ms"] = det_ms
        HEALTH["detections"] += 1
        self._update_tracks(dets, now, frame, det_ms)

    def _update_tracks(self, dets, now, frame, det_ms):
        for t in [t for t in self.tracks if now - t['last'] > TRACK_TTL_SEC]:
            if t['hits'] >= 2:
                self.recent.append(t)
        self.recent = [r for r in self.recent if now - r['last'] <= RECENT_MEMORY_SEC][-40:]
        self.tracks = [t for t in self.tracks if now - t['last'] <= TRACK_TTL_SEC]
        used = set()
        for (x, y, w, h, conf) in sorted(dets, key=lambda d: -min(d[2], d[3])):
            size = min(w, h)
            cx, cy = x + w / 2.0, y + h / 2.0
            best, bd = None, 1e9
            for t in self.tracks:
                if id(t) in used:
                    continue
                # 直前の速度から現在位置を予測 (歩行で1コマ内に顔が大きく動いても同じ追跡に当てる)
                px, py = t['cx'] + t.get('vx', 0.0) * min(now - t['last'], 1.0), t['cy'] + t.get('vy', 0.0) * min(now - t['last'], 1.0)
                dist = min(((cx - t['cx']) ** 2 + (cy - t['cy']) ** 2) ** 0.5, ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5)
                lim = max(1.6 * max(t['size'], size), 90, min(WALK_MATCH_PX, 70 + 220 * min(now - t['last'] + 0.15, 1.0)))
                if dist < lim and dist < bd:
                    best, bd = t, dist
            if best is None:
                if size < GREETING_FACE_MIN_SIZE or not self.motion.eligible((x, y, w, h)):
                    continue  # 小さすぎる / 動きの無い場所の「顔」は新規追跡しない
                # 直近に消えた人の近くへの再出現なら、同一人物として状態(挨拶済み/カウント済み)を引き継ぐ
                match, md = None, 1e9
                for r in self.recent:
                    d = ((cx - r['cx']) ** 2 + (cy - r['cy']) ** 2) ** 0.5
                    lim = max(RECENT_MATCH_PX, 1.5 * max(r['size'], size))
                    if d < lim and d < md:
                        match, md = r, d
                if match is not None:
                    self.recent.remove(match)
                    best = match
                else:
                    best = {'id': self._next_track_id, 'first': now, 'last': now, 'hits': 0, 'conf_sum': 0.0,
                            'max_conf': 0.0, 'max_size': 0, 'greeted': False, 'confirmed': False,
                            'engaged': False, 'cx': cx, 'cy': cy, 'size': size, 'box': (x, y, w, h),
                            'conf': conf, 'best_score': 0.0, 'best_frame': None, 'hist': []}
                    self._next_track_id += 1
                self.tracks.append(best)
            used.add(id(best))
            dt_ = now - best['last']
            if 0.02 < dt_ < 1.5 and best['hits'] >= 1:
                best['vx'] = 0.6 * best.get('vx', 0.0) + 0.4 * (cx - best['cx']) / dt_
                best['vy'] = 0.6 * best.get('vy', 0.0) + 0.4 * (cy - best['cy']) / dt_
            best.update(last=now, cx=cx, cy=cy, size=size, box=(x, y, w, h), conf=conf)
            best['hits'] += 1
            best['conf_sum'] += conf
            best['hist'].append((now, cx, cy))
            best['hist'] = [p for p in best['hist'] if now - p[0] <= ENGAGE_DWELL_SEC + 1.0]
            best['max_conf'] = max(best['max_conf'], conf)
            best['max_size'] = max(best['max_size'], size)
            score = conf * size
            if score > best['best_score']:
                best['best_score'] = score
                best['best_frame'] = frame
                best['best_box'] = (x, y, w, h)
                best['best_conf'] = conf
        HEALTH["tracks"] = len(self.tracks)
        if dets:
            HEALTH["last_face"] = now

        for t in self.tracks:
            span = t['last'] - t['first']
            mean_conf = t['conf_sum'] / max(1, t['hits'])
            # 1) 挨拶: 2ヒット (または高確度) で即座に
            if not t['greeted'] and (t['hits'] >= GREET_MIN_HITS or t['max_conf'] >= 0.88):
                t['greeted'] = True
                near_greeted = any(
                    ((t['cx'] - r['cx']) ** 2 + (t['cy'] - r['cy']) ** 2) ** 0.5 < RECENT_MATCH_PX
                    and r.get('greeted') and now - r['last'] < RECENT_MEMORY_SEC for r in self.recent)
                if not near_greeted and now - self.last_greeting_time >= self.greeting_cooldown:
                    self.last_greeting_time = now
                    logger.info(f"Greet: track#{t['id']} hits={t['hits']} size={t['size']}px conf={t['conf']:.2f} det={det_ms:.0f}ms")
                    self.speaker.queue_greeting(self._pick_greeting())
            # 2) 通行人として確定
            if not t['confirmed'] and t['hits'] >= TRACK_CONFIRM_HITS and span >= TRACK_MIN_SPAN_SEC \
                    and mean_conf >= TRACK_MIN_MEAN_CONF:
                t['confirmed'] = True
                dup = any(now - c[0] <= COUNT_SUPPRESS_SEC
                          and ((t['cx'] - c[1]) ** 2 + (t['cy'] - c[2]) ** 2) ** 0.5 <= COUNT_SUPPRESS_PX
                          for c in self._recent_counts)
                self._recent_counts = [c for c in self._recent_counts if now - c[0] <= COUNT_SUPPRESS_SEC][-20:]
                self._recent_counts.append((now, t['cx'], t['cy']))
                if dup:
                    t['engaged'] = True      # 近接の二重検出: カウントも来訪判定もしない
                    t['greeted'] = True
                    logger.info(f"Duplicate suppressed: track#{t['id']} (near a person counted <={COUNT_SUPPRESS_SEC:.0f}s ago)")
                    continue
                VISITS.add("passerby", t['max_size'], mean_conf, span)
                if t.get('best_frame') is not None:
                    pt = dict(t); pt['box'] = t.get('best_box', t['box']); pt['conf'] = t.get('best_conf', t['conf'])
                    _save_visit_snapshot(t['best_frame'], pt, "passerby")
                logger.info(f"Passerby counted: track#{t['id']} size={t['max_size']}px conf={mean_conf:.2f}")
            # 3) 来訪者(立ち止まった/近づいた人)として確定 → Discord
            if t['confirmed'] and not t['engaged'] and (self._is_lingering(t, now) or (t['max_size'] >= ENGAGE_SIZE and t['hits'] >= 4)):
                t['engaged'] = True
                VISITS.add("visitor", t['max_size'], mean_conf, span)
                CLIPS.trigger(f"visitor track#{t['id']} {t['max_size']}px {span:.0f}s")
                bf = t.get('best_frame')
                if bf is not None:
                    bt = dict(t); bt['box'] = t.get('best_box', t['box']); bt['conf'] = t.get('best_conf', t['conf'])
                    _save_visit_snapshot(bf, bt, "visitor")
                logger.info(f"VISITOR counted: track#{t['id']} size={t['max_size']}px span={span:.1f}s conf={mean_conf:.2f}")
                if bf is not None and now - self.last_discord_time >= DISCORD_MIN_INTERVAL_SEC:
                    self.last_discord_time = now
                    bx = t.get('best_box', t['box'])
                    threading.Thread(target=send_discord_notification,
                                     args=(bf.copy(), 1, bx, t.get('best_conf', t['conf'])), daemon=True).start()
        p, v = VISITS.today()
        HEALTH["passersby_today"], HEALTH["visitors_today"] = p, v

    @staticmethod
    def _is_lingering(t, now):
        """直近 ENGAGE_DWELL_SEC 秒、ほぼ同じ場所に居続けたか (歩いて通り過ぎる人は該当しない)"""
        hist = t['hist']
        if len(hist) < 4 or hist[-1][0] - hist[0][0] < ENGAGE_DWELL_SEC:
            return False
        xs = sorted(p[1] for p in hist); ys = sorted(p[2] for p in hist)
        mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
        lim = max(45.0, 0.9 * t['size'])
        return all(((p[1] - mx) ** 2 + (p[2] - my) ** 2) ** 0.5 <= lim for p in hist)

    def _pick_greeting(self):
        if SOLD_OUT:
            greetings = [
                "いらっしゃいませ！ごめんね、今は売り切れなんだ～。また来てね！",
                "こんにちは！売り切れちゃってごめんね！次の入荷をお楽しみに！",
                "あ、お客さん！残念だけど今は売り切れ！また補充するからね！",
                "いらっしゃいませ！売り切れ中だけど、秋葉原楽しんでね！",
                "Sorry, sold out now! Please come back later!",
                "Hi! We're sold out at the moment. Stay tuned for restock!",
                "ごめんなさい！Sold out! また入荷したら買いに来てね！",
                "売り切れちゃった！Sorry! 次は負けないぞ～！",
            ]
        else:
            greetings = [
                # --- UIAPduino (300円) ---
                "いらっしゃいませー！UIAPduino 300円！100円玉3枚でゲットしてね！",
                "こんにちは！300円でマイコンボード買えるよ！お土産にどうぞ！",
                "いらっしゃいませ！秋葉原のお土産にマイコンボードはいかが？UIAPduino 300円！",
                "あ、お客さんだ！UIAPduino 300円！ボタンをポチッとね！",
                "Welcome! UIAPduino board only 300 yen! Great souvenir from Akihabara!",
                "Hello! Get UIAPduino for just 300 yen! Insert 3 coins!",
                "Hi there! UIAPduino Pro Micro, 300 yen! Perfect Akihabara souvenir!",
                # --- Suittyo-On (500円) 新商品 ---
                "新商品！Suittyo-On (すいっちょおん) 500円！RISC-Vマイコン入荷したよ！",
                "いらっしゃいませ！Suittyo-On 500円！USB Type-Cで使えるRISC-Vマイコン！",
                "Suittyo-On入荷！500円でRISC-V CH32X035が手に入る！USB-PD対応！",
                "新登場！Suittyo-On 500円！WCH社のRISC-Vマイコン、48MHzで動くよ！",
                "いらっしゃい！Suittyo-On 500円！USB Power Deliveryで5から20V出力できるんだ！",
                "Welcome! New product! Suittyo-On RISC-V board, 500 yen!",
                "Hi! Check out Suittyo-On with CH32X035 RISC-V chip, only 500 yen!",
                "Hello! New Suittyo-On RISC-V microcontroller, just 500 yen!",
                # --- 両商品ミックス ---
                "いらっしゃいませ！UIAPduino 300円とSuittyo-On 500円、どちらも面白いマイコンだよ！",
                "マイコン2種類あるよ！UIAPduino 300円とSuittyo-On 500円！どっちも秋葉原のお土産にどうぞ！",
                "Two RISC-V boards! UIAPduino 300 yen and Suittyo-On 500 yen! Take your pick!",
                "We have UIAPduino 300 yen and Suittyo-On 500 yen! Both great souvenirs!",
                # --- ケース・両替・店内案内 ---
                "いらっしゃいませ！ケースが要らなかったら右横のかごに入れてね！",
                "Welcome! If you don't need the case, put it in the basket on the right!",
                "いらっしゃいませ！お店が開いてる時は両替もできるよ！気軽に声かけてね！",
                "こんにちは！両替が必要ならお店に声かけてね！100円玉に両替できるよ！",
                "いらっしゃい！店内でも同じマイコンボード扱ってるよ！ぜひ見ていってね！",
                "Welcome! We can make change when the store is open! Feel free to ask!",
                "Hi! We also sell these microcontroller boards inside the store!",
            ]
        return random.choice(greetings)


class NewsAnnouncer:
    """10分に1回秋葉原ニュースを喋る"""

    def __init__(self, speaker):
        self.speaker = speaker
        self.client = None
        self.last_news_time = 0
        self.running = True

        # Claude API初期化
        if CLAUDE_AVAILABLE and CLAUDE_API_KEY:
            self.client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
            logger.info("Claude API initialized for news")
        else:
            logger.warning("Claude API not available for news")

    def start(self):
        """ニュースアナウンススレッドを開始"""
        thread = threading.Thread(target=self._news_loop, daemon=True)
        thread.start()
        logger.info("News announcer started")

    def _news_loop(self):
        """ニュースループ"""
        # 起動時の挨拶は後で行う（メインループ開始後）
        time.sleep(5)
        if SOLD_OUT:
            self.speaker.queue_speak("ジハンキチャン起動！今は売り切れ中だけど、秋葉原から元気にお届けするよ！")
        else:
            self.speaker.queue_speak("ジハンキチャン起動！秋葉原からお届けするよ！")

        while self.running:
            current_time = time.time()

            if current_time - self.last_news_time >= NEWS_INTERVAL:
                self.last_news_time = current_time
                self._announce_news()

            time.sleep(10)  # 10秒ごとにチェック

    def _announce_news(self):
        """秋葉原ニュースを取得して読み上げ（たまにお土産宣伝）"""
        # 40%の確率でお土産宣伝・店舗案内 / 売り切れ案内
        if random.random() < 0.4:
            if SOLD_OUT:
                souvenir_messages = [
                    "売り切れ中！でも次の入荷をお楽しみにね！",
                    "Sold out now! But we'll restock soon!",
                    "ごめんね売り切れ！でも秋葉原にはまだまだ面白いものがあるよ！",
                    "今は売り切れだけど、また来てくれたら嬉しいな！",
                    "Sold out! Check back later for more UIAPduino!",
                    "売り切れちゃった～！次はもっとたくさん用意するからね！",
                    "売り切れだけど、店内でも同じマイコンボード扱ってるよ！ぜひ見ていってね！",
                    "Sold out here, but we have more boards inside the store!",
                ]
            else:
                souvenir_messages = [
                    # --- UIAPduino (300円) ---
                    "秋葉原のお土産にUIAPduino Pro Microはいかが？たったの300円！",
                    "Akihabara souvenir! UIAPduino microcontroller only 300 yen!",
                    "UIAPduino 300円！プログラミング始めるならコレ！",
                    "お土産コーナー！UIAPduino Pro Micro、300円で販売中！",
                    "Hey! Get a UIAPduino microcontroller as a souvenir! Just 300 yen!",
                    # --- Suittyo-On (500円) 新商品 ---
                    "新商品案内！Suittyo-On RISC-Vマイコン500円販売開始！48MHz、Flash 62KB搭載！",
                    "Suittyo-On入荷したよ！USB Power Delivery対応の珍しいRISC-Vマイコン、500円！",
                    "新登場！Suittyo-On 500円！WCH社のCH32X035搭載、USB Type-Cでサクッと書き込めるよ！",
                    "New product! Suittyo-On RISC-V board, 500 yen! USB-PD compatible, 48MHz!",
                    "Akihabara news! Now selling Suittyo-On RISC-V microcontroller, 500 yen!",
                    # --- 両商品プロモーション ---
                    "マイコン2種類あるよ！UIAPduino 300円とSuittyo-On 500円！どちらもRISC-V！",
                    "We have two boards: UIAPduino 300 yen and Suittyo-On 500 yen! Both RISC-V chips!",
                    "UIAPduinoとSuittyo-On、両方揃えてマイコンライフを楽しもう！300円と500円！",
                    # --- 両替・店内案内（重要情報） ---
                    "お知らせ！お店が開いている時は両替対応するよ！気軽に声かけてね！",
                    "店内でも同じマイコンボード扱ってます！ぜひ店内も見ていってね！",
                    "両替が必要な方はお店にお声がけください！100円玉に両替できるよ！",
                    "We can make change when the store is open! Feel free to ask!",
                    "We also sell microcontroller boards inside the store! Come check it out!",
                ]
            news = random.choice(souvenir_messages)
            self.speaker.queue_speak(news)
            return

        if not self.client:
            # Claude APIがない場合はプリセットのセリフ
            news_list = [
                "秋葉原情報！今日も電気街は賑わってるよ！新しいガジェットをチェックしてね！",
                "秋葉原ニュース！メイドカフェが新しくオープンしたみたい！行ってみてね！",
                "秋葉原だより！今週末はホコ天だよ！コスプレイヤーさんがいっぱい来るかも！",
                "秋葉原情報！新作アニメのグッズが入荷したって！早い者勝ちだよ！",
                "秋葉原ニュース！ジャンク通りで掘り出し物を探すのも楽しいよね！",
                "秋葉原だより！ラジオ会館にはレアなフィギュアがあるって噂だよ！",
                "秋葉原情報！今日の天気はどうかな？お買い物日和だといいね！",
                "Akihabara news! So many cool gadgets today! Check them out!",
                "秋葉原からこんにちは！Hello from Akihabara! 楽しんでね！",
            ]
            news = random.choice(news_list)
        else:
            try:
                system_prompt = """あなたは秋葉原に設置された案内ロボット「ジハンキチャン」です。
秋葉原に関する楽しい一言ニュースや情報を1文で話してください。
アニメ、ゲーム、電子部品、メイドカフェ、新製品など秋葉原らしい話題で。
50文字以内で、明るく元気に！"""
                if SOLD_OUT:
                    system_prompt += "\n今は商品が売り切れ中です。たまに売り切れに触れつつも、秋葉原の楽しい話題を中心に話してください。"
                response = self.client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=100,
                    system=system_prompt,
                    messages=[{"role": "user", "content": "秋葉原の最新情報を一言で教えて！"}]
                )
                news = response.content[0].text
                logger.info(f"News from Claude: {news}")
            except Exception as e:
                logger.error(f"Claude API error: {e}")
                news = "秋葉原情報！今日も電気街は元気いっぱいだよ！"

        self.speaker.queue_speak(news)

    def stop(self):
        self.running = False


class CameraManager:
    def __init__(self, camera_index=0):
        self.camera_index = camera_index
        self.cap = None
        self.recording = False
        self.video_writer = None
        self.current_segment_start = None
        self.running = True
        os.makedirs(RECORDING_DIR, exist_ok=True)

    def start(self):
        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            logger.error(f"Cannot open camera {self.camera_index}")
            return False
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 15)
        # 内部バッファを最小化して「古いフレームを掴む」遅延を減らす（効かないカメラもある）
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        self._apply_camera_tuning()
        logger.info(f"Camera {self.camera_index} started")
        return True

    def _apply_camera_tuning(self):
        """env var指定があればカメラの露出/明るさ/ゲインを設定。空文字列ならスキップ。"""
        def try_set(name, prop, value_str):
            if value_str == "":
                return
            try:
                val = float(value_str)
                ok = self.cap.set(prop, val)
                actual = self.cap.get(prop)
                logger.info(f"Camera {name}: requested={val} accepted={ok} actual={actual}")
            except Exception as e:
                logger.error(f"Camera {name} set error: {e}")

        try_set("AUTO_EXPOSURE", cv2.CAP_PROP_AUTO_EXPOSURE, CAMERA_AUTO_EXPOSURE)
        try_set("EXPOSURE", cv2.CAP_PROP_EXPOSURE, CAMERA_EXPOSURE)
        try_set("BRIGHTNESS", cv2.CAP_PROP_BRIGHTNESS, CAMERA_BRIGHTNESS)
        try_set("GAIN", cv2.CAP_PROP_GAIN, CAMERA_GAIN)

    def get_frame(self):
        """戻り値: frame=新しいコマ / False=デコードを省略(次へ) / None=失敗・未接続"""
        global current_frame
        if self.cap is None or not self.cap.isOpened():
            self._maybe_reopen()
            return None
        ok = self.cap.grab()            # grab はカメラfpsでブロックする。デコード(retrieve)は必要なコマだけ
        now = time.time()
        if ok and now - getattr(self, '_last_decode', 0.0) < 1.0 / DECODE_FPS:
            return False
        ret, frame = self.cap.retrieve() if ok else (False, None)
        if ret:
            self._last_decode = now
            self._fail_since = None
            HEALTH["last_frame"] = now
            HEALTH["frames"] += 1
            with frame_lock:
                current_frame = frame   # retrieve は毎回新しい配列を返すのでコピー不要
            return frame
        # 読み取り失敗: 5秒続いたらカメラを開き直す（USB抜け/ドライバ固まり対策）
        if getattr(self, '_fail_since', None) is None:
            self._fail_since = now
        elif now - self._fail_since > 5.0:
            self._maybe_reopen()
        return None

    def _maybe_reopen(self):
        """10秒に1回までカメラ再オープンを試みる"""
        now = time.time()
        if now - getattr(self, '_last_reopen', 0.0) < 10.0:
            return
        self._last_reopen = now
        self._fail_since = None
        logger.warning("Camera not responding - reopening")
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass
        self.cap = None
        if self.start():
            HEALTH["camera_reopens"] += 1
            if self.recording:
                self._start_new_segment()

    def start_recording(self):
        self.recording = True
        self.cleanup_old_recordings()
        self._start_new_segment()
        logger.info("Recording started")

    def _start_new_segment(self):
        if self.video_writer is not None:
            self.video_writer.release()
        now = datetime.now()
        filename = now.strftime("%Y%m%d_%H%M%S") + ".mp4"
        filepath = os.path.join(RECORDING_DIR, filename)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.video_writer = cv2.VideoWriter(filepath, fourcc, RECORD_FPS, (640, 480))
        self._last_rec = 0.0
        self.current_segment_start = now
        logger.info(f"New recording segment: {filename}")

    def write_frame(self, frame):
        if not self.recording or self.video_writer is None:
            return
        # RECORD_FPS に間引く（ディスクとエンコード負荷の節約）
        t_now = time.time()
        if t_now - getattr(self, '_last_rec', 0.0) < 1.0 / RECORD_FPS:
            return
        self._last_rec = t_now
        now = datetime.now()
        try:
            if (now - self.current_segment_start).seconds >= SEGMENT_MINUTES * 60:
                self._start_new_segment()
            self.video_writer.write(frame)
            self._write_errors = 0
        except Exception as e:
            # 2026-09-06: mpeg4 'Invalid pts' 等の OpenCV 内部例外でプロセスごと落ちていた対策
            self._write_errors = getattr(self, '_write_errors', 0) + 1
            logger.error(f"VideoWriter.write failed ({self._write_errors}): {e}")
            try:
                now_t = time.time()
                if now_t - getattr(self, '_last_seg_restart', 0.0) < 30.0:
                    return
                self._last_seg_restart = now_t
                self._start_new_segment()
            except Exception as e2:
                logger.error(f"Could not restart recording segment: {e2}")
                if self._write_errors >= 10:
                    logger.error("Recording disabled after repeated failures")
                    self.recording = False

    def cleanup_old_recordings(self):
        try:
            self._cleanup_by_age()
            self._cleanup_by_free_space()
        except Exception as e:
            logger.error(f"cleanup error: {e}")

    def _cleanup_by_free_space(self):
        """空き容量が MIN_FREE_GB 未満なら、古い順に録画を消して MIN_FREE_GB+2 まで回復させる"""
        import shutil
        free_gb = shutil.disk_usage(RECORDING_DIR).free / 1024 ** 3
        if free_gb >= MIN_FREE_GB:
            return
        logger.warning(f"Low disk: {free_gb:.1f} GB free < {MIN_FREE_GB} GB - deleting oldest recordings")
        files = sorted(f for f in os.listdir(RECORDING_DIR) if f.endswith('.mp4'))
        target = MIN_FREE_GB + 2
        removed = 0
        for name in files[:-1]:  # 書き込み中の最新は残す
            try:
                os.remove(os.path.join(RECORDING_DIR, name)); removed += 1
            except OSError:
                continue
            if shutil.disk_usage(RECORDING_DIR).free / 1024 ** 3 >= target:
                break
        logger.info(f"Low-disk cleanup removed {removed} files, free now {shutil.disk_usage(RECORDING_DIR).free / 1024 ** 3:.1f} GB")

    def _cleanup_by_age(self):
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
        for filename in os.listdir(RECORDING_DIR):
            if not filename.endswith(".mp4"):
                continue
            filepath = os.path.join(RECORDING_DIR, filename)
            try:
                date_str = filename[:15]
                file_date = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
                if file_date < cutoff:
                    os.remove(filepath)
                    logger.info(f"Deleted old recording: {filename}")
            except (ValueError, OSError):
                pass

    def stop(self):
        self.running = False
        self.recording = False
        if self.video_writer is not None:
            self.video_writer.release()
        if self.cap is not None:
            self.cap.release()
        logger.info("Camera stopped")


# Flaskルート
_jpeg_cache = {"seq": -1, "buf": b"", "lock": threading.Lock()}


def generate_frames():
    """MJPEG配信。JPEG変換は1コマにつき1回だけ行い、閲覧者全員で共有する (低性能機のCPU節約)"""
    last_seq = -1
    while True:
        with frame_lock:
            frame = current_frame
            seq = HEALTH["frames"]
        if frame is None:
            time.sleep(0.1)
            continue
        if seq == last_seq:
            time.sleep(0.03)
            continue
        with _jpeg_cache["lock"]:
            if _jpeg_cache["seq"] != seq:
                ok, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                if ok:
                    _jpeg_cache["seq"], _jpeg_cache["buf"] = seq, buffer.tobytes()
            data = _jpeg_cache["buf"]
        last_seq = seq
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + data + b'\r\n')
        time.sleep(1.0 / STREAM_FPS)


@app.route('/')
def index():
    return '''
    <html>
    <head><title>Jihankichan Camera</title></head>
    <body style="margin:0; background:#000;">
        <img src="/video_feed" style="width:100%; height:100vh; object-fit:contain;">
    </body>
    </html>
    '''


@app.route('/health')
def health():
    now = time.time()
    return jsonify({
        'uptime_sec': int(now - HEALTH['start']),
        'frame_age_sec': round(now - HEALTH['last_frame'], 1) if HEALTH['last_frame'] else None,
        'frames': HEALTH['frames'],
        'last_det_ms': round(HEALTH['last_det_ms'], 1),
        'detections': HEALTH['detections'],
        'last_face_age_sec': round(now - HEALTH['last_face'], 1) if HEALTH['last_face'] else None,
        'camera_reopens': HEALTH['camera_reopens'],
        'discord_sent': HEALTH['discord_sent'],
        'detect_scale': DETECT_SCALE,
        'night_mode': is_night_time(),
        'passersby_today': HEALTH['passersby_today'],
        'visitors_today': HEALTH['visitors_today'],
        'tracks': HEALTH['tracks'],
        'motion_cells': HEALTH['motion_cells'],
        'clips_sent': HEALTH.get('clips_sent', 0),
        'clips_failed': HEALTH.get('clips_failed', 0),
        'clip_ring_frames': len(CLIPS.ring),
    })


@app.route('/threads')
def threads_view():
    """診断用: スレッド別の累積CPU秒 (2回取って差を見ると、どの処理がCPUを使っているか分かる)"""
    try:
        import psutil
        names = {t.native_id: t.name for t in threading.enumerate() if getattr(t, 'native_id', None)}
        rows = [{'name': names.get(th.id, str(th.id)), 'cpu_sec': round(th.user_time + th.system_time, 2)}
                for th in psutil.Process().threads()]
        rows.sort(key=lambda r: -r['cpu_sec'])
        return jsonify({'t': round(time.time(), 2), 'uptime_sec': int(time.time() - HEALTH['start']),
                        'threads': rows[:14]})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/stats')
def stats():
    """日別・時間別の通行人/来訪者数 (直近14日)"""
    return jsonify(VISITS.snapshot())


@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


def run_flask():
    app.run(host='0.0.0.0', port=8080, threaded=True, use_reloader=False)


def camera_thread(camera_manager, face_detector):
    """カメラ読み取り + 録画 + 顔検出ループ

    反応速度のため:
    - cap.read() 自体がカメラfpsでブロックするので、追加の sleep(0.066) は撤廃
      （以前は実効 ~7fps × 5フレームに1回検出 = 最悪700ms遅延だった）
    - 検出は FACE_CHECK_EVERY_N_FRAMES (既定2) フレームに1回 → ~130ms 間隔
    """
    cleanup_counter = 0
    last_submit = 0.0

    while camera_manager.running:
        try:
            frame = camera_manager.get_frame()
        except Exception as e:
            logger.error(f"camera get_frame error: {e}")
            frame = None
        if frame is False:
            pass   # デコード省略フレーム: すぐ次の grab へ
        elif frame is not None:
            try:
                camera_manager.write_frame(frame)
            except Exception as e:
                logger.error(f"camera write_frame error: {e}")
            try:
                CLIPS.push(frame)
            except Exception as e:
                logger.error(f"clip push error: {e}")

            t_now = time.time()
            if t_now - last_submit >= 1.0 / DETECT_RATE_HZ:
                last_submit = t_now
                face_detector.submit(frame)
        else:
            # カメラ未接続/読み取り失敗時にCPUを食い潰さない
            time.sleep(0.05)

        cleanup_counter += 1
        if cleanup_counter >= 25 * 60 * 10:
            camera_manager.cleanup_old_recordings()
            cleanup_counter = 0

        time.sleep(0.002)


def main():
    if not acquire_single_instance("Local\\JihankichanApp"):
        logger.error("Another Jihankichan instance is already running - exiting (code 75)")
        sys.exit(75)
    pygame.init()
    clock = pygame.time.Clock()

    # タスクバーを非表示
    hide_taskbar()

    # 全画面で開始
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    screen_width, screen_height = screen.get_size()
    pygame.display.set_caption("Jihankichan")
    pygame.mouse.set_visible(False)

    # ウィンドウを最前面に強制（起動直後、複数回試行で確実性UP）
    for attempt in range(3):
        if bring_pygame_to_front():
            logger.info(f"Pygame window brought to front (attempt {attempt+1})")
        time.sleep(0.3)

    try:
        start_window_keeper(pygame.display.get_wm_info().get('window'))
        logger.info("Window keeper started (SDL minimize-on-focus-loss disabled)")
    except Exception as e:
        logger.warning(f"window keeper not started: {e}")

    face = StackchanFace(screen_width, screen_height)

    # システム音量を最大に
    set_system_volume_max()

    # プロセス優先度を少し上げて、検出/TTSが他プロセスに待たされにくくする
    try:
        import psutil
        # 起動直後は再起動後の Windows 処理(TiWorker/Defender)に負けないよう HIGH、安定後に NORMAL へ戻す
        psutil.Process().nice(psutil.HIGH_PRIORITY_CLASS)
        logger.info("Process priority: HIGH during startup (will drop to NORMAL after 3 min)")
    except Exception as e:
        logger.warning(f"Could not raise process priority: {e}")

    # スピーカー初期化
    speaker = Speaker(face)

    # 顔認識初期化
    face_detector = FaceDetector(speaker)

    # ニュースアナウンサー初期化
    news_announcer = NewsAnnouncer(speaker)
    news_announcer.start()

    # カメラマネージャー初期化
    camera_manager = CameraManager(camera_index)
    if camera_manager.start():
        camera_manager.start_recording()

    # カメラスレッド開始
    cam_thread = threading.Thread(target=camera_thread, args=(camera_manager, face_detector), daemon=True)
    cam_thread.start()

    # Flaskサーバースレッド開始
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Jihankichan started!")
    logger.info(f"Camera stream: http://localhost:8080")
    logger.info(f"Claude API: {'Available' if news_announcer.client else 'Not available'}")
    logger.info(f"TTS: {'Ready (' + speaker.tts_voice_name + ')' if speaker.tts_ready else 'Initializing / fallback'}")
    logger.info(f"Rates: decode {DECODE_FPS}fps, detect<= {DETECT_RATE_HZ}/s, record {RECORD_FPS}fps, "
                f"UI {UI_FPS_IDLE}/{UI_FPS_SPEAKING}fps (idle/speaking), stream {STREAM_FPS}fps")

    running = True
    front_check_timer = 0.0
    startup_t0 = time.time()
    priority_lowered = False
    while running:
        ui_fps = UI_FPS_SPEAKING if (face.is_speaking or face.status_text) else UI_FPS_IDLE
        dt = clock.tick(ui_fps) / 1000.0  # 待機中はスライドショーだけなので低fpsで十分

        # TTS キュー処理（メインスレッドで実行）
        speaker.process_queue()

        # 起動3分後に優先度を NORMAL に戻す（sshd/Flask を飢えさせない）
        if not priority_lowered and time.time() - startup_t0 > 180:
            priority_lowered = True
            try:
                import psutil
                psutil.Process().nice(psutil.NORMAL_PRIORITY_CLASS)
                logger.info("Process priority: back to NORMAL")
            except Exception:
                pass

        # 10秒に1回、ウィンドウを最前面に再保証（背面に行くのを防ぐ）
        front_check_timer += dt
        if front_check_timer >= 10.0:
            front_check_timer = 0.0
            bring_pygame_to_front()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_c:
                    # カメラ切り替え
                    camera_manager.stop()
                    camera_manager.camera_index = 1 - camera_manager.camera_index
                    camera_manager.start()
                    camera_manager.start_recording()
                elif event.key == pygame.K_1:
                    face.set_emotion(Emotion.NEUTRAL)
                elif event.key == pygame.K_2:
                    face.set_emotion(Emotion.HAPPY)
                elif event.key == pygame.K_3:
                    face.set_emotion(Emotion.SAD)
                elif event.key == pygame.K_4:
                    face.set_emotion(Emotion.ANGRY)
                elif event.key == pygame.K_n:
                    # 手動でニュースを読み上げ
                    threading.Thread(target=news_announcer._announce_news, daemon=True).start()

        face.update(dt)
        face.draw(screen)
        pygame.display.flip()

    news_announcer.stop()
    camera_manager.stop()
    show_taskbar()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
