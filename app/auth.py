"""Авторизация пользователей приложения (кто сканирует визитки).

Не путать с Google service-account (это отдельный ключ для Sheets API).
Хранилище — простые JSON-файлы рядом с data/photos: инструмент внутренний,
на один офисный ПК, отдельная БД избыточна. Пароли — PBKDF2-HMAC-SHA256
(stdlib, без новых зависимостей), сессии — непрозрачные токены с долгим
сроком жизни ("запомнить меня" должно переживать перезапуск сервера).

Все операции с data/users.json и data/sessions.json идут через один
asyncio.Lock — FastAPI-хендлеры выполняются конкурентно, а наивное
read-modify-write без блокировки рискует потерять параллельные изменения
(два одновременных логина/правки пользователя).

Python 3.9-совместимо.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request

from app.config import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
USERS_PATH = DATA_DIR / "users.json"
SESSIONS_PATH = DATA_DIR / "sessions.json"

PBKDF2_ITERATIONS = 600_000
SESSION_TTL_SECONDS = 180 * 24 * 3600  # 180 дней — «запомнить меня»

_lock = asyncio.Lock()


# ----- низкоуровневое хранилище (синхронный I/O, вызывается через to_thread) -----

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_users() -> Dict[str, Dict[str, Any]]:
    return _read_json(USERS_PATH)


def _save_users(users: Dict[str, Dict[str, Any]]) -> None:
    _write_json(USERS_PATH, users)


def _load_sessions() -> Dict[str, Dict[str, Any]]:
    """Читает сессии, попутно вычищая просроченные (ленивая уборка)."""
    sessions = _read_json(SESSIONS_PATH)
    now = time.time()
    alive = {tok: s for tok, s in sessions.items() if s.get("expires_at", 0) > now}
    if len(alive) != len(sessions):
        _write_json(SESSIONS_PATH, alive)
    return alive


def _save_sessions(sessions: Dict[str, Dict[str, Any]]) -> None:
    _write_json(SESSIONS_PATH, sessions)


# ----- пароли -----

def _hash_password(password: str, salt: Optional[str] = None) -> Dict[str, Any]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return {"hash": digest.hex(), "salt": salt, "iterations": PBKDF2_ITERATIONS}


def _verify_password(password: str, user: Dict[str, Any]) -> bool:
    iterations = int(user.get("iterations", PBKDF2_ITERATIONS))
    salt = str(user.get("salt", ""))
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return hmac.compare_digest(digest.hex(), str(user.get("password_hash", "")))


def _public_user(username: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Данные пользователя без хэша пароля/соли — то, что можно отдавать наружу."""
    return {
        "username": username,
        "position": user.get("position", ""),
        "is_admin": bool(user.get("is_admin", False)),
        "created_at": user.get("created_at"),
    }


def extract_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


# ----- публичное API (асинхронное — вызывается из FastAPI-хендлеров) -----

async def has_any_user() -> bool:
    async with _lock:
        users = await asyncio.to_thread(_load_users)
        return bool(users)


async def bootstrap_admin(username: str, password: str, position: str) -> Dict[str, Any]:
    """Создаёт первого пользователя (администратора). Только пока пользователей нет вовсе."""
    username = username.strip()
    if not username or not password:
        raise ValueError("Логин и пароль обязательны")
    async with _lock:
        users = await asyncio.to_thread(_load_users)
        if users:
            raise PermissionError("Пользователи уже созданы")
        hashed = _hash_password(password)
        users[username] = {
            "password_hash": hashed["hash"],
            "salt": hashed["salt"],
            "iterations": hashed["iterations"],
            "position": position.strip(),
            "is_admin": True,
            "created_at": time.time(),
        }
        await asyncio.to_thread(_save_users, users)
        return _public_user(username, users[username])


async def create_user(username: str, password: str, position: str, is_admin: bool) -> Dict[str, Any]:
    username = username.strip()
    if not username or not password:
        raise ValueError("Логин и пароль обязательны")
    async with _lock:
        users = await asyncio.to_thread(_load_users)
        if username in users:
            raise ValueError("Такой логин уже существует")
        hashed = _hash_password(password)
        users[username] = {
            "password_hash": hashed["hash"],
            "salt": hashed["salt"],
            "iterations": hashed["iterations"],
            "position": position.strip(),
            "is_admin": bool(is_admin),
            "created_at": time.time(),
        }
        await asyncio.to_thread(_save_users, users)
        return _public_user(username, users[username])


async def list_users() -> List[Dict[str, Any]]:
    async with _lock:
        users = await asyncio.to_thread(_load_users)
    return [_public_user(u, rec) for u, rec in sorted(users.items())]


async def delete_user(username: str) -> None:
    username = username.strip()
    async with _lock:
        users = await asyncio.to_thread(_load_users)
        if username not in users:
            raise KeyError("Пользователь не найден")
        admins = [u for u, rec in users.items() if rec.get("is_admin")]
        if users[username].get("is_admin") and len(admins) <= 1:
            raise PermissionError("Нельзя удалить последнего администратора")
        del users[username]
        await asyncio.to_thread(_save_users, users)

        sessions = await asyncio.to_thread(_load_sessions)
        alive = {t: s for t, s in sessions.items() if s.get("username") != username}
        if len(alive) != len(sessions):
            await asyncio.to_thread(_save_sessions, alive)


async def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    username = username.strip()
    async with _lock:
        users = await asyncio.to_thread(_load_users)
    user = users.get(username)
    if not user or not _verify_password(password, user):
        return None
    return _public_user(username, user)


async def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    async with _lock:
        sessions = await asyncio.to_thread(_load_sessions)
        sessions[token] = {"username": username, "created_at": now, "expires_at": now + SESSION_TTL_SECONDS}
        await asyncio.to_thread(_save_sessions, sessions)
    return token


async def delete_session(token: str) -> None:
    if not token:
        return
    async with _lock:
        sessions = await asyncio.to_thread(_load_sessions)
        if token in sessions:
            del sessions[token]
            await asyncio.to_thread(_save_sessions, sessions)


async def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    async with _lock:
        sessions = await asyncio.to_thread(_load_sessions)
        session = sessions.get(token)
        if not session:
            return None
        users = await asyncio.to_thread(_load_users)
    username = session.get("username")
    user = users.get(username) if username else None
    if not user:
        return None
    return _public_user(username, user)


# ----- FastAPI-зависимости -----

async def require_user(request: Request) -> Dict[str, Any]:
    token = extract_token(request)
    user = await get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Требуется вход в систему")
    return user


async def require_admin(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return user
