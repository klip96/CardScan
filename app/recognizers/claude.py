"""Распознаватель визиток через Claude (Anthropic) vision-модель.

Шаг 1 конвейера: получить с фото визитки структурированные поля
(имя, должность, компания, телефоны, e-mail, сайт, адрес, исходный текст).

Python 3.9-совместимо. Тяжёлый SDK `anthropic` импортируется лениво —
импорт самого модуля не должен падать без установленной библиотеки.
"""
from __future__ import annotations

import base64
import json
import re
from typing import TYPE_CHECKING, Any, Dict, List

from .base import CardData, Recognizer

if TYPE_CHECKING:  # только для типов, без рантайм-зависимости
    from app.config import Config


# Инструктаж для модели: строго JSON, без markdown и пояснений.
_PROMPT = (
    "Ты распознаёшь данные с фотографии визитной карточки. "
    "Извлеки информацию и верни СТРОГО один JSON-объект без markdown-ограждений, "
    "без комментариев и без какого-либо текста до или после него. "
    "Схема (используй ровно эти ключи):\n"
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
    "Правила: если поля нет на визитке — поставь пустую строку \"\" "
    "(или пустой список [] для phones и emails). "
    "Телефоны записывай в международном формате, если это однозначно. "
    "В raw_text помести весь видимый на визитке текст."
)


class ClaudeRecognizer(Recognizer):
    """Распознаватель на базе Anthropic Claude (vision)."""

    name = "cloud:claude"

    def __init__(self, config: "Config") -> None:
        self.config = config
        self.api_key = config.get("cloud.claude.api_key")
        self.model = config.get("cloud.claude.model", "claude-sonnet-4-6")

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> CardData:
        card = CardData(source_event="")
        try:
            text = self._call_model(image_bytes, mime_type)
        except RuntimeError as exc:
            # Отсутствует библиотека / не задан ключ — понятная ошибка.
            card.notes = "Claude error: {0}".format(exc)
            return card
        except Exception as exc:  # сетевые/SDK-ошибки — не роняем конвейер
            card.notes = "Claude error: {0}".format(exc)
            return card

        data = self._parse_json(text)
        if data is None:
            card.raw_text = text or ""
            card.notes = "Claude error: не удалось разобрать JSON-ответ"
            return card

        self._fill(card, data)
        return card

    # ----- внутреннее -----
    def _call_model(self, image_bytes: bytes, mime_type: str) -> str:
        """Вызвать Claude и вернуть текст ответа. Импорт SDK ленивый."""
        if not self.api_key:
            raise RuntimeError(
                "не задан ключ cloud.claude.api_key в конфигурации"
            )
        try:
            import anthropic  # ленивый импорт тяжёлой опциональной зависимости
        except ImportError as exc:
            raise RuntimeError(
                "не установлен пакет anthropic — выполните `pip install anthropic`"
            ) from exc

        client = anthropic.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": base64.b64encode(image_bytes).decode(),
                            },
                        },
                        {"type": "text", "text": _PROMPT},
                    ],
                }
            ],
        )
        return self._extract_text(msg)

    @staticmethod
    def _extract_text(msg: Any) -> str:
        """Достать текст из ответа Claude (первый text-блок)."""
        content = getattr(msg, "content", None) or []
        for block in content:
            if getattr(block, "type", None) == "text":
                return getattr(block, "text", "") or ""
        # запасной вариант: первый блок как в спецификации
        if content:
            return getattr(content[0], "text", "") or ""
        return ""

    @staticmethod
    def _parse_json(text: str) -> Any:
        """Безопасно распарсить JSON, сняв markdown-ограждения ```json ... ```."""
        if not text:
            return None
        cleaned = text.strip()
        # снять ограждение ```json ... ``` или ``` ... ```
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except (ValueError, TypeError):
            # модель могла добавить текст вокруг — выдернуть первый {...}
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except (ValueError, TypeError):
                    return None
            return None

    def _fill(self, card: CardData, data: Dict[str, Any]) -> None:
        """Заполнить поля шага 1 из распарсенного словаря."""
        card.name = self._as_str(data.get("name"))
        card.title = self._as_str(data.get("title"))
        card.company = self._as_str(data.get("company"))
        card.phones = self._as_list(data.get("phones"))
        card.emails = self._as_list(data.get("emails"))
        card.website = self._as_str(data.get("website"))
        card.address = self._as_str(data.get("address"))
        card.raw_text = self._as_str(data.get("raw_text"))

    @staticmethod
    def _as_str(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            v = value.strip()
            return [v] if v else []
        if isinstance(value, (list, tuple)):
            result = []  # type: List[str]
            for item in value:
                s = "" if item is None else str(item).strip()
                if s:
                    result.append(s)
            return result
        return []
