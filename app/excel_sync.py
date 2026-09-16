"""Extrae camiones/Spots/etiquetas del Excel real ('PARQUEOS ') y los
sincroniza con la base de datos.

Este módulo separa dos cosas a propósito:
- `extract_*()` / `resolve_fill_color()` / `NAME_MAP`: la lectura del Excel,
  compartida por el script de importación inicial (`scripts/import_layout.py`,
  que sí puede recolocar todo) y por `sync_truck_data()` (que NO debe tocar
  posiciones ya ajustadas a mano en el editor de layout).
- `sync_truck_data()`: actualiza SOLO datos de camión (HOD/Placa/SV) y
  supervisor/color de los Spots que ya existen (por su `code`), y crea los
  que sean nuevos -- nunca mueve/redimensiona un Spot existente ni borra
  nada. Se usa tanto desde un endpoint manual como desde el chequeo
  automático en segundo plano (`app/main.py`).
"""

import re
from pathlib import Path

import openpyxl
from sqlalchemy.orm import Session

from app import models

EXCEL_PATH = Path(__file__).resolve().parent.parent / "LAY OUT PARQUEO (CD SOYAPANGO) V2.xlsx"
SHEET = "PARQUEOS "

# Paleta del theme de Office embebido en este Excel ("Office 2007 - 2010"):
# dk1, lt1, dk2, lt2, accent1..6, hlink, folHlink.
THEME_PALETTE = [
    "000000", "FFFFFF", "1F497D", "EEECE1", "4F81BD", "C0504D",
    "9BBB59", "8064A2", "4BACC6", "F79646", "0000FF", "800080",
]

# Nombres de supervisor que pueden aparecer como etiqueta de un bloque, y sus
# alias: Saña casi siempre aparece como el texto literal "HOD" (no su nombre)
# y Coordinador (Brian Lazo) aparece como "BRIAN LAZO" junto a "RELEVO". Esto
# ya no depende de la tabla de leyenda de colores (el usuario la borró del
# Excel al limpiarlo) -- el color de cada bloque se resuelve directo del
# relleno de la celda, nunca por nombre.
NAME_MAP = {
    "HOD": "Saña",
    "SAÑA": "Saña",
    "JUAN": "Juan",
    "BRYAN": "Bryan",
    "ELMER": "Elmer",
    "KASSANDRA": "Kassandra",
    "COORDINADOR": "Coordinador",
    "BRIAN LAZO": "Coordinador",
}
SUPERVISOR_NAMES = {"SAÑA", "JUAN", "BRYAN", "ELMER", "KASSANDRA", "COORDINADOR"}
HOD_CODE_PATTERN = re.compile(r"^DS[0-9A-Z]{2,6}$", re.IGNORECASE)
SV_TEXT_PATTERN = re.compile(r"^SV-?(\d+)$", re.IGNORECASE)

# Para las etiquetas de zona (BODEGA, OFICINAS, etc.): todo lo que no sea
# parte de un bloque de Spot y no sea ruido (LIVIANO/CAMION, la leyenda de
# supervisores, o fragmentos sueltos como codigos/placas/SV ya cubiertos).
SKIP_LABEL_WORDS = {
    "LIVIANO", "CAMION", "HOD", "PLACA", "SV", "RELEVO", "BRIAN LAZO", "C", "VO",
} | SUPERVISOR_NAMES
SV_PATTERN = re.compile(r"^SV-?\d+$", re.IGNORECASE)
PLATE_PATTERN = re.compile(r"^[A-Z]\s?\d{4,7}$")
LEGEND_MIN_COL = 128


def resolve_fill_color(cell) -> str | None:
    fill = cell.fill
    if not fill or not fill.patternType:
        return None
    fg = fill.fgColor
    try:
        if fg.type == "rgb" and isinstance(fg.rgb, str):
            return "#" + fg.rgb[-6:]
    except Exception:
        pass
    if fg.type == "theme":
        try:
            base = THEME_PALETTE[fg.theme]
        except (IndexError, TypeError):
            return None
        tint = fg.tint or 0
        r, g, b = int(base[0:2], 16), int(base[2:4], 16), int(base[4:6], 16)

        def adjust(c: int) -> int:
            return round(c + (255 - c) * tint) if tint > 0 else round(c * (1 + tint))

        return "#%02X%02X%02X" % (adjust(r), adjust(g), adjust(b))
    return None


