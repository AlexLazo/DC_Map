// Reloj de la esquina: muestra la hora del SERVIDOR (zona del CD) y el día
// operativo. Sirve para verificar a simple vista que la hora con la que la
// app sella los eventos es la correcta -- si no coincide con tu reloj, algo
// está mal configurado. Se toma la hora del servidor una vez (compensando la
// latencia) y luego avanza sola con el reloj del dispositivo; se re-sincroniza
// cada 5 min y al volver a la app, para no acumular desfase.
(function () {
  const el = document.createElement('div');
  el.className = 'live-clock';
  el.id = 'live-clock';
  el.innerHTML =
    '<span class="lc-dot"></span>' +
    '<span class="lc-main"><b class="lc-time">--:--:--</b><small class="lc-day">sincronizando…</small></span>';
  document.body.appendChild(el);

  const timeEl = el.querySelector('.lc-time');
  const dayEl = el.querySelector('.lc-day');
  let offsetMs = 0;
  let synced = false;
  let tz = 'America/El_Salvador';
  let shiftStart = 22;
  let shiftEnd = 10;
  let shiftOpen = null;

  function fmt(opts, date) {
    return new Intl.DateTimeFormat('es-SV', Object.assign({ timeZone: tz }, opts)).format(date);
  }

  function render() {
    if (!synced) return;
    const now = new Date(Date.now() + offsetMs);
    timeEl.textContent = fmt({ hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true }, now);

    // Día operativo = el día en que TERMINA el turno (10 PM del 18 -> 10 AM del 19
    // es el "19"): se suman las horas que faltan para medianoche desde el inicio
    // del turno, igual que en el servidor (app/local_time.py).
    const opDay = new Date(now.getTime() + (24 - shiftStart) * 3600 * 1000);
    const hour = parseInt(fmt({ hour: '2-digit', hour12: false }, now), 10) % 24;
    const open = hour >= shiftStart || hour < shiftEnd;
    dayEl.textContent =
      `${fmt({ weekday: 'short', day: '2-digit', month: '2-digit' }, now)} · día op. ${fmt({ day: '2-digit', month: '2-digit' }, opDay)} · ` +
      (open ? 'en turno' : 'fuera de turno');
    el.classList.toggle('shift-closed', !open);

    // Avisa a la página en el instante exacto en que abre/cierra el horario
    // (el tablero se pone al día sin esperar su ciclo de 30 s).
    if (shiftOpen !== null && shiftOpen !== open) {
      window.dispatchEvent(new CustomEvent('shiftchange', { detail: { open } }));
    }
    shiftOpen = open;
  }

  async function sync() {
    try {
      const t0 = Date.now();
      const res = await fetch('/api/time', { cache: 'no-store' });
      if (!res.ok) return;
      const d = await res.json();
      const t1 = Date.now();
      offsetMs = Date.parse(d.utc) + (t1 - t0) / 2 - t1;
      tz = d.tz;
      shiftStart = d.shift_start_hour;
      shiftEnd = d.shift_end_hour;
      synced = true;
      el.title =
        `Hora del SERVIDOR (${tz}). Si no coincide con tu reloj, avisa al administrador. ` +
        `Turno de ${shiftStart % 12 || 12} ${shiftStart < 12 ? 'AM' : 'PM'} a ${shiftEnd % 12 || 12} ${shiftEnd < 12 ? 'AM' : 'PM'}: ` +
        `lo que se carga en ese turno cuenta para el día en que termina. Fuera de turno el tablero es solo de consulta.`;
      render();
      window.dispatchEvent(new Event('clocksync')); // p. ej. el contador del tablero se recalcula ya
    } catch (err) {
      // sin señal: se sigue mostrando la última hora sincronizada
    }
  }

  // Los módulos con conexión en vivo (tablero, dashboard) prenden el punto verde.
  window.LiveClock = {
    setLive(on) {
      el.classList.toggle('is-live', !!on);
    },
    // Momento actual según el SERVIDOR (ISO, UTC): se usa para sellar los cambios
    // marcados sin conexión con la hora real y no con la del dispositivo.
    nowIso() {
      return new Date(Date.now() + offsetMs).toISOString();
    },
    // Milisegundos que faltan para que cierre el turno; null si está cerrado y
    // undefined si el reloj aún no se sincroniza con el servidor (no se sabe).
    shiftRemainingMs() {
      if (!synced) return undefined;
      const now = new Date(Date.now() + offsetMs);
      const parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(now);
      const get = (t) => parseInt(parts.find((x) => x.type === t).value, 10);
      const h = get('hour');
      if (!(h >= shiftStart || h < shiftEnd)) return null;
      const secOfDay = h * 3600 + get('minute') * 60 + get('second');
      const endSec = h >= shiftStart ? 24 * 3600 + shiftEnd * 3600 : shiftEnd * 3600;
      return (endSec - secOfDay) * 1000;
    },
    // Hora del servidor ya formateada (para "actualizado a las ...").
    timeString() {
      return synced ? timeEl.textContent : new Date().toLocaleTimeString('es-SV');
    },
  };

  sync();
  setInterval(render, 1000);
  setInterval(sync, 5 * 60 * 1000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) sync();
  });
})();
