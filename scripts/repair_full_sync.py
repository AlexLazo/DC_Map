"""Reparación puntual (2026-09-16): el Excel fue reorganizado varias veces el
mismo día mientras el auto-sync de 20s seguia corriendo, y como la identidad
de un Spot es la coordenada de celda de "PLACA" (no algo estable como la
placa del camion), cada reorganizacion crea Spots+Trucks nuevos en vez de
reposicionar los existentes -- dejando duplicados de la misma placa en
distintas columnas y las etiquetas de zona (MapLabel) completamente atrasadas
(nunca se tocan desde sync_truck_data). Este script hace UNA lectura fresca
del Excel (con el servidor detenido, para que nada mas lo modifique a medio
camino) y dejar la base de datos consistente con ESA lectura:

- Cada Spot cuyo `code` SI aparece en la lectura actual: se actualiza dato y
  posicion (grid_row/col/span) a lo que dice el Excel ahora.
- Cada Spot cuyo `code` YA NO aparece en la lectura actual (duplicado viejo
  de una reorganizacion anterior, o algo realmente borrado del Excel): se
  elimina junto con su Truck y su historial de DailyStatus. Los Spots
  creados a mano en el editor (`code` empieza con "NEW-") nunca se tocan.
- Las 15 etiquetas de zona viejas se reemplazan por las que salgan de esta
  lectura fresca (ya no van a quedar huerfanas / desalineadas de los Spots).

Es exactamente la misma logica que scripts/import_layout.py, reescrita aqui
para poder correrla con salida detallada de verificacion.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl

from app import models
from app.database import SessionLocal
from app.excel_sync import (
    EXCEL_PATH,
    SHEET,
    extract_bare_code_spots,
    extract_labels,
    extract_legend,
    extract_placa_blocks,
    extract_relevo_spots,
)

wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
ws = wb[SHEET]
legend = extract_legend(ws)
placa_spots, covered = extract_placa_blocks(ws, legend)
bare_spots = extract_bare_code_spots(ws, covered)
relevo_spots = extract_relevo_spots(ws, covered)
all_spots = placa_spots + bare_spots + relevo_spots
labels = extract_labels(ws, covered)
current_codes = {s["code"] for s in all_spots}

print(f"Lectura fresca del Excel: {len(all_spots)} Spots, {len(labels)} etiquetas de zona.")

db = SessionLocal()

created = updated = 0
for s in all_spots:
    truck = None
    if s["hod_code"] or s["placa"] or s["sv_code"]:
        truck = db.query(models.Truck).filter_by(source_cell=s["code"]).first()
        if not truck:
            truck = models.Truck(source_cell=s["code"])
            db.add(truck)
        truck.hod_code = s["hod_code"]
        truck.placa = s["placa"]
        truck.sv_code = s["sv_code"]
        db.flush()

    existing = db.query(models.Spot).filter_by(code=s["code"]).first()
    if not existing:
        existing = models.Spot(code=s["code"])
        db.add(existing)
        created += 1
    else:
        updated += 1
    existing.supervisor_name = s["supervisor_name"]
    existing.color_hex = s["color_hex"]
    existing.grid_row = s["grid_row"]
    existing.grid_col = s["grid_col"]
    existing.row_span = s["row_span"]
    existing.col_span = s["col_span"]
    existing.truck = truck

stale_spots = db.query(models.Spot).filter(
    ~models.Spot.code.in_(current_codes), ~models.Spot.code.startswith("NEW-")
).all()
stale_ids = [sp.id for sp in stale_spots]
if stale_ids:
    db.query(models.DailyStatus).filter(models.DailyStatus.spot_id.in_(stale_ids)).delete(synchronize_session=False)
for sp in stale_spots:
    db.delete(sp)
db.flush()

stale_trucks = db.query(models.Truck).filter(
    models.Truck.source_cell.isnot(None), ~models.Truck.source_cell.in_(current_codes)
).all()
for t in stale_trucks:
    db.delete(t)

old_label_count = db.query(models.MapLabel).count()
db.query(models.MapLabel).delete()
for l in labels:
    db.add(models.MapLabel(**l))

db.commit()

print(f"Spots: {created} creados, {updated} actualizados/reposicionados, {len(stale_spots)} eliminados (obsoletos)")
print(f"Trucks huerfanos eliminados: {len(stale_trucks)}")
print(f"Etiquetas de zona: {old_label_count} viejas reemplazadas por {len(labels)} nuevas")
print(f"Total Spots en DB ahora: {db.query(models.Spot).count()}")

new_prefixed = db.query(models.Spot).filter(models.Spot.code.startswith("NEW-")).count()
print(f"Spots NEW- (creados a mano, deben seguir intactos): {new_prefixed}")

db.close()
