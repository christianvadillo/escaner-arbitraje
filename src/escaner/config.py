"""config.py — configuración desde variables de entorno (prefijo `ESCANER_`).

Un solo lugar para credenciales, rutas y umbrales, para que `scanner.py`/`cli.py` no lean
`os.environ` sueltos por el código (y para que cambiar un umbral sea editar `.env`, no grepear
el repo). Pydantic aquí sí se justifica (a diferencia de `models.py`): esto cruza una frontera
de verdad no confiable — lo que sea que el operador haya puesto en su `.env`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, field_validator

from escaner.economics.taxes import PRESETS, TaxProfile
from escaner.meli.oauth import TokenSet

ENV_PREFIX = "ESCANER_"

DEFAULT_ALLOWED_HOSTS = ("bodegaaurrera.com.mx", "homedepot.com.mx", "costco.com.mx")


class Settings(BaseModel):
    model_config = {"frozen": True}

    # credenciales de app de Mercado Libre
    ml_app_id: str = ""
    ml_client_secret: str = ""
    ml_redirect_uri: str = "https://localhost/callback"
    ml_user_id: str | None = None  # opcional: si falta, se resuelve con /users/me al usar la API

    token_path: Path = Path("data/ml_token.json")
    db_path: Path = Path("data/escaner.db")

    contact: str = "contacto@ejemplo.com"
    user_agent: str = ""  # se deriva de `contact` en from_env() si no se da explícito
    min_host_interval_s: float = 10.0

    # umbrales de decisión (ESPECIFICACION §1 y §5)
    min_roi: float = 0.15
    min_profit: float = 150.0
    match_threshold: float = 0.90
    review_threshold: float = 0.60
    undercut: float = 0.02
    min_comparables: int = 3

    tax_profile: str = "plataformas"
    reputation: str = "green"
    listing_type: str = "gold_special"
    default_weight_g: int = 1000

    keepa_key: str = ""

    llm_enabled: bool = False
    llm_model: str = "claude-opus-5"
    llm_effort: str = "low"

    allowed_product_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS

    @field_validator("tax_profile")
    @classmethod
    def _tax_profile_exists(cls, v: str) -> str:
        if v not in PRESETS:
            raise ValueError(f"tax_profile desconocido: {v!r} (válidos: {sorted(PRESETS)})")
        return v

    @property
    def tax(self) -> TaxProfile:
        return PRESETS[self.tax_profile]

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = env if env is not None else os.environ
        raw: dict[str, object] = {}
        for name in cls.model_fields:
            key = ENV_PREFIX + name.upper()
            if key in e and e[key] != "":
                raw[name] = e[key]
        if isinstance(raw.get("allowed_product_hosts"), str):
            raw["allowed_product_hosts"] = tuple(
                h.strip() for h in raw["allowed_product_hosts"].split(",") if h.strip()
            )
        if not raw.get("user_agent"):
            contact = raw.get("contact", cls.model_fields["contact"].default)
            raw["user_agent"] = f"escaner-arbitraje/0.1 (+uso personal; contacto: {contact})"
        return cls(**raw)


def save_token(path: Path | str, token: TokenSet) -> None:
    """Escritura atómica con permisos 0600 (ESPECIFICACION §1): el token es una credencial de
    acceso a la cuenta de ML del operador, no debe quedar ni un instante en un archivo legible
    por otros usuarios, y una escritura a medias (proceso interrumpido) no debe dejar un JSON
    corrupto en `token_path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(token.to_dict()), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)  # rename atómico dentro del mismo filesystem


def load_token(path: Path | str) -> TokenSet | None:
    path = Path(path)
    if not path.exists():
        return None
    return TokenSet.from_dict(json.loads(path.read_text(encoding="utf-8")))
