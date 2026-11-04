import {api} from "./api.js";
import {$, esc, S, toast} from "./core.js";

let open = false;

async function generate(state, scope, domain) {
  const preview = state.preview, stats = state.stats;
  preview.textContent = "Собираю промпт…";
  try {
    const data = await api("/api/sync/prompt", "POST", {scope, domain});
    state.markdown = data.markdown || "";
    preview.textContent = state.markdown;
    stats.textContent = `Узлов в области: ${(state.markdown.match(/^\| /gm) || []).length}`;
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
        <p class="hint">Промпт для AI-агента с доступом к папке проекта: агент прочитает реальный
        код узлов и приведёт их <b>описания и критерии</b> в metadata.json к фактическому поведению.
        Плановые узлы (их ещё нет в коде) в промпт не попадают. Домены и связи агент не меняет.</p>
        <div class="hint">После работы агента студия подхватит изменения сама (live-обновление).</div>
      </div>
      <div class="brief-preview">
        <div class="brief-stats" id="syncStats">Подготовка…</div>
        <pre id="syncPreview">Собираю промпт…</pre>
        <div class="hint">Промпт содержит узлы проекта. Перед отправкой во внешний AI-сервис это решает пользователь.</div>
        <div class="brief-actions">
          <button type="button" id="syncCopy">Скопировать промпт</button>
          <button type="button" id="syncDownload">Скачать .md</button>
          <button type="button" class="primary" id="syncClose">Закрыть</button>
        </div>
      </div>
    </div></div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener("click", ev => { if (ev.target === overlay) { overlay.remove(); open = false; } });

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
