"""Konversi struktur data internal (berisi datetime) menjadi JSON-safe."""
from datetime import datetime


def jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
