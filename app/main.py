"""FastAPI-приложение: веб-UI съёмки визиток + API конвейера.

Запуск (dev):  uvicorn app.main:app --host 0.0.0.0 --port 8000
С телефона:    http://<ip-ПК>:8000  (в той же Wi-Fi сети)

Маршруты:
    GET  /            — мобильная страница съёмки (app/web/index.html)
    GET  /static/*    — статика фронтенда
    GET  /photos/*    — сохранённые снимки визиток
    POST /upload      — приём фото (multipart), мгновенная постановка в очередь
    GET  /jobs        — статусы джобов (для живой ленты); попутно подтягивает
                        актуальный «Статус лида»/комментарий из Google Sheets
    GET  /settings    — текущие настройки (ключи замаскированы)
    POST /settings    — обновление настроек
    GET  /health      — проверка живости

Python 3.9-совместимо.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Config
from app.discovery import Discovery, get_lan_ip
from app.pipeline import JobManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cardscan.main")

WEB_DIR = Path(__file__).resolve().parent / "web"

# Допустимые движки распознавания (для UI настроек)
AVAILABLE_RECOGNIZERS = [
    "cloud:gemini",
    "cloud:openai",
    "cloud:claude",
    "local-ocr",
    "local-vision",
]

# расширение по mime-типу для сохранения снимка
_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config.load()
    manager = JobManager(config)
    manager.start()

    # Автопоиск ПК с телефона: публикуем себя в локальной сети (cardscan.local).
    # Регистрацию запускаем В ФОНЕ — она может занимать секунды (mDNS-проба),
    # и не должна задерживать старт сервера.
    port = int(config.get("server.port", 8000))
    discovery = Discovery(port=port, version=__version__)
    asyncio.create_task(asyncio.to_thread(discovery.start))

    app.state.config = config
    app.state.manager = manager
    app.state.discovery = discovery

    # Баннер с адресами для подключения телефона
    logger.info("=" * 56)
    logger.info("  Сканер визиток запущен. Движок: %s", config.recognizer)
    logger.info("  С телефона (та же Wi-Fi): %s", discovery.hostname_url())
    logger.info("  Или по IP:                %s", discovery.server_url())
    logger.info("  Подключение + QR:         %s/connect", discovery.server_url())
    logger.info("=" * 56)
    try:
        yield
    finally:
        await manager.stop()
        discovery.stop()


app = FastAPI(title="Сканер визиток", lifespan=lifespan)

# статика фронтенда и сохранённые фото
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# каталог фото монтируем лениво при старте (директория создаётся Config.photos_dir)
_photos_dir = Config.load().photos_dir
app.mount("/photos", StaticFiles(directory=str(_photos_dir)), name="photos")


def _manager(app_: FastAPI) -> JobManager:
    return app_.state.manager


def _config(app_: FastAPI) -> Config:
    return app_.state.config


@app.get("/")
async def index() -> Any:
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(
            {"error": "UI не найден (app/web/index.html). Сборка фронтенда не завершена."},
            status_code=500,
        )
    return FileResponse(str(index_file))


@app.get("/health")
async def health() -> Dict[str, Any]:
    cfg = _config(app)
    return {"status": "ok", "recognizer": cfg.recognizer}


# ---------- PWA: установка как приложение на телефон ----------

@app.get("/manifest.webmanifest")
async def manifest() -> Any:
    f = WEB_DIR / "manifest.webmanifest"
    if not f.exists():
        raise HTTPException(status_code=404, detail="manifest отсутствует")
    return FileResponse(str(f), media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker() -> Any:
    f = WEB_DIR / "sw.js"
    if not f.exists():
        raise HTTPException(status_code=404, detail="sw.js отсутствует")
    # Service-Worker-Allowed: / — чтобы SW, лежащий в корне, управлял всем сайтом
    return FileResponse(
        str(f),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


# ---------- Подключение телефона к ПК (автопоиск + QR) ----------

def _server_info() -> Dict[str, Any]:
    cfg = _config(app)
    port = int(cfg.get("server.port", 8000))
    disc = getattr(app.state, "discovery", None)
    lan_ip = disc.lan_ip if disc is not None else get_lan_ip()
    hostname = getattr(disc, "hostname", "cardscan")
    return {
        "lan_ip": lan_ip,
        "url": "http://{}:{}".format(lan_ip, port),
        "hostname": "{}.local".format(hostname),
        "hostname_url": "http://{}.local:{}".format(hostname, port),
        "port": port,
        "version": __version__,
    }


@app.get("/api/server-info")
async def server_info() -> Dict[str, Any]:
    return _server_info()


@app.get("/connect")
async def connect_page() -> Any:
    f = WEB_DIR / "connect.html"
    if not f.exists():
        return JSONResponse({"error": "connect.html отсутствует"}, status_code=500)
    return FileResponse(str(f))


@app.get("/qr.png")
async def qr_png() -> Any:
    """QR-код со ссылкой на сервер в локальной сети (для камеры телефона)."""
    info = _server_info()
    target = info["url"]
    try:
        import io
        import qrcode  # ленивый импорт

        img = qrcode.make(target)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception as exc:  # qrcode не установлен или ошибка генерации
        raise HTTPException(
            status_code=503,
            detail="QR недоступен ({}). Откройте вручную: {}".format(exc, target),
        )


# ---------- Подключение Google Sheets через интерфейс ----------

def _gspread_available() -> bool:
    try:
        import gspread  # noqa: F401
        import google.oauth2.service_account  # noqa: F401
        return True
    except Exception:
        return False


def _google_status() -> Dict[str, Any]:
    cfg = _config(app)
    from app.sheets import read_service_account_email

    cred_path = cfg.credentials_path()
    has_cred = cred_path.exists()
    email = read_service_account_email(cred_path) if has_cred else None
    sid = cfg.get("google_sheets.spreadsheet_id") or ""
    return {
        "enabled": bool(cfg.get("google_sheets.enabled", True)),
        "has_credentials": has_cred,
        "client_email": email,
        "spreadsheet_id": sid,
        "spreadsheet_url": ("https://docs.google.com/spreadsheets/d/%s" % sid) if sid else "",
        "worksheet": cfg.get("google_sheets.worksheet", "Лиды"),
        "sales_reps": cfg.sales_reps,
        "gspread_installed": _gspread_available(),
    }


@app.get("/setup")
async def setup_page() -> Any:
    f = WEB_DIR / "setup.html"
    if not f.exists():
        return JSONResponse({"error": "setup.html отсутствует"}, status_code=500)
    return FileResponse(str(f))


@app.get("/api/google/status")
async def google_status() -> Dict[str, Any]:
    return _google_status()


@app.post("/api/google/credentials")
async def google_upload_credentials(credentials: UploadFile) -> Dict[str, Any]:
    """Принимает JSON-ключ сервисного аккаунта и сохраняет его."""
    cfg = _config(app)
    raw = await credentials.read()
    try:
        import json

        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Файл не является корректным JSON")

    if (
        parsed.get("type") != "service_account"
        or not parsed.get("client_email")
        or not parsed.get("private_key")
    ):
        raise HTTPException(
            status_code=400,
            detail="Это не ключ сервисного аккаунта Google "
                   "(нужен JSON с type=service_account, client_email, private_key).",
        )

    dest = cfg.credentials_path()
    dest.write_bytes(raw)
    return {"ok": True, "client_email": parsed.get("client_email")}


@app.post("/api/google/config")
async def google_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Сохраняет настройки таблицы (ID/URL, лист, вкл/выкл, список менеджеров)."""
    cfg = _config(app)
    from app.sheets import extract_spreadsheet_id

    if "spreadsheet" in payload:
        sid = extract_spreadsheet_id(str(payload.get("spreadsheet") or ""))
        cfg.set("google_sheets.spreadsheet_id", sid)
    if "worksheet" in payload and str(payload.get("worksheet") or "").strip():
        cfg.set("google_sheets.worksheet", str(payload["worksheet"]).strip())
    if "enabled" in payload:
        cfg.set("google_sheets.enabled", bool(payload["enabled"]))
    if "sales_reps" in payload and isinstance(payload["sales_reps"], list):
        reps = [str(x).strip() for x in payload["sales_reps"] if str(x).strip()]
        cfg.set("sales_reps", reps)

    cfg.save()
    return _google_status()


