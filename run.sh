#!/usr/bin/env bash
# ============================================================
#  Запуск агента оцифровки визиток под macOS / Linux.
#  Создаёт venv (если нет), ставит зависимости, поднимает сервер
#  и открывает браузер на http://localhost:8000
#  Сделайте файл исполняемым один раз:  chmod +x run.sh
# ============================================================
set -euo pipefail

# Перейти в папку скрипта (корень проекта)
cd "$(dirname "$0")"

# Выбрать интерпретатор Python
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "[error] Python не найден. Установите Python 3.9+." >&2
    exit 1
fi

# --- Виртуальное окружение ---
if [ -f "venv/bin/activate" ]; then
    echo "[info] Активирую venv..."
    # shellcheck disable=SC1091
    source "venv/bin/activate"
else
    echo "[info] venv не найден, создаю..."
    "$PY" -m venv venv
    # shellcheck disable=SC1091
    source "venv/bin/activate"
    echo "[info] Устанавливаю зависимости..."
    python -m pip install --upgrade pip
    pip install -r requirements.txt
fi

# --- Конфиг ---
if [ ! -f "config.yaml" ]; then
    echo "[warn] config.yaml не найден. Копирую из шаблона config.example.yaml"
    cp config.example.yaml config.yaml
    echo "[warn] Откройте config.yaml и заполните настройки (движок, ключи, Google Sheets)."
fi

# --- Открыть браузер (в фоне; сервер поднимется через пару секунд) ---
URL="http://localhost:8000"
( sleep 2
  if command -v open >/dev/null 2>&1; then
      open "$URL"            # macOS
  elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$URL"        # Linux
  fi
) >/dev/null 2>&1 &

# --- Запуск сервера ---
echo "[info] Запускаю сервер на http://0.0.0.0:8000  (Ctrl+C для остановки)"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
