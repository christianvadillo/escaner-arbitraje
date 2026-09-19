"""normalize.py — de un título de retailer a atributos comparables.

El foso del producto es emparejar sin identificador común. Esto hace el trabajo sucio y
determinista: normalizar texto y unidades, y extraer lo que decide si dos publicaciones son el
MISMO artículo — GTIN válido, código de modelo, capacidades, cantidad por paquete, condición,
marca y si es un accesorio ("funda para iPhone 15" no es un iPhone 15).

Todo es puro y sin red, para poder probarlo con pares etiquetados y medir precisión.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Marcas frecuentes en electrónica, hogar y juguetes en MX (ampliable por configuración).
KNOWN_BRANDS = frozenset(
    {
        "acer",
        "adidas",
        "amazon",
        "apple",
        "asus",
        "bissell",
        "black+decker",
        "blackanddecker",
        "bosch",
        "bose",
        "brother",
        "canon",
        "dell",
        "dewalt",
        "epson",
        "garmin",
        "google",
        "hamilton beach",
        "hasbro",
        "hisense",
        "honor",
        "hp",
        "huawei",
        "hyperx",
        "instant pot",
        "jbl",
        "kingston",
        "kitchenaid",
        "lego",
        "lenovo",
        "lg",
        "logitech",
        "mabe",
        "makita",
        "mattel",
        "microsoft",
        "midea",
        "motorola",
        "nike",
        "nikon",
        "nintendo",
        "ninja",
        "oppo",
        "oster",
        "panasonic",
        "philips",
        "playstation",
        "realme",
        "redragon",
        "samsung",
        "sandisk",
        "seagate",
        "sharp",
        "sony",
        "stanley",
        "t-fal",
        "tcl",
        "tp-link",
        "truper",
        "ugreen",
        "vivo",
        "western digital",
        "whirlpool",
        "xbox",
        "xiaomi",
        "zte",
    }
)

# Alias y marcas equivalentes (submarca → marca que puede aparecer en la otra publicación).
BRAND_CANON = {
    "wd": "western digital",
    "xbox": "microsoft",
    "playstation": "sony",
    "blackanddecker": "black+decker",
    "tfal": "t-fal",
    "galaxy": "samsung",
    "iphone": "apple",
    "ipad": "apple",
    "macbook": "apple",
    "redmi": "xiaomi",
    "poco": "xiaomi",
}

ACCESSORY_WORDS = (
    "funda",
    "case ",
    "carcasa",
    "mica",
    "protector",
    "cristal templado",
    "compatible con",
    "para iphone",
    "para samsung",
    "para galaxy",
    "para xiaomi",
    "repuesto",
    "refaccion",
    "correa",
    "estuche",
    "soporte para",
    "base para",
    "skin",
    "sticker",
    "calcomania",
    "adaptador para",
    "control remoto para",
    "cable para",
    "cargador para",
    "bateria para",
    "pantalla para",
    "display para",
    "tapa",
    "replacement",
)

# Palabras que separan variantes del mismo modelo base (Buds 2 vs Buds 2 Pro, S24 vs S24+).
VARIANT_WORDS = frozenset(
    {"pro", "max", "plus", "ultra", "lite", "mini", "fe", "air", "neo", "edge", "slim", "oled", "digital", "note"}
)

USED_WORDS = (
    "reacondicionado",
    "reacondicionada",
    "refurbished",
    "renewed",
    "usado",
    "usada",
    "seminuevo",
    "seminueva",
    "open box",
    "caja abierta",
    "exhibicion",
    "de exhibicion",
    "grado a",
    "grade a",
)

_UNIT_ALIASES = {
    "gb": "gb",
    "gigas": "gb",
    "tb": "tb",
    "teras": "tb",
    "mb": "mb",
    "ml": "ml",
    "mililitros": "ml",
    "l": "l",
    "lt": "l",
    "lts": "l",
    "litro": "l",
    "litros": "l",
    "g": "g",
    "gr": "g",
    "grs": "g",
    "gramos": "g",
    "kg": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "kilogramos": "kg",
    "pulgadas": "in",
    "pulg": "in",
    "in": "in",
    '"': "in",
    "”": "in",
    "''": "in",
    "w": "w",
    "watts": "w",
    "mah": "mah",
    "hz": "hz",
    "mp": "mp",
    "v": "v",
    "cm": "cm",
    "mm": "mm",
    "m": "m",
}
# unidad canónica y factor: todo se compara en la unidad base de su dimensión
_CANON = {"tb": ("gb", 1000.0), "l": ("ml", 1000.0), "kg": ("g", 1000.0), "m": ("mm", 1000.0), "cm": ("mm", 10.0)}

_NUM_UNIT = re.compile(
    r"(?<![a-z0-9.])(\d+(?:[.,]\d+)?)\s*"
    r"(gb|gigas|tb|teras|mb|ml|mililitros|lts|lt|litros|litro|l|kg|kilogramos|kilos|kilo|grs|gramos|gr|g|"
    r"pulgadas|pulg|in|\"|”|''|watts|w|mah|hz|mp|v|cm|mm|m)(?![a-z])"
)
_PACK = re.compile(
    r"(?:paquete|pack|kit|set|caja|juego)\s+(?:de\s+)?(\d{1,3})\b|"
    r"\b(\d{1,3})\s*(?:piezas|pzas|pzs|pz|unidades|uds|u|pack|pares)\b|"
    r"\bx\s?(\d{1,3})\b|\b(\d{1,3})\s?x\b(?!\s*\d)"
)
_MODEL_TOKEN = re.compile(r"\b(?=[a-z0-9./-]*\d)(?=[a-z0-9./-]*[a-z])[a-z0-9][a-z0-9./-]{3,}\b")
_DIGITS = re.compile(r"(?<!\d)(\d{8}|\d{12,14})(?!\d)")
_SHORT_CODE = re.compile(r"^(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{2,3}$")
# Tokens cortos alfanuméricos que NO identifican modelo (resolución, red, generación genérica).
_SHORT_STOP = frozenset(
    {"4k", "8k", "2k", "5g", "4g", "3g", "2g", "hd1", "a1", "3d", "2d", "1a", "2a", "3a", "usb", "x2"}
)


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    t = strip_accents(title.lower())
    t = t.replace("&", " and ")
    t = re.sub(r"(?<=[a-z0-9])\+(?=\s|$)", " plus", t)  # S24+ → s24 plus
    t = re.sub(r"[()\[\]{},;:!¡?¿*|•·_]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def gtin_is_valid(code: str) -> bool:
    """Dígito verificador GS1 (GTIN-8/12/13/14)."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def canonical_gtin(code: str) -> str:
    """GTIN a 14 dígitos para comparar UPC-12 con EAN-13 del mismo producto."""
    return code.zfill(14)


