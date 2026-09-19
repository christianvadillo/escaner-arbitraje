"""OAuth de Mercado Libre: URL de autorización, canje de code, y el detalle que importa de
verdad — el refresh_token es de un solo uso y hay que persistir el `TokenSet` nuevo (con su
refresh_token rotado) ANTES de que el viejo se vuelva inservible."""

from __future__ import annotations

import httpx

from escaner.config import load_token, save_token
from escaner.meli.oauth import TokenSet, authorization_url, exchange_code, refresh


def test_authorization_url_has_the_right_shape():
    url = authorization_url("APP123", "https://miapp.example/callback", state="xyz")
    assert url.startswith("https://auth.mercadolibre.com.mx/authorization?")
    assert "client_id=APP123" in url
    assert "response_type=code" in url
    assert "state=xyz" in url


def test_exchange_code_builds_token_set():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        return httpx.Response(
            200,
            json={"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 10800, "user_id": 123},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    clock = iter([1000.0]).__next__
    token = exchange_code(http, "app", "secret", "https://cb", "the-code", clock=clock)
    assert token.access_token == "AT-1"
    assert token.refresh_token == "RT-1"
    assert token.expires_at == 1000.0 + 10800
    assert token.user_id == "123"


def test_is_expired_respects_skew():
    token = TokenSet(access_token="a", refresh_token="r", expires_at=1000.0)
    assert not token.is_expired(now=900.0)
    assert token.is_expired(now=950.0)  # dentro del margen de 60s por defecto
    assert token.is_expired(now=1000.0)


def test_refresh_rotates_the_refresh_token():
    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(x.split("=") for x in request.content.decode().split("&"))
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "RT-1"  # el que se le pasó
        return httpx.Response(200, json={"access_token": "AT-2", "refresh_token": "RT-2", "expires_in": 10800})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    new_token = refresh(http, "app", "secret", "RT-1", clock=lambda: 2000.0)
    assert new_token.access_token == "AT-2"
    assert new_token.refresh_token == "RT-2"  # NUNCA el mismo que el que entró: es de un solo uso
    assert new_token.refresh_token != "RT-1"


def test_rotation_is_persisted_to_disk_before_the_old_token_is_gone(tmp_path):
    """El flujo completo que `cli.py::_load_ml_client` implementa: cargar → si expiró, refrescar
    → GUARDAR el TokenSet nuevo → solo entonces usar el access_token nuevo."""
    token_path = tmp_path / "ml_token.json"
    original = TokenSet(access_token="AT-1", refresh_token="RT-1", expires_at=100.0, user_id="9")
    save_token(token_path, original)

    loaded = load_token(token_path)
    assert loaded == original

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "AT-2", "refresh_token": "RT-2", "expires_in": 10800})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    rotated = refresh(http, "app", "secret", loaded.refresh_token, clock=lambda: 5000.0)
    save_token(token_path, rotated)  # persistir ANTES de usar rotated.access_token en cualquier lado

    reloaded = load_token(token_path)
    assert reloaded.refresh_token == "RT-2"
    assert reloaded.access_token == "AT-2"
    assert reloaded.refresh_token != original.refresh_token


def test_save_token_is_atomic_and_0600(tmp_path):
    path = tmp_path / "sub" / "ml_token.json"
    save_token(path, TokenSet(access_token="a", refresh_token="b", expires_at=1.0))
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()  # el temporal no debe sobrevivir
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_load_token_missing_file_returns_none(tmp_path):
    assert load_token(tmp_path / "no-existe.json") is None
