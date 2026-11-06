/* Чистые функции окна «AI-агент»: проверяются node-тестами. */

export function connectionText(origin, token, mode) {
  return [
    `Studio: ${origin}`,
    `Token: ${token}`,
    `Mode: ${mode}`,
    "Заголовок запросов: X-Studio-Token. Начни с GET /api/agent — там маршруты и порядок работы.",
  ].join("\n");
}

export function tasksWord(n) {
  const a = n % 10;
  const b = n % 100;
  if (a === 1 && b !== 11) return "задача";
  return a >= 2 && a <= 4 && (b < 10 || b >= 20) ? "задачи" : "задач";
}

export function briefState(backend, frontend) {
  const part = (title, count, missing) => (count === null
    ? `${title}: ${missing}`
    : `${title}: ${count} ${tasksWord(count)}`);
  return `${part("ТЗ бэкенда", backend, "не сохранено (кнопка «ТЗ» на карте)")} · ${part("ТЗ фронтенда", frontend, "не собрано («⇄ ТЗ…» во вкладке «Фронтенд»)")}`;
}
