"""Migración one-off: agrega `truck_id`/`truck_overridden` a `daily_status`
para que "Asignar/cambiar camión" sea solo para el día (ver
app/truck_resolution.py) en vez de permanente. `Base.metadata.create_all()`
no agrega columnas a una tabla que ya existe, así que hace falta este ALTER
manual una sola vez.

Seguro de volver a correr: si las columnas ya existen, no hace nada.

Uso:
    python scripts/migrate_daily_truck_override.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402

with engine.connect() as conn:
    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(daily_status)"))}
    added = []
    if "truck_id" not in existing:
        conn.execute(text("ALTER TABLE daily_status ADD COLUMN truck_id INTEGER REFERENCES trucks(id)"))
        added.append("truck_id")
    if "truck_overridden" not in existing:
        conn.execute(text("ALTER TABLE daily_status ADD COLUMN truck_overridden BOOLEAN NOT NULL DEFAULT 0"))
        added.append("truck_overridden")
    conn.commit()

print(f"Columnas agregadas: {added}" if added else "Ya estaba migrado, no se hizo nada.")
