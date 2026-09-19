"""base.py — contrato mínimo de una fuente: entregar ofertas, sin más opiniones.

Cualquier objeto con `name` y `fetch(limit)` sirve como fuente para `scanner.py`; no hay una
clase base porque no hay estado ni comportamiento compartido que valga la pena heredar (cada
fuente trae su propio protocolo de red o ninguno, como `fixtures.py`).
"""

from __future__ import annotations

from typing import Protocol

from escaner.models import Offer


class Source(Protocol):
    name: str

    def fetch(self, limit: int = 50) -> list[Offer]: ...
