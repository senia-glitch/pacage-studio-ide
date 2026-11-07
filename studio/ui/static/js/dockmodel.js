/* Окно агента: чистые функции (без DOM и сети), проверяются node-тестами.
   Цепочка для человека — 7 шагов, у каждого видно, чей ход. Внутренние этапы студии
   (mode/ideas/review/confirm/select/implement/report) показываются через эти шаги. */

export const DOCK_STEPS = [
  {id: "goal", title: "Цель", turn: "you"},
  {id: "ideas", title: "Идеи", turn: "agent"},
  {id: "review", title: "Разбор", turn: "you"},
  {id: "select", title: "Выбор задач", turn: "you"},
  {id: "implement", title: "Реализация", turn: "agent"},
  {id: "verify", title: "Проверка", turn: "studio"},
  {id: "accept", title: "Приёмка", turn: "you"},
];

export const TURN_LABELS = {you: "ваш ход", agent: "ход агента", studio: "проверяет студия"};

const STAGE_TO_STEP = {
  mode: "goal",
  ideas: "ideas",
  review: "review",
  confirm: "review", // перенос на карту делает свайп вверх: отдельного шага у человека нет
  select: "select",
  implement: "implement",
  report: "accept",
};

/* Какой из 7 шагов сейчас активен. На этапе «Отчёт» пока тесты идут — это «Проверка». */
export function activeDockStep(flow) {
  if (!flow || !flow.stage) return "goal";
  if (flow.stage === "report" && flow.tests && flow.tests.state === "running") return "verify";
  return STAGE_TO_STEP[flow.stage] || "goal";
}

/* Шаги с состоянием done / active / todo / skipped (путь «реализация» пропускает идеи и разбор). */
export function dockSteps(flow) {
  const active = activeDockStep(flow);
  const activeIndex = DOCK_STEPS.findIndex((step) => step.id === active);
  const skipped = flow && flow.mode === "implement" ? new Set(["ideas", "review"]) : new Set();
  return DOCK_STEPS.map((step, index) => ({
    ...step,
    turnLabel: TURN_LABELS[step.turn],
    state: skipped.has(step.id)
      ? "skipped"
      : index < activeIndex ? "done" : index === activeIndex ? "active" : "todo",
  }));
}

/* Выбранный агент: имя, название для людей, найден ли он на компьютере и нужна ли ему модель. */
export function agentInfo(view) {
  const list = view && Array.isArray(view.agents) ? view.agents : [];
  const entry = list.find((item) => item.name === view.agent);
  if (entry) return {name: entry.name, label: entry.label, found: !!entry.found, needsModel: !!entry.needs_model};
  return {name: "claude", label: "Claude Code", found: !!(view && view.claude_found), needsModel: false};
}

/* Состояние связи с агентом для индикатора: tone = off | ok | run | wait | bad. */
export function agentStatus(view) {
  if (!view) return {tone: "off", text: "нет связи со студией", connected: false};
  const info = agentInfo(view);
  if (!info.found) return {tone: "bad", text: `${info.label} не найден`, connected: false};
  const mcp = view.mcp || {};
  if (mcp.ok === false && mcp.code && mcp.code !== "no_config") {
    // MCP-сервер не отвечает на initialize: агент остался бы без инструментов studio_*.
    const why = String(mcp.reason || "").split("\n")[0];
    return {tone: "bad", text: `MCP не запускается: ${why}`.slice(0, 120), connected: false};
  }
  if (info.needsModel && !view.model) return {tone: "off", text: "выберите модель", connected: false};
  if (!view.enabled) return {tone: "off", text: "не подключён", connected: false};
  const last = String(view.message || "").trim().split("\n").pop();
  switch (view.state) {
    case "queued":
    case "running":
      return {tone: "run", text: "работает", connected: true};
    case "waiting_user":
      return {tone: "wait", text: "ждёт вашего ответа", connected: true};
    case "failed":
      return {tone: "bad", text: `сбой: ${last}`.slice(0, 120), connected: true};
    case "stalled":
      return {tone: "bad", text: "завершился, этап не продвинулся", connected: true};
    default:
      // «Готов» только после успешного рукопожатия (initialize) с MCP-сервером студии.
      if (mcp.ok !== true) return {tone: "wait", text: "проверяю связь с MCP…", connected: true};
      return {tone: "ok", text: "готов, в простое токены не тратит", connected: true};
  }
}