@app.post("/api/google/test")
async def google_test() -> Dict[str, Any]:
    """Проверяет подключение: открывает таблицу и готовит заголовки."""
    cfg = _config(app)
    from app.sheets import SheetsWriter

    writer = SheetsWriter(cfg)
    try:
        info = await asyncio.to_thread(writer.test_connection)
        return {"ok": True, **info}
    except Exception as exc:  # noqa: BLE001 — ошибку показываем в UI
        return {"ok": False, "error": str(exc)}


@app.post("/upload")
async def upload(image: UploadFile, source_event: str = Form("")) -> Dict[str, str]:
    """Принимает фото визитки, СРАЗУ ставит в очередь и возвращает job_id.

    Не ждёт распознавания — сотрудник может тут же снимать следующую визитку.
    """
    cfg = _config(app)
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл изображения")

    mime = (image.content_type or "image/jpeg").lower()
    ext = _EXT_BY_MIME.get(mime, Path(image.filename or "").suffix or ".jpg")
    name = uuid.uuid4().hex[:12] + ext
    photo_path = cfg.photos_dir / name
    photo_path.write_bytes(data)

    job_id = _manager(app).enqueue(
        photo_path=photo_path,
        mime_type=mime,
        source_event=source_event.strip(),
        filename=image.filename or name,
    )
    return {"job_id": job_id}


@app.get("/jobs")
async def jobs() -> Dict[str, Any]:
    manager = _manager(app)
    await manager.refresh_lead_statuses()
    return {"jobs": manager.list_jobs()}


@app.get("/settings")
async def get_settings() -> Dict[str, Any]:
    cfg = _config(app)
    return {
        "recognizer": cfg.recognizer,
        "available_recognizers": AVAILABLE_RECOGNIZERS,
        "sales_reps": cfg.sales_reps,
        "config": cfg.public_dict(),
    }


@app.post("/settings")
async def post_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Обновляет ограниченный набор настроек и сохраняет config.yaml.

    Допустимые ключи: recognizer, sales_reps, и точечные пути вида
    "cloud.gemini.api_key", "google_sheets.spreadsheet_id" в payload["set"].
    """
    cfg = _config(app)

    if "recognizer" in payload:
        rec = str(payload["recognizer"])
        if rec not in AVAILABLE_RECOGNIZERS:
            raise HTTPException(status_code=400, detail="Неизвестный движок распознавания")
        cfg.set("recognizer", rec)

    if "sales_reps" in payload and isinstance(payload["sales_reps"], list):
        cfg.set("sales_reps", [str(x) for x in payload["sales_reps"]])

    for dotted, value in (payload.get("set") or {}).items():
        # маскированные значения (••••) не перезаписываем
        if isinstance(value, str) and value.startswith("••••"):
            continue
        cfg.set(str(dotted), value)

    cfg.save()
    return {"ok": True, "recognizer": cfg.recognizer}
