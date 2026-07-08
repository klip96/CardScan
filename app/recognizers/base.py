"""Базовые контракты распознавания визиток.

Этот модуль — ядро системы. От него зависят ВСЕ распознаватели и конвейер.
Менять структуру CardData / сигнатуру Recognizer.extract нужно осторожно.

Python 3.9-совместимо (никакого `X | Y`-синтаксиса в рантайме).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Dict, List


@dataclass
class CardData:
    """Структурированные данные одной визитки.

    Поля делятся на три группы:
      * распознанные с визитки (шаг 1),
      * обогащённые из интернета (шаг 2),
      * служебные (заполняются конвейером/UI).
    """

    # --- шаг 1: распознано с визитки ---
    name: str = ""          # ФИО контакта
    title: str = ""         # должность
    company: str = ""       # компания, как написано на визитке
    phones: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    website: str = ""
    address: str = ""       # адрес с визитки
    raw_text: str = ""      # весь распознанный текст (для отладки/фолбэка)

    # --- шаг 2: обогащение из интернета ---
    legal_name: str = ""        # точное юридическое название
    refined_address: str = ""   # уточнённый адрес
    industry: str = ""          # отрасль / сфера
    verified_website: str = ""  # проверенный сайт

    # --- служебное ---
    source_event: str = ""  # выставка / источник (задаётся в сессии съёмки)
    confidence: float = 0.0  # 0..1, грубая оценка распознавателя (опционально)
    notes: str = ""          # технические заметки распознавателя (например, "low quality")

    def to_dict(self) -> Dict:
        return asdict(self)

    def is_empty(self) -> bool:
        """True, если не удалось извлечь ни одного значимого поля."""
        return not any([
            self.name, self.company, self.phones, self.emails, self.website
        ])


class Recognizer(ABC):
    """Интерфейс распознавателя. Реализации: Gemini/OpenAI/Claude/локальные.

    Контракт:
      * конструктор принимает объект Config (app.config.Config);
      * extract() синхронный (конвейер вызывает его в отдельном потоке через
        asyncio.to_thread, чтобы не блокировать event loop);
      * extract() ВСЕГДА возвращает CardData (никогда не кидает в норме —
        при ошибке вернуть CardData с заполненным notes и пустыми полями
        либо пробросить исключение, которое поймает конвейер и пометит job как failed).
    """

    #: короткое имя движка, например "cloud:gemini" или "local-ocr"
    name: str = "base"

    @abstractmethod
    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> CardData:
        """Принимает байты изображения, возвращает CardData (одна визитка)."""
        raise NotImplementedError

    def extract_cards(
        self, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> List[CardData]:
        """Распознать ВСЕ визитки на изображении (на фото их может быть несколько).

        Базовая реализация — обёртка над extract() для движков, которые умеют
        только одну визитку (например, локальный OCR). Vision-движки
        переопределяют этот метод и возвращают список из 1..N карточек.
        Контракт: всегда возвращает непустой список (минимум одна CardData,
        возможно пустая, но с заполненным notes при ошибке)."""
        return [self.extract(image_bytes, mime_type=mime_type)]
