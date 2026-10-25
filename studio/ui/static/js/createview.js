import {api} from "./api.js";
import {openBriefDialog} from "./briefview.js";
import {
  $, S, applyGraph, esc, modal, schedulePersist, toast, KIND_NAMES,
} from "./core.js";
import {
  buildPayload, canCreate, emptyDraft, layerChips, normalizeDraft, statusLabel,
} from "./chainmodel.js";

let bound = false;
let previewToken = 0;
let previewTimer = null;

const TEXT_FIELDS = {
  title: "planTitle",
  description: "planDescription",
  acceptance: "planAcceptance",
  domain: "planDomain",
  http_method: "planMethod",
  path: "planPath",
  slug: "planSlug",
};

function field(name) {
  const id = TEXT_FIELDS[name];
  return id ? $(id) : null;
}

function readForm() {
  const draft = emptyDraft();
  for (const [name, id] of Object.entries(TEXT_FIELDS)) {
    const element = $(id);
    if (element) draft[name] = element.value;
  }
  const usesDb = $("planUsesDb");
  const withTest = $("planWithTest");
  draft.uses_db = usesDb ? usesDb.checked : true;
  draft.with_test = withTest ? withTest.checked : true;
  return normalizeDraft(draft);
}

function writeForm(draft) {
  const value = normalizeDraft(draft);
  for (const [name, id] of Object.entries(TEXT_FIELDS)) {
    const element = $(id);
    if (!element || element === document.activeElement) continue;
    if (element.value !== (value[name] || "")) element.value = value[name] || "";
  }
  const usesDb = $("planUsesDb");
  const withTest = $("planWithTest");
  if (usesDb) usesDb.checked = value.uses_db;
  if (withTest) withTest.checked = value.with_test;
}

function saveDraft() {
  S.createState = readForm();
  schedulePersist();
  updateCreateButton();
}

function schedulePreview(delay = 350) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => requestPreview(), delay);
}

function updateCreateButton() {
  const button = $("planCreate");
  if (!button) return;
  button.disabled = !canCreate(S.createState, S.planPreview);
}

function chainCard(layer) {
  const agent = layer.name_by_agent === true;
  const name = layer.suggested_name || layer.name || "";
  const file = layer.target_file || "";
  const hint = agent
    ? '<span class="plan-agent">имя выберет агент</span>'
    : "";
  return `<div class="plan-layer k-${esc(layer.layer)}">
    <div class="plan-layer-head"><span class="chip">${esc(KIND_NAMES[layer.layer] || layer.layer)}</span></div>
    <div class="plan-layer-name">${esc(name) || '<span class="hint">имя появится после названия</span>'}${hint}</div>
    <div class="plan-layer-file">${esc(file) || '<span class="hint">файл появится после названия</span>'}</div>
  </div>`;
}

function renderChain() {
  const box = $("planChain");
  if (!box) return;
  const preview = S.planPreview;
  if (!preview) {
    box.innerHTML = '<p class="hint">Нажмите «Построить цепочку» — слои появятся даже при пустой форме.</p>';
    return;
  }
  const layers = Array.isArray(preview.layers) ? preview.layers : [];
  const conflicts = Array.isArray(preview.conflicts) ? preview.conflicts : [];
  const cards = layers.map(chainCard);
  let html = '<div class="plan-chain-row">' + cards.join('<span class="plan-arrow">→</span>') + "</div>";
  if (conflicts.length) {
    html += '<div class="plan-conflicts"><div class="plan-conflicts-title">Конфликты</div>' +
      conflicts.map((item) => `<div class="plan-conflict">${esc(item.code || "ошибка")}: ${esc(item.message || "")}${item.file ? ` — <span class="hint">${esc(item.file)}</span>` : ""}</div>`).join("") +
      "</div>";
  }
  box.innerHTML = html;
}

function renderPlans() {
  const box = $("planList");
  if (!box) return;
  const plans = ((S.G && S.G.plans) || []).slice().sort((a, b) => String(a.slug).localeCompare(String(b.slug)));
  if (!plans.length) {
    box.innerHTML = '<p class="hint">Планов пока нет.</p>';
    return;
  }
  box.innerHTML = plans.map((plan) => {
    const chips = layerChips(plan).map((chip) =>
      `<span class="chip ${chip.done ? "ok" : "no"}">${esc(chip.text)}</span>`).join("");
    const done = plan.status === "done";
    return `<div class="plan-card" data-plan="${esc(plan.slug)}">
      <div class="plan-card-head">
        <b>${esc(plan.title || plan.slug)}</b>
        <span class="chip">${esc(plan.slug)}</span>
        <span class="chip ${done ? "ok" : plan.status === "partial" ? "warn" : "no"}">${esc(statusLabel(plan.status))}</span>
        <span class="spacer"></span>
        <span class="hint">${esc(plan.domain || "без домена")}</span>
      </div>
      <div class="plan-card-chips">${chips}</div>
      <div class="plan-card-actions">
        ${done ? '<span class="chip ok">Готово</span>' : `<button type="button" data-brief-plan="${esc(plan.slug)}">ТЗ для агента</button>`}
        <button type="button" data-edit-plan="${esc(plan.slug)}">Править описание</button>
        <button type="button" data-delete-plan="${esc(plan.slug)}">Удалить план</button>
      </div>
    </div>`;
  }).join("");
  box.querySelectorAll("[data-brief-plan]").forEach((button) => {
    button.onclick = () => openBriefDialog({plans: [button.dataset.briefPlan]});
  });
  box.querySelectorAll("[data-edit-plan]").forEach((button) => {
    const plan = plans.find((item) => item.slug === button.dataset.editPlan);
    if (plan) button.onclick = () => editPlan(plan);
  });
  box.querySelectorAll("[data-delete-plan]").forEach((button) => {
    button.onclick = () => deletePlan(button.dataset.deletePlan);
  });
}