def extract_legend(ws) -> dict[str, str]:
    """Lee la tabla de leyenda (columnas EA/EE/EN) -> {NOMBRE_CORTO: '#RRGGBB'}."""
    legend = {}
    for row in range(6, 30):
        name_cell = ws.cell(row=row, column=131)  # EA
        name = name_cell.value
        if isinstance(name, str) and name.strip() and name.strip().upper() in SUPERVISOR_NAMES:
            color = resolve_fill_color(name_cell)
            if color:
                legend[name.strip().upper()] = color
    return legend


def _match_legend_color(color: str | None, legend: dict[str, str]) -> str | None:
    if not color:
        return None
    for name, legend_color in legend.items():
        if legend_color == color:
            return name.title()
    return None


def extract_placa_blocks(ws, legend: dict[str, str]):
    """Cada bloque real: fila con 'PLACA' + su valor, fila siguiente 'SV' + su
    valor, y la fila ANTERIOR que trae el nombre del supervisor junto al
    codigo HOD (a veces el texto literal 'HOD' en vez del nombre) -- las tres
    filas son UN solo Spot."""
    spots = []
    covered = set()
    for row in ws.iter_rows():
        for cell in row:
            if not (isinstance(cell.value, str) and cell.value.strip().upper() == "PLACA"):
                continue
            r, c = cell.row, cell.column
            placa_val = ws.cell(row=r, column=c + 2).value
            if placa_val in (None, ""):
                continue

            sv_code = None
            sv_label = ws.cell(row=r + 1, column=c).value
            if isinstance(sv_label, str) and sv_label.strip().upper() == "SV":
                sv_val = ws.cell(row=r + 1, column=c + 2).value
                sv_code = str(sv_val).strip() if sv_val not in (None, "") else None

            hod_code = None
            supervisor_from_label = None
            has_row_above = False
            above_label = ws.cell(row=r - 1, column=c).value
            if isinstance(above_label, str):
                above_upper = above_label.strip().upper()
                if above_upper in NAME_MAP:
                    has_row_above = True
                    supervisor_from_label = NAME_MAP[above_upper]
                    hod_val = ws.cell(row=r - 1, column=c + 2).value
                    hod_code = str(hod_val).strip() if hod_val not in (None, "") else None

            r0 = r - 1 if has_row_above else r
            r1 = r + 1
            c0, c1 = c, c + 3
            for rr in range(r0, r1 + 1):
                for cc in range(c0, c1 + 1):
                    covered.add((rr, cc))

            color = resolve_fill_color(cell)
            supervisor = supervisor_from_label or _match_legend_color(color, legend)

            spots.append(dict(
                code=cell.coordinate,
                supervisor_name=supervisor,
                color_hex=color,
                grid_row=r0, grid_col=c0,
                row_span=r1 - r0 + 1, col_span=c1 - c0 + 1,
                hod_code=hod_code, placa=str(placa_val).strip(), sv_code=sv_code,
            ))
    return spots, covered


def extract_bare_code_spots(ws, covered: set):
    """Codigos HOD sueltos que no quedaron cubiertos por ningun bloque de
    PLACA (el cluster de la fila ~136 con geometria distinta: codigo y 'SV-###'
    lado a lado, la placa 4 filas abajo, el nombre 5 filas abajo)."""
    spots = []
    visited = set()
    for row in ws.iter_rows():
        for cell in row:
            r, c = cell.row, cell.column
            if (r, c) in covered or (r, c) in visited:
                continue
            value = cell.value
            if not (isinstance(value, str) and HOD_CODE_PATTERN.match(value.strip())):
                continue

            supervisor = None
            name_col = None
            for offset in (2, 1, 3, 4):
                name_cell = ws.cell(row=r, column=c - offset)
                if isinstance(name_cell.value, str) and name_cell.value.strip().upper() in SUPERVISOR_NAMES:
                    supervisor = name_cell.value.strip().upper()
                    name_col = c - offset
                    break
            if supervisor is None:
                for offset in range(1, 7):
                    name_cell = ws.cell(row=r + offset, column=c)
                    if isinstance(name_cell.value, str) and name_cell.value.strip().upper() in SUPERVISOR_NAMES:
                        supervisor = name_cell.value.strip().upper()
                        break

            sv_code = None
            placa_val = None
            sv_cell = ws.cell(row=r, column=c + 1).value
            m = SV_TEXT_PATTERN.match(sv_cell.strip()) if isinstance(sv_cell, str) else None
            if m:
                sv_code = m.group(1)
                placa_cell = ws.cell(row=r + 4, column=c + 1).value
                placa_val = str(placa_cell).strip() if placa_cell not in (None, "") else None

            c0 = name_col if name_col is not None else c
            c1 = c + 1
            for cc in range(c0, c1 + 1):
                visited.add((r, cc))
            covered.add((r, c))

            spots.append(dict(
                code=cell.coordinate,
                supervisor_name=NAME_MAP.get(supervisor) if supervisor else None,
                color_hex=resolve_fill_color(cell),
                grid_row=r, grid_col=c0,
                row_span=1, col_span=c1 - c0 + 1,
                hod_code=value.strip(), placa=placa_val, sv_code=sv_code,
            ))
    return spots


