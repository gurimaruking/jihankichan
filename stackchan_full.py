"""
ジハンキチャン - UIAPduino Pro Micro自販機用スクリプト
- オリジナルstack-chanに近い顔アニメーション
- カメラ映像をWebサーバーで配信（http://localhost:8080）
- 3日分の録画をローカル保存
- 顔認識で「いらっしゃいませー」と購入案内
- 待機時は製品紹介スライドショー
- 10分に1回秋葉原ニュースを喋る
"""

import pygame
import math
import random
import threading
import cv2
import os
import sys
import time
from datetime import datetime, timedelta
from flask import Flask, Response
import logging
import ctypes

# Windowsタスクバー非表示用
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
except:
    def hide_taskbar(): pass
    def show_taskbar(): pass

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

# ログ設定
LOG_DIR = os.path.join(os.path.expanduser("~"), "stackchan_logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "stackchan.log"), encoding='utf-8'),
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
camera_index = 0

# 録画設定
RECORDING_DIR = os.path.join(os.path.expanduser("~"), "stackchan_recordings")
RETENTION_DAYS = 3
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
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# ニュース間隔（秒）
NEWS_INTERVAL = 600  # 10分

# 製品情報
PRODUCT_NAME = "UIAPduino Pro Micro CH32V003"
PRODUCT_PRICE = "300円"
PRODUCT_IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")

# 売り切れモード（True: 売り切れ表示、False: 通常販売）
SOLD_OUT = False

# スライドショー設定
SLIDESHOW_INTERVAL = 5  # 5秒ごとに切り替え

# 夜間ミュート設定（23時〜8時は音声を出さない）
NIGHT_START_HOUR = 23  # 夜間開始（23時）
NIGHT_END_HOUR = 8     # 夜間終了（8時）


def is_night_time():
    """現在が夜間（ミュート時間）かどうかを判定"""
    current_hour = datetime.now().hour
    if NIGHT_START_HOUR > NIGHT_END_HOUR:
        # 23時〜8時のように日をまたぐ場合
        return current_hour >= NIGHT_START_HOUR or current_hour < NIGHT_END_HOUR
    else:
        # 同じ日の場合
        return NIGHT_START_HOUR <= current_hour < NIGHT_END_HOUR


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
        self.slideshow_images = []
        self.current_slide = 0
        self.slideshow_timer = 0

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
        """スライドショー用の製品画像を読み込む（JPG/PNG/WEBP対応）"""
        try:
            from PIL import Image

            # 対応する拡張子
            valid_ext = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')

            # ディレクトリ内の画像を全て読み込む
            if not os.path.isdir(PRODUCT_IMAGES_DIR):
                logger.warning(f"Image directory not found: {PRODUCT_IMAGES_DIR}")
                return

            filenames = sorted([
                f for f in os.listdir(PRODUCT_IMAGES_DIR)
                if f.lower().endswith(valid_ext)
            ])

            # 画像表示エリアのサイズ（画面上部30%を使う→テキスト領域を広げる）
            max_w = int(self.screen_width * 0.80)
            max_h = int(self.screen_height * 0.30)

            for filename in filenames:
                filepath = os.path.join(PRODUCT_IMAGES_DIR, filename)
                try:
                    pil_img = Image.open(filepath)
                    pil_img = pil_img.convert('RGB')
                    pil_img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)

                    mode = pil_img.mode
                    size = pil_img.size
                    data = pil_img.tobytes()
                    pygame_img = pygame.image.fromstring(data, size, mode)
                    self.slideshow_images.append(pygame_img)
                    logger.info(f"Loaded slideshow image: {filename} ({size[0]}x{size[1]})")
                except Exception as e:
                    logger.error(f"Failed to load {filename}: {e}")

            logger.info(f"Loaded {len(self.slideshow_images)} slideshow images")
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

            # --- 製品名（大きく太字） ---
            name_text = self.font_product_name.render(PRODUCT_NAME, True, CYAN)
            name_rect = name_text.get_rect(center=(cx, int(sh * 0.33)))
            surface.blit(name_text, name_rect)

            # --- 価格（超大型、画面の中心に） ---
            price_text = self.font_price.render(PRODUCT_PRICE, True, YELLOW)
            price_rect = price_text.get_rect(center=(cx, int(sh * 0.48)))
            surface.blit(price_text, price_rect)

            # --- 購入案内（日本語） ---
            guide_ja = self.font_guide.render(
                "100円玉3枚 → ボタンを押してね！", True, WHITE)
            guide_ja_rect = guide_ja.get_rect(center=(cx, int(sh * 0.62)))
            surface.blit(guide_ja, guide_ja_rect)

            # --- 購入案内（英語） ---
            guide_en = self.font_guide.render(
                "Insert 3x 100yen coins & push button!", True, WHITE)
            guide_en_rect = guide_en.get_rect(center=(cx, int(sh * 0.68)))
            surface.blit(guide_en, guide_en_rect)

            # --- 両替案内（重要情報） ---
            change_text = self.font_case_return.render(
                "お店が開いている時は両替対応します", True, CYAN)
            change_rect = change_text.get_rect(center=(cx, int(sh * 0.76)))
            surface.blit(change_text, change_rect)

            # --- 店内案内（重要情報） ---
            instore_text = self.font_case_return.render(
                "店内でもマイコンボード扱ってます", True, CYAN)
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

            # --- 製品名 ---
            name_text = self.font_product_name.render(PRODUCT_NAME, True, CYAN)
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
    """TTS用のシンプルなスピーカークラス（キュー方式）"""

    def __init__(self, face):
        self.face = face
        self.tts_engine = None
        self.speech_queue = []  # シンプルなリストをキューとして使う
        self.is_speaking = False

        # TTS初期化
        if TTS_AVAILABLE:
            try:
                self.tts_engine = pyttsx3.init()
                self.tts_engine.setProperty('rate', 150)
                voices = self.tts_engine.getProperty('voices')
                for voice in voices:
                    if 'japan' in voice.name.lower() or 'japanese' in voice.name.lower():
                        self.tts_engine.setProperty('voice', voice.id)
                        break
                logger.info("TTS initialized")
            except Exception as e:
                logger.error(f"TTS init error: {e}")
                self.tts_engine = None

    def queue_speak(self, text):
        """キューに追加（別スレッドから呼ばれる）"""
        self.speech_queue.append(text)
        logger.info(f"Queued speech: {text[:30]}...")

    def process_queue(self):
        """キューを処理（メインループから呼ばれる）"""
        if self.is_speaking or not self.speech_queue:
            return

        text = self.speech_queue.pop(0)
        self._speak_now(text)

    def _speak_now(self, text):
        """実際に音声を再生（PowerShell経由、別スレッド）"""
        # 夜間はミュート（字幕のみ表示）
        if is_night_time():
            logger.info(f"Night mode - muted: {text}")
            self.face.status_text = text  # 字幕は表示
            self.face.set_emotion(Emotion.HAPPY)
            # 字幕を数秒表示してからクリア
            def clear_subtitle():
                time.sleep(4)
                self.face.status_text = ""
                self.face.set_emotion(Emotion.NEUTRAL)
                self.is_speaking = False
            self.is_speaking = True
            threading.Thread(target=clear_subtitle, daemon=True).start()
            return

        self.is_speaking = True
        self.face.start_speaking()
        self.face.set_emotion(Emotion.HAPPY)
        self.face.status_text = text  # 全文表示
        logger.info(f"Speaking: {text}")

        # 別スレッドでTTS実行（メインループをブロックしない）
        def run_tts():
            import subprocess
            import tempfile
            try:
                # VBScriptで高速TTS（Shift-JISで書き込み）
                escaped_text = text.replace('"', '""')
                vbs_content = f'''Set sapi = CreateObject("SAPI.SpVoice")
sapi.Rate = 1
sapi.Volume = 100
sapi.Speak "{escaped_text}"
'''
                # 一時ファイルに書き込み（Shift-JISエンコーディング）
                vbs_path = os.path.join(os.environ.get('TEMP', '.'), 'jihankichan_tts.vbs')
                with open(vbs_path, 'w', encoding='shift_jis', errors='replace') as f:
                    f.write(vbs_content)

                subprocess.run(
                    ["cscript", "//nologo", vbs_path],
                    capture_output=True,
                    timeout=30
                )

            except Exception as e:
                logger.error(f"TTS error: {e}")
            finally:
                # 音声終了後も字幕を2秒間表示し続ける
                time.sleep(2)
                self.face.stop_speaking()
                self.face.set_emotion(Emotion.NEUTRAL)
                self.face.status_text = ""
                self.is_speaking = False

        threading.Thread(target=run_tts, daemon=True).start()


