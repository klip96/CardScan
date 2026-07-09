"""Запись лидов в Google Sheets.

Использует gspread + google-auth (оба — ленивые импорты внутри методов,
чтобы импорт модуля не падал без установленных библиотек и без сети).

Python 3.9-совместимо.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from .recognizers.base import CardData

if TYPE_CHECKING:  # только для типов, без рантайм-импортов
    from app.config import Config


# Скоуп доступа для service-account.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def extract_spreadsheet_id(value: str) -> str:
    """Из ссылки на Google Sheets или готового ID вытащить ID таблицы.

    Принимает как полный URL (.../spreadsheets/d/<ID>/edit#gid=0),
    так и просто ID. Возвращает ID (или исходную строку, если не распознали).
    """
    value = (value or "").strip()
    if not value:
        return ""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9-_]+)", value)
    if m:
        return m.group(1)
    # Иногда вставляют просто длинный ID — отдадим как есть.
    m = re.search(r"[A-Za-z0-9-_]{25,}", value)
    return m.group(0) if m else value


def _photo_cell(photo_ref: str) -> str:
    """Значение ячейки «Фото визитки».

    Для полного публичного URL (когда настроен туннель) отдаём формулу
    =IMAGE(...), чтобы в таблице сразу была видна миниатюра, а не текст
    ссылки — Google Sheets сам подтягивает картинку со своих серверов.
    Для относительного пути (туннель не настроен) отдаём как есть.
    Требует value_input_option="USER_ENTERED" при записи, иначе формула
    попадёт в ячейку как текст, а не будет вычислена.
    """
    if photo_ref.startswith("http://") or photo_ref.startswith("https://"):
        escaped = photo_ref.replace('"', '""')
        return '=IMAGE("{0}", 4, 90, 130)'.format(escaped)
    return photo_ref


def _col_letter(index: int) -> str:
    """Номер колонки (1-based) в букву A1-нотации (1->A, 27->AA и т.д.)."""
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def read_service_account_email(credentials_path) -> Optional[str]:
    """Читает client_email из JSON-ключа сервисного аккаунта (или None)."""
    try:
        data = json.loads(Path(str(credentials_path)).read_text(encoding="utf-8"))
        email = data.get("client_email")
        return str(email) if email else None
    except Exception:
        return None


class SheetsWriter:
    """Пишет распознанные визитки в указанную таблицу Google Sheets.

    Подключение к Google — ленивое: клиент и лист создаются по требованию,
    а не в конструкторе.
    """

    #: Порядок колонок строго фиксирован — строки собираются под него.
    HEADERS: List[str] = [
        "Дата добавления",
        "Выставка / источник",
        "Ответственный от продаж",
        "ФИО контакта",
        "Должность",
        "Компания (как на визитке)",
        "Телефон(ы)",
        "Email",
        "Сайт",
        "Адрес (с визитки)",
        "Точное юр. название",
        "Уточнённый адрес",
        "Отрасль / сфера",
        "Проверенный сайт",
        "Статус лида",
        "Комментарий",
        "Фото визитки",
        "Сотрудник",
        "Должность сотрудника",
    ]

    #: Буквы колонок «Ответственный от продаж» / «Статус лида» / «Комментарий» —
    #: совпадают с позицией в HEADERS (3-я, 15-я, 16-я), заданы явно, т.к.
    #: используются в A1-диапазонах.
    RESPONSIBLE_COL_LETTER = "C"
    STATUS_COL_LETTER = "O"
    COMMENT_COL_LETTER = "P"

    #: Фиксированный список статусов лида — выпадающий список в колонке
    #: «Статус лида». Меняется редко, поэтому не вынесен в конфиг (в отличие
    #: от sales_reps, который список людей и настраивается через /setup).
    LEAD_STATUS_OPTIONS: List[str] = [
        "В работе",
        "Наш пользователь",
        "Давно нет контакта",
        "Решение конкурентов",
    ]

    def __init__(self, config: "Config"):
        self.config = config
        self.spreadsheet_id = config.get("google_sheets.spreadsheet_id")
        self.worksheet_name = config.get("google_sheets.worksheet", "Лиды")
        self.credentials_path = config.credentials_path()
        # Кэш ленивых ресурсов.
        self._gc = None
        self._ws = None

    # ----- ленивая инициализация клиента / листа -----
    def _client(self):
        """Авторизуется в Google и возвращает gspread-клиент (с кэшированием)."""
        if self._gc is not None:
            return self._gc

        if not self.spreadsheet_id:
            raise RuntimeError(
                "Не задан google_sheets.spreadsheet_id в конфиге — "
                "укажите ID таблицы Google Sheets."
            )
        if not self.credentials_path.exists():
            raise RuntimeError(
                "Не найден файл сервисного аккаунта: {0}. "
                "Положите ключ service_account.json и укажите путь в "
                "google_sheets.credentials_file.".format(self.credentials_path)
            )

        try:
            import gspread  # noqa: F401
        except Exception as exc:  # библиотека не установлена
            raise RuntimeError(
                "Библиотека gspread не установлена. Установите: "
                "pip install gspread"
            ) from exc
        try:
            from google.oauth2.service_account import Credentials
        except Exception as exc:
            raise RuntimeError(
                "Библиотека google-auth не установлена. Установите: "
                "pip install google-auth"
            ) from exc

        creds = Credentials.from_service_account_file(
            str(self.credentials_path), scopes=SCOPES
        )
        self._gc = gspread.authorize(creds)
        return self._gc

    def _worksheet(self):
        """Открывает таблицу и нужный лист, создавая лист при отсутствии."""
        if self._ws is not None:
            return self._ws

        gc = self._client()
        try:
            import gspread
        except Exception as exc:
            raise RuntimeError(
                "Библиотека gspread не установлена. Установите: "
                "pip install gspread"
            ) from exc

        spreadsheet = gc.open_by_key(self.spreadsheet_id)
        try:
            ws = spreadsheet.worksheet(self.worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(
                title=self.worksheet_name,
                rows=100,
                cols=len(self.HEADERS),
            )
        self._ws = ws
        return ws

    # ----- работа с заголовками -----
    def ensure_headers(self) -> None:
        """Гарантирует наличие строки заголовков и выпадающего списка ответственных.

        Если лист совсем пустой — пишет все заголовки. Если в нём уже есть
        данные (лист использовался ДО того, как появились новые колонки,
        например «Сотрудник»/«Должность сотрудника»), но заголовков меньше,
        чем в текущем HEADERS — дописывает недостающий «хвост», не трогая
        уже существующие ячейки.
        """
        ws = self._worksheet()

        first_row = ws.row_values(1)
        if not any(cell.strip() for cell in first_row):
            ws.update("A1", [self.HEADERS])
        elif len(first_row) < len(self.HEADERS):
            missing = self.HEADERS[len(first_row):]
            start_col = _col_letter(len(first_row) + 1)
            ws.update("{0}1".format(start_col), [missing])

        self._apply_sales_validation(ws)
        self._apply_lead_status_validation(ws)

    def _apply_sales_validation(self, ws) -> None:
        """Ставит data-validation (выпадающий список) на колонку «Ответственный от продаж»."""
        reps = self.config.sales_reps
        if not reps:
            return
        self._apply_dropdown(ws, self.RESPONSIBLE_COL_LETTER, 2, list(reps))

    def _apply_lead_status_validation(self, ws) -> None:
        """Ставит data-validation (выпадающий список) на колонку «Статус лида»."""
        self._apply_dropdown(ws, self.STATUS_COL_LETTER, 14, self.LEAD_STATUS_OPTIONS)

    def _apply_dropdown(self, ws, col_letter: str, col_index0: int, options: List[str]) -> None:
        """Ставит data-validation (выпадающий список) на столбец col_letter, строки 2:1000.

        col_index0 — индекс столбца с отсчётом от 0 (для нативного batch_update API).
        Если версия gspread не поддерживает нужный API — тихо пропускаем (без падения).
        """
        if not options:
            return
        rng = "{0}2:{0}1000".format(col_letter)

        # Сначала пробуем удобный helper gspread (новые версии).
        try:
            from gspread_formatting import (  # type: ignore
                DataValidationRule,
                BooleanCondition,
                set_data_validation_for_cell_range,
            )
        except Exception:
            pass
        else:
            try:
                rule = DataValidationRule(
                    BooleanCondition("ONE_OF_LIST", list(options)),
                    showCustomUi=True,
                )
                set_data_validation_for_cell_range(ws, rng, rule)
                return
            except Exception:
                # gspread_formatting есть, но что-то пошло не так — пробуем batch_update.
                pass

        # Фолбэк: нативный batch_update через Sheets API.
        try:
            spreadsheet = ws.spreadsheet
            request = {
                "requests": [
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": ws.id,
                                "startRowIndex": 1,
                                "endRowIndex": 1000,
                                "startColumnIndex": col_index0,
                                "endColumnIndex": col_index0 + 1,
                            },
                            "rule": {
                                "condition": {
                                    "type": "ONE_OF_LIST",
                                    "values": [
                                        {"userEnteredValue": str(o)} for o in options
                                    ],
                                },
                                "showCustomUi": True,
                                "strict": False,
                            },
                        }
                    }
                ]
            }
            spreadsheet.batch_update(request)
        except Exception:
            # Версия gspread без batch_update или иная ошибка — продолжаем без валидации.
            pass

    # ----- проверка подключения (для интерфейса настроек) -----
    def test_connection(self) -> dict:
        """Пробует подключиться к таблице и подготовить её.

        Открывает таблицу (создаёт лист при отсутствии), записывает заголовки
        и выпадающий список ответственных. При проблеме бросает исключение
        (RuntimeError с подсказкой либо ошибку gspread/доступа).
        Возвращает информацию о подключённой таблице.
        """
        ws = self._worksheet()
        self.ensure_headers()
        spreadsheet = ws.spreadsheet
        return {
            "spreadsheet_title": spreadsheet.title,
            "worksheet": self.worksheet_name,
            "url": "https://docs.google.com/spreadsheets/d/{0}".format(self.spreadsheet_id),
            "headers": list(self.HEADERS),
        }

    # ----- добавление строки -----
    def append_card(
        self,
        card: CardData,
        photo_ref: str = "",
        scanned_by: str = "",
        scanned_by_position: str = "",
    ) -> int:
        """Добавляет визитку в таблицу и возвращает номер добавленной строки.

        Колонки «Ответственный от продаж» (3), «Статус лида» (15) и
        «Комментарий» (16) остаются пустыми — их заполняет человек.
        «Сотрудник»/«Должность сотрудника» (18-19) заполняются автоматически
        из аккаунта, которым выполнен вход на телефоне.
        """
        self.ensure_headers()
        ws = self._worksheet()

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        phones = ", ".join(p for p in (card.phones or []) if p)
        emails = ", ".join(e for e in (card.emails or []) if e)

        row = [
            date_str,             # Дата добавления
            card.source_event,    # Выставка / источник
            "",                   # Ответственный от продаж (заполняет человек)
            card.name,            # ФИО контакта
            card.title,           # Должность
            card.company,         # Компания (как на визитке)
            phones,               # Телефон(ы)
            emails,               # Email
            card.website,         # Сайт
            card.address,         # Адрес (с визитки)
            card.legal_name,      # Точное юр. название
            card.refined_address,  # Уточнённый адрес
            card.industry,        # Отрасль / сфера
            card.verified_website,  # Проверенный сайт
            "",                   # Статус лида (заполняет человек)
            "",                   # Комментарий (заполняет человек)
            _photo_cell(photo_ref),  # Фото визитки
            scanned_by,            # Сотрудник
            scanned_by_position,  # Должность сотрудника
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")

        # Номер добавленной строки = количество непустых строк в первой колонке.
        try:
            return len(ws.col_values(1))
        except Exception:
            return 0

    # ----- обратное чтение статуса лида (для синхронизации в мобильное приложение) -----
    def read_statuses(self, rows: List[int]) -> Dict[int, Dict[str, str]]:
        """Читает «Ответственного», «Статус лида» и «Комментарий» для заданных строк.

        Один батч-запрос (batch_get) на все строки сразу — вызывается на
        каждый /jobs с телефона, поэтому важно не делать по запросу на строку.
        Возвращает {номер_строки: {"responsible": ..., "status": ..., "comment": ...}};
        строки, для которых Sheets не вернул значений, в результате отсутствуют.
        """
        wanted = sorted({r for r in rows if r and r > 1})
        if not wanted:
            return {}

        ws = self._worksheet()
        ranges: List[str] = []
        for r in wanted:
            ranges.append("{0}{1}:{0}{1}".format(self.RESPONSIBLE_COL_LETTER, r))
            ranges.append("{0}{2}:{1}{2}".format(self.STATUS_COL_LETTER, self.COMMENT_COL_LETTER, r))
        grids = ws.batch_get(ranges)

        out: Dict[int, Dict[str, str]] = {}
        for i, row in enumerate(wanted):
            resp_grid = grids[i * 2]
            status_grid = grids[i * 2 + 1]
            responsible = resp_grid[0][0] if resp_grid and resp_grid[0] else ""
            values = status_grid[0] if status_grid else []
            status = values[0] if len(values) > 0 else ""
            comment = values[1] if len(values) > 1 else ""
            out[row] = {
                "responsible": str(responsible).strip(),
                "status": str(status).strip(),
                "comment": str(comment).strip(),
            }
        return out
