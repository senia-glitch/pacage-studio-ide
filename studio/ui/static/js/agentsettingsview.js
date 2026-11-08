import {api} from "./api.js";
import {$, esc, toast} from "./core.js";
import {agentStatus, gitNote, roleViews} from "./dockmodel.js";

/* Страница «Настройки агента»: кто строит план и кто реализует (агент и модель), подключение, права, git, запуск и инструкции
   человеческим языком. Агент работает в фоне: студия запускает его на ваш шаг. */

let loading = false;

async function load() {
  const [view, runner, tests, servers] = await Promise.all([
    api("/api/agent/status"),
    api("/api/runner"),
    api("/api/tests").catch(() => null),
    api("/api/devservers").catch(() => null),
  ]);
  return {view, runner, tests, servers};
}

const EXE = {claude: "claude", opencode: "opencode"};
/* Claude Code: модели, которые входят в подписку; Fable требует отдельных кредитов использования. */
const CLAUDE_MODELS = [
  {id: "opus", name: "Opus", tier: "subscription"},
  {id: "sonnet", name: "Sonnet", tier: "subscription"},
  {id: "haiku", name: "Haiku", tier: "subscription"},
  {id: "fable", name: "Fable", tier: "paid"},
];
const CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"];
const EFFORT_LABEL = {
  none: "Без рассуждений", minimal: "Минимальный", low: "Низкий", medium: "Средний", high: "Высокий",
  xhigh: "Сверхвысокий", max: "Максимальный", thinking: "С размышлением", ultracode: "Ultracode",
};
const TIER_NOTE = {free: " · бесплатная", paid: " · платная (кредиты)", subscription: ""};
const PAID_KEY = "studio:show-paid-models";
let showPaid = (() => { try { return localStorage.getItem(PAID_KEY) === "1"; } catch (_) { return false; } })();
let catalog = null; // ответ /api/agent/models?agent=opencode

const ROLE_HELP = {
  plan: "изучает проект, присылает идеи и карточки сценариев",
  implement: "пишет код по выбранным задачам и присылает отчёт",
  check: "принимает карточки, проверяет сценарии и пишет усиленные тесты (если включены на шаге «Цель»)",
};

function agentList(view) {
  return Array.isArray(view.agents) && view.agents.length
    ? view.agents : [{name: "claude", label: "Claude Code", found: !!view.claude_found}];
}

/* Уровни усилий: у Claude Code одинаковый набор, у OpenCode — тот, что умеет выбранная модель. */
function effortOptions(levels, current) {
  const list = levels.includes(current) || !current ? levels : [...levels, current];
  return `<option value="">по умолчанию</option>${list.map((level) => `<option value="${esc(level)}"${level === current ? " selected" : ""}>${esc(EFFORT_LABEL[level] || level)}</option>`).join("")}`;
}

/* Исполнитель одной роли: агент, модель (OpenCode — из подписки, Claude Code — из коротких имён) и уровень усилий. */
function rolePanel(role, item, view) {
  const options = agentList(view).map((entry) => `<option value="${esc(entry.name)}"${entry.name === item.agent ? " selected" : ""}>${esc(entry.label)}${entry.found ? "" : " (не найден)"}</option>`).join("");
  const opencode = item.agent === "opencode";
  const model = opencode
    ? `<label class="rm-task">Модель из вашей подписки
        <select data-role-model="${role}"><option value="">— выберите модель —</option>${item.model ? `<option value="${esc(item.model)}" selected>${esc(item.model)}</option>` : ""}</select></label>
       <label class="rm-task">Уровень усилий
        <select data-role-effort="${role}">${effortOptions([], item.variant || "")}</select></label>`
    : `<label class="rm-task">Модель
        <input data-role-model="${role}" list="claudeModels" value="${esc(item.model || "")}" placeholder="по умолчанию Claude Code" size="26"></label>
       <label class="rm-task">Уровень усилий
        <select data-role-effort="${role}">${effortOptions(CLAUDE_EFFORTS, item.variant || "")}</select></label>`;
  return `<div class="role-panel" data-role="${role}">
    <h4>${esc(item.title)} <small>— ${esc(ROLE_HELP[role])}</small></h4>
    <div class="agent-row"><label class="rm-task">Агент <select data-role-agent="${role}">${options}</select></label></div>
    ${model}
    <p class="hint" data-role-hint="${role}">${esc(item.reason || (item.inherited ? "Пока свой не выбран, контролёр повторяет «Реализацию». Лучше выбрать другую модель: чужой взгляд находит больше." : opencode ? "Список строит команда opencode models: только провайдеры, в которые вы вошли (opencode auth login)." : "Доступные уровни усилий зависят от модели."))}</p>
  </div>`;
}

