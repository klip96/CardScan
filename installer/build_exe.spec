# -*- mode: python ; coding: utf-8 -*-
# ============================================================
#  PyInstaller .spec — заготовка сборки приложения в exe.
#
#  Сборка:
#     pip install pyinstaller
#     pyinstaller installer/build_exe.spec
#
#  Точка входа — небольшой загрузчик, поднимающий uvicorn с app.main:app.
#  Так как app.main:app — ASGI-объект, а не скрипт, PyInstaller не может
#  взять его напрямую. Поэтому ниже сгенерирован файл-обёртка _entry.py.
#
#  Внимание к плейсхолдерам (<<< ... >>>): имя exe, иконка, режим onefile/onedir.
# ============================================================
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# --- Корень проекта (на уровень выше папки installer/) ---
# В .spec нет надёжного __file__, используем текущую рабочую директорию,
# из которой запускают pyinstaller (корень проекта).
PROJECT_ROOT = os.path.abspath(os.getcwd())
APP_DIR = os.path.join(PROJECT_ROOT, "app")

# --- Генерация файла-обёртки точки входа ---
ENTRY = os.path.join(PROJECT_ROOT, "_entry.py")
with open(ENTRY, "w", encoding="utf-8") as fh:
    fh.write(
        "import uvicorn\n"
        "import webbrowser\n"
        "import threading\n"
        "\n"
        "def _open():\n"
        "    import time; time.sleep(2)\n"
        "    try:\n"
        "        webbrowser.open('http://localhost:8000')\n"
        "    except Exception:\n"
        "        pass\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    threading.Thread(target=_open, daemon=True).start()\n"
        "    uvicorn.run('app.main:app', host='0.0.0.0', port=8000)\n"
    )

# --- Данные, которые нужно положить рядом с exe ---
datas = [
    # фронтенд: app/web -> app/web в сборке
    (os.path.join(APP_DIR, "web"), os.path.join("app", "web")),
]
# Шаблон конфигурации (config.yaml пользователь создаст сам)
example_cfg = os.path.join(PROJECT_ROOT, "config.example.yaml")
if os.path.exists(example_cfg):
    datas.append((example_cfg, "."))

# --- Скрытые импорты ---
# uvicorn/fastapi подтягивают часть зависимостей динамически.
hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("app")
# zeroconf нужен рантайму для публикации сервера в локальной сети
# (mDNS, cardscan.local). Его подмодули загружаются динамически —
# собираем их явно, иначе автопоиск ПК с телефона не заработает.
hiddenimports += collect_submodules("zeroconf")
# Опциональные распознаватели импортируются лениво; если планируете
# поставлять конкретный движок — добавьте его пакет сюда, например:
#   hiddenimports += collect_submodules("google.genai")   # cloud:gemini
#   hiddenimports += collect_submodules("openai")          # cloud:openai
#   hiddenimports += collect_submodules("anthropic")       # cloud:claude

# Дополнительные data-файлы зависимостей.
# zeroconf может тянуть свои не-python ресурсы — подхватываем их,
# если они есть в установленном пакете.
try:
    datas += collect_data_files("zeroconf")
except Exception:
    # Если у zeroconf нет data-файлов — это нормально, пропускаем.
    pass


a = Analysis(
    [ENTRY],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# --- onedir-режим (папка dist\CardScan\ с exe и зависимостями) ---
#     Этот вариант согласован с installer\installer.iss, который копирует
#     всю папку dist\CardScan\*. Для onefile сложите a.binaries/a.datas
#     прямо в EXE(...) и уберите COLLECT (см. документацию PyInstaller).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,           # onedir: бинарники собирает COLLECT ниже
    name="CardScan",                 # <<< имя итогового exe (должно совпадать с installer.iss)
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                    # <<< True = видно лог сервера; False = без консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                       # <<< путь к .ico при наличии, напр. "installer/app.ico"
)

# --- Сборка папки дистрибутива: dist\CardScan\ ---
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CardScan",                 # имя папки в dist\ (должно совпадать с installer.iss)
)
