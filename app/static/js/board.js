const CURRENT_ROLE = window.APP.role;

const STATUS_COLORS = {
  pendiente: '#9ca3af',
  carga_en_piso: '#ea580c',
  cargado: '#15803d',
  no_cargado: '#dc2626',
};
const STATUS_LABELS = {
  pendiente: 'Pendiente',
  carga_en_piso: 'Carga en Piso',
  cargado: 'Cargado',
  no_cargado: 'No Cargado',
};
// Marca visible además del color de fondo: algunos colores de supervisor
// (verde olivo de Juan, amarillo de Kassandra) se parecen demasiado a
// "Cargado"/"Carga en Piso" y el cambio de estatus casi no se notaba.
const STATUS_SYMBOLS = { carga_en_piso: '⏳', cargado: '✓', no_cargado: '✕' };

function displayCode(spot) {
  const t = spot.truck;
  if (!t) return spot.code;
  return t.sv_code || t.hod_code || t.placa || spot.code;
}

function tileBackground(spot) {
  if (spot.status === 'pendiente') return spot.color_hex || STATUS_COLORS.pendiente;
  return STATUS_COLORS[spot.status] || STATUS_COLORS.pendiente;
}

function readableTextColor(hex) {
  if (!hex) return '#ffffff';
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.6 ? '#111827' : '#ffffff';
}

let spotsById = {};
let labelsById = {};
let currentSpotId = null;
let currentFilter = 'todos';
let searchQuery = '';
let hasScrolledToContent = false;
let editMode = false;
let selected = []; // Array<{ type: 'spot' | 'label', id: number }> -- selección múltiple
let lastRowSizes = [];
let lastColSizes = [];
let justDragged = false;
let undoStack = [];
let suppressUndo = false;

const mapEl = document.getElementById('map');
const modal = document.getElementById('modal');
const datePicker = document.getElementById('date-picker');

async function loadBoard(fecha) {
  const res = await fetch(`/api/board?fecha=${fecha}`);
  if (res.status === 401) {
    window.location.href = '/login';
    return;
  }
  const data = await res.json();
  renderBoard(data);
}

function renderBoard(data) {
  mapEl.innerHTML = '';
  spotsById = {};
  labelsById = {};
  for (const spot of data.spots) spotsById[spot.id] = spot;
  for (const label of data.labels) labelsById[label.id] = label;

  // Clon literal: misma fila/columna/tamaño de bloque que en el Excel real
  // (`PARQUEOS `), solo recortando el margen totalmente vacío. Las filas/
  // columnas sin ningún Spot ni etiqueta (p.ej. una calle dibujada a mano
  // que no podemos reproducir) se dibujan delgadas en vez de al mismo
  // tamaño que una fila con contenido -- ver `row_sizes`/`col_sizes` en
  // board.py.
  mapEl.style.gridTemplateColumns = data.col_sizes.map((px) => `${px}px`).join(' ');
  mapEl.style.gridTemplateRows = data.row_sizes.map((px) => `${px}px`).join(' ');
  lastRowSizes = data.row_sizes;
  lastColSizes = data.col_sizes;

  for (const label of data.labels) mapEl.appendChild(renderMapLabel(label));
  for (const spot of data.spots) mapEl.appendChild(renderSpotTile(spot));

  const legendEl = document.getElementById('supervisor-legend');
  legendEl.innerHTML = data.legend
    .map((l) => `<span class="legend-item"><i style="background:${l.color_hex}"></i>${l.supervisor_name}</span>`)
    .join('');

  document.getElementById('readonly-banner').style.display = data.is_today ? 'none' : 'block';
  applyFilter();
  highlightSearch();
  if (!hasScrolledToContent) {
    hasScrolledToContent = true;
    scrollToFirstContent(data);
  }
  // Re-marca visualmente lo que seguía seleccionado (algunos pueden haber
  // sido borrados por otra persona mientras tanto).
  selected = selected.filter((s) => (s.type === 'spot' ? spotsById[s.id] : labelsById[s.id]));
  for (const s of selected) {
    const el = itemElement(s);
    if (el) el.classList.add('selected');
  }
}

