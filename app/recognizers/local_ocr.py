"""Локальный распознаватель визиток на классическом OCR.

Без LLM: извлекает текст движком OCR (tesseract / paddleocr) и парсит
поля визитки эвристиками (regex + простые правила).

Тяжёлые зависимости (PIL, pytesseract, paddleocr) импортируются лениво —
сам импорт модуля не требует их установки и не делает сетевых вызовов.

Python 3.9-совместимо.
"""
from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Dict, List

from .base import CardData, Recognizer

if TYPE_CHECKING:  # только для типов, без рантайм-зависимости
    from app.config import Config


# --- регулярные выражения для парсинга полей ---

# Email: локальная часть @ домен.зона
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)

# Телефонные последовательности: цифры, пробелы, +, (), -, точки.
# Пробельные символы — только горизонтальные (не \n), чтобы телефон не
# «склеивался» с соседними строками (например с почтовым индексом).
# Минимум 7 символов «телефонной» части — фильтруем по числу цифр позже.
_PHONE_RE = re.compile(r"(?<!\w)(\+?[\d()\-.\t ]{7,}\d)(?!\w)")

# Сайт: http(s)://, www. или голый домен с распространённой зоной.
_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s,;]+"
    r"|(?<![\w@.])[\w-]+(?:\.[\w-]+)+\.(?:ru|com|org|net|info|biz|io|me|su|рф|"
    r"co|ua|by|kz|de|uk|eu|us|tech|online|store|shop|pro)(?:/[^\s,;]*)?",
    re.IGNORECASE | re.UNICODE,
)

# Почтовый индекс РФ — ровно 6 цифр.
_ZIP_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")

# Имя: 2-3 слова с заглавной буквы (кириллица/латиница), без цифр и @.
_NAME_WORD = r"[А-ЯЁA-Z][а-яёa-zА-ЯЁA-Z'.-]+"
_NAME_RE = re.compile(
    r"^{w}(?:\s+{w}){{1,2}}$".format(w=_NAME_WORD), re.UNICODE
)

# Признаки компании (юр. формы).
_COMPANY_MARKERS = [
    "ооо", "оао", "пао", "ао", "зао", "ип", "нко", "ано", "фгуп", "гуп",
    "llc", "ltd", "inc", "gmbh", "co.", "corp", "company", "group", "холдинг",
    "корпорация", "компания", "фирма", "завод", "предприятие",
]

# Признаки должности.
_TITLE_MARKERS = [
    "директор", "менеджер", "руководитель", "начальник", "инженер",
    "специалист", "консультант", "аналитик", "бухгалтер", "юрист",
    "президент", "вице-президент", "заместитель", "помощник", "ассистент",
    "разработчик", "продавец", "архитектор", "технолог", "администратор",
    "владелец", "основатель", "партнёр", "партнер", "представитель",
    "manager", "director", "ceo", "cto", "cfo", "coo", "founder", "head",
    "engineer", "developer", "consultant", "specialist", "officer",
    "president", "lead", "analyst", "designer", "architect", "sales",
]

# Признаки адреса.
_ADDRESS_MARKERS = [
    "ул.", "улица", "г.", "город", "д.", "дом", "пр-т", "пр.", "проспект",
    "пер.", "переулок", "наб.", "набережная", "пл.", "площадь", "ш.", "шоссе",
    "офис", "оф.", "корп.", "корпус", "стр.", "строение", "кв.", "обл.",
    "область", "р-н", "район", "бульвар", "б-р", "проезд", "street", "str.",
    "ave", "avenue", "road", "rd.", "suite", "office", "floor",
]


def _has_marker(text_lower: str, markers: List[str]) -> bool:
    """True, если в строке (нижний регистр) встречается любой из маркеров.

    Для маркеров без точки проверяем границу слова, чтобы «инженер» не ловился
    внутри случайных подстрок; маркеры с точкой/дефисом ищем как подстроку.
    """
    for m in markers:
        if not m:
            continue
        if re.search(r"[.\-]", m):
            if m in text_lower:
                return True
        else:
            if re.search(r"(?<!\w)" + re.escape(m) + r"(?!\w)", text_lower):
                return True
    return False


def _normalize_phone(raw: str) -> str:
    """Нормализует телефон: оставляет цифры и ведущий '+', убирает мусор."""
    s = raw.strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    return ("+" + digits) if plus else digits


