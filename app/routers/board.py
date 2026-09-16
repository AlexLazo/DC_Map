import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.activity_log import log_activity
from app.asset_version import asset_version
from app.auth import get_current_user, require_login, require_role
from app.database import get_db
from app.excel_sync import sync_truck_data
from app.truck_resolution import resolve_truck
from app.ws_manager import manager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

VALID_STATUSES = {"pendiente", "carga_en_piso", "cargado", "no_cargado"}
CONDUCTOR_PATIO_STATUSES = {"carga_en_piso", "cargado"}
SUPERVISOR_STATUSES = {"no_cargado", "pendiente"}
STATUS_LABELS_ES = {
    "pendiente": "Pendiente",
    "carga_en_piso": "Carga en Piso",
    "cargado": "Cargado",
    "no_cargado": "No Cargado",
}


@router.get("/")
async def board_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        request, "board.html", {"user": user, "today": date.today().isoformat(), "asset_version": asset_version()}
    )


# Se probó transponer fila<->columna para ver el layout horizontal (más
# ancho que alto); el usuario decidió que la orientación vertical original
# (tal cual el Excel) se entendía mejor -- se dejó esta función como el
# único lugar que definía la orientación por si se vuelve a pedir.
def _placement(row: int, col: int, row_span: int, col_span: int, row_offset: int, col_offset: int) -> dict:
    return {
        "grid_row": row - row_offset,
        "grid_col": col - col_offset,
        "row_span": row_span,
        "col_span": col_span,
    }


def _compute_offsets(db: Session) -> tuple[int, int]:
    """Mismo cálculo de margen que usa /api/board -- se reutiliza para poder
    crear un Spot nuevo dándole la posición tal como se VE en pantalla (ya
    recortado el margen), sin que el admin necesite saber la coordenada
    cruda de Excel."""
    row_starts = [s.grid_row for s in db.query(models.Spot.grid_row)] + [l.grid_row for l in db.query(models.MapLabel.grid_row)]
    col_starts = [s.grid_col for s in db.query(models.Spot.grid_col)] + [l.grid_col for l in db.query(models.MapLabel.grid_col)]
    row_offset = (min(row_starts) - 1) if row_starts else 0
    col_offset = (min(col_starts) - 1) if col_starts else 0
    return row_offset, col_offset


def _spot_json(spot: models.Spot, status: models.DailyStatus | None, row_offset: int, col_offset: int) -> dict:
    truck = resolve_truck(spot, status)
    return {
        "id": spot.id,
        "code": spot.code,
        "supervisor_name": spot.supervisor_name,
        "color_hex": spot.color_hex,
        # `row_span`/`col_span` describen la FORMA en pantalla (después de
        # transponer) y sirven para ubicar el bloque en el grid; `lines` es
        # el dato semántico -- cuántos pares label/valor tiene este bloque
        # (nombre/HOD, PLACA, SV) -- y NUNCA cambia con la rotación. `tileRows()`
        # en board.js debe usar `lines`, no `row_span`, para decidir qué
        # mostrar dentro del bloque.
        "lines": spot.row_span,
        **_placement(spot.grid_row, spot.grid_col, spot.row_span, spot.col_span, row_offset, col_offset),
        "truck": None
        if not truck
        else {
            "id": truck.id,
            "hod_code": truck.hod_code,
            "placa": truck.placa,
            "sv_code": truck.sv_code,
        },
        "status": status.status if status else "pendiente",
        "comentario": status.comentario if status else None,
        "updated_by": status.updated_by.full_name if status and status.updated_by else None,
        "updated_at": status.updated_at.isoformat() if status else None,
    }


