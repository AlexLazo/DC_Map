"""Migración one-off: los roles viejos (admin/distribucion/almacen) pasan a
los 4 roles nuevos que reflejan los puestos reales del CD.

- distribucion   -> supervisor
- almacen        -> conductor_patio
- admin          -> super_admin  (la cuenta admin existente no debe perder
                                    ninguna capacidad; es la única cuenta
                                    admin de hoy, así que se promueve entera)

Seguro de volver a correr: si ya no quedan usuarios con los roles viejos, no
hace nada.

Uso:
    python scripts/migrate_roles.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402

MIGRATION = {
    "distribucion": "supervisor",
    "almacen": "conductor_patio",
    "admin": "super_admin",
}


def main():
    db = SessionLocal()
    try:
        total = 0
        for old_role, new_role in MIGRATION.items():
            users = db.query(models.User).filter_by(role=old_role).all()
            for u in users:
                print(f"  {u.username}: {old_role} -> {new_role}")
                u.role = new_role
                total += 1
        db.commit()
        print(f"Migrados: {total} usuario(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
