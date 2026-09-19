"""Emparejamiento: vetos que evitan comprar el producto equivocado + banda de revisión."""

import pytest

from escaner.matching.normalize import extract_gtins, features, gtin_is_valid
from escaner.matching.score import LabeledPair, calibrate, evaluate, score_pair, threshold_for_precision

PAIRS = [
    (
        "Samsung Galaxy A15 SM-A155M 128GB 4GB RAM Negro",
        "Celular Samsung Galaxy A15 128 GB Azul Oscuro Dual SIM",
        1.3,
        True,
    ),
    ("Samsung Galaxy A15 128GB", "Samsung Galaxy A25 5G 128GB", 1.3, False),
    ("Samsung Galaxy A15 128GB", "Funda para Samsung Galaxy A15 Silicon Case", 0.1, False),
    ("Audífonos Sony WH-1000XM5 Negro", "Sony Wh-1000xm5 Audífonos Inalámbricos Cancelación De Ruido", 1.25, True),
    ("Audífonos Sony WH-1000XM5 Negro", "Sony WH-1000XM4 Audífonos Bluetooth", 1.1, False),
    ('Pantalla Hisense 55" 4K 55A6K', "Smart TV Hisense 55A6K 55 Pulgadas 4K UHD Google TV", 1.2, True),
    ('Pantalla Hisense 55" 4K 55A6K', "Smart TV Hisense 65A6K 65 Pulgadas 4K UHD", 1.6, False),
    ("Paquete de 2 Termos Stanley 1.4 L Clásico", "Termo Stanley Clásico 1.4 L Verde", 0.6, False),
    ("Termo Stanley Clasico 1.4 litros verde", "Termo Stanley Clásico 1.4 L Verde Botella", 1.2, True),
    ("Licuadora Oster BLSTPYG1210 600W 1.25 Litros", "Licuadora Oster Xpert Series 1.25l Blstpyg1210 Plata", 1.3, True),
    ("Apple iPhone 15 (128 GB) - Negro", "Apple iPhone 15 128 GB Negro Reacondicionado", 0.9, False),
    ("Consola Xbox Series X 1TB", "Microsoft Xbox Series S 512GB Consola", 0.7, False),
    ("Consola Xbox Series X 1TB", "Consola Microsoft Xbox Series X 1 TB Negro", 1.2, True),
    ("Lego Star Wars 75301 Ala-X de Luke", "LEGO Star Wars Caza Ala-X de Luke Skywalker 75301", 1.4, True),
    ("LEGO Star Wars 75301 Ala-X", "LEGO Star Wars 75302 Imperial Shuttle", 1.2, False),
    ("Samsung Galaxy Buds 2 Audífonos", "Samsung Galaxy Buds 2 Pro Audífonos", 1.3, False),
    ("Apple iPhone 15 128GB Negro", "Apple iPhone 14 128GB Negro", 0.9, False),
    ("Xiaomi Redmi 13 256GB", "Xiaomi Redmi Note 13 256GB", 1.1, False),
    ("Consola PlayStation 5 Slim", "Consola PlayStation 5 Slim Digital", 0.9, False),
    ("Samsung Galaxy S24+ 256GB", "Samsung Galaxy S24 Plus 256 GB", 1.2, True),
    ("Nintendo Switch OLED Blanco", "Consola Nintendo Switch Oled 64gb Blanca", 1.2, True),
]


@pytest.mark.parametrize(("offer", "candidate", "ratio", "same"), PAIRS)
def test_pairs(offer, candidate, ratio, same):
    s = score_pair(features(offer), features(candidate), ratio)
    if same:
        assert s.decision in {"match", "review"}, s
    else:
        assert s.decision == "no_match" and s.veto, s


def test_gtin_checksum_and_extraction():
    assert gtin_is_valid("7501055363056")
    assert not gtin_is_valid("7501055363057")
    assert extract_gtins("EAN 0194253401698 / UPC 194253401698") == {"00194253401698"}


def test_same_gtin_wins_and_different_gtin_vetoes():
    a = features("Termo 1.4 L", gtin="7501055363056")
    assert score_pair(a, features("Botella térmica", gtin="7501055363056")).decision == "match"
    assert score_pair(a, features("Termo 1.4 L", gtin="0194253401698")).veto == "GTIN distinto"


def test_calibration_and_threshold():
    pairs = [LabeledPair(features(a), features(b), y, r) for a, b, r, y in PAIRS]
    w = calibrate(pairs, epochs=300)
    rows = evaluate(pairs, w)
    assert all(r.precision == 1.0 for r in rows)  # los negativos de la muestra mueren por veto
    assert threshold_for_precision(rows, 0.97) is not None
