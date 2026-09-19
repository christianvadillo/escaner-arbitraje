"""http.py — cliente HTTP cortés: la única puerta de salida a la red para páginas de retailers.

La política de "nunca evadir un bloqueo" (ESPECIFICACION §3) solo sirve si vive en un lugar y no
se repite (e, inevitablemente, diverge) en cada scraper. `PoliteClient` centraliza:

  - **robots.txt** por host, cacheado en memoria por la vida del cliente, evaluado con NUESTRO
    user-agent antes de pedir cualquier ruta.
  - **límite de tasa**: `max(min_host_interval_s, Crawl-delay de robots.txt)` entre peticiones al
    mismo host. Reloj y sleep son inyectables para poder probar el conteo de segundos sin
    esperar segundos de verdad.
  - **caché condicional** (ETag / Last-Modified) en sqlite, para no volver a pagar el costo de
    una página que no cambió.
  - **detección de bloqueo**: un 403/429/503, o un cuerpo con marcas de CAPTCHA/WAF, se trata
    como señal de que el sitio nos identificó como bot. La respuesta correcta NO es reintentar
    con otra huella: es apagar ese host por el resto de la corrida (`SourceBlocked`).
  - **lista negra dura**: unos hosts no se piden ni una vez, pase lo que pase.

Los reintentos (máximo 2) son solo para errores de RED transitorios (timeout, conexión rota);
un 403 nunca se reintenta, se interpreta.
"""

from __future__ import annotations

import sqlite3
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

BLACKLIST = frozenset(
    {
        "walmart.com.mx",
        "liverpool.com.mx",
        "coppel.com",
    }
)

_BLOCK_STATUSES = frozenset({403, 429, 503})
_BLOCK_BODY_MARKERS = (
    "captcha",
    "perimeterx",
    "px-captcha",
    "no eres un robot",
    "no eres robot",
    "access denied",
    "acceso denegado",
)


class BlacklistedHost(RuntimeError):
    """Host en la lista negra dura (ESPECIFICACION §3): nunca se pide, ni robots.txt."""


class RobotsDisallowed(RuntimeError):
    """robots.txt de este host prohíbe la ruta a nuestro user-agent."""