def parse_fields(raw_text: str) -> Dict[str, object]:
    """Парсит сырой текст OCR в словарь полей визитки (шаг 1).

    Возвращает ключи: name, title, company, phones, emails, website, address.
    Функция вынесена отдельно для удобства модульного тестирования.
    """
    result: Dict[str, object] = {
        "name": "",
        "title": "",
        "company": "",
        "phones": [],
        "emails": [],
        "website": "",
        "address": "",
    }
    if not raw_text:
        return result

    # --- email ---
    emails: List[str] = []
    for m in _EMAIL_RE.findall(raw_text):
        e = m.strip(" .,;:")
        if e and e not in emails:
            emails.append(e)
    result["emails"] = emails
    email_set = {e.lower() for e in emails}

    # --- телефоны ---
    phones: List[str] = []
    for m in _PHONE_RE.findall(raw_text):
        norm = _normalize_phone(m)
        # минимум 7 цифр, максимум 15 (E.164); фильтруем индексы/годы
        digits = norm.lstrip("+")
        if 7 <= len(digits) <= 15 and norm not in phones:
            phones.append(norm)
    result["phones"] = phones

    # --- сайт ---
    website = ""
    for m in _URL_RE.finditer(raw_text):
        candidate = m.group(0).strip(" .,;:")
        # исключаем то, что является частью email
        if "@" in candidate:
            continue
        low = candidate.lower()
        if low in email_set:
            continue
        # домен из email вида name@company.ru не должен попасть как сайт,
        # но отдельный домен на визитке — валидный сайт.
        is_part_of_email = any(low and low in e for e in email_set)
        if is_part_of_email:
            continue
        website = candidate
        break
    result["website"] = website

    # --- построчный разбор для name / company / title / address ---
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    company = ""
    title = ""
    name = ""
    address = ""

    for line in lines:
        low = line.lower()

        # пропускаем строки, целиком являющиеся контактами
        if _EMAIL_RE.fullmatch(line) or _PHONE_RE.fullmatch(line):
            continue

        if not company and _has_marker(low, _COMPANY_MARKERS):
            company = line
            continue

        if not title and _has_marker(low, _TITLE_MARKERS):
            title = line
            continue

        if not address and (
            _has_marker(low, _ADDRESS_MARKERS) or _ZIP_RE.search(line)
        ):
            address = line
            continue

        if not name and "@" not in line and not re.search(r"\d", line):
            if _NAME_RE.match(line):
                name = line
                continue

    # company-фолбэк: самая «крупная» строка (по числу букв), если не нашли
    if not company:
        candidates = []
        for line in lines:
            if line in (name, title, address, website):
                continue
            if "@" in line or _EMAIL_RE.search(line) or _PHONE_RE.fullmatch(line):
                continue
            # пропускаем строки, целиком являющиеся ссылкой
            url_match = _URL_RE.search(line)
            if url_match and url_match.group(0).strip(" .,;:") == line:
                continue
            letters = len(re.findall(r"[^\W\d_]", line, re.UNICODE))
            if letters >= 3:
                candidates.append((letters, line))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            company = candidates[0][1]

    result["name"] = name
    result["title"] = title
    result["company"] = company
    result["address"] = address
    return result


class LocalOcrRecognizer(Recognizer):
    """Распознаватель на классическом OCR без LLM.

    Поддерживает движки tesseract (через pytesseract) и paddleocr.
    """

    name = "local-ocr"

    def __init__(self, config: "Config"):
        self.config = config
        self.engine = str(config.get("local.ocr.engine", "tesseract"))
        self.lang = str(config.get("local.ocr.lang", "rus+eng"))

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> CardData:
        # --- открыть изображение (ленивый PIL) ---
        try:
            from PIL import Image  # noqa: WPS433 (ленивый импорт намеренно)
        except ImportError:
            raise RuntimeError(
                "Не установлен Pillow. Установите: pip install Pillow"
            )

        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.load()
        except Exception as exc:  # noqa: BLE001
            return CardData(notes="OCR error: не удалось открыть изображение: {0}".format(exc))

        # --- распознать текст выбранным движком ---
        engine = (self.engine or "tesseract").lower()
        try:
            if engine == "tesseract":
                raw_text = self._ocr_tesseract(img)
            elif engine == "paddleocr":
                raw_text = self._ocr_paddle(image_bytes)
            else:
                return CardData(
                    notes="OCR error: неизвестный движок '{0}' (ожидается tesseract|paddleocr)".format(self.engine)
                )
        except _TesseractMissing as exc:
            return CardData(notes=str(exc))
        except RuntimeError as exc:
            # понятные ошибки об отсутствии зависимостей пробрасываем в notes
            return CardData(notes="OCR error: {0}".format(exc))
        except Exception as exc:  # noqa: BLE001
            return CardData(notes="OCR error: {0}".format(exc))

        raw_text = raw_text or ""

        # --- распарсить поля ---
        fields = parse_fields(raw_text)
        return CardData(
            name=str(fields.get("name", "")),
            title=str(fields.get("title", "")),
            company=str(fields.get("company", "")),
            phones=list(fields.get("phones", []) or []),
            emails=list(fields.get("emails", []) or []),
            website=str(fields.get("website", "")),
            address=str(fields.get("address", "")),
            raw_text=raw_text,
            notes="" if raw_text.strip() else "OCR не распознал текст",
        )

    # ----- движки -----

    def _ocr_tesseract(self, img) -> str:
        try:
            import pytesseract  # noqa: WPS433
        except ImportError:
            raise RuntimeError(
                "Не установлен pytesseract. Установите: pip install pytesseract"
            )
        try:
            return pytesseract.image_to_string(img, lang=self.lang)
        except pytesseract.TesseractNotFoundError:
            raise _TesseractMissing(
                "OCR error: бинарь tesseract не найден. Установите его, например: "
                "macOS — brew install tesseract tesseract-lang; "
                "Ubuntu/Debian — sudo apt-get install tesseract-ocr tesseract-ocr-rus"
            )

    def _ocr_paddle(self, image_bytes: bytes) -> str:
        try:
            from paddleocr import PaddleOCR  # noqa: WPS433
        except ImportError:
            raise RuntimeError(
                "Не установлен paddleocr. Установите: pip install paddleocr paddlepaddle"
            )
        try:
            import numpy as np  # noqa: WPS433
            from PIL import Image  # noqa: WPS433
        except ImportError:
            raise RuntimeError(
                "Для paddleocr нужны numpy и Pillow. Установите: pip install numpy Pillow"
            )

        # paddleocr использует язык 'ru' для кириллицы (распознаёт и латиницу)
        lang = "ru" if "rus" in self.lang.lower() else "en"
        ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(img)
        raw = ocr.ocr(arr, cls=True)

        # Структура результата paddleocr: список страниц -> список [box, (text, score)]
        lines: List[str] = []
        for page in raw or []:
            for item in page or []:
                try:
                    text = item[1][0]
                except (IndexError, TypeError):
                    continue
                if text:
                    lines.append(str(text))
        return "\n".join(lines)


class _TesseractMissing(RuntimeError):
    """Внутреннее исключение: отсутствует бинарь tesseract."""