// El clon literal del Excel arrastra el margen vacío real de la hoja -- el
// primer Spot casi nunca está en la esquina (0,0) del grid, así que abrir el
// tablero sin esto deja al usuario viendo una pantalla en blanco hasta que
// adivina hacia dónde desplazarse. En un monitor ancho de escritorio pasa
// casi inadvertido (se alcanza a ver algo de contenido de reojo); en una
// tablet o celular la pantalla completa queda en blanco. Solo corre una vez,
// al cargar por primera vez -- no en cada actualización en vivo, para no
// arrancarle el scroll a alguien que ya está trabajando en otra parte del mapa.
function scrollToFirstContent(data) {
  if (!data.spots.length) return;
  // OJO: el más arriba y el más a la izquierda pueden ser Spots distintos --
  // tomar min(grid_row) y min(grid_col) por separado apunta a una esquina
  // que puede no tener ningún Spot real cerca (mayormente vacía otra vez).
  // Hay que usar la posición de UN solo Spot real.
  const first = data.spots.reduce((best, s) =>
    s.grid_row < best.grid_row || (s.grid_row === best.grid_row && s.grid_col < best.grid_col) ? s : best
  );
  const pxBefore = (sizes, line) => sizes.slice(0, line - 1).reduce((a, b) => a + b, 0);
  const top = pxBefore(data.row_sizes, first.grid_row) * zoom;
  const left = pxBefore(data.col_sizes, first.grid_col) * zoom;
  document.querySelector('.map-scroll').scrollTo({ left: Math.max(0, left - 20), top: Math.max(0, top - 20) });
}

function renderMapLabel(label) {
  const el = document.createElement('div');
  el.className = 'map-label';
  el.id = `label-${label.id}`;
  el.textContent = label.text;
  el.style.gridRow = `${label.grid_row} / span ${label.row_span}`;
  el.style.gridColumn = `${label.grid_col} / span ${label.col_span}`;
  el.addEventListener('mousedown', (e) => startDrag('label', label.id, el, e));
  el.addEventListener('click', (e) => {
    if (editMode && !justDragged) selectItem('label', label.id, e.shiftKey || e.ctrlKey || e.metaKey);
  });
  el.addEventListener('dblclick', () => {
    if (editMode) renameLabel(label.id);
  });
  return el;
}

function renderSpotTile(spot) {
  const el = document.createElement('div');
  el.className = 'spot-tile';
  el.id = `spot-${spot.id}`;
  el.style.gridRow = `${spot.grid_row} / span ${spot.row_span}`;
  el.style.gridColumn = `${spot.grid_col} / span ${spot.col_span}`;
  el.style.gridTemplateRows = `repeat(${spot.lines}, 1fr)`;
  applyTileStyle(el, spot);
  el.addEventListener('mousedown', (e) => startDrag('spot', spot.id, el, e));
  el.addEventListener('click', (e) => {
    if (justDragged) return;
    if (editMode) selectItem('spot', spot.id, e.shiftKey || e.ctrlKey || e.metaKey);
    else openModal(spot.id);
  });
  return el;
}

// Reproduce la tabla real de la celda de Excel: 3 filas (nombre/HOD, PLACA,
// SV) para un bloque completo, 2 filas si no hay nombre/HOD, o 1 fila
// (nombre, código) para los códigos sueltos sin Placa/SV.
function tileRows(spot) {
  const t = spot.truck;
  const name = spot.supervisor_name || 'HOD';
  if (spot.lines >= 3) {
    return [[name, (t && t.hod_code) || '-'], ['PLACA', (t && t.placa) || '-'], ['SV', (t && t.sv_code) || '-']];
  }
  if (spot.lines === 2) {
    return [['PLACA', (t && t.placa) || '-'], ['SV', (t && t.sv_code) || '-']];
  }
  // El cluster suelto de "zona de seguridad" es demasiado angosto para
  // mostrar código y SV lado a lado sin recortarse -- se prioriza el SV
  // (el dato que más se busca) en una sola celda de ancho completo; el HOD
  // completo sigue disponible en el tooltip/modal.
  if (t && t.sv_code) {
    return [[t.sv_code]];
  }
  return [[name, (t && t.hod_code) || '-']];
}

function applyTileStyle(el, spot) {
  const bg = tileBackground(spot);
  el.style.color = readableTextColor(bg);
  el.dataset.status = spot.status;
  el.title = tileTooltip(spot);
  const rowsHtml = tileRows(spot)
    .map((row) =>
      row.length === 2
        ? `<div class="cell label" style="background:${bg}">${row[0]}</div><div class="cell value" style="background:${bg}">${row[1]}</div>`
        : `<div class="cell value wide" style="background:${bg}">${row[0]}</div>`
    )
    .join('');
  const symbol = STATUS_SYMBOLS[spot.status];
  el.innerHTML = rowsHtml + (symbol ? `<span class="status-badge" style="background:${STATUS_COLORS[spot.status]}">${symbol}</span>` : '');
}

