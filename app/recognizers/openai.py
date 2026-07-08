"""Распознаватель визиток на базе OpenAI vision-моделей (gpt-4o и т.п.).

Изображение визитки отправляется vision-модели с просьбой вернуть строгий JSON.
Тяжёлый SDK (пакет ``openai``) импортируется лениво — внутри метода, чтобы импорт
самого модуля не падал, если библиотека не установлена.

Python 3.9-совместимо.
"""
from __future__ import annotations

import base64
import json
import re
from typing import TYPE_CHECKING, Any, Dict, List

from .base import CardData, Recognizer

if TYPE_CHECKING:  # только для типов, без рантайм-импорта
    from app.config import Config


# Инструкция модели: только JSON, никакого markdown и пояснений.
_PROMPT = (
    "Ты — точный парсер деловых визиток. На изображении одна визитка. "
    "Извлеки данные и верни СТРОГО один JSON-объект без markdown, без ```-ограждений "
    "и без каких-либо пояснений. Схема и порядок ключей:\n"
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
    "Правила: если поля нет на визитке — пустая строка \"\" или пустой список []. "
    "phones и emails — всегда массивы строк. Телефоны по возможности в международном "
    "формате. Не выдумывай данные, которых нет на изображении. "
    "В raw_text помести весь распознанный текст визитки."
)


class OpenAIRecognizer(Recognizer):
    """Распознаватель через OpenAI Chat Completions с vision-входом."""

    name = "cloud:openai"

    def __init__(self, config: "Config") -> None:
        self.config = config
        self.api_key = config.get("cloud.openai.api_key")
        self.model = config.get("cloud.openai.model", "gpt-4o")

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> CardData:
        # Ленивый импорт SDK: модуль должен импортироваться даже без openai.
        try:
            from openai import OpenAI
        except Exception as exc:  # библиотека не установлена
            raise RuntimeError(
                "Не установлен пакет openai. Установите: pip install openai"
            ) from exc

        if not self.api_key:
            return CardData(notes="OpenAI error: не задан ключ cloud.openai.api_key")

        try:
            data_url = "data:{0};base64,{1}".format(
                mime_type, base64.b64encode(image_bytes).decode()
            )
            client = OpenAI(api_key=self.api_key)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
            )
            text = resp.choices[0].message.content or ""
        except Exception as exc:
            return CardData(notes="OpenAI error: {0}".format(exc))

        return self._parse(text)

    # ----- разбор ответа модели -----
    def _parse(self, text: str) -> CardData:
        """Безопасно парсит JSON из ответа модели в CardData."""
        raw = text or ""
        payload = _strip_fences(raw)
        try:
            data = json.loads(payload)
        except Exception:
            # Фолбэк: попробовать вытащить первый JSON-объект из текста.
            data = _extract_json_object(payload)

        if not isinstance(data, dict):
            # Не удалось распарсить — сохраняем сырой текст для отладки.
            return CardData(
                raw_text=raw.strip(),
                notes="OpenAI error: не удалось разобрать JSON ответа",
            )

        card = CardData(
            name=_as_str(data.get("name")),
            title=_as_str(data.get("title")),
            company=_as_str(data.get("company")),
            phones=_as_list(data.get("phones")),
            emails=_as_list(data.get("emails")),
            website=_as_str(data.get("website")),
            address=_as_str(data.get("address")),
        )
        # raw_text: предпочитаем поле из модели, иначе — весь её ответ.
        card.raw_text = _as_str(data.get("raw_text")) or raw.strip()
        return card


# ----- вспомогательные функции -----
def _strip_fences(text: str) -> str:
    """Убирает markdown-ограждения ```json ... ``` вокруг JSON, если они есть."""
    s = (text or "").strip()
    if s.startswith("```"):
        # срезаем открывающую строку ограждения (```/```json) и закрывающую ```
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


def _extract_json_object(text: str) -> Any:
    """Пытается найти и распарсить первый сбалансированный {...} в тексте."""
    s = text or ""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start : i + 1])
                except Exception:
                    return None
    return None


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_list(value: Any) -> List[str]:
    """Нормализует значение в список непустых строк."""
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            s = _as_str(item)
            if s:
                out.append(s)
        return out
    s = _as_str(value)
    return [s] if s else []