function claudeDatalist() {
  const list = CLAUDE_MODELS.filter((item) => showPaid || item.tier !== "paid");
  return `<datalist id="claudeModels">${list.map((item) => `<option value="${item.id}" label="${esc(item.name + (TIER_NOTE[item.tier] || ""))}">`).join("")}</datalist>`;
}

function rolesBlock(view) {
  const roles = view.roles || {};
  const titles = {plan: "План", implement: "Реализация", check: "Контроль"};
  const slots = ["plan", "implement", "check"].map((role) => ({
    ...(roles[role] || {agent: view.agent || "claude", model: view.model || "", variant: view.variant || "", title: titles[role]}),
  }));
  const refresh = slots.some((slot) => slot.agent === "opencode")
    ? '<button type="button" id="modelsRefresh">Обновить список моделей</button>' : "";
  return `${claudeDatalist()}
    <div class="role-grid">${rolePanel("plan", slots[0], view)}${rolePanel("implement", slots[1], view)}${rolePanel("check", slots[2], view)}</div>
    <div class="agent-row">${refresh}
      <label class="rm-check"><input type="checkbox" id="showPaid"${showPaid ? " checked" : ""}>
        <span><b>Показывать платные модели</b><small>оплата по токенам или отдельные кредиты; по умолчанию в списке только бесплатные и входящие в подписку</small></span></label></div>`;
}

function connectionCard(view) {
  const status = agentStatus(view);
  const roles = roleViews(view);
  const names = [...new Set(roles.map((item) => item.label))];
  const exes = [...new Set(agentList(view).filter((entry) => roles.some((item) => item.label === entry.label)).map((entry) => EXE[entry.name] || entry.name))];
  const lines = roles.map((item) => `<li><b>${esc(item.title)}:</b> ${esc(item.label)}${item.model ? ` · ${esc(item.model)}` : ""} — ${item.found ? (item.reason ? esc(item.reason) : "готов к запуску") : `не найден (команда <code>${esc((exes[0] || ""))}</code> должна запускаться в терминале)`}</li>`).join("");
  return `<section class="agent-card" id="agentConn">
    <h3>Исполнители</h3>
    <p class="hint">План (идеи, карточки), реализацию кода и контроль (автоприём карточек, проверка и тесты сценариев) можно отдать разным агентам и моделям: например, анализ — Claude Code, код — OpenCode. Можно оставить одного агента на все роли.</p>
    ${rolesBlock(view)}
    <p><span class="dock-dot ${esc(status.tone)}"></span> <b>${esc(names.join(" + "))}</b> — ${esc(status.text)}</p>
    <ul class="agent-list">${lines}</ul>
    ${mcpBlock(view.mcp)}
    <p class="hint">${view.venv
      ? `Окружение проекта: <code>${esc(view.venv)}</code>. Агент и тесты запускаются в нём, поэтому видят зависимости проекта.`
      : "Окружение проекта (<code>.venv</code> или <code>venv</code>) не найдено: агент запускается в окружении студии и может не увидеть зависимости проекта."}</p>
    <div class="agent-row">
      <button type="button" class="primary" id="agentConnect">${view.enabled ? "Переподключить" : (names.length > 1 ? "Подключить агентов" : `Подключить ${esc(names[0])}`)}</button>
      <button type="button" id="agentCheck">Проверить связь</button>
      ${view.enabled ? '<button type="button" id="agentDisconnect">Отключить</button>' : ""}
      <button type="button" id="agentManual">Строка подключения вручную…</button>
    </div>
    <p class="hint" id="agentCheckResult">«Подключить» записывает конфигурацию в <code>.studio/</code> (файлы вашего проекта не трогаются) и включает автозапуск. «Проверить связь» запускает <code>--version</code> каждого выбранного агента и выполняет рукопожатие <code>initialize</code> с MCP-сервером студии (теми же командой и окружением, что получит агент); токены не тратятся. Пока оба исполнителя не готовы, «Начать» в окне агента недоступно.</p>
  </section>`;
}

