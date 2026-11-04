"""Master harga (DUMMY). Harga selalu dihitung di server dari master ini, tidak pernah
dipercaya dari input klien (checklist standar bagian 10: "Master harga").

Nanti diganti dengan sumber harga resmi (database / persetujuan Engineering).
"""
from typing import Dict, List

MASTER_PRICE = {
    "KBL-PV-004": {"description": "Kabel PV 4mm2", "unit": "meter", "unit_price": 15000},
    "MCB-AC-002": {"description": "MCB tambahan", "unit": "pcs", "unit_price": 125000},
    "JASA-001": {"description": "Instalasi khusus", "unit": "paket", "unit_price": 225000},
    "PLTS_01": {"description": "Panel Surya Extra", "unit": "unit", "unit_price": 5000000},
    "INV_01": {"description": "Inverter Upgrade", "unit": "unit", "unit_price": 3000000},
}

MAX_QTY = 10000


def build_items(selection: Dict[str, int]) -> List[dict]:
    """selection = {part_code: qty}. qty 0 diabaikan. Melempar ValueError jika tidak valid."""
    items = []
    for part_code, qty in (selection or {}).items():
        if isinstance(qty, bool) or not isinstance(qty, int):
            raise ValueError(f"Jumlah untuk {part_code} harus bilangan bulat")
        if qty < 0 or qty > MAX_QTY:
            raise ValueError(f"Jumlah untuk {part_code} di luar batas (0-{MAX_QTY})")
        if qty == 0:
            continue
        master = MASTER_PRICE.get(part_code)
        if master is None:
            raise ValueError(f"Kode material tidak dikenal: {part_code}")
        items.append({
            "part_code": part_code,
            "description": master["description"],
            "qty": qty,
            "unit": master["unit"],
            "unit_price": master["unit_price"],
            "subtotal": qty * master["unit_price"],
        })
    return items
