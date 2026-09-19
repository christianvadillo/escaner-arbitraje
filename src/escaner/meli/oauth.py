"""oauth.py — flujo OAuth de Mercado Libre: authorization code + refresh de un solo uso.

Verificado en vivo sin token (ESPECIFICACION §2): casi todos los endpoints que importan
(`search`, `products/search`, `listing_prices`, …) devuelven 403/401 sin un access_token de
usuario. El escáner solo puede operar contra ML si el operador autoriza su propia cuenta.

El detalle que rompe integraciones ingenuas: el `refresh_token` de ML es de UN SOLO USO — cada
`refresh()` devuelve un refresh_token nuevo y el anterior deja de servir. Este módulo no decide
CUÁNDO persistir (eso es de quien orquesta, en `config.save_token`), pero documenta el orden
correcto: persistir el `TokenSet` devuelto ANTES de usar el `access_token` nuevo. Si el proceso
muere entre refrescar y guardar, se pierde el acceso (hay que volver a autorizar) — la
alternativa, usar el access_token sin guardar el refresh_token nuevo primero, puede dejar
inservibles TANTO el token viejo como el nuevo sin guardar.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://auth.mercadolibre.com.mx/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

DEFAULT_EXPIRES_IN = 10800.0  # ~3h, el valor que documenta la especificación si el server no lo manda


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds
    user_id: str | None = None

    def is_expired(self, now: float | None = None, skew_s: float = 60.0) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at - skew_s

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> TokenSet:
        return cls(
            access_token=d["access_token"],
            refresh_token=d["refresh_token"],
            expires_at=float(d["expires_at"]),
            user_id=d.get("user_id"),
        )


def authorization_url(app_id: str, redirect_uri: str, state: str | None = None) -> str:
    """URL a la que el operador visita en su navegador para autorizar su cuenta ML."""
    params = {"response_type": "code", "client_id": app_id, "redirect_uri": redirect_uri}
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_set_from_response(payload: dict, clock: Callable[[], float]) -> TokenSet:
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_at=clock() + float(payload.get("expires_in", DEFAULT_EXPIRES_IN)),
        user_id=(str(payload["user_id"]) if payload.get("user_id") is not None else None),
    )


def exchange_code(
    http: httpx.Client,
    app_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    clock: Callable[[], float] = time.time,
) -> TokenSet:
    """Primer canje: el `code` de la redirección del navegador por un `TokenSet`."""
    resp = http.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": app_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    resp.raise_for_status()
    return _token_set_from_response(resp.json(), clock)


def refresh(
    http: httpx.Client,
    app_id: str,
    client_secret: str,
    refresh_token: str,
    clock: Callable[[], float] = time.time,
) -> TokenSet:
    """Renueva el access_token. El `refresh_token` recibido aquí queda inválido al terminar
    esta llamada: el `TokenSet` devuelto (con su refresh_token NUEVO) debe persistirse antes de
    que el llamador haga cualquier otra cosa con el access_token."""
    resp = http.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": app_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
    )
    resp.raise_for_status()
    return _token_set_from_response(resp.json(), clock)
