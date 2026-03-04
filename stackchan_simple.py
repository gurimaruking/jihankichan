"""
シンプルなスタックチャン（Stack-chan）の顔
黒背景に白い目と口のミニマルなデザイン
Fキーで全画面切り替え
"""

import pygame
import random

# 初期化
pygame.init()

# 基本サイズ（M5Stack風）
BASE_WIDTH = 320
BASE_HEIGHT = 240

# 色定義
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

clock = pygame.time.Clock()


class SimpleStackchan:
    def __init__(self, screen_width, screen_height):
        self.update_size(screen_width, screen_height)

        # まばたき
        self.blink_state = 1.0
        self.is_blinking = False
        self.blink_timer = 0
        self.next_blink_time = random.uniform(2.0, 5.0)

        # 視線オフセット
        self.gaze_x = 0
        self.gaze_y = 0
        self.target_gaze_x = 0
        self.target_gaze_y = 0

    def update_size(self, screen_width, screen_height):
        """画面サイズに合わせてパラメータを更新"""
        self.screen_width = screen_width
        self.screen_height = screen_height

        # スケール計算（基本サイズからの倍率）
        self.scale = min(screen_width / BASE_WIDTH, screen_height / BASE_HEIGHT)

        # 顔の中心
        self.center_x = screen_width // 2
        self.center_y = screen_height // 2

        # 目のパラメータ（スケールに合わせる）
        self.eye_radius = int(8 * self.scale)
        self.eye_spacing = int(100 * self.scale)  # 目の間隔（広め）
        self.eye_y = self.center_y - int(20 * self.scale)

        # 口のパラメータ
        self.mouth_width = int(50 * self.scale)
        self.mouth_thickness = max(2, int(4 * self.scale))
        self.mouth_y = self.center_y + int(35 * self.scale)

        # 視線の移動量もスケール
        self.gaze_scale_x = int(5 * self.scale)
        self.gaze_scale_y = int(3 * self.scale)

    def update(self, dt):
        """アニメーション更新"""
        # まばたき処理
        self.blink_timer += dt

        if not self.is_blinking:
            if self.blink_timer >= self.next_blink_time:
                self.is_blinking = True
                self.blink_timer = 0
        else:
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

    def draw(self, surface):
        """顔を描画"""
        # 黒背景
        surface.fill(BLACK)

        # 視線オフセット
        gaze_offset_x = self.gaze_x * self.gaze_scale_x
        gaze_offset_y = self.gaze_y * self.gaze_scale_y

        # 左目
        left_eye_x = self.center_x - self.eye_spacing // 2 + gaze_offset_x
        left_eye_y = self.eye_y + gaze_offset_y
        self._draw_eye(surface, left_eye_x, left_eye_y)

        # 右目
        right_eye_x = self.center_x + self.eye_spacing // 2 + gaze_offset_x
        right_eye_y = self.eye_y + gaze_offset_y
        self._draw_eye(surface, right_eye_x, right_eye_y)

        # 口（白い横線）
        mouth_start = (self.center_x - self.mouth_width // 2, self.mouth_y)
        mouth_end = (self.center_x + self.mouth_width // 2, self.mouth_y)
        pygame.draw.line(surface, WHITE, mouth_start, mouth_end, self.mouth_thickness)

    def _draw_eye(self, surface, x, y):
        """目を描画（白い小さな円）"""
        if self.blink_state > 0.3:
            # 開いた目（白い円）
            current_radius = int(self.eye_radius * self.blink_state)
            pygame.draw.circle(surface, WHITE, (int(x), int(y)), current_radius)
        else:
            # 閉じた目（白い横線）
            pygame.draw.line(
                surface, WHITE,
                (int(x) - self.eye_radius, int(y)),
                (int(x) + self.eye_radius, int(y)),
                max(1, int(2 * self.scale))
            )

    def set_gaze(self, x, y):
        """視線を設定"""
        self.target_gaze_x = max(-1.0, min(1.0, x))
        self.target_gaze_y = max(-1.0, min(1.0, y))


def main():
    # ウィンドウモードで開始
    screen_width = BASE_WIDTH * 2  # 少し大きめに
    screen_height = BASE_HEIGHT * 2
    screen = pygame.display.set_mode((screen_width, screen_height), pygame.RESIZABLE)
    pygame.display.set_caption("Stack-chan")

    face = SimpleStackchan(screen_width, screen_height)
    running = True
    is_fullscreen = False

    print("シンプルなスタックチャンを表示中...")
    print("  - Fキー: 全画面切り替え")
    print("  - マウス移動: 視線追従")
    print("  - Escキー: 終了（全画面時はウィンドウモードに戻る）")

    while running:
        dt = clock.tick(60) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if is_fullscreen:
                        # 全画面からウィンドウモードに戻る
                        screen = pygame.display.set_mode(
                            (BASE_WIDTH * 2, BASE_HEIGHT * 2), pygame.RESIZABLE
                        )
                        is_fullscreen = False
                        screen_width, screen_height = screen.get_size()
                        face.update_size(screen_width, screen_height)
                    else:
                        running = False

                elif event.key == pygame.K_f:
                    # 全画面切り替え
                    is_fullscreen = not is_fullscreen
                    if is_fullscreen:
                        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                    else:
                        screen = pygame.display.set_mode(
                            (BASE_WIDTH * 2, BASE_HEIGHT * 2), pygame.RESIZABLE
                        )
                    screen_width, screen_height = screen.get_size()
                    face.update_size(screen_width, screen_height)

            elif event.type == pygame.VIDEORESIZE:
                if not is_fullscreen:
                    screen_width, screen_height = event.w, event.h
                    screen = pygame.display.set_mode(
                        (screen_width, screen_height), pygame.RESIZABLE
                    )
                    face.update_size(screen_width, screen_height)

        # マウス位置で視線を制御
        mouse_x, mouse_y = pygame.mouse.get_pos()
        gaze_x = (mouse_x - screen_width // 2) / (screen_width // 2)
        gaze_y = (mouse_y - screen_height // 2) / (screen_height // 2)
        face.set_gaze(gaze_x, gaze_y)

        face.update(dt)
        face.draw(screen)
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
