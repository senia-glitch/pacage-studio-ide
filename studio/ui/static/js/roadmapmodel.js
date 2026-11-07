/* Чистые функции дорожной карты и свайпа карточек: без DOM и сети, проверяются node-тестами. */

export const SWIPE_DISTANCE = 110; // на сколько пикселей нужно утащить карточку по вертикали
export const SWIPE_VELOCITY = 0.55; // …или так быстро (px/мс) — тогда хватит половины пути

/* Решение по жесту: вверх — принять, вниз — отклонить, иначе карточка возвращается. */
export function swipeDecision(dy, velocity = 0, distance = SWIPE_DISTANCE) {
  const move = Number(dy) || 0;
  const speed = Number(velocity) || 0;
  const fast = Math.abs(speed) >= SWIPE_VELOCITY && Math.abs(move) >= distance / 2;
  if (move <= -distance || (fast && move < 0)) return "accepted";
  if (move >= distance || (fast && move > 0)) return "rejected";
  return null;
}

/* Насколько жест «дозрел» до решения: 0…1 и знак (подсветка краёв и наклон карточки). */
export function swipeProgress(dy, distance = SWIPE_DISTANCE) {
  const ratio = Math.max(-1, Math.min(1, (Number(dy) || 0) / distance));
  return {ratio, side: ratio < 0 ? "accepted" : ratio > 0 ? "rejected" : "", strength: Math.abs(ratio)};
}

function list(value) {
  return Array.isArray(value) ? value.map((item) => String(item).trim()).filter(Boolean) : [];
}

function view(idea, kind, item) {
  const source = item.source && typeof item.source === "object" ? item.source : {};
  const base = {
    id: String(item.id || ""),
    kind,
    kindLabel: kind === "scenario" ? "Сценарий бэкенда" : "Страница фронтенда",
    idea: String((idea && idea.title) || ""),
    title: String(item.title || item.name || source.title || ""),
    description: String(item.description || ""),
    acceptance: list(item.acceptance),
    domain: String(item.domain || ""),
    decision: String(item.decision || ""),
    mapped: Boolean(item.planSlug || item.mapPageId || item.mapNodeId),
  };
  if (kind === "scenario") {
    const method = String(source.http_method || "").toUpperCase();
    return {
      ...base,
      route: [method, String(source.path || "")].filter(Boolean).join(" "),
      slug: String(source.slug || ""),
      extras: [source.uses_db ? "работает с БД" : "", source.with_test ? "с тестом" : ""].filter(Boolean),
      calls: [],
      blocks: [],
    };
  }
  const extendsPage = String(item.extends || "");
  return {
    ...base,
    kindLabel: extendsPage ? "Блоки на существующей странице" : base.kindLabel,
    route: extendsPage ? `страница ${extendsPage}` : String(item.route || ""),
    slug: String(item.name || ""),
    extends: extendsPage,
    extras: [],
    calls: list(item.calls),
    blocks: (Array.isArray(item.blocks) ? item.blocks : []).map((block) => ({
      name: String((block && block.name) || ""),
      description: String((block && block.description) || ""),
    })),
  };
}

/* Все карточки доски в порядке идей; own — только решаемые в цепочке (есть decision). */
export function cardsOf(board) {
  const out = [];
  for (const idea of (board && board.ideas) || []) {
    if (!idea || typeof idea !== "object") continue;
    for (const [kind, key] of [["scenario", "scenarios"], ["page", "pages"]]) {
      for (const item of Array.isArray(idea[key]) ? idea[key] : []) {
        if (item && typeof item === "object" && ["pending", "accepted", "rejected"].includes(item.decision)) {
          out.push(view(idea, kind, item));
        }
      }
    }
  }
  return out;
}

export function pendingQueue(board) {
  return cardsOf(board).filter((card) => card.decision === "pending");
}

export function tally(cards) {
  const result = {pending: 0, accepted: 0, rejected: 0, total: 0};
  for (const card of cards) {
    if (card.decision in result) result[card.decision] += 1;
    result.total += 1;
  }
  return result;
}

export function steps(flow) {
  return Array.isArray(flow && flow.steps) ? flow.steps : [];
}

/* Агент «готов», если он уже обращался к студии или студия запускает его сама (автозапуск). */
export function agentReady(flow) {
  return !!(flow && flow.agent && (flow.agent.connected || flow.agent.auto));
}

/* Что говорит автозапуск на этапах агента: строка для полосы и нужна ли кнопка «Запустить снова». */
export function runnerNote(flow) {
  const runner = flow && flow.runner;
  if (!runner || !runner.enabled || !["ideas", "implement"].includes(flow.stage)) return null;
  const messages = {
    queued: "Студия запускает агента…",
    running: "Агент запущен студией и работает",
    done: "Агент выполнил этап и завершился",
    stalled: runner.message,
    failed: `Агент не отработал: ${String(runner.message || "").trim().split("\n").pop()}`,
  };
  const text = messages[runner.state] || "";
  return text ? {text, retry: runner.state === "stalled" || runner.state === "failed", state: runner.state} : null;
}