function applyFilter() {
  document.querySelectorAll('.spot-tile').forEach((el) => {
    el.style.display = currentFilter === 'todos' || el.dataset.status === currentFilter ? '' : 'none';
  });
}

document.querySelectorAll('.filter-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    currentFilter = btn.dataset.filter;
    document.querySelectorAll('.filter-btn').forEach((b) => b.classList.toggle('active', b === btn));
    applyFilter();
  });
});

// Buscador de SV/ruta/placa: resalta el o los Spots que hacen match (color
// distinto + pulso) y apaga el resto -- para no tener que ir tile por tile
// en un grid tan denso, sobre todo en tablet/celular. Compara contra todo lo
// que identifica al camión, no solo un campo, porque "ruta" en la práctica
// puede ser el SV, el supervisor/zona, o la placa según cómo lo busquen.
function matchesSearch(spot, q) {
  const t = spot.truck;
  const haystack = [spot.code, spot.supervisor_name, t && t.placa, t && t.hod_code, t && t.sv_code]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(q);
}

function highlightSearch() {
  const q = searchQuery.trim().toLowerCase();
  const clearBtn = document.getElementById('spot-search-clear');
  if (clearBtn) clearBtn.style.display = q ? '' : 'none';

  if (!q) {
    document.querySelectorAll('.search-match, .search-dimmed').forEach((el) => el.classList.remove('search-match', 'search-dimmed'));
    return null;
  }
  let firstMatchEl = null;
  for (const spot of Object.values(spotsById)) {
    const el = document.getElementById(`spot-${spot.id}`);
    if (!el) continue;
    const isMatch = matchesSearch(spot, q);
    el.classList.toggle('search-match', isMatch);
    el.classList.toggle('search-dimmed', !isMatch);
    if (isMatch && !firstMatchEl) firstMatchEl = el;
  }
  document.querySelectorAll('.map-label').forEach((el) => el.classList.add('search-dimmed'));
  return firstMatchEl;
}

const spotSearchInput = document.getElementById('spot-search');
const spotSearchClear = document.getElementById('spot-search-clear');
if (spotSearchInput) {
  spotSearchInput.addEventListener('input', () => {
    searchQuery = spotSearchInput.value;
    // Un match escondido por el filtro de estatus (p.ej. buscando un camión
    // "Pendiente" con el filtro en "Cargado") nunca se vería -- al buscar,
    // el filtro vuelve a "Todos" para que el resultado siempre aparezca.
    if (searchQuery.trim() && currentFilter !== 'todos') {
      currentFilter = 'todos';
      document.querySelectorAll('.filter-btn').forEach((b) => b.classList.toggle('active', b.dataset.filter === 'todos'));
      applyFilter();
    }
    const firstMatch = highlightSearch();
    if (firstMatch) firstMatch.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
  });
  spotSearchClear.addEventListener('click', () => {
    spotSearchInput.value = '';
    searchQuery = '';
    highlightSearch();
    spotSearchInput.focus();
  });
}

function tileTooltip(spot) {
  let t = `${displayCode(spot)} · ${STATUS_LABELS[spot.status]}`;
  if (spot.supervisor_name) t += ` · ${spot.supervisor_name}`;
  if (spot.truck) {
    t += `\nHOD: ${spot.truck.hod_code || '-'} · Placa: ${spot.truck.placa || '-'} · SV: ${spot.truck.sv_code || '-'}`;
  } else {
    t += '\nSin camión asignado';
  }
  if (spot.comentario) t += `\nComentario: ${spot.comentario}`;
  return t;
}

function updateTile(spot) {
  spotsById[spot.id] = spot;
  const el = document.getElementById(`spot-${spot.id}`);
  if (!el) return;
  applyTileStyle(el, spot);
  applyFilter();
}

