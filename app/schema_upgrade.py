"""Migraciones mínimas al arrancar.

`Base.metadata.create_all()` crea tablas que faltan pero NUNCA agrega columnas a
una tabla que ya existe -- y en Railway la base ya existe. Aquí se agregan, de
forma idempotente y compatible con SQLite y Postgres, las columnas que se fueron
sumando después del primer despliegue, para no depender de correr scripts a mano.
"""

from sqlalchemy import inspect, text

from app.database import engine


def ensure_schema() -> None:
    insp = inspect(engine)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "must_change_password" not in user_cols:
        default = "FALSE" if engine.dialect.name == "postgresql" else "0"
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT {default}"))
        print("[schema] users.must_change_password agregada")
