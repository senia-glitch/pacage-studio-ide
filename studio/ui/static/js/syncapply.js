import {api} from "./api.js";
import {esc, toast} from "./core.js";

/* Панель «Принять ответ агента»: общая для синхронизации бэкенда и фронтенда.
   Ответ агента (JSON) проверяется на сервере, пользователь видит отличия
   и сам выбирает, что записать. Без явного «Применить» ничего не пишется. */

/* Число узлов в области — из заголовка промпта «всего N», а не по строкам таблицы. */
export function promptTotal(markdown) {
  const match = /всего (\d+)\)/.exec(markdown || "");
  return match ? Number(match[1]) : 0;
}

const STATUS_TEXT = {change: "изменится", same: "без изменений", rejected: "отклонено", applied: "записано"};

function listHtml(items) {
  const rows = (items || []).filter(Boolean);
  if (!rows.length) return "";
  return `<ul>${rows.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
}

function stateHtml(state) {
  const description = state.description ? `<div>${esc(state.description)}</div>` : '<div class="hint">описания нет</div>';
  const acceptance = listHtml(state.acceptance) || '<div class="hint">критериев нет</div>';
  return description + acceptance;
}

function rowHtml(row) {
  const status = STATUS_TEXT[row.status] || row.status;
  const head = `<span class="sync-chip ${esc(row.status)}">${esc(status)}</span> <b>${esc(row.label || row.ref || row.id || "—")}</b>${row.label && row.label !== row.id ? ` <code>${esc(row.id)}</code>` : ""}`;
  if (row.status === "rejected") {
    return `<div class="sync-row rejected"><div class="sync-head">${head}</div><div class="hint">${esc(row.reason)}</div></div>`;
  }
  const checkbox = row.status === "change"
    ? `<input type="checkbox" class="sync-pick" value="${esc(row.id)}" checked aria-label="Записать ${esc(row.label || row.id)}">`
    : "";
  const diff = row.status === "same"
    ? ""
    : `<div class="sync-diff"><div><div class="hint">Сейчас</div>${stateHtml(row.before)}</div>
       <div><div class="hint">Предложено</div>${stateHtml(row.after)}</div></div>`;
  return `<div class="sync-row ${esc(row.status)}"><label class="sync-head">${checkbox}${head}</label>${diff}</div>`;
}

export function createSyncAcceptPane({endpoint, onApplied, intro, undoEndpoint}) {
  const undoPath = undoEndpoint || endpoint.replace("/apply", "/undo");
  const pane = document.createElement("div");
  pane.className = "sync-accept";
  pane.innerHTML = `<p class="hint">${esc(intro || "Вставьте JSON, который вернул агент (подойдёт и весь ответ с блоком json). Студия проверит записи и покажет отличия; в метаданные пишутся только отмеченные записи, и только по кнопке «Применить».")}</p>
    <textarea class="sync-json" rows="7" spellcheck="false"
      placeholder='{"updates": [{"id": "…", "description": "…", "acceptance": ["…"]}], "not_synced": []}'></textarea>
    <div class="brief-actions">
      <button type="button" data-act="check">Проверить</button>
      <button type="button" class="primary" data-act="apply" disabled>Применить выбранные</button>
    </div>
    <div class="sync-result" aria-live="polite"></div>`;
  const input = pane.querySelector(".sync-json");
  const result = pane.querySelector(".sync-result");
  const checkButton = pane.querySelector('[data-act="check"]');
  const applyButton = pane.querySelector('[data-act="apply"]');

  const picked = () => [...pane.querySelectorAll(".sync-pick:checked")].map((box) => box.value);
  const refreshApply = () => { applyButton.disabled = picked().length === 0; };

  function render(report, note, undo) {
    const c = report.counts || {};
    const parts = [
      `К записи: ${c.change || 0}`,
      `без изменений: ${c.same || 0}`,
      `отклонено: ${c.rejected || 0}`,
    ];
    if (c.not_synced) parts.push(`не синхронизировано агентом: ${c.not_synced}`);
    const notSynced = (report.not_synced || []).length
      ? `<div class="sync-row"><div class="sync-head"><b>Не синхронизировано агентом</b></div>${listHtml(report.not_synced.map((item) => item.reason ? `${item.id} — ${item.reason}` : item.id))}</div>`
      : "";
    const agent = report.report;
    const agentBlock = agent
      ? `<div class="sync-row"><div class="sync-head"><b>Отчёт агента</b> <span class="hint">(только для чтения)</span></div>
          ${agent.tests ? `<div>Тесты: ${esc(agent.tests)}</div>` : ""}
          ${[["Изменения вне целевых файлов", agent.outside_changes], ["Решения", agent.decisions], ["Вопросы и допущения", agent.questions]]
            .filter(([, items]) => items && items.length)
            .map(([title, items]) => `<div class="hint">${esc(title)}</div>${listHtml(items)}`).join("")}</div>`
      : "";
    const warnings = (report.warnings || []).length
      ? `<div class="hint">${(report.warnings || []).map(esc).join("<br>")}</div>`
      : "";
    const undoButton = undo && undo.length
      ? ' <button type="button" data-act="undo">↩ Отменить применение</button>'
      : "";
    result.innerHTML = `${note ? `<div class="brief-stats">${esc(note)}${undoButton}</div>` : ""}
      <div class="brief-stats">${esc(parts.join(" · "))}</div>${warnings}
      ${(report.rows || []).map(rowHtml).join("")}${notSynced}${agentBlock}`;
    result.querySelectorAll(".sync-pick").forEach((box) => { box.onchange = refreshApply; });
    const undoNode = result.querySelector('[data-act="undo"]');
    if (undoNode) undoNode.onclick = () => undoApplied(undo);
    refreshApply();
  }

  async function undoApplied(undo) {
    try {
      const data = await api(undoPath, "POST", {undo});
      if (data.restored && onApplied) onApplied(data);
      const skipped = (data.skipped || []).length;
      toast(`Возвращено: ${data.restored}` + (skipped ? ` · пропущено (изменены позже): ${skipped}` : ""));
      const fresh = await run(false);
      if (fresh) render(fresh, `Возвращено: ${data.restored}` + (skipped ? ` · пропущено: ${skipped}` : ""));
    } catch (error) {
      toast("Не удалось отменить: " + (error.message || ""));
    }
  }

  async function run(apply) {
    const text = input.value.trim();
    if (!text) {
      toast("Вставьте JSON-ответ агента");
      return null;
    }
    checkButton.disabled = true;
    applyButton.disabled = true;
    try {
      const body = {json: text};
      if (apply) { body.apply = true; body.ids = picked(); }
      return await api(endpoint, "POST", body);
    } catch (error) {
      result.innerHTML = `<div class="sync-row rejected"><div class="sync-head">${esc(error.message || "Ошибка")}</div></div>`;
      refreshApply();
      return null;
    } finally {
      checkButton.disabled = false;
    }
  }

  checkButton.onclick = async () => {
    const report = await run(false);
    if (report) render(report);
  };
  applyButton.onclick = async () => {
    const applied = await run(true);
    if (!applied) return;
    if (onApplied) onApplied(applied);
    toast(`Записано: ${applied.applied}`);
    const fresh = await run(false);
    if (fresh) render(fresh, `Записано: ${applied.applied}`, applied.undo);
  };
  return pane;
}
