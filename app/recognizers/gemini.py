"""Распознаватель визиток через Google Gemini (vision).

SDK: пакет ``google-genai`` (импорт ``from google import genai``).
Импорт SDK ленивый — модуль не падает, если пакет не установлен.

Python 3.9-совместимо.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List

from .base import CardData, Recognizer

if TYPE_CHECKING:  # только для аннотаций, без рантайм-импорта
    from app.config import Config


# Промпт: просим строго JSON по фиксированной схеме (шаг 1 — распознавание).
_PROMPT = (
    "Ты — точный экстрактор данных с фотографии визитной карточки. "
    "Распознай текст и верни СТРОГО валидный JSON без markdown, без ```-ограждений "
    "и без каких-либо пояснений. Схема ответа:\n"
    "{\n"
    '  "name": "ФИО контакта",\n'
    '  "title": "должность",\n'
    '  "company": "название компании как на визитке",\n'
    '  "phones": ["+7..."],\n'
    '  "emails": ["..."],\n'
    '  "website": "...",\n'
    '  "address": "адрес с визитки",\n'
    '  "raw_text": "весь распознанный текст"\n'
    "}\n"
    "Правила: телефоны и e-mail — массивы строк; остальные поля — строки. "
    "Если значения нет на визитке — верни пустую строку или пустой массив. "
    "Ничего не выдумывай, бери только то, что реально видно на изображении."
)


class GeminiRecognizer(Recognizer):
    """Vision-распознаватель на базе Google Gemini."""

    name = "cloud:gemini"

    def __init__(self, config: "Config") -> None:
        self.config = config
        self.api_key = config.get("cloud.gemini.api_key") or ""
        self.model = config.get("cloud.gemini.model", "gemini-2.0-flash") or "gemini-2.0-flash"

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> CardData:
        if not self.api_key:
            return CardData(
                notes="Gemini error: не задан API-ключ (cloud.gemini.api_key)",
            )

        # Ленивый импорт SDK: модуль должен импортироваться и без google-genai.
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:  # ImportError и проблемы окружения
            return CardData(
                notes=(
                    "Gemini error: не установлен пакет google-genai "
                    "(pip install google-genai): " + str(exc)
                ),
            )

        try:
            client = genai.Client(api_key=self.api_key)
            resp = client.models.generate_content(
                model=self.model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    _PROMPT,
                ],
            )
            text = getattr(resp, "text", None) or ""
        except Exception as exc:
            return CardData(notes="Gemini error: " + str(exc))

        if not text.strip():
            return CardData(
                raw_text="",
                notes="Gemini error: пустой ответ модели",
            )

        try:
            data = _parse_json(text)
        except Exception as exc:
            # Парсинг не удался — сохраним сырой текст для отладки/фолбэка.
            return CardData(
                raw_text=text.strip(),
                notes="Gemini error: не удалось разобрать JSON ответа: " + str(exc),
            )

        return _card_from_dict(data, fallback_raw=text)


def _parse_json(text: str) -> Dict[str, Any]:
    """Безопасно достаёт JSON-объект из ответа модели.

    Снимает markdown-ограждения ```json ... ``` и при необходимости вычленяет
    первый {...}-блок из текста.
    """
    cleaned = _strip_code_fences(text).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Фолбэк: вырезать первый сбалансированный объект {...}.
        snippet = _extract_first_object(cleaned)
        if snippet is None:
            raise
        obj = json.loads(snippet)
    if not isinstance(obj, dict):
        raise ValueError("ожидался JSON-объект")
    return obj


def _strip_code_fences(text: str) -> str:
    """Убирает обрамляющие ```json ... ``` (или просто ```...```), если есть."""
    s = text.strip()
    if not s.startswith("```"):
        return s
    # Срезаем открывающую строку-ограждение (```), возможно с указанием языка.
    s = re.sub(r"^```[a-zA-Z0-9_-]*[ \t]*\r?\n?", "", s)
    # Срезаем закрывающее ограждение в конце.
    s = re.sub(r"\r?\n?```[ \t]*$", "", s)
    return s.strip()


def _extract_first_object(text: str) -> "Any":
    """Возвращает первый сбалансированный {...}-фрагмент или None.

    Учитывает строковые литералы и экранирование, чтобы скобки внутри строк
    не сбивали баланс.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _as_str(value: Any) -> str:
    """Приводит значение к непустой строке (без None), с обрезкой пробелов."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_str_list(value: Any) -> List[str]:
    """Приводит значение к списку непустых строк."""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            s = _as_str(item)
            if s:
                out.append(s)
        return out
    s = _as_str(value)
    return [s] if s else []


def _card_from_dict(data: Dict[str, Any], fallback_raw: str = "") -> CardData:
    """Складывает распознанные поля (шаг 1) в CardData."""
    raw_text = _as_str(data.get("raw_text")) or fallback_raw.strip()
    return CardData(
        name=_as_str(data.get("name")),
        title=_as_str(data.get("title")),
        company=_as_str(data.get("company")),
        phones=_as_str_list(data.get("phones")),
        emails=_as_str_list(data.get("emails")),
        website=_as_str(data.get("website")),
        address=_as_str(data.get("address")),
        raw_text=raw_text,
    )