function openModal(spotId) {
  currentSpotId = spotId;
  const spot = spotsById[spotId];

  document.getElementById('modal-title').textContent = `${displayCode(spot)} (${spot.code})`;
  const statusBadge = document.getElementById('modal-status');
  statusBadge.textContent = STATUS_LABELS[spot.status];
  statusBadge.style.background = STATUS_COLORS[spot.status];

  const supervisorLine = spot.supervisor_name ? ` · Supervisor: ${spot.supervisor_name}` : '';
  document.getElementById('modal-truck-info').textContent = spot.truck
    ? `HOD ${spot.truck.hod_code || '-'} · Placa ${spot.truck.placa || '-'} · SV ${spot.truck.sv_code || '-'}${supervisorLine}`
    : `Sin camión asignado${supervisorLine}`;

  document.getElementById('modal-comment').value = spot.comentario || '';
  document.getElementById('modal-updated').textContent = spot.updated_by
    ? `Últ. cambio: ${spot.updated_by} (${new Date(spot.updated_at).toLocaleString('es-SV')})`
    : '';

  document.getElementById('truck-search').value = '';
  document.getElementById('truck-results').innerHTML = '';
  document.getElementById('edit-hod').value = spot.truck ? spot.truck.hod_code || '' : '';
  document.getElementById('edit-placa').value = spot.truck ? spot.truck.placa || '' : '';
  document.getElementById('edit-sv').value = spot.truck ? spot.truck.sv_code || '' : '';

  const isToday = datePicker.value === window.APP.today;
  const isAdmin = CURRENT_ROLE === 'admin' || CURRENT_ROLE === 'super_admin';
  document.querySelectorAll('.status-btn').forEach((btn) => {
    const st = btn.dataset.status;
    const allowed =
      isToday &&
      (isAdmin ||
        (CURRENT_ROLE === 'conductor_patio' && ['carga_en_piso', 'cargado'].includes(st)) ||
        (CURRENT_ROLE === 'supervisor' && ['no_cargado', 'pendiente'].includes(st)));
    btn.style.display = allowed ? '' : 'none';
  });

  document.getElementById('assign-section').style.display =
    isToday && (CURRENT_ROLE === 'supervisor' || isAdmin) ? '' : 'none';

  modal.classList.add('open');
}

function closeModal() {
  modal.classList.remove('open');
  currentSpotId = null;
}

async function setStatus(status) {
  let comentario = null;
  if (status === 'no_cargado') {
    comentario = document.getElementById('modal-comment').value.trim();
    if (!comentario) {
      alert('El comentario es obligatorio para marcar "No Cargado".');
      return;
    }
  }
  const res = await fetch(`/api/spots/${currentSpotId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, comentario }),
  });
  if (!res.ok) {
    const err = await res.json();
    alert(err.detail || 'Error al actualizar el estado');
    return;
  }
  closeModal();
}

let searchTimeout;
document.getElementById('truck-search').addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  const q = e.target.value.trim();
  searchTimeout = setTimeout(async () => {
    const list = document.getElementById('truck-results');
    if (!q) {
      list.innerHTML = '';
      return;
    }
    const res = await fetch(`/api/trucks?q=${encodeURIComponent(q)}`);
    const trucks = await res.json();
    list.innerHTML =
      trucks
        .map(
          (t) =>
            `<div class="truck-result" data-id="${t.id}">${t.placa || '(sin placa)'} · HOD ${t.hod_code || '-'} · SV ${t.sv_code || '-'}</div>`
        )
        .join('') || '<div class="truck-result muted">Sin resultados</div>';
    list.querySelectorAll('.truck-result[data-id]').forEach((elm) => {
      elm.addEventListener('click', () => assignTruck(parseInt(elm.dataset.id, 10)));
    });
  }, 250);
});

async function assignTruck(truckId) {
  const res = await fetch(`/api/spots/${currentSpotId}/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ truck_id: truckId }),
  });
  if (!res.ok) {
    const err = await res.json();
    alert(err.detail || 'Error al asignar el camión');
    return;
  }
  closeModal();
}

async function saveTruckEdit() {
  const placa = document.getElementById('edit-placa').value.trim();
  if (!placa) {
    alert('La placa es obligatoria.');
    return;
  }
  const res = await fetch(`/api/spots/${currentSpotId}/truck`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      hod_code: document.getElementById('edit-hod').value.trim() || null,
      placa,
      sv_code: document.getElementById('edit-sv').value.trim() || null,
    }),
  });
  if (!res.ok) {
    const err = await res.json();
    alert(err.detail || 'Error al guardar los cambios');
    return;
  }
  closeModal();
}

