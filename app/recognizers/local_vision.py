"""Распознавание визиток через локальную или облачную vision-модель Ollama.

Работает по HTTP к Ollama (эндпоинт /api/generate), без отдельного python-SDK.
Изображение передаётся в base64, модель просят вернуть СТРОГО JSON со списком
визиток (на одном фото их может быть несколько).

Поддерживает как локальные модели (moondream, qwen2.5vl:3b, ...), так и
облачные модели Ollama (например, qwen3-vl:235b-cloud) — они работают через
тот же локальный эндпоинт после `ollama signin`.

Тяжёлый/опциональный httpx импортируется лениво внутри метода.

Python 3.9-совместимо.
"""
from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, Dict, List

from .base import CardData, Recognizer
from ._multicard import MULTI_CARD_PROMPT, parse_cards

if TYPE_CHECKING:  # только для типов, без рантайм-импорта
    from app.config import Config


# Таймаут по умолчанию (сек). Локальные vision-модели могут считать долго,
# поэтому значение вынесено в конфиг: local.vision.timeout_sec.
_DEFAULT_TIMEOUT_SEC = 180.0


class LocalVisionRecognizer(Recognizer):
    """Распознаватель на базе vision-модели Ollama (локальной или облачной)."""

    name = "local-vision"

    def __init__(self, config: "Config") -> None:
        self.config = config
        self.host = str(
            config.get("local.vision.ollama_host", "http://localhost:11434")
        ).rstrip("/")
        self.model = str(config.get("local.vision.model", "moondream"))
        try:
            self.timeout = float(
                config.get("local.vision.timeout_sec", _DEFAULT_TIMEOUT_SEC)
                or _DEFAULT_TIMEOUT_SEC
            )
        except (TypeError, ValueError):
            self.timeout = _DEFAULT_TIMEOUT_SEC
        try:
            self.max_cards = int(config.get("local.vision.max_cards", 0) or 0)
        except (TypeError, ValueError):
            self.max_cards = 0

    # ----- одна визитка (обратная совместимость) -----
    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> CardData:
        cards = self.extract_cards(image_bytes, mime_type=mime_type)
        return cards[0] if cards else CardData(notes="Ollama: визитки не найдены")

    # ----- несколько визиток на одном фото -----
    def extract_cards(
        self, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> List[CardData]:
        try:
            import httpx  # ленивый импорт опциональной зависимости
        except ImportError:
            return [CardData(notes="Не установлен httpx. Установите: pip install httpx")]

        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": MULTI_CARD_PROMPT,
            "images": [image_b64],
            "stream": False,
            "format": "json",
        }

        try:
            resp = httpx.post(
                self.host + "/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            return [CardData(notes="Ollama не запущен на {}".format(self.host))]
        except httpx.TimeoutException:
            return [CardData(
                notes="Ollama: таймаут запроса ({:.0f} c) к модели {}. "
                      "Увеличьте local.vision.timeout_sec или возьмите модель "
                      "побыстрее (например, облачную qwen3-vl:235b-cloud).".format(
                          self.timeout, self.model)
            )]
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200]
            hint = ""
            if exc.response.status_code in (401, 403):
                hint = (" — для облачной модели нужен вход: выполните "
                        "`ollama signin`.")
            return [CardData(notes="Ollama HTTP {}: {}{}".format(
                exc.response.status_code, body, hint))]
        except Exception as exc:  # прочие сетевые/клиентские ошибки
            return [CardData(notes="Ollama error: {}".format(exc))]

        try:
            outer = resp.json()
        except Exception as exc:
            return [CardData(notes="Ollama: не удалось разобрать ответ API: {}".format(exc))]

        raw_response = outer.get("response", "") if isinstance(outer, dict) else ""
        if not raw_response:
            return [CardData(notes="Ollama: пустой ответ модели {}".format(self.model))]

        cards = parse_cards(raw_response, max_cards=self.max_cards)
        if not cards:
            # Не удалось распарсить — сохраняем сырой текст для отладки/фолбэка.
            return [CardData(
                raw_text=raw_response.strip(),
                notes="Ollama: модель вернула не-JSON или пустой список визиток",
            )]
        return cards
