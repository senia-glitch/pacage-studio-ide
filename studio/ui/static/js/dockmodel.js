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

/* Настройки цикла на шаге «Цель». Предел итераций совпадает с серверным (MAX_ITERATIONS). */
export const MAX_ITERATIONS = 5;
export const GOAL_DEFAULTS = {text: "", skipIdeas: false, iterations: 1, autoDecide: false, verify: false, tests: false};

/* Тело запроса «Начать»: без идей итерации и автоприём не нужны, проверки остаются. */
export function startPayload(draft) {
  const skip = !!draft.skipIdeas;
  const iterations = Math.min(MAX_ITERATIONS, Math.max(1, Math.floor(Number(draft.iterations)) || 1));
  return {
    mode: skip ? "implement" : "plan",
    task: skip ? "" : String(draft.text || "").trim(),
    options: {
      iterations: skip ? 1 : iterations,
      auto_decide: skip ? false : !!draft.autoDecide,
      verify: !!draft.verify,
      tests: !!draft.tests,
    },
  };
}

/* Что включено в цикле, одной строкой (для заметки окна). */
export function optionsSummary(options) {
  const o = options || {};
  const parts = [];
  if (o.iterations > 1) parts.push(`идей: ${o.iterations} итерации`);
  if (o.auto_decide) parts.push("карточки решает агент");
  if (o.verify) parts.push("проверка сценариев");
  if (o.tests) parts.push("усиленные тесты");
  return parts.join(" · ");
}

/* Студия сама ведёт идеи (итерации и/или автоприём): кнопка «К разбору» человеку не нужна. */
export function ideasAutomatic(flow) {
  const o = (flow && flow.options) || {};
  return !!(o.iterations > 1 || o.auto_decide);
}

const UNIT_STEPS = {
  implement: "реализация", verify: "проверка", tests: "тесты",
  "fix-verify": "исправление замечаний", "fix-tests": "исправление после тестов",
};

/* Сценарии реализации с отметкой: сделан (done), с замечаниями (issues), текущий (current), ждёт (todo). */
export function unitRows(flow) {
  const p = (flow && flow.pipeline) || {};
  const units = Array.isArray(p.units) ? p.units : [];
  return units.map((unit, index) => {
    const current = flow.stage === "implement" && !p.final && index === p.unit;
    const state = current ? "current" : unit.state === "done" || unit.state === "issues"
      ? unit.state : index < p.unit || p.final ? "done" : "todo";
    return {
      title: unit.title || unit.id, state,
      step: current ? UNIT_STEPS[p.step] || "" : "",
      remarks: [...Object.values(unit.checks || {})].flatMap((check) => (check.ok ? [] : check.remarks || [])),
    };
  });
}

/* Где автоматический ход сейчас: итерация идей, решение карточек, сценарий и его шаг. */
export function pipelineNote(flow) {
  const o = (flow && flow.options) || {};
  const p = (flow && flow.pipeline) || {};
  if (!flow) return "";
  if (flow.stage === "ideas" && ideasAutomatic(flow)) {
    return p.phase === "decide"
      ? "Контроль принимает и отклоняет карточки идей."
      : `Агент готовит идеи: итерация ${p.iteration || 1} из ${o.iterations || 1}.`;
  }
  const units = Array.isArray(p.units) ? p.units : [];
  if (flow.stage === "implement" && units.length) {
    if (p.final) return "Все сценарии готовы: агент готовит итоговый отчёт.";
    const unit = units[p.unit] || {};
    return `Сценарий ${(p.unit || 0) + 1} из ${units.length} «${unit.title || ""}»: ${UNIT_STEPS[p.step] || ""}.`;
  }
  return "";
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
      else if (cards.total > 0 && !busy && !ideasAutomatic(flow)) {
        out.push({id: "to-review", label: "К разбору карточек", primary: true});
      }
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
  const step = activeDockStep(flow);
  if (status.tone === "bad" && view.state === "stalled" && view.message) return String(view.message);
  const running = pipelineNote(flow);
  if (running && (step === "ideas" || step === "implement")) return running;
  switch (step) {
    case "goal":
      return "Опишите цель, отметьте нужные автоматические шаги и нажмите «Начать»: студия сама запустит агента.";
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
      hint: item.hint && item.hint.cause
        ? {cause: String(item.hint.cause), fix: String(item.hint.fix || ""), where: String(item.hint.where || "")} : null,
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
