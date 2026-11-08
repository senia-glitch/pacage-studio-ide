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

/* Два исполнителя: кто строит план и кто реализует. Без поля roles (старый ответ) — один агент на оба. */
export function roleViews(view) {
  if (view && view.roles && view.roles.plan && view.roles.implement) {
    return ["plan", "implement"].map((role) => {
      const item = view.roles[role];
      return {
        role, title: item.title || (role === "plan" ? "План" : "Реализация"), label: item.label,
        model: item.model || "", found: item.found !== false, needsModel: !!item.needs_model,
        reason: item.reason || "",
      };
    });
  }
  const info = agentInfo(view);
  const model = (view && view.model) || "";
  return ["plan", "implement"].map((role) => ({
    role, title: role === "plan" ? "План" : "Реализация", label: info.label, model,
    found: info.found, needsModel: info.needsModel, reason: "",
  }));
}

/* Название исполнителей для заголовка окна: один агент или «План: A · Реализация: B». */
export function agentTitle(view) {
  const [plan, implement] = roleViews(view);
  return plan.label === implement.label
    ? plan.label : `${plan.title}: ${plan.label} · ${implement.title}: ${implement.label}`;
}

/* Состояние связи с агентом для индикатора: tone = off | ok | run | wait | bad. */
export function agentStatus(view) {
  if (!view) return {tone: "off", text: "нет связи со студией", connected: false};
  const roles = roleViews(view);
  const lost = roles.find((item) => !item.found);
  if (lost) return {tone: "bad", text: `${lost.label} не найден`, connected: false};
  const mcp = view.mcp || {};
  if (mcp.ok === false && mcp.code && mcp.code !== "no_config") {
    // MCP-сервер не отвечает на initialize: агент остался бы без инструментов studio_*.
    const why = String(mcp.reason || "").split("\n")[0];
    return {tone: "bad", text: `MCP не запускается: ${why}`.slice(0, 120), connected: false};
  }
  const unset = roles.find((item) => item.needsModel && !item.model);
  if (unset) {
    const both = roles.filter((item) => item.needsModel && !item.model).length > 1;
    return {tone: "off", text: both ? "выберите модели" : `выберите модель (${unset.title})`, connected: false};
  }
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

/* Серверы проекта (бэкенд и фронтенд): строки для блока «Проект» окна агента. */
const SERVER_TEXT = {stopped: "остановлен", starting: "запускается…", running: "работает", failed: "сбой"};

export function serverRows(data) {
  const list = data && Array.isArray(data.servers) ? data.servers : [];
  return list.map((item) => {
    const state = SERVER_TEXT[item.state] ? item.state : "stopped";
    const live = state === "starting" || state === "running";
    const available = item.available !== false;
    return {
      name: item.name,
      title: item.title || item.name,
      state,
      text: item.message || (!available && state === "stopped" ? item.reason || "не найден" : SERVER_TEXT[state]),
      url: state === "running" ? item.url || "" : "",
      note: item.note || "",
      tail: Array.isArray(item.tail) ? item.tail : [],
      available,
      canStart: available && !live,
      canStop: live,
    };
  });
}

/* Главная кнопка блока: запустить проект целиком (бэкенд, затем фронтенд) или остановить всё. */
export function projectButton(data) {
  const rows = serverRows(data);
  if (!rows.length) return null;
  if (rows.some((row) => row.canStop) || (data && data.waiting)) return {id: "stop-all", label: "■ Остановить всё"};
  if (rows.some((row) => row.available)) return {id: "start-all", label: "▶ Запустить проект"};
  return null;
}

/* Опрос чаще, пока что-то запускается: ссылка появляется, как только сервер ответил. */
export function serversBusy(data) {
  return !!(data && (data.waiting || (data.servers || []).some((item) => item.state === "starting")));
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
