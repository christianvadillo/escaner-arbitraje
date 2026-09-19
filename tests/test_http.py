"""PoliteClient: robots.txt, límite de tasa, caché condicional, detección de bloqueo, lista
negra. Todo sobre `httpx.MockTransport` — cero red, reloj y sleep falsos e inyectados."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from escaner.http import (
    BlacklistedHost,
    PoliteClient,
    RobotsDisallowed,
    SourceBlocked,
)

UA = "escaner-arbitraje/0.1 (+uso personal; contacto: test@example.com)"


class FakeClock:
    """`now()` es el reloj; `advance()` hace las veces de `sleep()` (avanza el reloj en vez de
    esperar de verdad) — así una prueba de "espera 7s" corre en microsegundos."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _client(handler, **kw) -> PoliteClient:
    clock = kw.pop("clock", None) or FakeClock()
    return PoliteClient(
        UA,
        _conn(),
        min_host_interval_s=kw.pop("min_host_interval_s", 10.0),
        transport=httpx.MockTransport(handler),
        clock=clock.now,
        sleep=clock.advance,
        **kw,
    ), clock


def test_robots_disallow_blocks_without_requesting_the_page():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /privado\n")
        return httpx.Response(200, text="no deberia llegar aqui")

    client, _clock = _client(handler)
    with pytest.raises(RobotsDisallowed):
        client.get("https://tienda.example.com/privado/producto")

    assert "/privado/producto" not in requested
    assert requested == ["/robots.txt"]  # se leyó robots.txt pero nunca la página prohibida


def test_source_blocked_is_not_retried_and_disables_the_host():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)  # sin robots.txt: se asume permitir todo
        return httpx.Response(403, text="Access Denied - PerimeterX captcha")

    client, _clock = _client(handler)
    with pytest.raises(SourceBlocked):
        client.get("https://bloqueado.example.com/producto/1")
    n_after_first = len(calls)
    assert n_after_first == 2  # robots.txt + el intento único a la página (sin reintento)

    # Un segundo intento al MISMO host, aunque sea otra ruta, ni toca la red: el host quedó
    # deshabilitado para el resto de esta corrida (esta instancia de PoliteClient).
    with pytest.raises(SourceBlocked):
        client.get("https://bloqueado.example.com/producto/2")
    assert len(calls) == n_after_first


def test_blacklisted_host_is_never_requested_not_even_robots_txt():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text="ok")

    client, _clock = _client(handler)
    with pytest.raises(BlacklistedHost):
        client.get("https://www.walmart.com.mx/producto/1")
    assert calls == []


def test_crawl_delay_from_robots_overrides_min_host_interval():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\nCrawl-delay: 7\n")
        return httpx.Response(200, text="ok")

    client, clock = _client(handler, min_host_interval_s=1.0)
    client.get("https://lento.example.com/a")  # dispara el fetch de robots.txt (sin espera: 1ª vez)
    client.get("https://lento.example.com/b")  # debe esperar el Crawl-delay (7s), no solo 1s

    assert clock.slept, "se esperaba al menos una espera antes de la segunda petición"
    assert max(clock.slept) == pytest.approx(7.0, abs=0.01)


def test_conditional_cache_reuses_body_on_304():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        if request.headers.get("If-None-Match") == "abc123":
            return httpx.Response(304)
        return httpx.Response(200, text="contenido v1", headers={"ETag": "abc123"})

    client, _clock = _client(handler)
    first = client.get("https://cache.example.com/pagina")
    second = client.get("https://cache.example.com/pagina")

    assert first.text == "contenido v1"
    assert second.text == "contenido v1"
    assert second.from_cache is True
    assert calls["n"] == 2  # ambas peticiones llegaron al server (304 no evita la ida y vuelta)


def test_blacklist_covers_subdomains():
    from escaner.http import is_blacklisted

    assert is_blacklisted("https://m.liverpool.com.mx/tienda/pdp/123")
    assert is_blacklisted("super.walmart.com.mx")
    assert is_blacklisted("www.coppel.com")
    assert not is_blacklisted("bodegaaurrera.com.mx")
    assert not is_blacklisted("notwalmart.com.mx")
