"""Migración one-off: normaliza los `Truck.sv_code` que ya existen en la DB
a 4 dígitos con cero(s) a la izquierda (misma regla que `app/truck_format.py`,
usada desde ahora en adelante por la sincronización de Excel y la edición
manual de camión). Segura de volver a correr: un código ya normalizado no
cambia.

Uso:
    python scripts/reformat_sv_codes.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.truck_format import format_sv_code  # noqa: E402

db = SessionLocal()
changed = 0
for t in db.query(models.Truck).all():
    new_code = format_sv_code(t.sv_code)
    if new_code != t.sv_code:
        print(f"  {t.sv_code!r} -> {new_code!r} (placa {t.placa})")
        t.sv_code = new_code
        changed += 1
db.commit()
print(f"Listo: {changed} codigo(s) SV reformateado(s).")
db.close()
