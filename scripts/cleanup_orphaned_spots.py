"""Limpieza puntual (2026-09-16): borra Spots (y su Truck asociado) cuyo
`code` (coordenada de celda del Excel, ej. 'AF13') ya no existe en el Excel
actual -- huerfanos dejados por `sync_truck_data()` cuando un bloque se
mueve de celda en el Excel (nunca actualiza/mueve un Spot existente a
proposito, asi que el movimiento crea un Spot nuevo en la celda nueva y deja
el viejo como fantasma). Nunca toca Spots con code que empiece con 'NEW-'
(creados a mano en el editor de layout)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl

from app.database import SessionLocal
from app import models
from app.excel_sync import extract_all_spots, SHEET, EXCEL_PATH

wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
ws = wb[SHEET]
excel_codes = {s["code"] for s in extract_all_spots(ws)}

db = SessionLocal()
orphaned = [
    sp for sp in db.query(models.Spot).all()
    if sp.code not in excel_codes and not sp.code.startswith("NEW-")
]

print(f"Borrando {len(orphaned)} Spot(s) huerfano(s)...")
deleted_trucks = 0
deleted_statuses = 0
for sp in orphaned:
    truck = sp.truck
    for st in list(sp.statuses):
        db.delete(st)
        deleted_statuses += 1
    db.delete(sp)
    if truck:
        db.delete(truck)
        deleted_trucks += 1

db.commit()
print(f"Listo: {len(orphaned)} Spot(s), {deleted_trucks} Truck(s) y {deleted_statuses} DailyStatus(es) eliminados.")
print(f"Spots restantes en DB: {db.query(models.Spot).count()}")
db.close()