/* Итог прогона тестов проекта для отчёта: тон (ok/bad/run/none), строка и список упавших. */
export function testsView(tests) {
  const state = tests && tests.state;
  if (!state || state === "none") {
    return {tone: "none", text: "Тесты проекта студия ещё не запускала.", failures: []};
  }
  if (state === "running") return {tone: "run", text: "Тесты проекта выполняются…", failures: []};
  if (state === "error") {
    const last = String((tests && tests.message) || "").trim().split("\n").pop();
    return {tone: "bad", text: `Тесты не запустились: ${last || "неизвестная ошибка"}`, failures: []};
  }
  const bad = (Number(tests.failed) || 0) + (Number(tests.errors) || 0);
  const skipped = Number(tests.skipped) ? `, пропущено ${tests.skipped}` : "";
  const names = {backend: "бэкенд", frontend: "фронтенд"};
  const parts = Array.isArray(tests.parts) && tests.parts.length > 1
    ? " (" + tests.parts.map((p) => p.state === "done"
      ? `${names[p.name] || p.name}: ${p.passed || 0} из ${p.total || 0}`
      : `${names[p.name] || p.name}: не запустились`).join("; ") + ")"
    : "";
  return {
    tone: bad ? "bad" : "ok",
    text: `Тесты проекта: прошло ${tests.passed || 0} из ${tests.total || 0}` + (bad ? `, упало ${bad}` : "") + skipped + parts,
    failures: (tests.failures || []).slice(0, 10),
  };
}

const NOTES = {
  mode: (flow) => (agentReady(flow)
    ? (flow.agent.connected ? "Агент подключён. Выберите, с чего начать." : "Агент запустится сам, когда понадобится. Выберите, с чего начать.")
    : "Запустите агента в папке проекта: он найдёт студию сам."),
  ideas: (flow) => `Агент готовит идеи · карточек получено: ${(flow.cards && flow.cards.total) || 0}`,
  review: (flow) => `Осталось карточек: ${(flow.cards && flow.cards.pending) || 0}`,
  confirm: (flow) => `Принято карточек: ${(flow.cards && flow.cards.accepted) || 0}. Перенести на карту?`,
  select: () => "Отметьте, что отдать агенту на реализацию.",
  implement: () => "Агент реализует выбранное. Здесь появится его отчёт.",
  report: () => "Агент прислал отчёт.",
};

export function stageNote(flow) {
  if (!flow || !flow.stage) return "";
  const note = NOTES[flow.stage];
  return note ? note(flow) : "";
}

/* Главная кнопка этапа: что человек делает сейчас. */
export function primaryAction(flow) {
  if (!flow) return null;
  const cards = flow.cards || {};
  switch (flow.stage) {
    case "mode":
      return {id: "mode", label: agentReady(flow) ? "Выбрать режим" : "Как подключить агента"};
    case "ideas":
      return cards.total > 0 ? {id: "review", label: "К валидации карточек"} : null;
    case "review":
      return {id: "swipe", label: "Открыть карточки"};
    case "confirm":
      return {id: "confirm", label: "Перенести на карту…"};
    case "select":
      return {id: "select", label: "Выбрать задачи"};
    case "implement":
      return {id: "manual", label: "Принять работу без отчёта"};
    case "report":
      return {id: "report", label: "Открыть отчёт"};
    default:
      return null;
  }
}

/* Модальное окно, которое нужно показать само при входе на этап (по разу за цикл). */
export function autoOpen(flow) {
  if (!flow) return "";
  switch (flow.stage) {
    case "mode":
      return agentReady(flow) ? "mode" : "";
    case "review":
      return "swipe";
    case "confirm":
      return "confirm";
    case "select":
      return "select";
    case "report":
      return "report";
    default:
      return "";
  }
}

export function autoKey(flow) {
  return flow && flow.stage ? `${flow.cycle || 1}:${flow.stage}:${flow.entries || 0}` : "";
}

/* Чекбоксы выбора задач: по умолчанию отмечено всё готовое (с описанием). */
export function defaultSelection(candidates) {
  const pick = (items, key) => new Set((items || []).filter((item) => item.ready).map((item) => item[key]));
  return {
    plans: pick(candidates && candidates.plans, "slug"),
    nodes: pick(candidates && candidates.nodes, "id"),
    pages: pick(candidates && candidates.pages, "id"),
  };
}

export function selectionPayload(selection) {
  return {
    plans: [...selection.plans],
    nodes: [...selection.nodes],
    pages: [...selection.pages],
  };
}

export function selectionCount(selection) {
  return selection.plans.size + selection.nodes.size + selection.pages.size;
}

/* «Выполнено x из y» по отчётам verify обеих частей. */
export function progressOf(reports) {
  let done = 0;
  let total = 0;
  for (const report of reports || []) {
    if (!report) continue;
    done += Number(report.done) || 0;
    total += Number(report.total) || 0;
  }
  return {done, total, complete: total > 0 && done >= total};
}
