#!/bin/bash
# Double-click để chạy trên macOS
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Chưa có Python 3. Cài tại https://www.python.org/downloads/ rồi chạy lại."
  read -n 1 -s -r -p "Nhấn phím bất kỳ để thoát..."; exit 1
fi

if [ ! -d .venv ]; then
  echo "Lần đầu chạy: đang cài môi trường (mất vài phút)..."
  python3 -m venv .venv || exit 1
  .venv/bin/pip install -q -r requirements.txt || exit 1
  .venv/bin/python -m playwright install chromium || exit 1
fi

# nếu cổng 8765 đang bị server cũ giữ thì tắt đi
lsof -ti :8765 | xargs kill 2>/dev/null

(sleep 2; open "http://127.0.0.1:8765") &
echo "Đang chạy tại http://127.0.0.1:8765 - đóng cửa sổ này để dừng."
.venv/bin/python app.py