async function unassignTruck() {
  const res = await fetch(`/api/spots/${currentSpotId}/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ truck_id: null }),
  });
  if (res.ok) closeModal();
}

datePicker.addEventListener('change', (e) => loadBoard(e.target.value));
document.getElementById('modal-close').addEventListener('click', closeModal);
document.getElementById('modal-unassign').addEventListener('click', unassignTruck);
document.getElementById('edit-truck-save').addEventListener('click', saveTruckEdit);
document.querySelectorAll('.status-btn').forEach((btn) => {
  btn.addEventListener('click', () => setStatus(btn.dataset.status));
});

let zoom = 1.5;
mapEl.style.transform = `scale(${zoom})`;
document.getElementById('zoom-in').addEventListener('click', () => {
  zoom = Math.min(zoom + 0.25, 5);
  mapEl.style.transform = `scale(${zoom})`;
});
document.getElementById('zoom-out').addEventListener('click', () => {
  zoom = Math.max(zoom - 0.25, 0.5);
  mapEl.style.transform = `scale(${zoom})`;
});

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/board`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'layout_update') {
      loadBoard(datePicker.value);
      return;
    }
    if (datePicker.value !== window.APP.today) return;
    const spot = spotsById[msg.spot_id];
    if (!spot) return;
    if (msg.type === 'status_update') {
      spot.status = msg.status;
      spot.comentario = msg.comentario;
      spot.updated_by = msg.updated_by;
      spot.updated_at = msg.updated_at;
      updateTile(spot);
    } else if (msg.type === 'assign_update') {
      spot.truck = msg.truck;
      updateTile(spot);
    }
  };
  ws.onclose = () => setTimeout(connectWS, 2000);
}

// --- Editor de layout (admin) ---------------------------------------------
// Mueve/redimensiona Spots Y etiquetas de zona (BODEGA, OFICINAS, BAÑOS...)
// en pasos de 1 celda real de Excel, para corregir lo que no quedó bien al
// leer el archivo, sin tener que volver a tocar el importador cada vez.
const editModeBtn = document.getElementById('edit-mode-btn');
const editModeHint = document.getElementById('edit-mode-hint');
const newLabelBtn = document.getElementById('new-label-btn');
const syncExcelBtn = document.getElementById('sync-excel-btn');

if (syncExcelBtn) {
  syncExcelBtn.addEventListener('click', async () => {
    syncExcelBtn.disabled = true;
    syncExcelBtn.textContent = '🔄 Sincronizando...';
    try {
      const res = await fetch('/api/sync-excel', { method: 'POST' });
      const stats = await res.json();
      if (!res.ok) {
        alert(stats.detail || 'No se pudo sincronizar el Excel');
      } else {
        alert(`Excel sincronizado: ${stats.created} Spot(s) nuevo(s), ${stats.moved || 0} movido(s), ${stats.updated} actualizado(s), ${stats.unchanged} sin cambios.`);
      }
    } finally {
      syncExcelBtn.disabled = false;
      syncExcelBtn.textContent = '🔄 Sincronizar Excel';
    }
  });
}

function itemElement(sel) {
  return sel && document.getElementById(sel.type === 'spot' ? `spot-${sel.id}` : `label-${sel.id}`);
}

function isSelected(type, id) {
  return selected.some((s) => s.type === type && s.id === id);
}

function clearSelection() {
  document.querySelectorAll('.selected').forEach((el) => el.classList.remove('selected'));
  selected = [];
}

// `add=true` (Ctrl/Shift+clic) suma o quita ese ítem de la selección actual
// en vez de reemplazarla -- así se pueden mover/redimensionar/borrar varios
// Spots o etiquetas a la vez.
function selectItem(type, id, add = false) {
  if (type == null || id == null) {
    clearSelection();
    return;
  }
  if (!add) {
    clearSelection();
    selected = [{ type, id }];
    const el = itemElement({ type, id });
    if (el) el.classList.add('selected');
    return;
  }
  if (isSelected(type, id)) {
    selected = selected.filter((s) => !(s.type === type && s.id === id));
    const el = itemElement({ type, id });
    if (el) el.classList.remove('selected');
  } else {
    selected.push({ type, id });
    const el = itemElement({ type, id });
    if (el) el.classList.add('selected');
  }
}

// --- Deshacer (Ctrl+Z) -----------------------------------------------------
// Es muy fácil crear/mover/borrar de más mientras se acomoda el layout a
// mano -- cada acción del editor guarda su inversa acá, y Ctrl+Z la aplica.
// No hay rehacer (Ctrl+Y); el usuario solo pidió poder devolverse.
function pushUndo(action) {
  if (suppressUndo) return;
  undoStack.push(action);
  if (undoStack.length > 50) undoStack.shift();
}

