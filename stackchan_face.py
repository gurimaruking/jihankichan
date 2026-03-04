"""
スタックチャン（Stack-chan）の顔をWindows上で表示するプログラム
Pygameを使用してアニメーション付きの顔を描画します
"""

import pygame
import math
import random
import time

# 初期化
pygame.init()

# 画面サイズ（M5Stack Core2風のサイズ）
SCREEN_WIDTH = 320
SCREEN_HEIGHT = 240

# 色定義
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
SKIN_COLOR = (255, 220, 180)  # 肌色（背景）

# 画面作成
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("Stack-chan Face")

clock = pygame.time.Clock()


class StackchanFace:
    def __init__(self):
        # 顔の中心位置
        self.center_x = SCREEN_WIDTH // 2
        self.center_y = SCREEN_HEIGHT // 2

        # 目のパラメータ
        self.eye_width = 40
        self.eye_height = 40
        self.eye_spacing = 120  # 目の間隔（広め）
        self.eye_y_offset = -20  # 目のY位置オフセット

        # まばたきのパラメータ
        self.blink_state = 1.0  # 1.0 = 完全に開いている, 0.0 = 閉じている
        self.is_blinking = False
        self.blink_timer = 0
        self.next_blink_time = random.uniform(2.0, 5.0)

        # 視線のパラメータ
        self.gaze_x = 0  # -1.0 ~ 1.0
        self.gaze_y = 0  # -1.0 ~ 1.0
        self.target_gaze_x = 0
        self.target_gaze_y = 0

        # 呼吸アニメーション
        self.breath_phase = 0
        self.breath_speed = 2.0

        # 口のパラメータ
        self.mouth_open = 0.0  # 0.0 = 閉じている, 1.0 = 開いている
        self.is_speaking = False

    def update(self, dt):
        """アニメーション更新"""
        # まばたき処理
        self.blink_timer += dt

        if not self.is_blinking:
            if self.blink_timer >= self.next_blink_time:
                self.is_blinking = True
                self.blink_timer = 0
        else:
            # まばたきアニメーション（0.15秒で閉じて開く）
            blink_duration = 0.15
            if self.blink_timer < blink_duration / 2:
                self.blink_state = 1.0 - (self.blink_timer / (blink_duration / 2))
            elif self.blink_timer < blink_duration:
                self.blink_state = (self.blink_timer - blink_duration / 2) / (blink_duration / 2)
            else:
                self.blink_state = 1.0
                self.is_blinking = False
                self.blink_timer = 0
                self.next_blink_time = random.uniform(2.0, 5.0)

        # 視線のスムーズな追従
        self.gaze_x += (self.target_gaze_x - self.gaze_x) * 0.1
        self.gaze_y += (self.target_gaze_y - self.gaze_y) * 0.1

        # 呼吸アニメーション
        self.breath_phase += dt * self.breath_speed

        # 話しているときの口の動き
        if self.is_speaking:
            self.mouth_open = 0.3 + 0.3 * math.sin(time.time() * 15)
        else:
            self.mouth_open = max(0, self.mouth_open - dt * 5)

    def draw(self, surface):
        """顔を描画"""
        # 背景
        surface.fill(SKIN_COLOR)

        # 呼吸による微妙な動き
        breath_offset = math.sin(self.breath_phase) * 2

        # 目の位置計算
        left_eye_x = self.center_x - self.eye_spacing // 2
        right_eye_x = self.center_x + self.eye_spacing // 2
        eye_y = self.center_y + self.eye_y_offset + breath_offset

        # 視線によるオフセット
        gaze_offset_x = self.gaze_x * 8
        gaze_offset_y = self.gaze_y * 5

        # 左目を描画
        self._draw_eye(surface, left_eye_x + gaze_offset_x, eye_y + gaze_offset_y)

        # 右目を描画
        self._draw_eye(surface, right_eye_x + gaze_offset_x, eye_y + gaze_offset_y)

        # 口を描画
        mouth_y = self.center_y + 40 + breath_offset
        self._draw_mouth(surface, self.center_x, mouth_y)

    def _draw_eye(self, surface, x, y):
        """目を描画（スタックチャン風の丸い目）"""
        # まばたきに応じて高さを調整
        current_height = int(self.eye_height * self.blink_state)

        if current_height > 2:
            # 目の外形（黒い楕円）
            eye_rect = pygame.Rect(
                x - self.eye_width // 2,
                y - current_height // 2,
                self.eye_width,
                current_height
            )
            pygame.draw.ellipse(surface, BLACK, eye_rect)

            # ハイライト（白い小さな円）
            if self.blink_state > 0.5:
                highlight_x = int(x - self.eye_width // 4)
                highlight_y = int(y - current_height // 4)
                pygame.draw.circle(surface, WHITE, (highlight_x, highlight_y), 6)
        else:
            # 閉じた目（横線）
            pygame.draw.line(
                surface, BLACK,
                (x - self.eye_width // 2, y),
                (x + self.eye_width // 2, y),
                3
            )

    def _draw_mouth(self, surface, x, y):
        """口を描画"""
        mouth_width = 30

        if self.mouth_open > 0.1:
            # 開いた口（楕円）
            mouth_height = int(15 * self.mouth_open)
            mouth_rect = pygame.Rect(
                x - mouth_width // 2,
                y - mouth_height // 2,
                mouth_width,
                mouth_height
            )
            pygame.draw.ellipse(surface, BLACK, mouth_rect)
        else:
            # 閉じた口（にっこり曲線）
            points = []
            for i in range(21):
                angle = math.pi * i / 20
                px = x - mouth_width // 2 + int(mouth_width * i / 20)
                py = y + int(math.sin(angle) * 5)
                points.append((px, py))

            if len(points) > 1:
                pygame.draw.lines(surface, BLACK, False, points, 3)

    def set_gaze(self, x, y):
        """視線を設定（-1.0 ~ 1.0）"""
        self.target_gaze_x = max(-1.0, min(1.0, x))
        self.target_gaze_y = max(-1.0, min(1.0, y))

    def start_speaking(self):
        """話し始める"""
        self.is_speaking = True

    def stop_speaking(self):
        """話し終わる"""
        self.is_speaking = False


def main():
    """メインループ"""
    face = StackchanFace()
    running = True

    print("スタックチャンの顔を表示中...")
    print("操作方法:")
    print("  - マウス移動: 視線が追従します")
    print("  - スペースキー: 話すアニメーション")
    print("  - Escキー: 終了")

    while running:
        dt = clock.tick(60) / 1000.0  # デルタタイム（秒）

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    face.start_speaking()
            elif event.type == pygame.KEYUP:
                if event.key == pygame.K_SPACE:
                    face.stop_speaking()

        # マウス位置で視線を制御
        mouse_x, mouse_y = pygame.mouse.get_pos()
        gaze_x = (mouse_x - SCREEN_WIDTH // 2) / (SCREEN_WIDTH // 2)
        gaze_y = (mouse_y - SCREEN_HEIGHT // 2) / (SCREEN_HEIGHT // 2)
        face.set_gaze(gaze_x, gaze_y)

        # 更新と描画
        face.update(dt)
        face.draw(screen)

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