@router.get("/api/board")
async def board_data(request: Request, fecha: str | None = None, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, "No autenticado")

    target_date = date.fromisoformat(fecha) if fecha else date.today()

    spots = db.query(models.Spot).options(joinedload(models.Spot.truck)).all()
    labels = db.query(models.MapLabel).all()
    statuses = {
        s.spot_id: s
        for s in db.query(models.DailyStatus)
        .options(joinedload(models.DailyStatus.updated_by), joinedload(models.DailyStatus.truck))
        .filter(models.DailyStatus.fecha == target_date)
        .all()
    }
    legend = {}
    for s in spots:
        if s.supervisor_name and s.color_hex and s.supervisor_name not in legend:
            legend[s.supervisor_name] = s.color_hex

    # Clon literal del layout real: mismas filas/columnas de Excel, mismo
    # tamaño de cada bloque (row_span/col_span) -- lo único que se recorta es
    # el margen totalmente vacío alrededor de todo (Spots + etiquetas), para
    # no arrastrar cientos de filas/columnas sin nada. Ningún hueco interno
    # se toca: si en el Excel real dos bloques no son vecinos, tampoco lo son
    # aquí. (Todo esto en las coordenadas ORIGINALES del Excel; la transposición
    # a horizontal pasa al final, en `_placement`.)
    all_row_starts = [s.grid_row for s in spots] + [l.grid_row for l in labels]
    all_col_starts = [s.grid_col for s in spots] + [l.grid_col for l in labels]
    all_row_ends = [s.grid_row + s.row_span - 1 for s in spots] + [l.grid_row + l.row_span - 1 for l in labels]
    all_col_ends = [s.grid_col + s.col_span - 1 for s in spots] + [l.grid_col + l.col_span - 1 for l in labels]
    row_offset = min(all_row_starts) - 1
    col_offset = min(all_col_starts) - 1
    grid_rows = max(all_row_ends) - row_offset
    grid_cols = max(all_col_ends) - col_offset

    # Filas/columnas sin ningún Spot (un camión) SÍ existen en el Excel real
    # -- ahí suele haber una calle/división dibujada a mano que no podemos
    # reproducir como forma. En vez de dejarlas del mismo tamaño que una fila
    # con contenido (lo que se ve como un hueco enorme e inexplicado), se
    # dibujan como una línea delgada: mismo orden, ningún hueco reordenado,
    # solo no desperdician el mismo espacio que un bloque real.
    # Tanto Spots como etiquetas cuentan para fila Y columna: una etiqueta
    # (BAÑOS, OFICINAS, "zona de segurida"...) necesita su alto/ancho real
    # para que su texto se vea legible, no solo una rendija -- el usuario
    # pidió explícitamente que las etiquetas grises se vean más grandes.
    # (Esto puede volver a agrandar algún hueco vacío que comparta fila/columna
    # con una etiqueta ancha; si eso vuelve a verse mal, ver el historial de
    # HORIZONTAL_STRIP_ROWS/occupied_rows en memoria antes de tocar esto de nuevo.)
    occupied_rows = set()
    occupied_cols = set()
    for item in list(spots) + list(labels):
        r0 = item.grid_row - row_offset
        for rr in range(r0, r0 + item.row_span):
            occupied_rows.add(rr)
        c0 = item.grid_col - col_offset
        for cc in range(c0, c0 + item.col_span):
            occupied_cols.add(cc)
    row_line_sizes = [16 if rr in occupied_rows else 4 for rr in range(1, grid_rows + 1)]
    col_line_sizes = [16 if cc in occupied_cols else 4 for cc in range(1, grid_cols + 1)]

    return {
        "fecha": target_date.isoformat(),
        "is_today": target_date == date.today(),
        "spots": [_spot_json(s, statuses.get(s.id), row_offset, col_offset) for s in spots],
        "row_sizes": row_line_sizes,
        "col_sizes": col_line_sizes,
        "labels": [
            {"id": l.id, "text": l.text, **_placement(l.grid_row, l.grid_col, l.row_span, l.col_span, row_offset, col_offset)}
            for l in labels
        ],
        "legend": [{"supervisor_name": name, "color_hex": color} for name, color in sorted(legend.items())],
        "grid_rows": grid_rows,
        "grid_cols": grid_cols,
    }