/* Итог рукопожатия с MCP-сервером: причина сбоя и готовая команда исправления. */
function mcpBlock(mcp) {
  if (!mcp || mcp.ok === null || mcp.ok === undefined) return "";
  if (mcp.ok) return `<p class="hint">MCP-сервер студии отвечает на <code>initialize</code>: агент получит инструменты <code>studio_*</code>.</p>`;
  if (mcp.code === "no_config") return "";
  const command = mcp.fix_command
    ? `<pre class="agent-fix">${esc(mcp.fix_command)}</pre>` : "";
  return `<div class="agent-error"><p><b>MCP-сервер не запускается.</b> ${esc(mcp.reason || "")}</p>
    ${mcp.fix ? `<p>${esc(mcp.fix)}</p>` : ""}${command}</div>`;
}

function howCard() {
  return `<section class="agent-card">
    <h3>Как это работает</h3>
    <p>Агент не сидит в чате и не ждёт: студия сама запускает выбранного агента в фоне, когда наступает его ход, и он завершается, сделав шаг. Пока вы ничего не нажимаете, агент не запущен и токены не расходуются. Пользоваться чатом агента не нужно.</p>
    <div class="agent-scenario"><b>1. Цель.</b> В окне агента (справа внизу) опишите, что хотите, и нажмите «Начать». Студия запустит агента.</div>
    <div class="agent-scenario"><b>2. Идеи.</b> Агент изучает проект и кладёт на страницу «Генератор идей» карточки с пометкой «агент».</div>
    <div class="agent-scenario"><b>3. Разбор — ваш ход.</b> Свайпайте карточки прямо на странице: вверх — отправить на карту, вниз — удалить. Затем в окне агента нажмите «Дальше».</div>
    <div class="agent-scenario"><b>4. Выбор задач — ваш ход.</b> Отметьте, что отдать агенту в реализацию. Студия сама соберёт ТЗ и запустит агента.</div>
    <div class="agent-scenario"><b>5. Реализация и проверка.</b> Агент пишет код, студия сохраняет чекпоинт в git и прогоняет тесты.</div>
    <div class="agent-scenario"><b>6. Приёмка — ваш ход.</b> Прочитайте отчёт: «Принять», «Вернуть на доработку» (агент учтёт ваше пожелание) или «Откат» (проект вернётся к состоянию до работы агента).</div>
    <div class="agent-scenario"><b>Если агент спросил.</b> Вопрос появится в окне агента. Ответьте — студия запустит агента снова с тем же контекстом.</div>
    <div class="agent-scenario"><b>Режимы сверху.</b> «Агент режим» — всё описанное выше. «Ручной режим» — без агента: вы сами собираете промпт, копируете его в любой чат и вставляете ответ обратно.</div>
  </section>`;
}

