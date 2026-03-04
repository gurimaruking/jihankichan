#!/bin/bash
# スタックチャン環境セットアップスクリプト

echo "=== スタックチャン セットアップ開始 ==="

# Python確認
echo "Python version:"
python3 --version

# pip更新
echo "pip をアップグレード中..."
pip3 install --upgrade pip --quiet

# 必要なパッケージをインストール
echo "pygame をインストール中..."
pip3 install pygame --quiet

echo "opencv-python をインストール中..."
pip3 install opencv-python --quiet

# 確認
echo ""
echo "=== インストール確認 ==="
python3 -c "import pygame; print(f'pygame: {pygame.version.ver}')"
python3 -c "import cv2; print(f'opencv: {cv2.__version__}')"

echo ""
echo "=== セットアップ完了 ==="
echo "ファイル一覧:"
ls -la /home/karu/stackchan*.py 2>/dev/null || ls -la /home/karu/*.py

echo ""
echo "実行方法:"
echo "  python3 ~/stackchan_simple.py   # シンプル版"
echo "  python3 ~/stackchan_camera.py   # カメラ版"