async function applyUndoAction(action) {
  if (action.kind === 'adjust') {
    const base = action.type === 'spot' ? '/api/spots' : '/api/labels';
    await fetch(`${base}/${action.id}/adjust`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        d_row: -action.d_row, d_col: -action.d_col,
        d_row_span: -action.d_row_span, d_col_span: -action.d_col_span,
      }),
    });
  } else if (action.kind === 'create') {
    const base = action.type === 'spot' ? '/api/spots' : '/api/labels';
    await fetch(`${base}/${action.id}`, { method: 'DELETE' });
    selected = selected.filter((s) => !(s.type === action.type && s.id === action.id));
  } else if (action.kind === 'rename') {
    await fetch(`/api/labels/${action.id}/text`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: action.oldText }),
    });
  } else if (action.kind === 'delete') {
    const snap = action.snapshot;
    const createRes = await fetch(action.type === 'spot' ? '/api/spots' : '/api/labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(
        action.type === 'spot'
          ? { grid_row: snap.grid_row, grid_col: snap.grid_col }
          : { text: snap.text, grid_row: snap.grid_row, grid_col: snap.grid_col }
      ),
    });
    const created = await createRes.json();
    const baseSpan = 1;
    const baseColSpan = action.type === 'spot' ? 1 : 2;
    if (snap.row_span !== baseSpan || snap.col_span !== baseColSpan) {
      await fetch(`${action.type === 'spot' ? '/api/spots' : '/api/labels'}/${created.id}/adjust`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ d_row: 0, d_col: 0, d_row_span: snap.row_span - baseSpan, d_col_span: snap.col_span - baseColSpan }),
      });
    }
    if (action.type === 'spot' && snap.truck) {
      await fetch(`/api/spots/${created.id}/truck`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hod_code: snap.truck.hod_code,
          placa: snap.truck.placa || 'SIN-PLACA',
          sv_code: snap.truck.sv_code,
        }),
      });
    }
  }
}

async function undo() {
  const action = undoStack.pop();
  if (!action) return;
  suppressUndo = true;
  try {
    if (action.kind === 'batch') {
      for (const sub of [...action.actions].reverse()) await applyUndoAction(sub);
    } else {
      await applyUndoAction(action);
    }
  } catch (err) {
    alert('No se pudo deshacer la última acción.');
  } finally {
    suppressUndo = false;
  }
}

// Aplica el mismo movimiento/redimensión a TODOS los ítems seleccionados
// (selección múltiple) y agrupa sus deshacer en un solo paso de Ctrl+Z.
async function adjustSelected(dRow, dCol, dRowSpan, dColSpan) {
  if (!selected.length) return;
  const applied = [];
  for (const item of selected) {
    const base = item.type === 'spot' ? '/api/spots' : '/api/labels';
    const res = await fetch(`${base}/${item.id}/adjust`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ d_row: dRow, d_col: dCol, d_row_span: dRowSpan, d_col_span: dColSpan }),
    });
    if (res.ok) {
      applied.push({ kind: 'adjust', type: item.type, id: item.id, d_row: dRow, d_col: dCol, d_row_span: dRowSpan, d_col_span: dColSpan });
    } else if (selected.length === 1) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || 'No se pudo mover/redimensionar');
    }
  }
  if (applied.length === 1) pushUndo(applied[0]);
  else if (applied.length > 1) pushUndo({ kind: 'batch', actions: applied });
  // El propio servidor nos manda "layout_update" por WebSocket y eso dispara
  // loadBoard(); no hace falta refrescar aquí también.
}

async function deleteSelected() {
  if (!selected.length) return;
  const names = selected.map((s) => (s.type === 'spot' ? displayCode(spotsById[s.id]) : labelsById[s.id].text));
  const what = selected.length === 1 ? `"${names[0]}"` : `${selected.length} elementos (${names.join(', ')})`;
  if (!confirm(`¿Eliminar ${what}? (Ctrl+Z lo puede deshacer)`)) return;

  const deleted = [];
  for (const item of selected) {
    const isSpot = item.type === 'spot';
    const snapshot = isSpot ? spotsById[item.id] : labelsById[item.id];
    const res = await fetch(`${isSpot ? '/api/spots' : '/api/labels'}/${item.id}`, { method: 'DELETE' });
    if (res.ok) deleted.push({ kind: 'delete', type: item.type, id: item.id, snapshot });
  }
  if (deleted.length === 1) pushUndo(deleted[0]);
  else if (deleted.length > 1) pushUndo({ kind: 'batch', actions: deleted });
  clearSelection();
}