function manualCard(view) {
  const manual = roleViews(view).some((item) => item.label === "OpenCode") && !roleViews(view).some((item) => item.label === "Claude Code")
    ? `<p>Студия кладёт MCP и права OpenCode в свой файл <code>.studio/opencode.json</code> и передаёт его через <code>OPENCODE_CONFIG</code>; <code>opencode.json</code> проекта она не трогает. Чтобы подключить вручную, добавьте в свой конфиг OpenCode (<code>opencode mcp add</code>) локальный сервер <code>studio mcp</code> с переменной <code>STUDIO_PROJECT</code> и выполните <code>studio skill --target all</code>.</p>`
    : `<p>Те же шаги в терминале, из папки проекта:</p>
    <pre>claude mcp add pacage-studio -- studio mcp
studio skill --target all</pre>`;
  return `<section class="agent-card">
    <h3>Подключение вручную (если не хотите кнопку)</h3>
    ${manual}
    <p>Затем запустите агента в папке проекта и скажите ему: <i>«Работай со студией: вызови studio_connect и studio_workflow»</i>. Так агент будет работать в вашем окне, но студия не сможет будить его сама: автоматизация работает, когда студия запускает агента в фоне.</p>
    <p class="hint">Что умеет агент (MCP-инструменты): читать карту и фронтенд, присылать идеи (<code>studio_idea_add</code>), читать ТЗ (<code>studio_brief</code>), проверять (<code>studio_verify</code>, <code>studio_doctor</code>), отправлять отчёт (<code>studio_report</code>) и задавать вам вопросы (<code>studio_ask</code>).</p>
  </section>`;
}

function rightsCard(view) {
  const denied = (view.denied_tools || []).map((item) => `<li><code>${esc(item)}</code></li>`).join("");
  return `<section class="agent-card">
    <h3>Права агента</h3>
    <p>Агент запускается в корне проекта и свободно правит файлы и запускает тесты внутри задач. Всегда запрещено ему:</p>
    <ul class="agent-list">${denied}</ul>
    <p class="hint">Историю и GitHub ведёт студия, а не агент. Честная оговорка: настоящей песочницы, запрещающей выход за корень проекта, на Windows у Claude Code и OpenCode нет — рамки держатся на правах доступа и запретах выше. Полный список разрешений — в команде запуска (раздел «Запуск»).</p>
  </section>`;
}

function gitCard(view) {
  const git = (view.settings && view.settings.git) || {};
  const status = view.git || {};
  return `<section class="agent-card">
    <h3>Git и GitHub</h3>
    <p class="hint">${esc(gitNote(view))}${status.repo && status.dirty_count ? ` · несохранённых файлов: ${status.dirty_count}` : ""}</p>
    <label class="rm-check"><input type="checkbox" id="gitCheckpoints"${git.checkpoints ? " checked" : ""}>
      <span><b>Чекпоинт после работы агента</b><small>отдельная ветка <code>studio/c&lt;цикл&gt;</code>, можно откатить. Не создаётся, пока в проекте есть ваши несохранённые изменения.</small></span></label>
    <label class="rm-check"><input type="checkbox" id="gitPush"${git.push ? " checked" : ""}>
      <span><b>Отправлять чекпоинт на GitHub</b><small>через <code>git push</code> с вашими учётными данными git; студия токены не хранит</small></span></label>
    <label class="rm-task">Имя remote <input id="gitRemote" value="${esc(git.remote || "origin")}" size="16"></label>
    <div class="agent-row"><button type="button" class="primary" id="gitSave">Сохранить</button>
      ${status.can_rollback ? '<button type="button" id="gitRollback">↩ Откатить работу агента</button>' : ""}</div>
  </section>`;
}

const AGENT_LABEL = {claude: "Claude Code", opencode: "OpenCode"};
const SERVER_DEFAULT_PORT = {backend: 8000, frontend: 5173};
const NEWLINE = String.fromCharCode(10);