def extract_gtins(*texts: str | None) -> set[str]:
    out: set[str] = set()
    for text in texts:
        for m in _DIGITS.finditer(text or ""):
            code = m.group(1)
            if gtin_is_valid(code):
                out.add(canonical_gtin(code))
    return out


def extract_quantities(norm: str) -> dict[str, set[float]]:
    """{'gb': {128.0}, 'in': {55.0}, 'ml': {500.0}} con unidades canónicas."""
    out: dict[str, set[float]] = {}
    for num_s, unit_s in _NUM_UNIT.findall(norm):
        unit = _UNIT_ALIASES.get(unit_s, unit_s)
        value = float(num_s.replace(",", "."))
        base, factor = _CANON.get(unit, (unit, 1.0))
        out.setdefault(base, set()).add(round(value * factor, 3))
    return out


def extract_pack_qty(norm: str) -> int:
    for m in _PACK.finditer(norm):
        n = next((int(g) for g in m.groups() if g), 1)
        if 1 < n <= 200:
            return n
    return 1


def extract_model_codes(norm: str, quantities: dict[str, set[float]] | None = None) -> set[str]:
    """Tokens alfanuméricos tipo código de modelo, sin separadores (sm-a155m → sma155m).
    Excluye capacidades (128gb), años sueltos y tokens que son solo unidad+número."""
    out: set[str] = set()
    for tok in _MODEL_TOKEN.findall(norm):
        if _NUM_UNIT.fullmatch(tok) or re.fullmatch(r"\d+(?:[.,]\d+)?[a-z]{1,4}", tok):
            continue
        code = re.sub(r"[./-]", "", tok)
        if len(code) < 4 or code.isdigit():
            continue
        out.add(code)
    # Números de 5–7 dígitos sueltos (sets de LEGO, SKUs de fabricante). Los de 8 y 12–14 son
    # candidatos a GTIN y se tratan aparte.
    for num in re.findall(r"(?<![\d.,])(\d{5,7})(?![\d.,])", norm):
        out.add(num)
    return out


