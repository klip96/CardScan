#!/usr/bin/env bash
# ============================================================
#  Установка Ollama и загрузка локальной vision-модели (macOS/Linux).
#  Запуск:
#     chmod +x ollama_setup/install_and_pull.sh
#     ./ollama_setup/install_and_pull.sh
#  При необходимости поменяйте модель в переменной MODEL.
# ============================================================
set -euo pipefail

# Модель по умолчанию (совпадает с config: local.vision.model = moondream).
# Альтернативы (точнее, но тяжелее): "qwen2.5-vl:3b", "gemma3:4b"
MODEL="moondream"

echo "[info] Проверяю наличие Ollama..."
if ! command -v ollama >/dev/null 2>&1; then
    echo "[info] Ollama не найдена. Устанавливаю..."
    if [ "$(uname)" = "Darwin" ]; then
        # macOS: официальный установочный скрипт тоже работает, но рекомендуется .app
        if command -v brew >/dev/null 2>&1; then
            brew install ollama || curl -fsSL https://ollama.com/install.sh | sh
        else
            curl -fsSL https://ollama.com/install.sh | sh
        fi
    else
        # Linux
        curl -fsSL https://ollama.com/install.sh | sh
    fi
fi

if ! command -v ollama >/dev/null 2>&1; then
    echo "[error] Ollama так и не появилась в PATH. Установите вручную: https://ollama.com/download" >&2
    exit 1
fi

echo "[info] Запускаю 'ollama serve' в фоне (если ещё не запущен)..."
if ! pgrep -x ollama >/dev/null 2>&1; then
    nohup ollama serve >/tmp/ollama_serve.log 2>&1 &
    sleep 3
fi

echo "[info] Загружаю модель '$MODEL' (может занять время при первом запуске)..."
ollama pull "$MODEL"

echo "[ok] Готово. Модель '$MODEL' доступна на http://localhost:11434"
echo "[ok] В config.yaml установите: recognizer: local-vision"