/* Что делает человек сейчас: кнопки окна. id связан с обработчиками в roadmapview.js. */
export function dockActions(flow, view) {
  const status = agentStatus(view);
  if (!flow) return [];
  if (!status.connected) return [{id: "settings", label: "Подключить агента", primary: true}];
  const cards = flow.cards || {};
  const busy = status.tone === "run";
  const out = [];
  switch (activeDockStep(flow)) {
    case "goal":
      out.push({id: "start", label: "Начать", primary: true});
      break;
    case "ideas":
      if (status.tone === "bad") out.push({id: "retry", label: "Запустить агента снова", primary: true});
      else if (cards.total > 0 && !busy) out.push({id: "to-review", label: "К разбору карточек", primary: true});
      break;
    case "review":
      if (cards.pending > 0) {
        out.push({id: "goto-ideas", label: `Разобрать карточки (${cards.pending})`, primary: true});
      } else {
        out.push({id: "to-select", label: "Дальше: выбор задач", primary: true});
        out.push({id: "goto-ideas", label: "К карточкам"});
      }
      break;
    case "select":
      out.push({id: "select", label: "Выбрать задачи и отдать агенту", primary: true});
      break;
    case "implement":
      if (status.tone === "bad") out.push({id: "retry", label: "Запустить агента снова", primary: true});
      break;
    case "verify":
      break;
    case "accept":
      out.push({id: "report", label: "Открыть отчёт"});
      out.push({id: "rework", label: "Вернуть на доработку"});
      out.push({id: "finish", label: "Принять", primary: true});
      break;
    default:
      break;
  }
  return out;
}

/* Короткая подсказка под шагами: что происходит и что делать. */
export function dockNote(flow, view) {
  const status = agentStatus(view);
  if (!status.connected) return "Подключите агента на странице «Настройки агента» — дальше всё идёт само.";
  if (status.tone === "wait") return "Агент задал вопрос и ждёт ответа — ответьте ниже.";
  const cards = (flow && flow.cards) || {};
  switch (activeDockStep(flow)) {
    case "goal":
      return "Опишите цель и нажмите «Начать»: студия сама запустит агента.";
    case "ideas":
      return status.tone === "run"
        ? "Агент готовит идеи со сценариями. Страницу можно не открывать."
        : `Карточек от агента: ${cards.total || 0}.`;
    case "review":
      return cards.pending > 0
        ? "На странице «Генератор идей» свайпните карточки: вверх — на карту, вниз — удалить."
        : "Все карточки разобраны. Можно переходить к выбору задач.";
    case "select":
      return "Отметьте, что отдать агенту на реализацию.";
    case "implement":
      return status.tone === "run"
        ? "Агент пишет код по ТЗ. После него студия сделает чекпоинт в git."
        : "Ждём завершения работы агента.";
    case "verify":
      return "Студия прогоняет тесты проекта.";
    case "accept":
      return "Агент прислал отчёт. Примите работу или верните на доработку.";
    default:
      return "";
  }
}

/* Откат доступен, если студия записала точку возврата в ветке studio/*. */
export function canRollback(view) {
  return !!(view && view.git && view.git.can_rollback);
}

/* Короткая строка про git для окна: ветка, чекпоинт или причина, почему его нет. */
export function gitNote(view) {
  const git = view && view.git;
  if (!git || !git.repo) return "git: проект вне репозитория — откат недоступен";
  if (view.git_note) return view.git_note;
  if (git.checkpoint) return `git: чекпоинт на ветке ${git.branch}${git.pushed ? " (отправлен на GitHub)" : ""}`;
  return `git: ветка ${git.branch}`;
}