@router.post("/api/spots/{spot_id}/status")
async def update_status(
    spot_id: int,
    payload: schemas.StatusUpdateIn,
    user: models.User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if payload.status not in VALID_STATUSES:
        raise HTTPException(400, "Estado inválido")
    if user.role == "conductor_patio" and payload.status not in CONDUCTOR_PATIO_STATUSES:
        raise HTTPException(403, "Conductor de Patio solo puede marcar Carga en Piso o Cargado")
    if user.role == "supervisor" and payload.status not in SUPERVISOR_STATUSES:
        raise HTTPException(403, "Supervisor solo puede marcar No Cargado o reiniciar a Pendiente")
    if payload.status == "no_cargado" and not (payload.comentario and payload.comentario.strip()):
        raise HTTPException(400, "El comentario es obligatorio para 'No Cargado'")

    spot = db.get(models.Spot, spot_id)
    if not spot:
        raise HTTPException(404, "Spot no encontrado")

    today = date.today()
    ds = db.query(models.DailyStatus).filter_by(spot_id=spot_id, fecha=today).first()
    old_status = ds.status if ds else "pendiente"
    if not ds:
        ds = models.DailyStatus(spot_id=spot_id, fecha=today)
        db.add(ds)
    ds.status = payload.status
    ds.comentario = payload.comentario.strip() if payload.comentario else None
    ds.updated_by_id = user.id
    detail = f"{spot.code}: {STATUS_LABELS_ES[old_status]} → {STATUS_LABELS_ES[payload.status]}"
    if ds.comentario:
        detail += f" ({ds.comentario})"
    log_activity(db, user, "status_change", spot_id=spot.id, detail=detail)
    db.commit()
    db.refresh(ds)

    message = {
        "type": "status_update",
        "spot_id": spot.id,
        "status": ds.status,
        "comentario": ds.comentario,
        "updated_by": user.full_name,
        "updated_at": ds.updated_at.isoformat(),
    }
    await manager.broadcast(message)
    return message


@router.post("/api/spots/{spot_id}/assign")
async def assign_truck(
    spot_id: int,
    payload: schemas.AssignTruckIn,
    user: models.User = Depends(require_role("supervisor")),
    db: Session = Depends(get_db),
):
    """Reasigna qué camión ocupa este Spot -- SOLO para hoy (ver
    app/truck_resolution.py): no toca `Spot.truck_id` (el camión oficial que
    trae el Excel), sino el `DailyStatus` de hoy. Mañana, si nadie vuelve a
    reasignar, el Spot vuelve a mostrar su camión oficial."""
    spot = db.get(models.Spot, spot_id)
    if not spot:
        raise HTTPException(404, "Spot no encontrado")

    truck_json = None
    if payload.truck_id is not None:
        truck = db.get(models.Truck, payload.truck_id)
        if not truck:
            raise HTTPException(404, "Camión no encontrado")
        truck_json = {
            "id": truck.id,
            "hod_code": truck.hod_code,
            "placa": truck.placa,
            "sv_code": truck.sv_code,
        }

    today = date.today()
    ds = db.query(models.DailyStatus).filter_by(spot_id=spot_id, fecha=today).first()
    if not ds:
        ds = models.DailyStatus(spot_id=spot_id, fecha=today)
        db.add(ds)
    ds.truck_id = payload.truck_id
    ds.truck_overridden = True

    detail = f"{spot.code}: camión asignado {truck_json['placa']} (solo hoy)" if truck_json else f"{spot.code}: camión quitado (solo hoy)"
    log_activity(db, user, "assign", spot_id=spot.id, detail=detail)
    db.commit()

    message = {"type": "assign_update", "spot_id": spot.id, "truck": truck_json}
    await manager.broadcast(message)
    return message


@router.post("/api/spots/{spot_id}/truck")
async def edit_or_create_truck(
    spot_id: int,
    payload: schemas.TruckEditIn,
    user: models.User = Depends(require_role("supervisor")),
    db: Session = Depends(get_db),
):
    """Edita los datos del camión que hoy ocupa este Spot (el reasignado hoy,
    si lo hay, si no el oficial del Excel) -- una corrección de placa/HOD/SV
    es un dato propio de ESE camión y se queda permanente donde sea que se
    use ese camión. Si el Spot no tiene ningún camión hoy, crea uno nuevo y
    lo dejar asignado SOLO por hoy (mismo criterio que /assign, ver
    app/truck_resolution.py) en vez de pegarlo para siempre al Spot."""
    spot = db.get(models.Spot, spot_id)
    if not spot:
        raise HTTPException(404, "Spot no encontrado")
    if not payload.placa.strip():
        raise HTTPException(400, "La placa es obligatoria")

    today = date.today()
    ds = db.query(models.DailyStatus).filter_by(spot_id=spot_id, fecha=today).first()
    truck = resolve_truck(spot, ds)
    is_new = truck is None
    if is_new:
        truck = models.Truck()
        db.add(truck)
    truck.hod_code = payload.hod_code.strip() if payload.hod_code else None
    truck.placa = payload.placa.strip()
    truck.sv_code = payload.sv_code.strip() if payload.sv_code else None
    db.flush()

    if is_new:
        if not ds:
            ds = models.DailyStatus(spot_id=spot_id, fecha=today)
            db.add(ds)
        ds.truck_id = truck.id
        ds.truck_overridden = True

    log_activity(db, user, "truck_edit", spot_id=spot.id, detail=f"{spot.code}: HOD {truck.hod_code or '-'} · Placa {truck.placa} · SV {truck.sv_code or '-'}")
    db.commit()

    truck_json = {"id": truck.id, "hod_code": truck.hod_code, "placa": truck.placa, "sv_code": truck.sv_code}
    message = {"type": "assign_update", "spot_id": spot.id, "truck": truck_json}
    await manager.broadcast(message)
    return message


@router.post("/api/spots/{spot_id}/adjust")
async def adjust_spot_position(
    spot_id: int,
    payload: schemas.SpotAdjustIn,
    user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Editor de layout: mueve/redimensiona un Spot en pasos relativos (no en
    coordenadas absolutas) para no depender del recorte de margen que hace
    /api/board -- un delta de +1 mueve/agranda un Spot exactamente una celda
    real de Excel, sin importar qué tan lejos esté el borde del layout."""
    spot = db.get(models.Spot, spot_id)
    if not spot:
        raise HTTPException(404, "Spot no encontrado")

    new_row_span = spot.row_span + payload.d_row_span
    new_col_span = spot.col_span + payload.d_col_span
    if new_row_span < 1 or new_col_span < 1:
        raise HTTPException(400, "El bloque no puede quedar más chico que 1x1")

    detail = f"{spot.code}: Δfila={payload.d_row} Δcol={payload.d_col} Δalto={payload.d_row_span} Δancho={payload.d_col_span}"
    log_activity(db, user, "spot_adjust", spot_id=spot.id, detail=detail)
    spot.grid_row += payload.d_row
    spot.grid_col += payload.d_col
    spot.row_span = new_row_span
    spot.col_span = new_col_span
    db.commit()

    await manager.broadcast({"type": "layout_update"})
    return {
        "id": spot.id,
        "grid_row": spot.grid_row,
        "grid_col": spot.grid_col,
        "row_span": spot.row_span,
        "col_span": spot.col_span,
    }


@router.post("/api/spots")
async def create_spot(
    payload: schemas.SpotCreateIn,
    user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Editor de layout: crea un Spot en blanco (sin camión) del tamaño
    mínimo 1x1 en la posición que se ve en pantalla -- el admin lo mueve y
    redimensiona con /adjust, y le asigna un camión desde el modal normal."""
    row_offset, col_offset = _compute_offsets(db)
    spot = models.Spot(
        code=f"NEW-{uuid.uuid4().hex[:8]}",
        grid_row=payload.grid_row + row_offset,
        grid_col=payload.grid_col + col_offset,
        row_span=1,
        col_span=1,
    )
    db.add(spot)
    db.flush()
    log_activity(db, user, "spot_create", spot_id=spot.id, detail=f"Spot nuevo creado ({spot.code})")
    db.commit()

    await manager.broadcast({"type": "layout_update"})
    return {"id": spot.id}


@router.delete("/api/spots/{spot_id}")
async def delete_spot(
    spot_id: int,
    user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    spot = db.get(models.Spot, spot_id)
    if not spot:
        raise HTTPException(404, "Spot no encontrado")
    log_activity(db, user, "spot_delete", spot_id=None, detail=f"Spot eliminado ({spot.code})")
    db.query(models.DailyStatus).filter_by(spot_id=spot_id).delete()
    db.delete(spot)
    db.commit()

    await manager.broadcast({"type": "layout_update"})
    return {"ok": True}


@router.post("/api/labels/{label_id}/adjust")
async def adjust_label_position(
    label_id: int,
    payload: schemas.SpotAdjustIn,
    user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Igual que /api/spots/{id}/adjust pero para las etiquetas de zona
    (BODEGA, OFICINAS, BAÑOS, etc.) -- mismo esquema de deltas relativos."""
    label = db.get(models.MapLabel, label_id)
    if not label:
        raise HTTPException(404, "Etiqueta no encontrada")

    new_row_span = label.row_span + payload.d_row_span
    new_col_span = label.col_span + payload.d_col_span
    if new_row_span < 1 or new_col_span < 1:
        raise HTTPException(400, "La etiqueta no puede quedar más chica que 1x1")

    log_activity(db, user, "label_adjust", detail=f'"{label.text}": Δfila={payload.d_row} Δcol={payload.d_col} Δalto={payload.d_row_span} Δancho={payload.d_col_span}')
    label.grid_row += payload.d_row
    label.grid_col += payload.d_col
    label.row_span = new_row_span
    label.col_span = new_col_span
    db.commit()

    await manager.broadcast({"type": "layout_update"})
    return {
        "id": label.id,
        "grid_row": label.grid_row,
        "grid_col": label.grid_col,
        "row_span": label.row_span,
        "col_span": label.col_span,
    }


@router.patch("/api/labels/{label_id}/text")
async def rename_label(
    label_id: int,
    payload: schemas.LabelTextIn,
    user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    label = db.get(models.MapLabel, label_id)
    if not label:
        raise HTTPException(404, "Etiqueta no encontrada")
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "El texto no puede quedar vacío")
    old_text = label.text
    label.text = text
    log_activity(db, user, "label_rename", detail=f'"{old_text}" → "{text}"')
    db.commit()

    await manager.broadcast({"type": "layout_update"})
    return {"id": label.id, "text": label.text}


@router.post("/api/labels")
async def create_label(
    payload: schemas.LabelCreateIn,
    user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "El texto no puede quedar vacío")
    row_offset, col_offset = _compute_offsets(db)
    label = models.MapLabel(
        text=text,
        grid_row=payload.grid_row + row_offset,
        grid_col=payload.grid_col + col_offset,
        row_span=1,
        col_span=2,
    )
    db.add(label)
    db.flush()
    log_activity(db, user, "label_create", detail=f'Etiqueta nueva creada ("{text}")')
    db.commit()

    await manager.broadcast({"type": "layout_update"})
    return {"id": label.id}


@router.delete("/api/labels/{label_id}")
async def delete_label(
    label_id: int,
    user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    label = db.get(models.MapLabel, label_id)
    if not label:
        raise HTTPException(404, "Etiqueta no encontrada")
    log_activity(db, user, "label_delete", detail=f'Etiqueta eliminada ("{label.text}")')
    db.delete(label)
    db.commit()

    await manager.broadcast({"type": "layout_update"})
    return {"ok": True}


@router.post("/api/sync-excel")
async def sync_excel_now(
    user: models.User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Sincronización manual inmediata (sin esperar el chequeo automático en
    segundo plano) -- misma lógica segura de `sync_truck_data`: actualiza
    HOD/Placa/SV/supervisor de Spots existentes, reposiciona un Spot si el
    mismo camión (por placa/HOD) reaparece en otra celda, y crea los que
    sean genuinamente nuevos. Nunca borra nada."""
    stats = sync_truck_data(db)
    if "error" in stats:
        raise HTTPException(400, stats["error"])
    if stats["created"] or stats["updated"] or stats["moved"]:
        await manager.broadcast({"type": "layout_update"})
    return stats


@router.get("/api/trucks")
async def list_trucks(q: str | None = None, user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    query = db.query(models.Truck).filter(models.Truck.active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(
            (models.Truck.placa.ilike(like))
            | (models.Truck.hod_code.ilike(like))
            | (models.Truck.sv_code.ilike(like))
        )
    trucks = query.order_by(models.Truck.placa).limit(50).all()
    return [
        {
            "id": t.id,
            "hod_code": t.hod_code,
            "placa": t.placa,
            "sv_code": t.sv_code,
        }
        for t in trucks
    ]
