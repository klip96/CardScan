"""Общие утилиты для распознавания НЕСКОЛЬКИХ визиток на одном фото.

Vision-модели (Ollama / Gemini / OpenAI / Claude) получают единый промпт,
который просит вернуть JSON-объект ``{"cards": [ {...}, ... ]}`` — по одному
элементу на каждую визитку, найденную на изображении. Здесь же — устойчивый
парсер ответа, переживающий markdown-ограждения и «почти-JSON».

Python 3.9-совместимо.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .base import CardData


# Единый промпт для vision-распознавателей: НЕСКОЛЬКО визиток на одном фото.
MULTI_CARD_PROMPT = (
    "Ты распознаёшь данные с фотографии. На изображении может быть НЕСКОЛЬКО "
    "визитных карточек (примерно от 1 до 10), иногда повёрнутых под разными углами "
    "или лежащих на столе вперемешку. Найди КАЖДУЮ визитку и аккуратно прочитай "
    "весь её текст. Верни СТРОГО один JSON-объект без markdown, без ```-ограждений "
    "и без пояснений, по схеме:\n"
    "{\n"
    '  "cards": [\n'
    "    {\n"
    '      "name": "ФИО контакта",\n'
    '      "title": "должность",\n'
    '      "company": "название компании как на визитке",\n'
    '      "phones": ["+7..."],\n'
    '      "emails": ["..."],\n'
    '      "website": "...",\n'
    '      "address": "адрес с визитки",\n'
    '      "raw_text": "весь распознанный текст этой визитки"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Правила:\n"
    "- Один элемент массива cards = одна визитка. Сколько визиток на фото — столько элементов.\n"
    "- Если визитка одна — массив из одного элемента. Если визиток нет — \"cards\": [].\n"
    "- phones и emails — всегда массивы строк; остальные поля — строки.\n"
    "- Если поля нет на визитке — пустая строка \"\" или пустой список [].\n"
    "- Не выдумывай данные, которых нет на изображении.\n"
    "Верни только JSON."
)


def _as_str(value: Any) -> str:
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


def card_from_dict(data: Dict[str, Any], fallback_raw: str = "") -> CardData:
    """Складывает распознанные поля (шаг 1) одной визитки в CardData."""
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


def _strip_code_fences(text: str) -> str:
    """Убирает обрамляющие ```json ... ``` (или просто ```...```), если есть."""
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    s = re.sub(r"^```[a-zA-Z0-9_-]*[ \t]*\r?\n?", "", s)
    s = re.sub(r"\r?\n?```[ \t]*$", "", s)
    return s.strip()


def _extract_balanced(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    """Возвращает первый сбалансированный фрагмент open_ch...close_ch или None.

    Учитывает строковые литералы и экранирование, чтобы скобки внутри строк
    не сбивали баланс.
    """
    start = text.find(open_ch)
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
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _loads_any(text: str) -> Optional[Any]:
    """json.loads с фолбэком: вычленяет первый сбалансированный [...] или {...}."""
    cleaned = _strip_code_fences(text)
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        snippet = _extract_balanced(cleaned, open_ch, close_ch)
        if snippet is not None:
            try:
                return json.loads(snippet)
            except Exception:
                continue
    return None


# Ключи, по которым распознаём «голую» одиночную визитку без обёртки cards.
_CARD_KEYS = ("name", "title", "company", "phones", "emails", "website", "address", "raw_text")


def parse_cards(text: str, max_cards: int = 0) -> List[CardData]:
    """Разбирает ответ модели в список CardData (по одной на визитку).

    Поддерживаемые форматы ответа:
      * {"cards": [ {...}, ... ]}   — основной
      * [ {...}, ... ]              — просто массив визиток
      * {...}                       — одна визитка без обёртки

    Возвращает [] если ничего распарсить не удалось (решение, что показать,
    остаётся за вызывающим распознавателем).
    """
    data = _loads_any(text or "")
    if data is None:
        return []

    items: List[Any] = []
    if isinstance(data, dict):
        cards = data.get("cards")
        if isinstance(cards, list):
            items = cards
        elif isinstance(cards, dict):
            items = [cards]
        elif any(k in data for k in _CARD_KEYS):
            items = [data]  # одна визитка без обёртки cards
    elif isinstance(data, list):
        items = data

    result: List[CardData] = []
    for it in items:
        if isinstance(it, dict):
            result.append(card_from_dict(it))
    if max_cards and len(result) > max_cards:
        result = result[:max_cards]
    return result