/* Серверы самого проекта (кнопки в окне агента): команды определяются сами, здесь их можно заменить. */
function serversCard(servers) {
  if (!servers || !Array.isArray(servers.servers)) return "";
  const config = servers.config || {};
  const rows = servers.servers.map((item) => {
    const own = config[item.name] || {};
    return `<div class="role-cmd" data-srv-name="${esc(item.name)}">
      <p class="hint"><b>${esc(item.title)}.</b> ${item.custom ? "Своя команда." : item.available ? "Определено автоматически:" : esc(item.reason || "не найден")}
        ${item.custom || !item.available ? "" : `<code>${esc(item.command)}</code>`}</p>
      <textarea rows="4" spellcheck="false" style="width:100%" placeholder="Пусто — автоопределение. По аргументу на строку; {port} — порт.">${esc((own.command || []).join(NEWLINE))}</textarea>
      <label class="rm-task">Предпочтительный порт
        <input type="number" min="1024" max="65535" data-srv-port placeholder="${SERVER_DEFAULT_PORT[item.name]}" value="${esc(own.port || "")}" style="width:90px"></label></div>`;
  }).join("");
  return `<section class="agent-card">
    <h3>Серверы проекта</h3>
    <p>Кнопки «▶» в окне агента запускают бэкенд (<code>uvicorn … --reload</code>), а после него фронтенд (<code>npm run dev</code> в <code>frontend/</code>). Занятый порт заменяется ближайшим свободным, ссылка берётся из вывода сервера, а при закрытии студии серверы останавливаются.</p>
    <details><summary>Команды и порты (для опытных)</summary>${rows}</details>
    <div class="agent-row"><button type="button" class="primary" id="srvSave">Сохранить</button></div>
  </section>`;
}

function runCard(runner, tests, view) {
  const testsOn = !!(tests && tests.config && tests.config.enabled);
  const roles = (runner && runner.roles) || {};
  const used = [...new Set(["plan", "implement", "check"].map((role) => (roles[role] || {}).agent || (view && view.agent) || "claude"))];
  return `<section class="agent-card">
    <h3>Запуск</h3>
    <label class="rm-task">Предел одного запуска, минут
      <input id="runTimeout" type="number" min="1" max="240" value="${esc(runner.timeout_minutes)}"></label>
    <label class="rm-check"><input type="checkbox" id="runTests"${testsOn ? " checked" : ""}>
      <span><b>Запускать тесты проекта, когда агент прислал отчёт</b>
      <small>независимая проверка; команда — <code>.studio/tests.json</code> (по умолчанию pytest)</small></span></label>
    <details><summary>Команда запуска (для опытных)</summary>
      ${used.map((name) => `<div class="role-cmd" data-cmd-agent="${esc(name)}">
        <p class="hint"><b>${esc(AGENT_LABEL[name] || name)}.</b> По аргументу на строку; <code>{prompt}</code> — текст запроса агенту. ${name === "opencode"
          ? "<code>--model</code>, <code>--variant</code> и <code>--session</code> студия добавляет сама, а MCP и права передаёт через <code>OPENCODE_CONFIG</code>."
          : "<code>--output-format</code>, <code>--resume</code>, <code>--model</code> и <code>--mcp-config</code> студия добавляет сама."}</p>
        <textarea rows="10" spellcheck="false" style="width:100%">${esc(((runner.commands || {})[name] || runner.command || []).join("\n"))}</textarea>
        <div class="agent-row"><button type="button" data-cmd-default="${esc(name)}">Вернуть стандартную</button></div></div>`).join("")}
    </details>
    <div class="agent-row"><button type="button" class="primary" id="runSave">Сохранить</button></div>
  </section>`;
}