// --- Copiar/pegar (Ctrl+C / Ctrl+V) -----------------------------------------
// Copia la forma de los Spots/etiquetas seleccionados (tamaño, y el texto si
// es una etiqueta) -- nunca el camión asignado, un Spot pegado nace en
// blanco igual que uno creado con clic, para que el admin le asigne su
// propio camión desde el modal. Pegar los crea con un desfase fijo para no
// quedar exactamente encima de lo copiado, y los deja seleccionados listos
// para arrastrar a su lugar.
let clipboard = [];
const PASTE_OFFSET_ROW = 2;
const PASTE_OFFSET_COL = 3;

function copySelected() {
  if (!selected.length) return;
  clipboard = selected.map((s) => {
    const item = s.type === 'spot' ? spotsById[s.id] : labelsById[s.id];
    return {
      type: s.type,
      grid_row: item.grid_row,
      grid_col: item.grid_col,
      row_span: item.row_span,
      col_span: item.col_span,
      text: s.type === 'label' ? item.text : undefined,
    };
  });
}

async function pasteClipboard() {
  if (!clipboard.length) return;
  const createdActions = [];
  const createdSel = [];
  for (const c of clipboard) {
    const base = c.type === 'spot' ? '/api/spots' : '/api/labels';
    const payload =
      c.type === 'spot'
        ? { grid_row: c.grid_row + PASTE_OFFSET_ROW, grid_col: c.grid_col + PASTE_OFFSET_COL }
        : { text: c.text, grid_row: c.grid_row + PASTE_OFFSET_ROW, grid_col: c.grid_col + PASTE_OFFSET_COL };
    const res = await fetch(base, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) continue;
    const created = await res.json();
    createdActions.push({ kind: 'create', type: c.type, id: created.id });
    createdSel.push({ type: c.type, id: created.id });

    // El endpoint de creación siempre usa el tamaño base (Spot 1x1, etiqueta
    // 1x2) -- si lo copiado era de otro tamaño, se ajusta en un segundo paso.
    const baseRowSpan = 1;
    const baseColSpan = c.type === 'spot' ? 1 : 2;
    if (c.row_span !== baseRowSpan || c.col_span !== baseColSpan) {
      await fetch(`${base}/${created.id}/adjust`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ d_row: 0, d_col: 0, d_row_span: c.row_span - baseRowSpan, d_col_span: c.col_span - baseColSpan }),
      });
    }
  }
  if (!createdActions.length) return;
  pushUndo(createdActions.length === 1 ? createdActions[0] : { kind: 'batch', actions: createdActions });
  selectItem(null, null);
  createdSel.forEach((s) => selectItem(s.type, s.id, true));
}

async function renameLabel(labelId) {
  const label = labelsById[labelId];
  const text = prompt('Texto de la etiqueta:', label.text);
  if (text == null || !text.trim() || text.trim() === label.text) return;
  const oldText = label.text;
  const res = await fetch(`/api/labels/${labelId}/text`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text.trim() }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || 'No se pudo renombrar la etiqueta');
    return;
  }
  pushUndo({ kind: 'rename', id: labelId, oldText });
}

// Convierte una posición en píxeles (dentro del grid, sin zoom) al número de
// línea de grid (1-based) sumando los tamaños de fila/columna hasta pasarla.
function lineAt(pos, sizes) {
  let acc = 0;
  for (let i = 0; i < sizes.length; i++) {
    acc += sizes[i];
    if (pos < acc) return i + 1;
  }
  return sizes.length;
}

function clickToGridLine(e) {
  const rect = mapEl.getBoundingClientRect();
  const x = (e.clientX - rect.left) / zoom;
  const y = (e.clientY - rect.top) / zoom;
  return { grid_col: lineAt(x, lastColSizes), grid_row: lineAt(y, lastRowSizes) };
}

async function createSpotAtClick(e) {
  const { grid_row, grid_col } = clickToGridLine(e);
  const res = await fetch('/api/spots', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ grid_row, grid_col }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || 'No se pudo crear el Spot');
    return;
  }
  const created = await res.json();
  selectItem('spot', created.id);
  pushUndo({ kind: 'create', type: 'spot', id: created.id });
}

let placingLabel = false;

async function createLabelAtClick(e) {
  const text = prompt('Texto de la nueva etiqueta (ej. OFICINAS):');
  placingLabel = false;
  newLabelBtn.classList.remove('active');
  if (!text || !text.trim()) return;
  const { grid_row, grid_col } = clickToGridLine(e);
  const res = await fetch('/api/labels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text.trim(), grid_row, grid_col }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || 'No se pudo crear la etiqueta');
    return;
  }
  const created = await res.json();
  selectItem('label', created.id);
  pushUndo({ kind: 'create', type: 'label', id: created.id });
}

