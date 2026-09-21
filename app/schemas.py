from pydantic import BaseModel


class StatusUpdateIn(BaseModel):
    status: str
    comentario: str | None = None
    # Solo lo manda el envío diferido de un cambio marcado sin señal: la hora
    # (ISO-8601, UTC) en que la persona lo marcó de verdad.
    client_ts: str | None = None


class AssignTruckIn(BaseModel):
    truck_id: int | None = None


class TruckEditIn(BaseModel):
    hod_code: str | None = None
    placa: str
    sv_code: str | None = None


class SpotAdjustIn(BaseModel):
    d_row: int = 0
    d_col: int = 0
    d_row_span: int = 0
    d_col_span: int = 0


class SpotCreateIn(BaseModel):
    grid_row: int
    grid_col: int


class LabelCreateIn(BaseModel):
    text: str
    grid_row: int
    grid_col: int


class LabelTextIn(BaseModel):
    text: str
