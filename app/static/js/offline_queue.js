// Cola de cambios de estatus marcados SIN conexión.
//
// En el patio el wifi se cae por ratos. Si una persona toca "Cargado" justo
// entonces, el cambio no se pierde: se guarda en este dispositivo (localStorage)
// CON LA HORA EN QUE SE MARCÓ, y se reenvía solo cuando vuelve la señal. El
// servidor sella el evento con esa hora original (no con la de la reconexión) y
// rechaza lo que ya no aplica (más de 3 h, o alguien cambió el camión después).
//
// Este módulo solo guarda y reenvía en orden; qué hacer con cada resultado
// (mostrar avisos, pintar el mapa) lo decide board.js. Los cambios de otro
// usuario que use la misma tablet se conservan pero NO se envían con esta sesión.
window.OfflineQueue = (function () {
  const KEY = 'dcmap_queue_v1';
  const listeners = [];
  let flushing = false;

  function read() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || '[]');
      return Array.isArray(v) ? v : [];
    } catch (err) {
      return [];
    }
  }

  // Si localStorage falla (modo privado, lleno) la cola sigue viva en memoria
  // mientras la pestaña esté abierta.
  let items = read();

  function persist() {
    try {
      localStorage.setItem(KEY, JSON.stringify(items));
    } catch (err) {
      /* solo memoria */
    }
  }

  function notify() {
    listeners.forEach((fn) => {
      try {
        fn();
      } catch (err) {
        /* un listener roto no debe frenar la cola */
      }
    });
  }

  // Otra pestaña del mismo dispositivo cambió la cola.
  window.addEventListener('storage', (e) => {
    if (e.key === KEY) {
      items = read();
      notify();
    }
  });

  const mine = (userId) => items.filter((i) => i.userId === userId);

  return {
    add(item) {
      items.push(item);
      persist();
      notify();
    },
    count: (userId) => mine(userId).length,
    pending: (userId) => mine(userId).slice(),
    hasPending: (spotId, userId) => mine(userId).some((i) => i.spotId === spotId),
    onChange(fn) {
      listeners.push(fn);
    },
    // Envía en orden los cambios de `userId` con `sender(item)`, que debe devolver:
    //   {ok: true}                -> aplicado, se quita de la cola
    //   {rejected: true, detail}  -> el servidor lo rechazó, se quita y se reporta
    //   {retry: true}             -> sin conexión / servidor reiniciando: se detiene y se reintenta luego
    async flush(userId, sender) {
      if (flushing) return null;
      flushing = true;
      const result = { applied: [], rejected: [] };
      try {
        for (const item of mine(userId)) {
          const r = await sender(item);
          if (r.retry) break;
          items = items.filter((i) => i.id !== item.id);
          persist();
          if (r.ok) result.applied.push(item);
          else result.rejected.push({ item, detail: r.detail });
          notify();
        }
      } finally {
        flushing = false;
      }
      return result;
    },
  };
})();