function bind(root, data) {
  const reload = () => renderAgentSettings();
  const run = async (button, job) => {
    button.disabled = true;
    try { await job(); } catch (error) { toast(error.message); }
    button.disabled = false;
  };
  const on = (id, job) => { const el = root.querySelector(id); if (el) el.onclick = () => run(el, job); };
  const patchRole = async (role, change, message) => {
    try {
      await api("/api/runner/config", "POST", {roles: {[role]: change}});
      if (message) toast(message);
    } catch (error) { toast(error.message); }
  };
  root.querySelectorAll("[data-role-agent]").forEach((el) => {
    el.onchange = async () => {
      await patchRole(el.dataset.roleAgent, {agent: el.value}, "Агент выбран. Подключите: у каждого агента свои модель и подключение");
      await reload();
    };
  });
  root.querySelectorAll("[data-role-model]").forEach((el) => {
    el.onchange = async () => {
      const role = el.dataset.roleModel;
      const change = {model: el.value.trim()};
      const effort = root.querySelector(`[data-role-effort="${role}"]`);
      if (el.tagName === "SELECT" && effort) {
        // уровень усилий — свойство модели: непригодный для новой модели сбрасываем
        const item = catalogItem(el.value);
        if (effort.value && !(item && item.variants.includes(effort.value))) change.variant = "";
      }
      await patchRole(role, change, change.model ? "Модель сохранена" : "Модель сброшена");
      await reload();
    };
  });
  root.querySelectorAll("[data-role-effort]").forEach((el) => {
    el.onchange = () => patchRole(el.dataset.roleEffort, {variant: el.value}, "Уровень усилий сохранён");
  });
  const paid = root.querySelector("#showPaid");
  if (paid) paid.onchange = () => {
    showPaid = paid.checked;
    try { localStorage.setItem(PAID_KEY, showPaid ? "1" : "0"); } catch (_) { /* без хранилища */ }
    reload();
  };
  if (root.querySelector("select[data-role-model]")) loadModels(root, data.view);
  on("#modelsRefresh", async () => { await loadModels(root, data.view, true); });
  on("#agentConnect", async () => {
    const view = await api("/api/agent/connect", "POST", {});
    const unset = roleViews(view).find((item) => item.needsModel && !item.model);
    toast(view && view.enabled
      ? "Агенты подключены: студия будет запускать их на ваши шаги"
      : unset
        ? `Выберите модель (${unset.title}) и подключите ещё раз`
        : "Агенты не подключены: MCP-сервер студии не отвечает (причина и команда ниже)");
    await reload();
  });
  on("#agentCheck", async () => {
    const result = await api("/api/agent/check", "POST", {});
    const box = root.querySelector("#agentCheckResult");
    const parts = Object.values(result.roles || {}).map((item) => item.ok
      ? `${item.label}${item.model ? ` (${item.model})` : ""} ${item.version || ""}${item.providers && item.providers.length ? `, вход: ${item.providers.join(", ")}` : ""}`.trim()
      : `${item.title || item.role}: ${item.error}`);
    box.textContent = result.ok
      ? `Связь есть. ${parts.join("; ")}. MCP-сервер ответил на initialize.${result.mcp_config ? "" : " MCP-конфиг ещё не записан — нажмите «Подключить»."}`
      : `Проблема: ${result.error}`;
    await reload();
  });
  on("#agentDisconnect", async () => {
    await api("/api/runner/config", "POST", {enabled: false});
    toast("Агент отключён: студия больше не запускает его сама");
    await reload();
  });
  on("#agentManual", async () => { const manual = $("agentBtn"); if (manual) manual.click(); });
  on("#gitSave", async () => {
    await api("/api/ui", "POST", {git: {
      checkpoints: root.querySelector("#gitCheckpoints").checked,
      push: root.querySelector("#gitPush").checked,
      remote: root.querySelector("#gitRemote").value.trim() || "origin",
    }});
    toast("Настройки git сохранены");
    await reload();
  });
  on("#gitRollback", async () => {
    if (!confirm("Вернуть проект к состоянию до работы агента? Его правки и новые файлы исчезнут.")) return;
    await api("/api/git/rollback", "POST", {});
    toast("Проект возвращён к состоянию до работы агента");
    await reload();
  });
  root.querySelectorAll("[data-cmd-default]").forEach((el) => {
    el.onclick = () => {
      const name = el.dataset.cmdDefault;
      const box = root.querySelector(`[data-cmd-agent="${name}"] textarea`);
      const commands = data.runner.default_commands || {};
      if (box) box.value = (commands[name] || data.runner.default_command || []).join("\n");
    };
  });
  on("#srvSave", async () => {
    const payload = {};
    root.querySelectorAll("[data-srv-name]").forEach((el) => {
      const port = el.querySelector("[data-srv-port]").value.trim();
      payload[el.dataset.srvName] = {command: el.querySelector("textarea").value, port: port ? Number(port) : null};
    });
    await api("/api/devservers/config", "POST", payload);
    toast("Настройки серверов проекта сохранены");
    await reload();
  });
  on("#runSave", async () => {
    const commands = {};
    root.querySelectorAll("[data-cmd-agent]").forEach((el) => { commands[el.dataset.cmdAgent] = el.querySelector("textarea").value; });
    await api("/api/runner/config", "POST", {
      commands,
      timeout_minutes: Number(root.querySelector("#runTimeout").value),
    });
    await api("/api/tests/config", "POST", {enabled: root.querySelector("#runTests").checked});
    toast("Настройки запуска сохранены");
    await reload();
  });
}