class SourceBlocked(RuntimeError):
    """El host respondió como bloqueado (status o cuerpo). No se reintenta; el host queda
    deshabilitado para el resto de esta corrida (nueva instancia de `PoliteClient` = nueva corrida)."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(f"{host}: {reason}")
        self.host = host
        self.reason = reason


def is_blacklisted(host: str) -> bool:
    """Incluye subdominios: m.liverpool.com.mx y super.walmart.com.mx también quedan fuera."""
    host = _bare_host(host)
    return any(host == b or host.endswith("." + b) for b in BLACKLIST)


def _bare_host(url_or_host: str) -> str:
    host = urlparse(url_or_host).hostname or url_or_host
    return host.removeprefix("www.").lower()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crea `http_cache` si no existe. Idempotente: seguro de llamar desde `store.init_db` y
    también standalone en tests de este módulo."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS http_cache (
            url TEXT PRIMARY KEY,
            status INTEGER NOT NULL,
            etag TEXT,
            last_modified TEXT,
            body TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int
    text: str
    from_cache: bool
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class _HostState:
    last_request_ts: float | None = None
    disabled: bool = False
    disabled_reason: str = ""


def _looks_blocked(status_code: int, text: str) -> str | None:
    if status_code in _BLOCK_STATUSES:
        return f"status {status_code}"
    low = text.lower()
    for marker in _BLOCK_BODY_MARKERS:
        if marker in low:
            return f'cuerpo contiene "{marker}"'
    return None


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        conn: sqlite3.Connection,
        min_host_interval_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
        timeout: float = 15.0,
    ) -> None:
        self.user_agent = user_agent
        self.min_host_interval_s = min_host_interval_s
        self.max_retries = max_retries
        conn.row_factory = sqlite3.Row  # este módulo es autosuficiente: no asume que el llamador ya lo puso
        self._conn = conn
        ensure_schema(conn)
        self._clock = clock
        self._sleep = sleep
        self._httpx = httpx.Client(transport=transport, timeout=timeout, follow_redirects=True)
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._host_state: dict[str, _HostState] = {}

    def close(self) -> None:
        self._httpx.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── política ──────────────────────────────────────────────────────────────────────────

    def _state(self, host: str) -> _HostState:
        return self._host_state.setdefault(host, _HostState())

    def _robots_for(self, host: str) -> urllib.robotparser.RobotFileParser:
        if host in self._robots:
            return self._robots[host]
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"https://{host}/robots.txt"
        try:
            self._throttle(host, self.min_host_interval_s)
            result = self._fetch(robots_url, use_cache=True)
            if result.status_code < 400:
                rp.parse(result.text.splitlines())
            else:
                rp.parse([])  # sin robots.txt (o bloqueado): se asume permitir todo
        except Exception:
            rp.parse([])
        # `parse()` (a diferencia de `read()`) no marca `last_checked`, y `can_fetch` devuelve
        # False incondicionalmente mientras `last_checked` sea falsy (para no dar falsos
        # positivos antes de leer el robots.txt). Como sí lo leímos arriba, lo marcamos.
        rp.modified()
        self._robots[host] = rp
        return rp

    def _throttle(self, host: str, delay: float) -> None:
        state = self._state(host)
        if state.last_request_ts is not None:
            wait = delay - (self._clock() - state.last_request_ts)
            if wait > 0:
                self._sleep(wait)
        state.last_request_ts = self._clock()

    def _ensure_allowed(self, url: str) -> tuple[str, float]:
        host = _bare_host(url)
        if is_blacklisted(host):
            raise BlacklistedHost(f"{host} está en la lista negra: no se pide")
        state = self._state(host)
        if state.disabled:
            raise SourceBlocked(host, state.disabled_reason)
        rp = self._robots_for(host)
        if not rp.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"{host}: robots.txt prohíbe {url} para {self.user_agent!r}")
        delay = max(self.min_host_interval_s, rp.crawl_delay(self.user_agent) or 0.0)
        return host, delay

    # ── red ───────────────────────────────────────────────────────────────────────────────

    def _send(self, url: str, headers: dict[str, str]) -> httpx.Response:
        attempts = 0
        while True:
            try:
                return self._httpx.get(url, headers={"User-Agent": self.user_agent, **headers})
            except httpx.TransportError:
                attempts += 1
                if attempts > self.max_retries:
                    raise
                self._sleep(min(2.0**attempts, 5.0))

    def _cache_row(self, url: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT status, etag, last_modified, body FROM http_cache WHERE url = ?", (url,)
        ).fetchone()

    def _cache_store(self, url: str, resp: httpx.Response) -> None:
        self._conn.execute(
            """
            INSERT INTO http_cache (url, status, etag, last_modified, body, fetched_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(url) DO UPDATE SET
                status = excluded.status, etag = excluded.etag,
                last_modified = excluded.last_modified, body = excluded.body,
                fetched_at = excluded.fetched_at
            """,
            (url, resp.status_code, resp.headers.get("ETag"), resp.headers.get("Last-Modified"), resp.text),
        )
        self._conn.commit()

    def _fetch(self, url: str, use_cache: bool) -> FetchResult:
        cached = self._cache_row(url) if use_cache else None
        headers: dict[str, str] = {}
        if cached:
            if cached["etag"]:
                headers["If-None-Match"] = cached["etag"]
            if cached["last_modified"]:
                headers["If-Modified-Since"] = cached["last_modified"]
        resp = self._send(url, headers)
        if cached and resp.status_code == 304:
            return FetchResult(url, cached["status"], cached["body"], from_cache=True)
        blocked = _looks_blocked(resp.status_code, resp.text)
        if blocked:
            raise SourceBlocked(_bare_host(url), blocked)
        if use_cache and resp.status_code < 400:
            self._cache_store(url, resp)
        return FetchResult(url, resp.status_code, resp.text, from_cache=False, headers=dict(resp.headers))

    # ── API pública ───────────────────────────────────────────────────────────────────────

    def get(self, url: str, *, use_cache: bool = True) -> FetchResult:
        """Trae `url` respetando robots.txt, el límite de tasa del host y la caché condicional.

        Lanza `BlacklistedHost` / `RobotsDisallowed` sin tocar la red, o `SourceBlocked` si el
        host responde como bloqueado (y lo deshabilita para el resto de esta instancia)."""
        host, delay = self._ensure_allowed(url)
        self._throttle(host, delay)
        try:
            return self._fetch(url, use_cache)
        except SourceBlocked as e:
            state = self._state(host)
            state.disabled = True
            state.disabled_reason = e.reason
            raise
