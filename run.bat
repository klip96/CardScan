@echo off
REM ============================================================
REM  Запуск агента оцифровки визиток под Windows.
REM  Создаёт venv (если нет), ставит зависимости, поднимает сервер
REM  и открывает браузер на http://localhost:8000
REM ============================================================
setlocal

REM Перейти в папку скрипта (корень проекта)
cd /d "%~dp0"

REM --- Виртуальное окружение ---
if exist "venv\Scripts\activate.bat" (
    echo [info] Активирую venv...
    call "venv\Scripts\activate.bat"
) else (
    echo [info] venv не найден, создаю...
    python -m venv venv
    if errorlevel 1 (
        echo [error] Не удалось создать venv. Установлен ли Python и есть ли он в PATH?
        pause
        exit /b 1
    )
    call "venv\Scripts\activate.bat"
    echo [info] Устанавливаю зависимости...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
)

REM --- Конфиг ---
if not exist "config.yaml" (
    echo [warn] config.yaml не найден. Копирую из шаблона config.example.yaml
    copy /Y "config.example.yaml" "config.yaml" >nul
    echo [warn] Откройте config.yaml и заполните настройки (движок, ключи, Google Sheets).
)

REM --- Открыть браузер (сервер поднимется через пару секунд) ---
start "" "http://localhost:8000"

REM --- Запуск сервера ---
echo [info] Запускаю сервер на http://0.0.0.0:8000  (Ctrl+C для остановки)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

endlocal