function catalogItem(id) {
  return catalog && Array.isArray(catalog.items) ? catalog.items.find((item) => item.id === id) : null;
}

/* Подписочные модели: список спрашиваем у OpenCode и кладём в выпадающие списки ролей.
   Платные (по токенам) скрыты, пока не включён переключатель; выбранная модель остаётся видна. */
async function loadModels(root, view, force = false) {
  const selects = [...root.querySelectorAll("select[data-role-model]")];
  const refresh = root.querySelector("#modelsRefresh");
  if (!selects.length) return;
  if (refresh) refresh.disabled = true;
  const hints = [...root.querySelectorAll("[data-role-hint]")].filter((el) => root.querySelector(`select[data-role-model="${el.dataset.roleHint}"]`));
  try {
    if (!catalog || force) catalog = await api(`/api/agent/models?agent=opencode${force ? "&refresh=1" : ""}`);
    const items = catalog.items || [];
    for (const select of selects) {
      const current = select.value || "";
      const shown = items.filter((item) => showPaid || item.tier !== "paid" || item.id === current);
      const groups = new Map();
      for (const item of shown) {
        if (!groups.has(item.provider)) groups.set(item.provider, []);
        groups.get(item.provider).push(item);
      }
      const known = new Set(shown.map((item) => item.id));
      const extra = current && !known.has(current) ? `<option value="${esc(current)}" selected>${esc(current)} (нет в списке подписки)</option>` : "";
      select.innerHTML = `<option value="">— выберите модель —</option>${extra}`
        + [...groups].map(([provider, list]) => `<optgroup label="${esc(provider)}">${list.map((item) => `<option value="${esc(item.id)}"${item.id === current ? " selected" : ""}>${esc(item.name + (TIER_NOTE[item.tier] || ""))}</option>`).join("")}</optgroup>`).join("");
      // уровни усилий выбранной модели
      const role = select.dataset.roleModel;
      const effort = root.querySelector(`[data-role-effort="${role}"]`);
      if (effort) {
        const levels = (catalogItem(current) || {variants: []}).variants;
        const now = effort.value;
        effort.innerHTML = effortOptions(levels, now);
        effort.disabled = !!current && !levels.length;
        effort.title = effort.disabled ? "У этой модели нет настраиваемых уровней" : "";
      }
    }
    for (const hint of hints) {
      if (catalog.error) hint.textContent = catalog.error;
      else if (!hint.textContent.startsWith("Выберите")) {
        const hidden = showPaid ? 0 : items.filter((item) => item.tier === "paid").length;
        hint.textContent = `Моделей в списке: ${items.length - hidden}${hidden ? ` (платных по токенам скрыто: ${hidden})` : ""}.`;
      }
    }
  } catch (error) {
    for (const hint of hints) hint.textContent = `Не удалось получить список моделей: ${error.message}`;
  }
  if (refresh) refresh.disabled = false;
}

export async function renderAgentSettings() {
  const root = $("agentBody");
  if (!root || loading) return;
  loading = true;
  try {
    const data = await load();
    root.innerHTML = connectionCard(data.view) + howCard() + rightsCard(data.view)
      + gitCard(data.view) + runCard(data.runner, data.tests, data.view)
      + serversCard(data.servers) + manualCard(data.view);
    bind(root, data);
  } catch (error) {
    root.innerHTML = `<p class="hint">Не удалось загрузить настройки агента: ${esc(error.message)}</p>`;
  } finally {
    loading = false;
  }
}
