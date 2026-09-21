// Dashboard en vivo. La página se renderiza en el servidor, así que en vez de
// duplicar todos los cálculos en JS se vuelve a pedir la MISMA URL cuando algo
// cambia y se reemplazan solo las secciones que difieren (sin recargar, sin
// perder el scroll de las tablas ni lo que la persona esté mirando).
//
// Qué dispara un refresco:
//   * WebSocket /ws/board  -> cualquier cambio en el tablero (estatus, asignación, layout)
//   * cada 30 s            -> red de seguridad por si se perdió algún evento
//   * volver a la pestaña / recuperar internet / reconectar el WebSocket
(function () {
  const POLL_MS = 30000;
  const MIN_GAP_MS = 800; // varios eventos seguidos (p. ej. pegar 10 Spots) -> un solo refresco

  const SCROLLERS = '.table-scroll, .hour-chart-wrap';

  // Franja "En vivo" entre las pestañas y el contenido (fuera de <main>, así no
  // entra en la comparación de secciones).
  const strip = document.createElement('div');
  strip.className = 'live-strip';
  strip.innerHTML = '<span class="ls-dot"></span><span class="ls-text">Conectando…</span>';
  const nav = document.querySelector('.admin-tabs');
  if (nav) nav.insertAdjacentElement('afterend', strip);
  const stripText = strip.querySelector('.ls-text');

  let connected = false;
  let inflight = false;
  let pending = false;
  let lastStart = 0;
  let debounce = null;
  let hasConnectedBefore = false;

  function now() {
    if (window.LiveClock && window.LiveClock.timeString) return window.LiveClock.timeString();
    return new Date().toLocaleTimeString('es-SV');
  }

  function paintStrip(updatedAt) {
    strip.classList.toggle('is-live', connected);
    if (!connected) {
      stripText.textContent = hasConnectedBefore ? 'Reconectando… (se sigue actualizando cada 30 s)' : 'Conectando…';
    } else {
      stripText.textContent = 'En vivo · actualizado ' + (updatedAt || now());
    }
  }

  function flash(nodes) {
    nodes.forEach((n) => {
      n.classList.remove('live-updated');
      void n.offsetWidth; // reinicia la animación si ya la tenía
      n.classList.add('live-updated');
    });
  }

  function apply(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const freshMain = doc.querySelector('main.admin-main');
    const curMain = document.querySelector('main.admin-main');
    if (!freshMain || !curMain) return;

    const cur = Array.from(curMain.children);
    const fresh = Array.from(freshMain.children);
    if (cur.length !== fresh.length) {
      curMain.innerHTML = freshMain.innerHTML;
      flash(Array.from(curMain.children));
      return;
    }

    const changed = [];
    fresh.forEach((f, i) => {
      const c = cur[i];
      if (c.innerHTML === f.innerHTML && c.className === f.className) return;
      const scrolls = Array.from(c.querySelectorAll(SCROLLERS)).map((e) => [e.scrollLeft, e.scrollTop]);
      const node = document.importNode(f, true);
      c.replaceWith(node);
      node.querySelectorAll(SCROLLERS).forEach((e, j) => {
        if (scrolls[j]) {
          e.scrollLeft = scrolls[j][0];
          e.scrollTop = scrolls[j][1];
        }
      });
      changed.push(node);
    });
    flash(changed);
  }

  async function refresh() {
    if (document.hidden) return;
    // No pisar el campo que la persona está usando (p. ej. el selector de fecha).
    const a = document.activeElement;
    if (a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)) return;
    if (inflight) {
      pending = true;
      return;
    }
    inflight = true;
    lastStart = Date.now();
    try {
      const res = await fetch(location.pathname + location.search, { cache: 'no-store', credentials: 'same-origin' });
      // Sesión vencida o sin permiso: el servidor redirige -> seguir ese redirect.
      if (res.redirected && new URL(res.url).pathname !== location.pathname) {
        location.href = res.url;
        return;
      }
      if (!res.ok) return;
      apply(await res.text());
      paintStrip();
    } catch (err) {
      // sin red: se reintenta en el próximo ciclo
    } finally {
      inflight = false;
      if (pending) {
        pending = false;
        schedule();
      }
    }
  }

  function schedule() {
    if (debounce) return;
    const wait = Math.max(0, MIN_GAP_MS - (Date.now() - lastStart));
    debounce = setTimeout(() => {
      debounce = null;
      refresh();
    }, wait);
  }

  function setConnected(on) {
    connected = on;
    if (window.LiveClock) window.LiveClock.setLive(on);
    paintStrip();
  }

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/board`);
    ws.onopen = () => {
      setConnected(true);
      // Al reconectar pudimos perdernos eventos: ponerse al día.
      if (hasConnectedBefore) schedule();
      hasConnectedBefore = true;
    };
    ws.onmessage = () => schedule();
    ws.onclose = () => {
      setConnected(false);
      setTimeout(connect, 2000);
    };
  }

  connect();
  // El dashboard se usa también como pantalla de pared: que la pantalla no se apague.
  if (window.KeepAwake) window.KeepAwake.set(true);
  setInterval(schedule, POLL_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) schedule();
  });
  window.addEventListener('online', schedule);
})();