function editPlan(plan) {
  const edited = {
    description: plan.description || "",
    acceptance: (plan.acceptance || []).join("\n"),
    domain: plan.domain || "",
  };
  const body = `<div class="plan-edit">
    <div class="field"><label>Описание сценария</label><textarea id="planEditDescription" rows="7">${esc(edited.description)}</textarea></div>
    <div class="field"><label>Критерии приёмки — по одной на строку</label><textarea id="planEditAcceptance" rows="3">${esc(edited.acceptance)}</textarea></div>
    <div class="field"><label>Домен</label><input id="planEditDomain" value="${esc(edited.domain)}"></div>
  </div>`;
  modal("Править описание плана", body, [
    {label: "Сохранить", cls: "primary", action: async () => {
      try {
        const data = await api("/api/plan/update", "POST", {
          slug: plan.slug,
          description: edited.description,
          acceptance: edited.acceptance.split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
          domain: edited.domain,
        });
        applyGraph(data.graph);
        renderPlans();
        toast("Описание плана сохранено");
      } catch (error) {
        toast("Не удалось сохранить план: " + error.message);
      }
    }},
    {label: "Отмена", action: () => {}},
  ]);
  const description = $("planEditDescription");
  const acceptance = $("planEditAcceptance");
  const domain = $("planEditDomain");
  if (description) description.oninput = () => { edited.description = description.value; };
  if (acceptance) acceptance.oninput = () => { edited.acceptance = acceptance.value; };
  if (domain) domain.oninput = () => { edited.domain = domain.value; };
}

async function deletePlan(slug) {
  if (!confirm(`Удалить план ${slug}? Нетронутые файлы-скелеты будут удалены.`)) return;
  try {
    const data = await api("/api/plan/delete", "POST", {slug});
    applyGraph(data.graph);
    renderPlans();
    const kept = (data.kept_files || []).length;
    toast(`План ${slug} удалён${kept ? `, файлов оставлено: ${kept}` : ""}`);
  } catch (error) {
    toast("Не удалось удалить план: " + error.message);
  }
}

async function requestPreview() {
  clearTimeout(previewTimer);
  saveDraft();
  const token = ++previewToken;
  const chain = $("planChain");
  if (chain) chain.innerHTML = '<p class="hint">Строю цепочку…</p>';
  try {
    const data = await api("/api/plan/preview", "POST", buildPayload(S.createState));
    if (token !== previewToken) return;
    S.planPreview = data;
    renderChain();
  } catch (error) {
    if (token !== previewToken) return;
    S.planPreview = {
      ok: false,
      layers: [],
      conflicts: [{code: "E_INTERNAL", message: error.message}],
    };
    renderChain();
  }
  updateCreateButton();
}

async function submitCreate() {
  saveDraft();
  if ($("planCreate") && $("planCreate").disabled) return;
  const result = $("createResult");
  try {
    const data = await api("/api/plan", "POST", buildPayload(S.createState));
    applyGraph(data.graph);
    const slug = (data.plan && data.plan.slug) || S.createState.slug || "";
    S.createState = emptyDraft();
    S.planPreview = null;
    writeForm(S.createState);
    renderChain();
    renderPlans();
    updateCreateButton();
    schedulePersist();
    showCreated(data, slug);
  } catch (error) {
    if (result) {
      result.style.display = "block";
      result.textContent = "Ошибка: " + error.message;
    }
  }
}

function showCreated(data, slug) {
  const result = $("createResult");
  if (!result) return;
  const files = (data.created || []).map((item) =>
    `  ${item.path} — ${KIND_NAMES[item.kind] || item.kind} ${item.name}`);
  result.style.display = "block";
  result.innerHTML = `<div>Созданы файлы-скелеты с TODO:</div>
    <pre>${esc(files.join("\n") || "файлы не созданы")}</pre>
    <div class="hint">Описания записаны в .studio/metadata.json. Код проекта не изменялся: в новых файлах только TODO.</div>
    <button type="button" id="createBrief">Сформировать ТЗ</button>`;
  const button = $("createBrief");
  if (button) button.onclick = () => openBriefDialog({plans: slug ? [slug] : []});
}

function renderDomains() {
  const list = $("planDomains");
  if (!list) return;
  const names = [...new Set(((S.G && S.G.nodes) || [])
    .filter((node) => node.kind === "endpoint")
    .flatMap((node) => String(node.domain || "").split(",").map((part) => part.trim()).filter(Boolean)))].sort();
  list.innerHTML = names.map((name) => `<option value="${esc(name)}"></option>`).join("");
}

function bindForm() {
  if (bound) return;
  bound = true;
  for (const id of Object.values(TEXT_FIELDS)) {
    const element = $(id);
    if (!element) continue;
    element.addEventListener("input", () => {
      saveDraft();
      schedulePreview();
    });
    element.addEventListener("change", () => {
      saveDraft();
      schedulePreview();
    });
  }
  for (const id of ["planUsesDb", "planWithTest"]) {
    const element = $(id);
    if (element) element.addEventListener("change", () => {
      saveDraft();
      schedulePreview(120);
    });
  }
  const build = $("planBuild");
  if (build) build.onclick = () => requestPreview();
  const create = $("planCreate");
  if (create) create.onclick = submitCreate;
}

function renderCreate() {
  bindForm();
  writeForm(S.createState);
  renderDomains();
  renderChain();
  renderPlans();
  updateCreateButton();
}

export {renderCreate, submitCreate, requestPreview};