def extract_relevo_spots(ws, covered: set):
    """Camiones de relevo de Coordinador: una celda 'SV-####' con 'RELEVO' a
    la derecha, sin codigo HOD fijo (son un cluster chico e irregular, no
    forman un bloque de 3 filas como los demas)."""
    spots = []
    for row in ws.iter_rows():
        for cell in row:
            r, c = cell.row, cell.column
            if (r, c) in covered:
                continue
            m = SV_TEXT_PATTERN.match(cell.value.strip()) if isinstance(cell.value, str) else None
            if not m:
                continue
            right = ws.cell(row=r, column=c + 1).value
            if not (isinstance(right, str) and right.strip().upper() == "RELEVO"):
                continue
            covered.add((r, c))
            covered.add((r, c + 1))
            spots.append(dict(
                code=cell.coordinate,
                supervisor_name="Coordinador",
                color_hex=resolve_fill_color(cell),
                grid_row=r, grid_col=c,
                row_span=1, col_span=2,
                hod_code=None, placa=None, sv_code=m.group(1),
            ))
    return spots


def extract_labels(ws, covered: set):
    """Etiquetas de zona (BODEGA, TALLER, OFICINAS...) que dan contexto visual
    al layout real, excluyendo LIVIANO/CAMION, la leyenda, y cualquier celda
    ya usada por un Spot."""
    labels = []
    seen_merge_cells = set()
    for mr in ws.merged_cells.ranges:
        for rr in range(mr.min_row, mr.max_row + 1):
            for cc in range(mr.min_col, mr.max_col + 1):
                seen_merge_cells.add((rr, cc))
        anchor = ws.cell(row=mr.min_row, column=mr.min_col)
        _maybe_add_label(anchor, mr.min_row, mr.min_col, mr.max_row, mr.max_col, covered, labels)

    for row in ws.iter_rows():
        for cell in row:
            if (cell.row, cell.column) in seen_merge_cells:
                continue
            _maybe_add_label(cell, cell.row, cell.column, cell.row, cell.column, covered, labels)
    return labels


def _maybe_add_label(cell, r0, c0, r1, c1, covered, labels):
    if c0 >= LEGEND_MIN_COL:
        return
    if any((rr, cc) in covered for rr in range(r0, r1 + 1) for cc in range(c0, c1 + 1)):
        return
    value = cell.value
    if not (isinstance(value, str) and value.strip()):
        return
    text = value.strip()
    if (
        text.upper() in SKIP_LABEL_WORDS
        or SV_PATTERN.match(text)
        or PLATE_PATTERN.match(text)
        or HOD_CODE_PATTERN.match(text)
        or len(text) <= 2
    ):
        return
    labels.append(dict(text=text, grid_row=r0, grid_col=c0, row_span=r1 - r0 + 1, col_span=c1 - c0 + 1))


def extract_all_spots(ws) -> list[dict]:
    legend = extract_legend(ws)
    placa_spots, covered = extract_placa_blocks(ws, legend)
    bare_spots = extract_bare_code_spots(ws, covered)
    relevo_spots = extract_relevo_spots(ws, covered)
    return placa_spots + bare_spots + relevo_spots


