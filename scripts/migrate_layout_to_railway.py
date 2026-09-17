"""Copia el layout REAL (Trucks, Spots, Etiquetas) de tu SQLite local hacia
Railway -- a diferencia de `import_layout.py` (que reconstruye posiciones
solo desde el Excel), esto copia tal cual las posiciones que ya tienes en tu
base local, incluyendo cualquier ajuste hecho a mano en el editor de layout
(esos ajustes NUNCA se escriben de vuelta al Excel, así que `import_layout.py`
no puede reproducirlos -- por eso Railway se veía distinto).

NO toca Usuarios/DailyStatus/Bitácora en Railway -- si ya creaste cuentas
reales ahí, se quedan igual.

Uso (con Railway CLI ya instalado y logueado -- `railway login`,
`railway link` una vez en esta carpeta):

    railway run python scripts/migrate_layout_to_railway.py

`railway run` inyecta el DATABASE_URL real de Railway (túnel seguro, sin
exponer la base de datos públicamente) mientras el script sigue corriendo en
tu máquina, con acceso directo a tu dc_map.db local.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base

LOCAL_URL = "sqlite:///./dc_map.db"
TABLES_IN_ORDER = [models.Truck, models.Spot, models.MapLabel]

target_url = os.environ.get("DATABASE_URL")
if not target_url or target_url.startswith("sqlite"):
    print("DATABASE_URL no apunta a Postgres de Railway -- ¿corriste esto con `railway run`?")
    sys.exit(1)

print(f"Origen (local): {LOCAL_URL}")
print(f"Destino (Railway): {target_url.split('@')[-1]}")  # no imprime la contraseña

src_engine = create_engine(LOCAL_URL)
dst_engine = create_engine(target_url)
Base.metadata.create_all(dst_engine)

Src = sessionmaker(bind=src_engine)()
counts = {}

with dst_engine.begin() as conn:
    for model in reversed(TABLES_IN_ORDER):
        conn.execute(text(f"TRUNCATE TABLE {model.__tablename__} RESTART IDENTITY CASCADE"))

    for model in TABLES_IN_ORDER:
        rows = Src.query(model).all()
        cols = model.__table__.columns.keys()
        for row in rows:
            conn.execute(model.__table__.insert().values(**{c: getattr(row, c) for c in cols}))
        counts[model.__tablename__] = len(rows)

    for model in TABLES_IN_ORDER:
        conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{model.__tablename__}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {model.__tablename__}), 1))"
        ))

print(f"Listo: {counts}")
Src.close()
