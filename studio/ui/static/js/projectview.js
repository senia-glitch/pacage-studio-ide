import {api} from "./api.js";
import {$, esc, setTab, toast} from "./core.js";

/* «Настройки»: что относится к проекту, а не к конвейеру. Настройки самого конвейера (режим участия,
   пороги, критерии ворот, модель каждой роли) — на экране «Конвейер → Настройки»; сюда ведёт ссылка. */

const SERVER_DEFAULT_PORT = {backend: 8000, frontend: 5173};
const NEWLINE = String.fromCharCode(10);

function pipelineCard() {
  return `<section class="pj-card">
    <h3>Конвейер</h3>
    <p>Режим участия (вручную, на воротах, автопилот), пороги, критерии оценки каждых ворот и модель для каждой роли агента
      (Claude Code Pro или OpenCode Go Plus) настраиваются на экране конвейера.</p>
    <div class="pj-row"><button type="button" class="primary" id="pjOpenPipeline">Открыть настройки конвейера</button></div>
  </section>`;
}

function stackCard(info) {
  if (!info) return "";
  const ready = !info.missing.length;
  const tools = Object.entries(info.tools).map(([name, ok]) => `<code>${esc(name)}</code> ${ok ? "✓" : "✗"}`).join(" · ");
  return `<section class="pj-card">
    <h3>Стек проекта</h3>
    <p>Бэкенд: <b>fastbase → core-package → event-infra</b> (PostgreSQL), фронтенд: <b>Vue 3, Vite, Pinia, Vue Router</b>.
      Студия строит и проверяет проект именно по этому стеку: каркас кодом, правила в каждом ТЗ, проверки сканерами на воротах этапов 3–4.</p>
    <p>${ready ? "В проекте есть всё, что создают генераторы стека." : `Не хватает: <code>${esc(info.missing.join(", "))}</code>.`}
      Генераторы: ${tools}. Фронтенд: ${info.frontend ? "каркас есть" : "каркаса ещё нет (его создаёт шаг 3.1)"}.</p>
    ${ready ? "" : `<p class="hint">Стек ставится так: <code>${esc(info.install)}</code>. Дальше генераторы (<code>fb init</code>, <code>infra-init</code>, <code>core-init</code>) запустит сама студия на шаге 4.2 — или кнопкой ниже.</p>
    <div class="pj-row"><button type="button" class="primary" id="stackInit">Подготовить проект на стеке</button></div>`}
  </section>`;
}

function serversCard(servers) {
  if (!servers || !Array.isArray(servers.servers)) return "";
  const config = servers.config || {};
  const rows = servers.servers.map((item) => {
    const own = config[item.name] || {};
    return `<div class="pj-srv" data-srv-name="${esc(item.name)}">
      <p class="hint"><b>${esc(item.title)}.</b> ${item.custom ? "Своя команда." : item.available ? "Определено автоматически:" : esc(item.reason || "не найден")}
        ${item.custom || !item.available ? "" : `<code>${esc(item.command)}</code>`}</p>
      <textarea rows="4" spellcheck="false" placeholder="Пусто — автоопределение. По аргументу на строку; {port} — порт.">${esc((own.command || []).join(NEWLINE))}</textarea>
      <label>Предпочтительный порт
        <input type="number" min="1024" max="65535" data-srv-port placeholder="${SERVER_DEFAULT_PORT[item.name] || ""}" value="${esc(own.port || "")}"></label></div>`;
  }).join("");
  return `<section class="pj-card">
    <h3>Серверы проекта</h3>
    <p>Этап 5 («Запуск») поднимает бэкенд (<code>uvicorn … --reload</code>) и фронтенд (<code>npm run dev</code> в <code>frontend/</code>).
      Занятый порт заменяется ближайшим свободным, а при закрытии студии серверы останавливаются.</p>
    <details><summary>Команды и порты (для опытных)</summary>${rows}</details>
    <div class="pj-row"><button type="button" class="primary" id="srvSave">Сохранить</button></div>
  </section>`;
}

function testsCard(tests) {
  if (!tests) return "";
  const on = !!(tests.config && tests.config.enabled);
  return `<section class="pj-card">
    <h3>Тесты проекта</h3>
    <label class="pj-check"><input type="checkbox" id="testsOn"${on ? " checked" : ""}>
      <span><b>Независимая проверка тестами проекта</b>
      <small>команда — <code>.studio/tests.json</code> (по умолчанию pytest); итог привязывается к узлам карты «Система»</small></span></label>
    <div class="pj-row"><button type="button" class="primary" id="testsSave">Сохранить</button></div>
  </section>`;
}

function gitCard() {
  return `<section class="pj-card">
    <h3>Откат</h3>
    <p>После реализации на этапах 3–4 студия сохраняет точку отката в git (если проект — репозиторий). Откат возвращает проект
      к состоянию до последней работы студии: её правки и новые файлы исчезнут.</p>
    <div class="pj-row"><button type="button" class="danger" id="gitRollback">↩ Откатить работу студии</button></div>
  </section>`;
}

export async function renderProject() {
  const root = $("projectBody");
  if (!root) return;
  const [tests, servers, stackInfo] = await Promise.all([
    api("/api/tests").catch(() => null),
    api("/api/devservers").catch(() => null),
    api("/api/pipeline/stack").catch(() => null),
  ]);
  const top = root.scrollTop;
  const opened = [...root.querySelectorAll("details")].map((d) => d.open);
  root.innerHTML = `<div class="pj-wrap"><h2>Настройки</h2>${pipelineCard()}${stackCard(stackInfo)}${serversCard(servers)}${testsCard(tests)}${gitCard()}</div>`;
  root.querySelectorAll("details").forEach((d, i) => { d.open = !!opened[i]; });
  root.scrollTop = top;
  const on = (selector, handler) => {
    const element = root.querySelector(selector);
    if (element) element.onclick = async () => {
      try { await handler(); } catch (error) { toast("Не получилось: " + error.message); }
    };
  };
  on("#pjOpenPipeline", async () => {
    const pipeline = await import("./pipeline/main.js");
    pipeline.showView("settings");
    await setTab("pipeline");
  });
  on("#stackInit", async () => {
    await api("/api/pipeline/stack/init", "POST", {});
    toast("Подготовка проекта запущена: ход виден на экране «Конвейер»");
  });
  on("#srvSave", async () => {
    const payload = {};
    root.querySelectorAll("[data-srv-name]").forEach((el) => {
      const port = el.querySelector("[data-srv-port]").value.trim();
      payload[el.dataset.srvName] = {command: el.querySelector("textarea").value, port: port ? Number(port) : null};
    });
    await api("/api/devservers/config", "POST", payload);
    toast("Настройки серверов сохранены");
    await renderProject();
  });
  on("#testsSave", async () => {
    await api("/api/tests/config", "POST", {enabled: root.querySelector("#testsOn").checked});
    toast("Настройки тестов сохранены");
    await renderProject();
  });
  on("#gitRollback", async () => {
    if (!confirm("Вернуть проект к состоянию до последней работы студии? Её правки и новые файлы исчезнут.")) return;
    await api("/api/git/rollback", "POST", {});
    toast("Проект возвращён к прежнему состоянию");
  });
}
