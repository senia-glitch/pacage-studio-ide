import {api} from "./api.js";
import {$, esc, S, toast} from "./core.js";
import {createSyncAcceptPane, promptTotal} from "./syncapply.js";

let open = false;

async function generate(state, scope, domain) {
  const preview = state.preview, stats = state.stats;
  preview.textContent = "Собираю промпт…";
  try {
    const data = await api("/api/sync/prompt", "POST", {scope, domain});
    state.markdown = data.markdown || "";
    preview.textContent = state.markdown;
    stats.textContent = `Узлов в области: ${promptTotal(state.markdown)}`;
  } catch (e) {
    preview.textContent = "Ошибка: " + e.message;
  }
}

function openSyncDialog() {
  if (open) return;
  open = true;
  const domains = [...new Set(
    (S.G && S.G.nodes || [])
      .filter(n => !n.planned && n.kind === "endpoint")
      .flatMap(n => String(n.domain || "").split(",").map(v => v.trim()).filter(Boolean))
  )].sort((a, b) => (a === "Без домена") - (b === "Без домена") || a.localeCompare(b, "ru"));

  const overlay = document.createElement("div");
  overlay.className = "studio-modal";
  overlay.innerHTML = `<div class="modal-box brief-box"><h3>Синхронизация описаний с кодом</h3>
    <div class="brief-layout">
      <div class="brief-left">
        <div class="brief-controls">
          <label class="hint">Область:
            <select id="syncScope">
              <option value="all">весь проект</option>
              <option value="domain">конкретный домен</option>
              <option value="unready">только узлы без описания</option>
            </select>
          </label>
          <label class="hint" id="syncDomainWrap" hidden>Домен:
            <select id="syncDomain">${domains.map(d => `<option>${esc(d)}</option>`).join("")}</select>
          </label>
        </div>
        <p class="hint">Шаг 1. Промпт для AI-агента с доступом к папке проекта: агент прочитает реальный
        код узлов и вернёт <b>описания и критерии</b> одним JSON-блоком. Файлы агент не правит.
        Плановые узлы (их ещё нет в коде) в промпт не попадают.</p>
        <p class="hint">Шаг 2. Вставьте JSON-ответ агента на вкладке «Принять ответ»: студия покажет отличия,
        и вы сами решите, что записать в metadata.json. Домены и связи ответом не меняются.</p>
      </div>
      <div class="brief-preview">
        <div class="sync-steps" role="tablist">
          <button type="button" class="on" data-step="prompt" role="tab">1. Промпт</button>
          <button type="button" data-step="accept" role="tab">2. Принять ответ</button>
        </div>
        <div class="sync-step" id="syncStepPrompt">
          <div class="brief-stats" id="syncStats">Подготовка…</div>
          <pre id="syncPreview">Собираю промпт…</pre>
          <div class="hint">Промпт содержит узлы проекта. Перед отправкой во внешний AI-сервис это решает пользователь.</div>
          <div class="brief-actions">
            <button type="button" id="syncCopy">Скопировать промпт</button>
            <button type="button" id="syncDownload">Скачать .md</button>
          </div>
        </div>
        <div class="sync-step" id="syncStepAccept" hidden></div>
        <div class="brief-actions"><button type="button" class="primary" id="syncClose">Закрыть</button></div>
      </div>
    </div></div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener("click", ev => { if (ev.target === overlay) { overlay.remove(); open = false; } });

  const acceptHost = overlay.querySelector("#syncStepAccept");
  acceptHost.appendChild(createSyncAcceptPane({endpoint: "/api/sync/apply"}));
  overlay.querySelectorAll(".sync-steps button").forEach(button => {
    button.onclick = () => {
      overlay.querySelectorAll(".sync-steps button").forEach(b => b.classList.toggle("on", b === button));
      overlay.querySelector("#syncStepPrompt").hidden = button.dataset.step !== "prompt";
      acceptHost.hidden = button.dataset.step !== "accept";
    };
  });

  const scopeSelect = overlay.querySelector("#syncScope");
  const domainWrap = overlay.querySelector("#syncDomainWrap");
  const domainSelect = overlay.querySelector("#syncDomain");
  const state = { preview: overlay.querySelector("#syncPreview"), stats: overlay.querySelector("#syncStats"), markdown: "" };

  const regenerate = () => {
    const scope = scopeSelect.value;
    domainWrap.hidden = scope !== "domain";
    generate(state, scope, scope === "domain" ? domainSelect.value : "");
  };
  scopeSelect.onchange = regenerate;
  domainSelect.onchange = regenerate;
  regenerate();

  overlay.querySelector("#syncCopy").onclick = () => {
    const text = state.markdown || state.preview.textContent;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => toast("Промпт скопирован")).catch(() => toast("Не удалось скопировать"));
    } else {
      toast("Не удалось скопировать");
    }
  };
  overlay.querySelector("#syncDownload").onclick = () => {
    const blob = new Blob([state.markdown || state.preview.textContent], {type: "text/markdown;charset=utf-8"});
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "sync-brief.md";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  };
  overlay.querySelector("#syncClose").onclick = () => { overlay.remove(); open = false; };
}

export function initSync() {
  const button = $("syncPromptsBtn");
  if (button) button.onclick = openSyncDialog;
}
