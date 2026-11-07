import {api} from "./api.js";
import {$, esc, toast} from "./core.js";
import {agentStatus, gitNote} from "./dockmodel.js";

/* Страница «Настройки агента»: подключение Claude Code, права, git, запуск и инструкции
   человеческим языком. Агент работает в фоне: студия запускает его на ваш шаг. */

let loading = false;

async function load() {
  const [view, runner, tests] = await Promise.all([
    api("/api/agent/status"),
    api("/api/runner"),
    api("/api/tests").catch(() => null),
  ]);
  return {view, runner, tests};
}

function connectionCard(view) {
  const status = agentStatus(view);
  const found = view.claude_found
    ? "Claude Code найден на этом компьютере."
    : "Claude Code не найден: установите его (команда <code>claude</code> должна запускаться в терминале) и вернитесь сюда.";
  return `<section class="agent-card" id="agentConn">
    <h3>Подключение</h3>
    <p><span class="dock-dot ${esc(status.tone)}"></span> <b>Claude Code</b> — ${esc(status.text)}</p>
    <p class="hint">${found}</p>
    <div class="agent-row">
      <button type="button" class="primary" id="agentConnect">${view.enabled ? "Переподключить" : "Подключить Claude Code"}</button>
      <button type="button" id="agentCheck">Проверить связь</button>
      ${view.enabled ? '<button type="button" id="agentDisconnect">Отключить</button>' : ""}
      <button type="button" id="agentManual">Строка подключения вручную…</button>
    </div>
    <p class="hint" id="agentCheckResult">«Подключить» записывает конфигурацию в <code>.studio/mcp.json</code> (файлы вашего проекта не трогаются) и включает автозапуск. «Проверить связь» запускает <code>claude --version</code> и токенов не тратит.</p>
  </section>`;
}

function howCard() {
  return `<section class="agent-card">
    <h3>Как это работает</h3>
    <p>Агент не сидит в чате и не ждёт: студия сама запускает Claude Code в фоне, когда наступает его ход, и он завершается, сделав шаг. Пока вы ничего не нажимаете, агент не запущен и токены не расходуются. Пользоваться чатом агента не нужно.</p>
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

function manualCard() {
  return `<section class="agent-card">
    <h3>Подключение вручную (если не хотите кнопку)</h3>
    <p>Те же шаги в терминале, из папки проекта:</p>
    <pre>claude mcp add pacage-studio -- studio mcp
studio skill --target all</pre>
    <p>Затем запустите Claude Code в папке проекта и скажите ему: <i>«Работай со студией: вызови studio_connect и studio_workflow»</i>. Так агент будет работать в вашем окне Claude Code, но студия не сможет будить его сама: автоматизация работает, когда студия запускает агента в фоне.</p>
    <p class="hint">Что умеет агент (MCP-инструменты): читать карту и фронтенд, присылать идеи (<code>studio_idea_add</code>), читать ТЗ (<code>studio_brief</code>), проверять (<code>studio_verify</code>, <code>studio_doctor</code>), отправлять отчёт (<code>studio_report</code>) и задавать вам вопросы (<code>studio_ask</code>).</p>
  </section>`;
}

function rightsCard(view) {
  const denied = (view.denied_tools || []).map((item) => `<li><code>${esc(item)}</code></li>`).join("");
  return `<section class="agent-card">
    <h3>Права агента</h3>
    <p>Агент запускается в корне проекта и свободно правит файлы и запускает тесты внутри задач. Всегда запрещено ему:</p>
    <ul class="agent-list">${denied}</ul>
    <p class="hint">Историю и GitHub ведёт студия, а не агент. Честная оговорка: настоящей песочницы, запрещающей выход за корень проекта, на Windows у Claude Code нет — рамки держатся на правах доступа и запретах выше. Полный список разрешений — в команде запуска (раздел «Запуск»).</p>
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

function runCard(runner, tests) {
  const testsOn = !!(tests && tests.config && tests.config.enabled);
  return `<section class="agent-card">
    <h3>Запуск</h3>
    <label class="rm-task">Предел одного запуска, минут
      <input id="runTimeout" type="number" min="1" max="240" value="${esc(runner.timeout_minutes)}"></label>
    <label class="rm-check"><input type="checkbox" id="runTests"${testsOn ? " checked" : ""}>
      <span><b>Запускать тесты проекта, когда агент прислал отчёт</b>
      <small>независимая проверка; команда — <code>.studio/tests.json</code> (по умолчанию pytest)</small></span></label>
    <details><summary>Команда запуска (для опытных)</summary>
      <p class="hint">По аргументу на строку; <code>{prompt}</code> — текст запроса агенту. <code>--output-format</code>, <code>--resume</code> и <code>--mcp-config</code> студия добавляет сама.</p>
      <textarea id="runCommand" rows="10" spellcheck="false" style="width:100%">${esc(runner.command.join("\n"))}</textarea>
      <div class="agent-row"><button type="button" id="runDefault">Вернуть стандартную</button></div>
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
  on("#agentConnect", async () => {
    await api("/api/agent/connect", "POST", {});
    toast("Claude Code подключён: студия будет запускать его на ваши шаги");
    await reload();
  });
  on("#agentCheck", async () => {
    const result = await api("/api/agent/check", "POST", {});
    const box = root.querySelector("#agentCheckResult");
    box.textContent = result.ok
      ? `Связь есть: ${result.version}.${result.mcp_config ? "" : " MCP-конфиг ещё не записан — нажмите «Подключить»."}`
      : `Проблема: ${result.error}`;
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
  on("#runDefault", async () => {
    root.querySelector("#runCommand").value = data.runner.default_command.join("\n");
  });
  on("#runSave", async () => {
    await api("/api/runner/config", "POST", {
      command: root.querySelector("#runCommand").value,
      timeout_minutes: Number(root.querySelector("#runTimeout").value),
    });
    await api("/api/tests/config", "POST", {enabled: root.querySelector("#runTests").checked});
    toast("Настройки запуска сохранены");
    await reload();
  });
}

export async function renderAgentSettings() {
  const root = $("agentBody");
  if (!root || loading) return;
  loading = true;
  try {
    const data = await load();
    root.innerHTML = connectionCard(data.view) + howCard() + rightsCard(data.view)
      + gitCard(data.view) + runCard(data.runner, data.tests) + manualCard();
    bind(root, data);
  } catch (error) {
    root.innerHTML = `<p class="hint">Не удалось загрузить настройки агента: ${esc(error.message)}</p>`;
  } finally {
    loading = false;
  }
}
