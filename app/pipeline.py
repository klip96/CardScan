"""Асинхронный конвейер обработки визиток.

Ключевой сценарий: сотрудник снимает визитки потоком. Каждое фото мгновенно
ставится в очередь и обрабатывается в фоне независимо, пока снимается следующее.

Этапы одного джоба:
    queued -> recognizing -> enriching -> writing -> done | failed

Распознавание/обогащение/запись — блокирующие (сеть/диск), поэтому выполняются
в пуле потоков через asyncio.to_thread, чтобы не блокировать event loop FastAPI.

Python 3.9-совместимо.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import Config
from app.recognizers.base import CardData

logger = logging.getLogger("cardscan.pipeline")

# Допустимые статусы джоба (для справки/валидации UI)
STATUSES = ("queued", "recognizing", "enriching", "writing", "done", "failed")


@dataclass
class Job:
    id: str
    photo_path: Path
    mime_type: str
    source_event: str = ""
    filename: str = ""
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    # result/sheet_row — первая визитка (обратная совместимость со старым UI);
    # results/sheet_rows — ВСЕ визитки, найденные на одном фото.
    result: Optional[Dict[str, Any]] = None
    results: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    sheet_row: Optional[int] = None
    sheet_rows: Optional[List[int]] = None
    # Статус лида (см. SheetsWriter.LEAD_STATUS_OPTIONS), комментарий и
    # ответственный от продаж из колонок Google Sheets — по одному на каждую
    # визитку, выровнены по индексу с results/sheet_rows. Заполняются офис-
    # менеджером в таблице и подтягиваются обратно через
    # JobManager.refresh_lead_statuses().
    lead_statuses: Optional[List[str]] = None
    lead_comments: Optional[List[str]] = None
    lead_responsible: Optional[List[str]] = None
    # Кто снял визитку (логин + должность из аккаунта, не из тела запроса) —
    # пишется в таблицу отдельными колонками "Сотрудник"/"Должность сотрудника".
    scanned_by: str = ""
    scanned_by_position: str = ""

    def to_public(self) -> Dict[str, Any]:
        """Представление джоба для отдачи в UI."""
        cards_count = len(self.results) if self.results else (1 if self.result else 0)
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "source_event": self.source_event,
            "filename": self.filename,
            "result": self.result,
            "results": self.results,
            "cards_count": cards_count,
            "error": self.error,
            "sheet_row": self.sheet_row,
            "sheet_rows": self.sheet_rows,
            "lead_statuses": self.lead_statuses,
            "lead_comments": self.lead_comments,
            "lead_responsible": self.lead_responsible,
            "scanned_by": self.scanned_by,
            "scanned_by_position": self.scanned_by_position,
        }


class JobManager:
    """Очередь джобов + фоновый воркер. Создаётся один на приложение."""

    def __init__(self, config: Config):
        self.config = config
        self._queue: "asyncio.Queue[str]" = asyncio.Queue()
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []  # порядок поступления
        self._worker_task: Optional[asyncio.Task] = None
        self._writer: Optional[Any] = None  # кэш SheetsWriter (авторизация не на каждый вызов)

    # ----- управление жизненным циклом -----
    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("Воркер конвейера запущен")

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    # ----- API для маршрутов -----
    def enqueue(
        self,
        photo_path: Path,
        mime_type: str,
        source_event: str,
        filename: str,
        scanned_by: str = "",
        scanned_by_position: str = "",
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            photo_path=Path(photo_path),
            mime_type=mime_type,
            source_event=source_event,
            filename=filename,
            scanned_by=scanned_by,
            scanned_by_position=scanned_by_position,
        )
        self._jobs[job_id] = job
        self._order.append(job_id)
        self._queue.put_nowait(job_id)
        logger.info("Джоб %s поставлен в очередь (источник=%r)", job_id, source_event)
        return job_id

    def list_jobs(self, limit: int = 100) -> List[Dict[str, Any]]:
        # свежие сверху
        ids = list(reversed(self._order))[:limit]
        return [self._jobs[i].to_public() for i in ids if i in self._jobs]

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def _sheets_writer(self) -> Any:
        if self._writer is None:
            from app.sheets import SheetsWriter  # ленивый импорт

            self._writer = SheetsWriter(self.config)
        return self._writer

    async def refresh_lead_statuses(self) -> None:
        """Подтягивает статус лида/комментарий/ответственного из Google Sheets.

        Вызывается на каждый /jobs (поллинг с телефона), поэтому все номера
        строк читаются ОДНИМ батч-запросом, а не по одному на визитку.
        Ошибки (нет сети, не настроена таблица) не критичны — просто не
        обновляем статусы в этом тике.
        """
        if not self.config.get("google_sheets.enabled", True):
            return

        targets = [j for j in self._jobs.values() if j.sheet_rows]
        rows: List[int] = []
        for j in targets:
            rows.extend(r for r in j.sheet_rows if r)
        if not rows:
            return

        try:
            statuses = await asyncio.to_thread(self._sheets_writer().read_statuses, rows)
        except Exception as exc:  # noqa: BLE001 — не критично для основного функционала
            logger.debug("Не удалось обновить статусы лидов из таблицы: %s", exc)
            return

        for j in targets:
            j.lead_statuses = [(statuses.get(r) or {}).get("status", "") for r in j.sheet_rows]
            j.lead_comments = [(statuses.get(r) or {}).get("comment", "") for r in j.sheet_rows]
            j.lead_responsible = [(statuses.get(r) or {}).get("responsible", "") for r in j.sheet_rows]

    # ----- воркер -----
    async def _worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None:
                self._queue.task_done()
                continue
            try:
                await self._process(job)
            except Exception as exc:  # noqa: BLE001 — воркер не должен умирать
                logger.exception("Джоб %s упал", job_id)
                job.status = "failed"
                job.error = str(exc)
            finally:
                self._queue.task_done()

    async def _process(self, job: Job) -> None:
        # читаем байты фото
        image_bytes = await asyncio.to_thread(job.photo_path.read_bytes)

        # --- шаг 1: распознавание (на фото может быть несколько визиток) ---
        job.status = "recognizing"
        cards = await asyncio.to_thread(self._recognize_cards, image_bytes, job.mime_type)
        if not cards:
            cards = [CardData(notes="Не распознано ни одной визитки")]
        for card in cards:
            card.source_event = job.source_event

        # --- шаг 2: обогащение из интернета (каждая визитка отдельно) ---
        if self.config.get("enrichment.enabled", True):
            job.status = "enriching"
            try:
                cards = await asyncio.to_thread(self._enrich_all, cards)
            except Exception as exc:  # обогащение не критично
                logger.warning("Обогащение джоба %s не удалось: %s", job.id, exc)

        # предварительный результат уже доступен для UI
        self._set_results(job, cards)

        # --- шаг 3: запись в Google Sheets (по строке на визитку) ---
        if self.config.get("google_sheets.enabled", True):
            job.status = "writing"
            try:
                photo_ref = self._photo_ref(job)
                rows = await asyncio.to_thread(
                    self._write_all, cards, photo_ref, job.scanned_by, job.scanned_by_position
                )
                job.sheet_rows = rows
                job.sheet_row = rows[0] if rows else None
            except Exception as exc:
                logger.warning("Запись джоба %s в таблицу не удалась: %s", job.id, exc)
                job.error = "Не записано в таблицу: " + str(exc)

        self._set_results(job, cards)
        job.status = "done"
        logger.info("Джоб %s готов (визиток: %d)", job.id, len(cards))

    @staticmethod
    def _set_results(job: Job, cards: List[CardData]) -> None:
        """Раскладывает список визиток в поля джоба (results + первая в result)."""
        dicts = [c.to_dict() for c in cards]
        job.results = dicts
        job.result = dicts[0] if dicts else None

    # ----- блокирующие операции (выполняются в потоке) -----
    def _recognize_cards(self, image_bytes: bytes, mime_type: str) -> List[CardData]:
        from app.recognizers import get_recognizer  # ленивый импорт

        primary_key = self.config.recognizer
        recognizer = get_recognizer(self.config, engine=primary_key)
        cards = list(recognizer.extract_cards(image_bytes, mime_type=mime_type) or [])

        fallback_key = str(self.config.get("recognizer_fallback", "") or "").strip()
        if fallback_key and fallback_key != primary_key and all(c.is_empty() for c in cards):
            logger.info("Основной движок %s пуст, пробуем резервный %s", primary_key, fallback_key)
            try:
                fb_recognizer = get_recognizer(self.config, engine=fallback_key)
                fb_cards = list(fb_recognizer.extract_cards(image_bytes, mime_type=mime_type) or [])
            except Exception as exc:  # noqa: BLE001 — резерв не должен ронять джоб
                logger.warning("Резервный движок %s не сработал: %s", fallback_key, exc)
                fb_cards = []
            if fb_cards and not all(c.is_empty() for c in fb_cards):
                for c in fb_cards:
                    tag = "engine: {} (резерв)".format(fallback_key)
                    c.notes = (c.notes + " | " + tag).strip(" |") if c.notes else tag
                return fb_cards

        return cards

    def _enrich_all(self, cards: List[CardData]) -> List[CardData]:
        from app.enrich import enrich  # ленивый импорт

        out: List[CardData] = []
        for card in cards:
            try:
                out.append(enrich(card, self.config))
            except Exception as exc:  # noqa: BLE001 — обогащение не критично
                logger.warning("Обогащение визитки не удалось: %s", exc)
                out.append(card)
        return out

    def _write_all(
        self, cards: List[CardData], photo_ref: str, scanned_by: str = "", scanned_by_position: str = ""
    ) -> List[int]:
        from app.sheets import SheetsWriter  # ленивый импорт

        writer = SheetsWriter(self.config)
        rows: List[int] = []
        for card in cards:
            rows.append(
                writer.append_card(
                    card,
                    photo_ref=photo_ref,
                    scanned_by=scanned_by,
                    scanned_by_position=scanned_by_position,
                )
            )
        return rows

    def _photo_ref(self, job: Job) -> str:
        # локальная ссылка на сохранённый снимок (доступна с того же ПК/сети)
        return "/photos/" + job.photo_path.name
