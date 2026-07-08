"""Обогащение данных визитки из интернета.

Шаг 2 конвейера: по компании/сайту с визитки ищем сведения в вебе
(DuckDuckGo) и сводим их LLM-ом в строгий JSON, заполняя поля
legal_name / refined_address / industry / verified_website.

Любая ошибка (нет сети, нет ключа, кривой JSON) НЕ роняет конвейер:
ошибка пишется в card.notes, card возвращается как есть.

Python 3.9-совместимо. Тяжёлые/опциональные библиотеки импортируются
лениво внутри функций, чтобы импорт модуля не падал без них.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List

from .recognizers.base import CardData

if TYPE_CHECKING:  # только для типов, без рантайм-импорта
    from app.config import Config


# Поля, которые обогащаем; ключи совпадают с тем, что просим у LLM.
_ENRICH_FIELDS = ("legal_name", "refined_address", "industry", "verified_website")


def enrich(card: CardData, config: "Config") -> CardData:
    """Дополняет card сведениями из интернета. Всегда возвращает CardData.

    Ничего не делает, если обогащение выключено или с визитки нечего искать
    (нет ни компании, ни сайта).
    """
    if not config.get("enrichment.enabled"):
        return card
    if not (card.company or card.website):
        return card

    try:
        query = _build_query(card)
        max_results = _as_int(config.get("enrichment.max_results", 5), 5)
        snippets = _web_search(query, max_results)
        result = _consolidate(card, snippets, config)
        _apply(card, result)
    except Exception as exc:  # любой сбой не должен ронять конвейер
        _note(card, "Enrich error: {}".format(exc))
    return card


def _build_query(card: CardData) -> str:
    """Поисковый запрос из данных визитки."""
    parts = [card.company or "", card.website or "", "официальный сайт адрес"]
    return " ".join(p for p in parts if p).strip()


def _web_search(query: str, n: int) -> List[Dict[str, str]]:
    """Веб-поиск через duckduckgo_search. Возвращает список {title, href, body}."""
    try:
        from duckduckgo_search import DDGS  # ленивый импорт
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен duckduckgo_search. Установите: pip install duckduckgo_search"
        ) from exc

    results: List[Dict[str, str]] = []
    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=n):
            results.append(
                {
                    "title": str(item.get("title", "")),
                    "href": str(item.get("href", "")),
                    "body": str(item.get("body", "")),
                }
            )
            if len(results) >= n:
                break
    return results


def _consolidate(card: CardData, snippets: List[Dict[str, str]], config: "Config") -> Dict[str, str]:
    """Сводит данные визитки и сниппеты поиска через LLM в словарь полей."""
    provider = _resolve_provider(config)
    prompt = _build_prompt(card, snippets)
    text = _call_llm(provider, config, prompt)
    return _parse_json(text)


def _resolve_provider(config: "Config") -> str:
    """Выбирает провайдера LLM для сведения результатов."""
    llm = str(config.get("enrichment.llm", "auto") or "auto")
    if llm != "auto":
        return llm
    recognizer = config.recognizer
    if recognizer.startswith("cloud:"):
        return recognizer
    return "cloud:gemini"


def _build_prompt(card: CardData, snippets: List[Dict[str, str]]) -> str:
    """Промпт для LLM: исходные данные визитки + сниппеты поиска, ответ строго JSON."""
    card_lines = [
        "Компания: {}".format(card.company or "—"),
        "Сайт: {}".format(card.website or "—"),
        "Адрес: {}".format(card.address or "—"),
        "Контакт: {}".format(card.name or "—"),
        "Должность: {}".format(card.title or "—"),
    ]
    snippet_lines = []
    for i, s in enumerate(snippets, 1):
        snippet_lines.append(
            "[{}] {}\n{}\n{}".format(i, s.get("title", ""), s.get("href", ""), s.get("body", ""))
        )
    snippets_block = "\n\n".join(snippet_lines) if snippet_lines else "(результатов поиска нет)"

    return (
        "Ты помогаешь уточнить данные компании по визитке и результатам веб-поиска.\n"
        "Данные с визитки:\n"
        + "\n".join(card_lines)
        + "\n\nРезультаты поиска:\n"
        + snippets_block
        + "\n\nВерни СТРОГО один JSON-объект без пояснений и без markdown, ровно с такими ключами:\n"
        '{"legal_name": "", "refined_address": "", "industry": "", "verified_website": ""}\n'
        "Где:\n"
        "- legal_name — точное юридическое название компании;\n"
        "- refined_address — уточнённый полный адрес;\n"
        "- industry — отрасль/сфера деятельности;\n"
        "- verified_website — проверенный официальный сайт.\n"
        "Если значение неизвестно — оставь пустую строку. Никакого текста кроме JSON."
    )


def _parse_json(text: str) -> Dict[str, str]:
    """Безопасно вытаскивает JSON-объект из ответа модели."""
    if not text:
        return {}
    raw = text.strip()

    # снять возможные ```json ... ``` ограждения
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()

    try:
        data = json.loads(raw)
    except Exception:
        # фолбэк: выдрать первый {...} блок из текста
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except Exception:
            return {}

    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for key in _ENRICH_FIELDS:
        value = data.get(key, "")
        out[key] = str(value).strip() if value is not None else ""
    return out


def _apply(card: CardData, result: Dict[str, str]) -> None:
    """Записывает непустые значения в card, не затирая уже заполненные пустыми."""
    for key in _ENRICH_FIELDS:
        value = (result.get(key) or "").strip()
        if value:
            setattr(card, key, value)


# --------------------------------------------------------------------------
# LLM-провайдеры (ленивые импорты SDK)
# --------------------------------------------------------------------------

def _call_llm(provider: str, config: "Config", prompt: str) -> str:
    """Вызывает облачный LLM по имени провайдера и возвращает текст ответа.

    provider: "cloud:gemini" | "cloud:openai" | "cloud:claude".
    """
    key = provider.split(":", 1)[1] if ":" in provider else provider
    if key == "gemini":
        return _call_gemini(config, prompt)
    if key == "openai":
        return _call_openai(config, prompt)
    if key == "claude":
        return _call_claude(config, prompt)
    raise RuntimeError("Неизвестный провайдер LLM: {}".format(provider))


def _call_gemini(config: "Config", prompt: str) -> str:
    api_key = config.get("cloud.gemini.api_key", "")
    if not api_key:
        raise RuntimeError("Не задан ключ cloud.gemini.api_key")
    try:
        from google import genai  # ленивый импорт google-genai
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен google-genai. Установите: pip install google-genai"
        ) from exc

    model = config.get("cloud.gemini.model", "gemini-2.0-flash")
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=model, contents=prompt)
    return str(getattr(resp, "text", "") or "")


def _call_openai(config: "Config", prompt: str) -> str:
    api_key = config.get("cloud.openai.api_key", "")
    if not api_key:
        raise RuntimeError("Не задан ключ cloud.openai.api_key")
    try:
        from openai import OpenAI  # ленивый импорт openai
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен openai. Установите: pip install openai"
        ) from exc

    model = config.get("cloud.openai.model", "gpt-4o")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    return str(choices[0].message.content or "")


def _call_claude(config: "Config", prompt: str) -> str:
    api_key = config.get("cloud.claude.api_key", "")
    if not api_key:
        raise RuntimeError("Не задан ключ cloud.claude.api_key")
    try:
        import anthropic  # ленивый импорт anthropic
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен anthropic. Установите: pip install anthropic"
        ) from exc

    model = config.get("cloud.claude.model", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    parts: List[str] = []
    for block in getattr(resp, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "".join(parts)


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def _as_int(value: Any, default: int) -> int:
    try:
        n = int(value)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _note(card: CardData, text: str) -> None:
    """Добавляет техническую заметку в card.notes, не затирая прежние."""
    card.notes = (card.notes + " | " + text).strip(" |") if card.notes else text