def extract_bare_numbers(norm: str) -> set[str]:
    """Enteros sueltos de 1–4 dígitos que no son cantidad con unidad ni paquete: la generación
    del modelo ("iPhone 15" vs "iPhone 14", "Buds 2" vs "Buds 3")."""
    text = _NUM_UNIT.sub(" ", compact_units(norm))
    text = _PACK.sub(" ", text)
    return set(re.findall(r"(?<![\w.,])(\d{1,4})(?![\w.,])", text))


def variant_words(norm: str) -> set[str]:
    return {t for t in re.split(r"[\s/-]+", norm) if t in VARIANT_WORDS}


def canonical_brand(brand: str | None) -> str | None:
    if not brand:
        return None
    b = normalize_title(brand)
    return BRAND_CANON.get(b, b) or None


def detect_brand(norm: str, explicit: str | None = None) -> str | None:
    if explicit:
        return canonical_brand(explicit)
    for brand in sorted(KNOWN_BRANDS | set(BRAND_CANON), key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])", norm):
            return canonical_brand(brand)
    return None


def extract_short_codes(norm: str) -> set[str]:
    """Modelos cortos que distinguen variantes: a15 vs a25, s24, g54, x7. Sin ellos,
    "Galaxy A15" y "Galaxy A25" parecen el mismo teléfono."""
    out = set()
    for tok in re.split(r"[\s/-]+", norm):
        if _SHORT_CODE.match(tok) and tok not in _SHORT_STOP and not _NUM_UNIT.fullmatch(tok):
            out.add(tok)
    return out


_ACCESSORY_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(w.strip()) for w in ACCESSORY_WORDS) + r")(?![a-z0-9])"
)


def is_accessory(norm: str) -> bool:
    """Por palabra completa: "mica" es accesorio, "térmica" no."""
    return bool(_ACCESSORY_RE.search(norm))


def condition_of(norm: str, explicit: str | None = None) -> str:
    if explicit:
        e = explicit.lower()
        if e in {"used", "usado", "refurbished", "reacondicionado"}:
            return "used"
        if e in {"new", "nuevo"}:
            return "new"
    return "used" if any(w in norm for w in USED_WORDS) else "new"


_UNIT_JOIN = re.compile(r"(\d)\s+(gb|tb|mb|ml|lts|lt|l|kg|g|gr|w|mah|hz|mp|in|pulgadas|pulg)\b")


def compact_units(norm: str) -> str:
    """ "128 gb" → "128gb" para que los tokens de ambos lados coincidan."""
    return _UNIT_JOIN.sub(r"\1\2", norm)


def tokens(norm: str) -> set[str]:
    return {t for t in re.split(r"[\s/-]+", compact_units(norm)) if len(t) > 1}


@dataclass(frozen=True)
class ProductFeatures:
    raw_title: str
    norm: str
    gtins: frozenset[str] = frozenset()
    brand: str | None = None
    model_codes: frozenset[str] = frozenset()
    short_codes: frozenset[str] = frozenset()
    bare_numbers: frozenset[str] = frozenset()
    variants: frozenset[str] = frozenset()
    quantities: dict[str, frozenset[float]] = field(default_factory=dict)
    pack_qty: int = 1
    accessory: bool = False
    condition: str = "new"
    token_set: frozenset[str] = frozenset()


def features(
    title: str,
    brand: str | None = None,
    gtin: str | None = None,
    model: str | None = None,
    condition: str | None = None,
    extra_text: str | None = None,
) -> ProductFeatures:
    """Atributos de un producto. `brand/gtin/model/condition` explícitos (JSON-LD del
    retailer, atributos BRAND/GTIN/MODEL de ML) pesan más que lo que se infiere del título."""
    norm = normalize_title(title)
    qty = extract_quantities(norm)
    gtins = extract_gtins(gtin, extra_text) if (gtin or extra_text) else set()
    if gtin and gtin_is_valid(gtin.strip()):
        gtins.add(canonical_gtin(gtin.strip()))
    codes = extract_model_codes(norm, qty)
    if model:
        m = re.sub(r"[\s./-]", "", strip_accents(model.lower()))
        if len(m) >= 3:
            codes.add(m)
    return ProductFeatures(
        raw_title=title,
        norm=norm,
        gtins=frozenset(gtins),
        brand=detect_brand(norm, brand),
        model_codes=frozenset(codes),
        short_codes=frozenset(extract_short_codes(norm)),
        bare_numbers=frozenset(extract_bare_numbers(norm)),
        variants=frozenset(variant_words(norm)),
        quantities={k: frozenset(v) for k, v in qty.items()},
        pack_qty=extract_pack_qty(norm),
        accessory=is_accessory(norm),
        condition=condition_of(norm, condition),
        token_set=frozenset(tokens(norm)),
    )
