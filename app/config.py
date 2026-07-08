"""Загрузка и доступ к конфигурации приложения.

Читает config.yaml (рядом с корнем проекта), накладывает поверх DEFAULTS.
Доступ к вложенным значениям — через точечный путь: cfg.get("cloud.gemini.api_key").

Python 3.9-совместимо.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # PyYAML
except Exception:  # pragma: no cover - подсказка, если забыли поставить зависимости
    yaml = None


# Корень проекта = родитель папки app/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: Dict[str, Any] = {
    # cloud:gemini | cloud:openai | cloud:claude | local-ocr | local-vision
    "recognizer": "local-vision",
    # Резервный движок: если основной вернул пустой результат для ВСЕХ визиток
    # на фото, конвейер автоматически пробует этот. "" = резерв выключен.
    "recognizer_fallback": "",
    "cloud": {
        "gemini": {"api_key": "", "model": "gemini-2.0-flash"},
        "openai": {"api_key": "", "model": "gpt-4o"},
        "claude": {"api_key": "", "model": "claude-sonnet-4-6"},
    },
    "local": {
        "ocr": {"engine": "tesseract", "lang": "rus+eng"},
        "vision": {
            "ollama_host": "http://localhost:11434",
            # qwen3-vl:235b-cloud — облачная модель Ollama (нужен `ollama signin`)
            "model": "qwen3-vl:235b-cloud",
            "timeout_sec": 240,   # таймаут запроса к Ollama (сек)
            "max_cards": 0,       # 0 = без ограничения числа визиток с одного фото
        },
    },
    "enrichment": {
        "enabled": True,
        # какой провайдер использовать для LLM-сведения результатов поиска:
        # "auto" = тот же, что recognizer (если он cloud), иначе cloud.gemini
        "llm": "auto",
        "search": "duckduckgo",
        "max_results": 5,
    },
    "google_sheets": {
        "enabled": True,
        "credentials_file": "service_account.json",
        "spreadsheet_id": "",
        "worksheet": "Лиды",
    },
    "sales_reps": ["Иванов", "Петров", "Сидорова"],
    "server": {"host": "0.0.0.0", "port": 8000},
    "storage": {"photos_dir": "data/photos"},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Рекурсивно мержит override поверх base (не мутируя аргументы)."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None):
        self._data = data
        self.path = path

    # ----- загрузка / сохранение -----
    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        cfg_path = Path(path) if path else (PROJECT_ROOT / "config.yaml")
        data: Dict[str, Any] = {}
        if cfg_path.exists() and yaml is not None:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        merged = _deep_merge(DEFAULTS, data)
        return cls(merged, cfg_path)

    def save(self) -> None:
        if yaml is None or self.path is None:
            return
        with open(self.path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self._data, fh, allow_unicode=True, sort_keys=False)

    # ----- доступ -----
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    # ----- удобные геттеры -----
    @property
    def recognizer(self) -> str:
        return str(self.get("recognizer", "cloud:gemini"))

    @property
    def sales_reps(self) -> List[str]:
        reps = self.get("sales_reps", [])
        return list(reps) if isinstance(reps, list) else []

    @property
    def photos_dir(self) -> Path:
        rel = self.get("storage.photos_dir", "data/photos")
        p = Path(rel)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def credentials_path(self) -> Path:
        rel = self.get("google_sheets.credentials_file", "service_account.json")
        p = Path(rel)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    def as_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

    def public_dict(self) -> Dict[str, Any]:
        """Конфиг для отдачи в UI: ключи API замаскированы."""
        data = self.as_dict()
        cloud = data.get("cloud", {})
        for prov in cloud.values():
            if isinstance(prov, dict) and prov.get("api_key"):
                prov["api_key"] = "••••" + str(prov["api_key"])[-4:]
        return data
