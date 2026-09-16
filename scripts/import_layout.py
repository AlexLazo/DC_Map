"""Importa el layout de parqueo desde la hoja 'PARQUEOS ' del Excel (el layout
real que usa el CD) hacia la base de datos de la app: cada Spot (un bloque de
celdas = un camion) con su color de supervisor/canal y su HOD/Placa/SV, y las
etiquetas de zona (BODEGA, OFICINAS, etc.).

A diferencia de `app/excel_sync.py` (que solo actualiza datos de camión y
nunca toca posiciones), este script SÍ recoloca cada Spot/etiqueta a la
posición que dice el Excel y elimina los que ya no existan -- pensado para la
importación inicial o para "resetear" el layout completo. Si ya usaste el
editor visual para mover cosas a mano, correr esto las regresa a la posición
original del Excel.

Es seguro volver a correrlo: los Spots se actualizan por su `code` (coordenada
de celda de Excel) y los Trucks por su `source_cell`.

Uso:
    python scripts/import_layout.py
"""

import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.excel_sync import EXCEL_PATH, SHEET, extract_labels, extract_placa_blocks  # noqa: E402
from app.excel_sync import extract_bare_code_spots, extract_legend, extract_relevo_spots  # noqa: E402


def main():
    if not EXCEL_PATH.exists():
        print(f"No se encontro el Excel en: {EXCEL_PATH}")
        sys.exit(1)

    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
    ws = wb[SHEET]

    legend = extract_legend(ws)
    placa_spots, covered = extract_placa_blocks(ws, legend)
    bare_spots = extract_bare_code_spots(ws, covered)
    relevo_spots = extract_relevo_spots(ws, covered)
    all_spots = placa_spots + bare_spots + relevo_spots
    labels = extract_labels(ws, covered)

    Base.metadata.create_all(engine)
    db = SessionLocal()
    created_spots = updated_spots = 0
    try:
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
                created_spots += 1
            else:
                updated_spots += 1
            existing.supervisor_name = s["supervisor_name"]
            existing.color_hex = s["color_hex"]
            existing.grid_row = s["grid_row"]
            existing.grid_col = s["grid_col"]
            existing.row_span = s["row_span"]
            existing.col_span = s["col_span"]
            existing.truck = truck

        # Sincroniza: si el Excel ya no tiene una celda que antes sí tenía un
        # Spot (p. ej. el usuario reorganizó/limpió la hoja), se elimina el
        # Spot y su Truck asociado en vez de dejarlos huérfanos con la
        # posición vieja. Los Spots creados a mano en el editor (code
        # "NEW-xxxxxxxx") nunca aparecen en `current_codes` -- para no
        # borrarlos, se excluyen explícitamente de esta limpieza.
        current_codes = {s["code"] for s in all_spots}
        stale_spots = db.query(models.Spot).filter(
            ~models.Spot.code.in_(current_codes), ~models.Spot.code.startswith("NEW-")
        ).all()
        stale_spot_ids = [sp.id for sp in stale_spots]
        removed_spots = len(stale_spots)
        if stale_spot_ids:
            db.query(models.DailyStatus).filter(models.DailyStatus.spot_id.in_(stale_spot_ids)).delete(synchronize_session=False)
        for sp in stale_spots:
            db.delete(sp)
        db.flush()
        stale_trucks = db.query(models.Truck).filter(
            models.Truck.source_cell.isnot(None), ~models.Truck.source_cell.in_(current_codes)
        ).all()
        for t in stale_trucks:
            db.delete(t)

        db.query(models.MapLabel).delete()
        for l in labels:
            db.add(models.MapLabel(**l))

        db.commit()
    finally:
        db.close()

    print(f"Spots: {created_spots} creados, {updated_spots} actualizados, {removed_spots} eliminados (obsoletos) (total: {len(all_spots)})")
    print(f"  - bloques completos (Placa+SV, con o sin HOD): {len(placa_spots)}")
    print(f"  - codigos sueltos sin bloque Placa/SV: {len(bare_spots)}")
    sin_color = sum(1 for s in all_spots if not s["color_hex"])
    print(f"  - sin supervisor/color identificado: {sin_color}")
    print(f"Etiquetas de zona: {len(labels)} importadas")
    print(f"Leyenda de supervisores detectada: {legend}")


if __name__ == "__main__":
    main()