def sync_truck_data(db: Session) -> dict:
    """Sincronización SEGURA para correr sola/automática: actualiza HOD,
    Placa, SV y supervisor/color de los Spots que ya existen, y crea los que
    aparezcan nuevos en el Excel.

    Identidad de un Spot: primero se busca por `code` (la celda de Excel
    exacta). Si esa celda es nueva pero el camión (misma `placa`, o mismo
    `hod_code` cuando no hay placa) ya tiene un Spot en otra celda, se
    entiende que el bloque simplemente se MOVIÓ en el Excel -- se reposiciona
    ESE Spot a la celda nueva en vez de crear un duplicado. Antes de esto (ver
    Round 14 en la memoria del proyecto) cada reorganización del Excel creaba
    un Spot fantasma por cada bloque movido, porque la única identidad era la
    celda. Con Coordinador/RELEVO sin placa ni HOD real (el cluster suelto de
    `extract_relevo_spots`) no hay forma confiable de reconocer el mismo
    camión movido -- ese caso sigue creando un Spot nuevo, como antes.

    A propósito NUNCA:
    - borra un Spot, aunque su celda ya no exista en el Excel y no se
      encuentre en ningún otro lado por placa/HOD -- una baja real hay que
      hacerla a mano (o con el importador completo), no de forma automática
      y desatendida.
    - toca las etiquetas de zona -- no tienen una llave estable para
      emparejarlas de forma confiable (no tienen `code`); usa el importador
      completo (`scripts/import_layout.py`) si las etiquetas quedaron
      desalineadas de los Spots tras una reorganización grande.
    """
    if not EXCEL_PATH.exists():
        return {"error": f"No se encontró el Excel en: {EXCEL_PATH}"}

    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
    ws = wb[SHEET]
    all_spots = extract_all_spots(ws)

    created = updated = unchanged = moved = 0
    claimed_ids: set[int] = set()
    for s in all_spots:
        existing = db.query(models.Spot).filter_by(code=s["code"]).first()

        is_move = False
        if not existing:
            # Un mismo camion (misma placa) puede aparecer legitimamente dos
            # veces en el Excel (p. ej. un relevo asignado tambien a un
            # supervisor fijo) -- una vez que un Spot de este run ya "resolvio"
            # el movimiento de una placa, no se vuelve a usar como destino
            # para otra celda de la misma placa en el mismo run.
            if s["placa"]:
                existing = (
                    db.query(models.Spot)
                    .join(models.Truck)
                    .filter(models.Truck.placa == s["placa"], ~models.Spot.id.in_(claimed_ids))
                    .first()
                )
            if not existing and s["hod_code"] and HOD_CODE_PATTERN.match(s["hod_code"]):
                existing = (
                    db.query(models.Spot)
                    .join(models.Truck)
                    .filter(models.Truck.hod_code == s["hod_code"], ~models.Spot.id.in_(claimed_ids))
                    .first()
                )
            if existing:
                is_move = True
                moved += 1
                claimed_ids.add(existing.id)
                existing.code = s["code"]
                existing.grid_row = s["grid_row"]
                existing.grid_col = s["grid_col"]
                existing.row_span = s["row_span"]
                existing.col_span = s["col_span"]

        truck = None
        if s["hod_code"] or s["placa"] or s["sv_code"]:
            truck = existing.truck if existing else None
            if not truck:
                truck = db.query(models.Truck).filter_by(source_cell=s["code"]).first()
            if not truck:
                truck = models.Truck(source_cell=s["code"])
                db.add(truck)
            truck.source_cell = s["code"]
            truck.hod_code = s["hod_code"]
            truck.placa = s["placa"]
            truck.sv_code = s["sv_code"]
            db.flush()

        if not existing:
            spot = models.Spot(
                code=s["code"],
                supervisor_name=s["supervisor_name"],
                color_hex=s["color_hex"],
                grid_row=s["grid_row"],
                grid_col=s["grid_col"],
                row_span=s["row_span"],
                col_span=s["col_span"],
                truck=truck,
            )
            db.add(spot)
            created += 1
        else:
            changed = (
                is_move
                or existing.supervisor_name != s["supervisor_name"]
                or existing.color_hex != s["color_hex"]
                or existing.truck_id != (truck.id if truck else None)
                or (truck and (truck.hod_code, truck.placa, truck.sv_code) != (s["hod_code"], s["placa"], s["sv_code"]))
            )
            existing.code = s["code"]
            existing.supervisor_name = s["supervisor_name"]
            existing.color_hex = s["color_hex"]
            existing.truck = truck
            if changed:
                updated += 1
            else:
                unchanged += 1

    db.commit()
    return {"created": created, "updated": updated, "moved": moved, "unchanged": unchanged, "total": len(all_spots)}
