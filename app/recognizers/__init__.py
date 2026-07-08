"""Фабрика распознавателей.

get_recognizer(config) выбирает реализацию по строке config.recognizer:
    cloud:gemini | cloud:openai | cloud:claude | local-ocr | local-vision

Импорты ленивые — тяжёлые/опциональные зависимости (paddleocr, google-genai,
openai, anthropic, ollama) подтягиваются только для выбранного движка,
поэтому приложение стартует даже без всех библиотек.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .base import CardData, Recognizer

if TYPE_CHECKING:  # только для подсказок типов, без рантайм-импорта
    from app.config import Config

__all__ = ["CardData", "Recognizer", "get_recognizer"]


def get_recognizer(config: "Config", engine: Optional[str] = None) -> Recognizer:
    """Создаёт распознаватель по ключу движка.

    engine, если задан, переопределяет config.recognizer — так конвейер
    может запросить конкретный (например, резервный) движок отдельно
    от того, что выбран основным в настройках.
    """
    key = (engine or config.recognizer or "cloud:gemini").strip().lower()

    if key == "cloud:gemini":
        from .gemini import GeminiRecognizer
        return GeminiRecognizer(config)
    if key == "cloud:openai":
        from .openai import OpenAIRecognizer
        return OpenAIRecognizer(config)
    if key == "cloud:claude":
        from .claude import ClaudeRecognizer
        return ClaudeRecognizer(config)
    if key == "local-ocr":
        from .local_ocr import LocalOcrRecognizer
        return LocalOcrRecognizer(config)
    if key == "local-vision":
        from .local_vision import LocalVisionRecognizer
        return LocalVisionRecognizer(config)

    raise ValueError(
        f"Неизвестный распознаватель: {key!r}. "
        "Допустимо: cloud:gemini, cloud:openai, cloud:claude, local-ocr, local-vision"
    )