class FaceDetector:
    """顔認識でいらっしゃいませーを言う"""

    def __init__(self, speaker):
        self.speaker = speaker
        self.last_greeting_time = 0
        self.greeting_cooldown = 30  # 30秒間は同じ人に挨拶しない

        # OpenCV顔認識カスケード
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        logger.info("Face detector initialized")

    def check_frame(self, frame):
        """フレームをチェックして顔があれば挨拶"""
        if frame is None:
            return

        current_time = time.time()
        if current_time - self.last_greeting_time < self.greeting_cooldown:
            return

        try:
            # グレースケールに変換
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # 顔検出（バランス調整）
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.15,
                minNeighbors=4,
                minSize=(60, 60)
            )

            if len(faces) > 0:
                self.last_greeting_time = current_time
                logger.info(f"Face detected! ({len(faces)} faces)")

                if SOLD_OUT:
                    # 売り切れ時のグリーティング
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
                    # 通常時のグリーティング
                    greetings = [
                        "いらっしゃいませー！マイコンボード300円！100円玉3枚でゲットしてね！",
                        "こんにちは！300円でマイコンボード買えるよ！お土産にどうぞ！",
                        "いらっしゃいませ！秋葉原のお土産にマイコンボードはいかが？300円！",
                        "あ、お客さんだ！UIAPduino300円！ボタンをポチッとね！",
                        "Welcome! Microcontroller board only 300 yen! Great souvenir from Akihabara!",
                        "Hello! Get your microcontroller for just 300 yen! Insert 3 coins!",
                        "Hi there! UIAPduino Pro Micro, 300 yen! Perfect Akihabara souvenir!",
                        "いらっしゃいませ！Welcome! マイコンボード300円だよ！",
                        "Hello! こんにちは！300 yen for a microcontroller! お土産にどうぞ！",
                        "いらっしゃいませ！300円でマイコンゲット！ケースが要らなかったら右横のかごに入れてね！",
                        "Welcome! 300 yen! If you don't need the case, put it in the basket above!",
                        # 両替・店内案内（重要なので多めに入れる）
                        "いらっしゃいませ！お店が開いてる時は両替もできるよ！マイコンボード300円！",
                        "こんにちは！両替が必要ならお店に声かけてね！マイコンボード300円だよ！",
                        "いらっしゃい！店内でもマイコンボード扱ってるよ！ぜひ見ていってね！",
                        "いらっしゃいませ！マイコンボード300円！店内にも色々あるよ！",
                        "Welcome! We can make change when the store is open! 300 yen!",
                        "Hi! We also sell microcontroller boards inside the store! Come check it out!",
                    ]
                greeting = random.choice(greetings)
                self.speaker.queue_speak(greeting)

        except Exception as e:
            logger.error(f"Face detection error: {e}")


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
                    "売り切れだけど、店内でもマイコンボード扱ってるよ！ぜひ見ていってね！",
                    "Sold out here, but we have more boards inside the store!",
                ]
            else:
                souvenir_messages = [
                    "秋葉原のお土産にマイコンボードはいかが？たったの300円！",
                    "Akihabara souvenir! Microcontroller board only 300 yen!",
                    "マイコンボード、お土産に買ってね！300円だよ！",
                    "お土産コーナー！UIAPduino Pro Micro、300円で販売中！",
                    "Hey! Get a microcontroller as a souvenir! Just 300 yen!",
                    "秋葉原らしいお土産！マイコンボード300円！プログラミングしよう！",
                    # 両替・店内案内（重要情報）
                    "お知らせ！お店が開いている時は両替対応するよ！気軽に声かけてね！",
                    "店内でもマイコンボード扱ってます！ぜひ店内も見ていってね！",
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
        logger.info(f"Camera {self.camera_index} started")
        return True

    def get_frame(self):
        global current_frame
        if self.cap is None or not self.cap.isOpened():
            return None
        ret, frame = self.cap.read()
        if ret:
            with frame_lock:
                current_frame = frame.copy()
            return frame
        return None

    def start_recording(self):
        self.recording = True
        self._start_new_segment()
        logger.info("Recording started")

    def _start_new_segment(self):
        if self.video_writer is not None:
            self.video_writer.release()
        now = datetime.now()
        filename = now.strftime("%Y%m%d_%H%M%S") + ".mp4"
        filepath = os.path.join(RECORDING_DIR, filename)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.video_writer = cv2.VideoWriter(filepath, fourcc, 15, (640, 480))
        self.current_segment_start = now
        logger.info(f"New recording segment: {filename}")

    def write_frame(self, frame):
        if not self.recording or self.video_writer is None:
            return
        now = datetime.now()
        if (now - self.current_segment_start).seconds >= SEGMENT_MINUTES * 60:
            self._start_new_segment()
        self.video_writer.write(frame)

    def cleanup_old_recordings(self):
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
def generate_frames():
    while True:
        with frame_lock:
            if current_frame is None:
                time.sleep(0.1)
                continue
            frame = current_frame.copy()
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ret:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        time.sleep(0.066)


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


@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


def run_flask():
    app.run(host='0.0.0.0', port=8080, threaded=True, use_reloader=False)


def camera_thread(camera_manager, face_detector):
    cleanup_counter = 0
    face_check_counter = 0

    while camera_manager.running:
        frame = camera_manager.get_frame()
        if frame is not None:
            camera_manager.write_frame(frame)

            # 5フレームに1回顔検出（負荷軽減）
            face_check_counter += 1
            if face_check_counter >= 5:
                face_check_counter = 0
                face_detector.check_frame(frame)

        cleanup_counter += 1
        if cleanup_counter >= 15 * 60 * 60:
            camera_manager.cleanup_old_recordings()
            cleanup_counter = 0

        time.sleep(0.066)


def main():
    pygame.init()
    clock = pygame.time.Clock()

    # タスクバーを非表示
    hide_taskbar()

    # 全画面で開始
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    screen_width, screen_height = screen.get_size()
    pygame.display.set_caption("Jihankichan")
    pygame.mouse.set_visible(False)

    face = StackchanFace(screen_width, screen_height)

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
    logger.info(f"TTS: {'Available' if speaker.tts_engine else 'Not available'}")

    running = True
    while running:
        dt = clock.tick(30) / 1000.0  # 30FPSで十分

        # TTS キュー処理（メインスレッドで実行）
        speaker.process_queue()

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