// --- Arrastrar con el mouse -------------------------------------------------
// Alternativa a las flechas: agarrar un Spot/etiqueta y soltarlo donde se
// quiere, encajando siempre en una línea real del grid (como un rompecabezas
// -- nunca queda "a medias" entre dos celdas).
let dragState = null;

// Si el ítem que se agarra ya es parte de la selección actual, se arrastra
// TODO el grupo junto (mismo delta para todos); si no, la selección se
// reemplaza por ese único ítem, como es de esperarse al agarrar algo suelto.
function startDrag(type, id, el, e) {
  if (!editMode || e.button !== 0) return;
  e.preventDefault();
  if (!isSelected(type, id)) selectItem(type, id);
  const groupItems = selected.map((s) => {
    const item = s.type === 'spot' ? spotsById[s.id] : labelsById[s.id];
    return { type: s.type, id: s.id, el: itemElement(s), startRow: item.grid_row, startCol: item.grid_col, rowSpan: item.row_span, colSpan: item.col_span };
  });
  const anchor = groupItems.find((g) => g.type === type && g.id === id);
  dragState = { groupItems, anchorStartRow: anchor.startRow, anchorStartCol: anchor.startCol, currentRow: anchor.startRow, currentCol: anchor.startCol, moved: false };
  document.addEventListener('mousemove', onDragMove);
  document.addEventListener('mouseup', onDragEnd);
}

function onDragMove(e) {
  if (!dragState) return;
  const { grid_row, grid_col } = clickToGridLine(e);
  if (grid_row === dragState.currentRow && grid_col === dragState.currentCol) return;
  dragState.moved = true;
  dragState.currentRow = grid_row;
  dragState.currentCol = grid_col;
  const dRow = grid_row - dragState.anchorStartRow;
  const dCol = grid_col - dragState.anchorStartCol;
  for (const g of dragState.groupItems) {
    g.el.style.gridRow = `${g.startRow + dRow} / span ${g.rowSpan}`;
    g.el.style.gridColumn = `${g.startCol + dCol} / span ${g.colSpan}`;
    g.el.classList.add('dragging');
  }
}

async function onDragEnd() {
  document.removeEventListener('mousemove', onDragMove);
  document.removeEventListener('mouseup', onDragEnd);
  const drag = dragState;
  dragState = null;
  if (!drag) return;
  drag.groupItems.forEach((g) => g.el.classList.remove('dragging'));
  if (!drag.moved) return;
  justDragged = true;
  setTimeout(() => (justDragged = false), 0);
  const dRow = drag.currentRow - drag.anchorStartRow;
  const dCol = drag.currentCol - drag.anchorStartCol;
  if (dRow === 0 && dCol === 0) return;
  await adjustSelected(dRow, dCol, 0, 0);
}

if (editModeBtn) {
  editModeBtn.addEventListener('click', () => {
    editMode = !editMode;
    editModeBtn.classList.toggle('active', editMode);
    editModeHint.style.display = editMode ? '' : 'none';
    newLabelBtn.style.display = editMode ? '' : 'none';
    mapEl.classList.toggle('edit-mode', editMode);
    if (!editMode) {
      selectItem(null, null);
      placingLabel = false;
      newLabelBtn.classList.remove('active');
    }
  });

  newLabelBtn.addEventListener('click', () => {
    placingLabel = !placingLabel;
    newLabelBtn.classList.toggle('active', placingLabel);
  });

  mapEl.addEventListener('click', (e) => {
    if (!editMode || e.target !== mapEl) return;
    if (placingLabel) createLabelAtClick(e);
    else createSpotAtClick(e);
  });

  document.addEventListener('keydown', (e) => {
    if (!editMode) return;
    const typing = e.target && ['INPUT', 'TEXTAREA'].includes(e.target.tagName);
    if (typing) return; // no robarse el Ctrl+Z nativo de un campo de texto

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      undo();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c') {
      e.preventDefault();
      copySelected();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') {
      e.preventDefault();
      pasteClipboard();
      return;
    }
    if (!selected.length) return;
    const step = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[e.key];
    if (e.key === 'Escape') {
      selectItem(null, null);
      e.preventDefault();
      return;
    }
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      deleteSelected();
      return;
    }
    if (!step) return;
    e.preventDefault();
    const [dr, dc] = step;
    if (e.shiftKey) adjustSelected(0, 0, dr, dc);
    else adjustSelected(dr, dc, 0, 0);
  });
}

loadBoard(datePicker.value);
connectWS();
