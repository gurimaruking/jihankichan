"""
スタックチャン（Stack-chan）の顔 - カメラ顔認識版
カメラで人の顔を検出して視線が追従します
"""

import pygame
import cv2
import random
import threading
import numpy as np

# 初期化
pygame.init()

# 基本サイズ
BASE_WIDTH = 320
BASE_HEIGHT = 240

# 色定義
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

clock = pygame.time.Clock()


class FaceDetector:
    """カメラで顔を検出するクラス"""

    def __init__(self):
        self.cap = None
        self.face_cascade = None
        self.running = False
        self.face_position = (0.0, 0.0)  # -1.0 ~ 1.0 の正規化座標
        self.face_detected = False
        self.lock = threading.Lock()
        self.frame = None  # デバッグ表示用

    def start(self):
        """カメラと顔検出を開始"""
        # カメラ初期化
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            print("エラー: カメラを開けませんでした")
            return False

        # カメラ解像度を設定（処理を軽くするため）
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

        # Haar Cascade分類器を読み込み
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        self.face_cascade = cv2.CascadeClassifier(cascade_path)

        if self.face_cascade.empty():
            print("エラー: 顔検出用の分類器を読み込めませんでした")
            return False

        self.running = True

        # 別スレッドで顔検出を実行
        self.thread = threading.Thread(target=self._detection_loop, daemon=True)
        self.thread.start()

        print("カメラ顔検出を開始しました")
        return True

    def _detection_loop(self):
        """顔検出ループ（別スレッドで実行）"""
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                continue

            # 左右反転（鏡像）
            frame = cv2.flip(frame, 1)

            # グレースケールに変換
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # 顔検出
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(30, 30)
            )

            h, w = frame.shape[:2]

            with self.lock:
                if len(faces) > 0:
                    # 最も大きい顔を使用
                    largest_face = max(faces, key=lambda f: f[2] * f[3])
                    x, y, fw, fh = largest_face

                    # 顔の中心を計算
                    face_center_x = x + fw // 2
                    face_center_y = y + fh // 2

                    # -1.0 ~ 1.0 に正規化
                    self.face_position = (
                        (face_center_x - w // 2) / (w // 2),
                        (face_center_y - h // 2) / (h // 2)
                    )
                    self.face_detected = True

                    # デバッグ用：顔に矩形を描画
                    cv2.rectangle(frame, (x, y), (x + fw, y + fh), (0, 255, 0), 2)
                else:
                    self.face_detected = False

                self.frame = frame

    def get_face_position(self):
        """顔の位置を取得"""
        with self.lock:
            return self.face_position, self.face_detected

    def get_frame(self):
        """現在のフレームを取得（デバッグ用）"""
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def stop(self):
        """カメラを停止"""
        self.running = False
        if self.cap is not None:
            self.cap.release()
        print("カメラを停止しました")


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

        # スケール計算
        self.scale = min(screen_width / BASE_WIDTH, screen_height / BASE_HEIGHT)

        # 顔の中心
        self.center_x = screen_width // 2
        self.center_y = screen_height // 2

        # 目のパラメータ
        self.eye_radius = int(8 * self.scale)
        self.eye_spacing = int(60 * self.scale)
        self.eye_y = self.center_y - int(20 * self.scale)

        # 口のパラメータ
        self.mouth_width = int(50 * self.scale)
        self.mouth_thickness = max(2, int(4 * self.scale))
        self.mouth_y = self.center_y + int(35 * self.scale)

        # 視線の移動量
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

    def draw(self, surface, face_detected=True):
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

        # 顔未検出時はインジケータ表示
        if not face_detected:
            indicator_radius = int(5 * self.scale)
            pygame.draw.circle(
                surface, (100, 100, 100),
                (int(30 * self.scale), int(30 * self.scale)),
                indicator_radius
            )

    def _draw_eye(self, surface, x, y):
        """目を描画"""
        if self.blink_state > 0.3:
            current_radius = int(self.eye_radius * self.blink_state)
            pygame.draw.circle(surface, WHITE, (int(x), int(y)), current_radius)
        else:
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
    # 顔検出器を初期化
    detector = FaceDetector()
    if not detector.start():
        print("カメラの初期化に失敗しました。マウスモードで起動します。")
        use_camera = False
    else:
        use_camera = True

    # ウィンドウ作成
    screen_width = BASE_WIDTH * 2
    screen_height = BASE_HEIGHT * 2
    screen = pygame.display.set_mode((screen_width, screen_height), pygame.RESIZABLE)
    pygame.display.set_caption("Stack-chan (Camera)")

    face = SimpleStackchan(screen_width, screen_height)
    running = True
    is_fullscreen = False
    show_camera = False  # カメラプレビュー表示

    print("\nスタックチャン（カメラ顔認識版）を表示中...")
    print("  - Fキー: 全画面切り替え")
    print("  - Cキー: カメラプレビュー表示/非表示")
    print("  - Escキー: 終了")

    while running:
        dt = clock.tick(60) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if is_fullscreen:
                        screen = pygame.display.set_mode(
                            (BASE_WIDTH * 2, BASE_HEIGHT * 2), pygame.RESIZABLE
                        )
                        is_fullscreen = False
                        screen_width, screen_height = screen.get_size()
                        face.update_size(screen_width, screen_height)
                    else:
                        running = False

                elif event.key == pygame.K_f:
                    is_fullscreen = not is_fullscreen
                    if is_fullscreen:
                        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                    else:
                        screen = pygame.display.set_mode(
                            (BASE_WIDTH * 2, BASE_HEIGHT * 2), pygame.RESIZABLE
                        )
                    screen_width, screen_height = screen.get_size()
                    face.update_size(screen_width, screen_height)

                elif event.key == pygame.K_c:
                    show_camera = not show_camera

            elif event.type == pygame.VIDEORESIZE:
                if not is_fullscreen:
                    screen_width, screen_height = event.w, event.h
                    screen = pygame.display.set_mode(
                        (screen_width, screen_height), pygame.RESIZABLE
                    )
                    face.update_size(screen_width, screen_height)

        # 顔の位置を取得
        face_detected = False
        if use_camera:
            position, face_detected = detector.get_face_position()
            if face_detected:
                face.set_gaze(position[0], position[1])
        else:
            # カメラが使えない場合はマウスで制御
            mouse_x, mouse_y = pygame.mouse.get_pos()
            gaze_x = (mouse_x - screen_width // 2) / (screen_width // 2)
            gaze_y = (mouse_y - screen_height // 2) / (screen_height // 2)
            face.set_gaze(gaze_x, gaze_y)
            face_detected = True

        face.update(dt)
        face.draw(screen, face_detected)

        # カメラプレビュー表示
        if show_camera and use_camera:
            frame = detector.get_frame()
            if frame is not None:
                # OpenCV画像をPygame用に変換
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame = np.rot90(frame)
                frame = pygame.surfarray.make_surface(frame)

                # 小さくリサイズして右上に表示
                preview_size = (160, 120)
                frame = pygame.transform.scale(frame, preview_size)
                screen.blit(frame, (screen_width - preview_size[0] - 10, 10))

        pygame.display.flip()

    # 終了処理
    if use_camera:
        detector.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
