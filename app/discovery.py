"""Автопоиск ПК в локальной сети (mDNS / Bonjour).

Сервер публикует себя по mDNS как `cardscan.local`, плюс service-запись
`_cardscan._tcp.local.`. Телефон в той же Wi-Fi сети открывает
http://cardscan.local:<port> без ввода IP-адреса — браузер резолвит `.local`
через системный mDNS-резолвер (iOS/Android умеют это «из коробки»).

Если zeroconf не установлен или публикация не удалась — приложение продолжает
работать, просто без автопоиска (остаётся вариант с прямым IP и QR-кодом на /connect).

Python 3.9-совместимо. zeroconf импортируется лениво.
"""
from __future__ import annotations

import logging
import socket
from typing import Optional

logger = logging.getLogger("cardscan.discovery")

SERVICE_TYPE = "_cardscan._tcp.local."
DEFAULT_HOSTNAME = "cardscan"  # -> cardscan.local


def get_lan_ip() -> str:
    """Определяет основной IP компьютера в локальной сети.

    Открывает UDP-сокет «в сторону» внешнего адреса (реальные пакеты не шлются)
    и смотрит, какой локальный интерфейс выбрала ОС.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        sock.close()
    return ip


class Discovery:
    """Публикация/снятие mDNS-записи сервера. Один экземпляр на приложение."""

    def __init__(self, port: int, hostname: str = DEFAULT_HOSTNAME, version: str = "0.1.0"):
        self.port = int(port)
        self.hostname = hostname
        self.version = version
        self._zc = None        # type: Optional[object]  (zeroconf.Zeroconf)
        self._info = None       # type: Optional[object]  (zeroconf.ServiceInfo)
        # IP определяем сразу (мгновенно) — нужен для баннера/QR даже если
        # сама публикация mDNS запустится в фоне или не удастся.
        self.lan_ip = get_lan_ip()

    def start(self) -> None:
        self.lan_ip = get_lan_ip()
        try:
            from zeroconf import ServiceInfo, Zeroconf  # ленивый импорт
        except Exception as exc:  # библиотека не установлена
            logger.warning(
                "mDNS недоступен (нет zeroconf: %s). Автопоиск с телефона не работает — "
                "используйте http://%s:%s или QR на /connect.",
                exc, self.lan_ip, self.port,
            )
            return

        try:
            instance = "CardScan ({})".format(socket.gethostname())
            self._info = ServiceInfo(
                SERVICE_TYPE,
                name="{}.{}".format(instance, SERVICE_TYPE),
                addresses=[socket.inet_aton(self.lan_ip)],
                port=self.port,
                properties={"version": self.version, "path": "/"},
                server="{}.local.".format(self.hostname),
            )
            self._zc = Zeroconf()
            self._zc.register_service(self._info)
            logger.info(
                "mDNS опубликован: http://%s.local:%s  (и http://%s:%s)",
                self.hostname, self.port, self.lan_ip, self.port,
            )
        except Exception as exc:
            logger.warning(
                "Не удалось опубликовать mDNS (%s). Автопоиск может не работать "
                "(песочница/файрвол/изоляция Wi-Fi) — используйте http://%s:%s или QR на /connect.",
                repr(exc), self.lan_ip, self.port,
            )
            self._safe_close()

    def stop(self) -> None:
        self._safe_close()

    # ----- внутреннее -----
    def _safe_close(self) -> None:
        if self._zc is not None:
            try:
                if self._info is not None:
                    self._zc.unregister_service(self._info)
            except Exception:
                pass
            try:
                self._zc.close()
            except Exception:
                pass
        self._zc = None
        self._info = None

    # ----- удобные свойства для UI/эндпоинтов -----
    def server_url(self) -> str:
        return "http://{}:{}".format(self.lan_ip, self.port)

    def hostname_url(self) -> str:
        return "http://{}.local:{}".format(self.hostname, self.port)
