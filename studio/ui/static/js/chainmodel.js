// Чистая модель вкладки «Создание»: без DOM, fetch и localStorage.
// Один сценарий — одна цепочка слоёв; старый черновик узлов/связей отбрасывается.

export const DRAFT_VERSION = 2;

const LAYER_NAMES = {
  endpoint: "эндпоинт",
  scenario: "сценарий",
  io: "io",
  model: "модель",
  test: "тест",
};

const STATUS_LABELS = {
  done: "выполнено",
  partial: "частично",
  not_done: "не сделано",
};

const TEXT_FIELDS = [
  "title",
  "description",
  "acceptance",
  "domain",
  "http_method",
  "path",
  "slug",
];

export function emptyDraft() {
  return {
    version: DRAFT_VERSION,
    title: "",
    description: "",
    acceptance: "",
    domain: "",
    http_method: "",
    path: "",
    slug: "",
    uses_db: true,
    with_test: true,
  };
}

function asBoolean(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

export function normalizeDraft(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return emptyDraft();
  // Черновик старого конструктора хранил nodes/edges. Он не переносится:
  // новый экран описывает сценарий одним текстом, а не набором узлов.
  if (Array.isArray(raw.nodes) || Array.isArray(raw.edges)) return emptyDraft();
  const draft = emptyDraft();
  for (const field of TEXT_FIELDS) {
    if (typeof raw[field] === "string") draft[field] = raw[field].trim();
  }
  draft.http_method = draft.http_method.toLowerCase();
  draft.uses_db = asBoolean(raw.uses_db, asBoolean(raw.usesDb, true));
  draft.with_test = asBoolean(raw.with_test, asBoolean(raw.withTest, true));
  return draft;
}

export function canCreate(draft, preview) {
  const value = normalizeDraft(draft);
  if (!value.title || !value.description) return false;
  if (!preview || preview.ok !== true) return false;
  const conflicts = Array.isArray(preview.conflicts) ? preview.conflicts : [];
  return conflicts.length === 0;
}

export function buildPayload(draft) {
  const value = normalizeDraft(draft);
  const acceptance = value.acceptance
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^-\s*/, "").trim())
    .filter(Boolean);
  return {
    title: value.title,
    slug: value.slug,
    description: value.description,
    acceptance,
    domain: value.domain,
    http_method: value.http_method,
    path: value.path,
    uses_db: value.uses_db,
    with_test: value.with_test,
  };
}

export function layerChips(planState) {
  const layers = (planState && planState.layers) || [];
  return layers.map((layer) => {
    const done = layer.status === "done";
    const label = LAYER_NAMES[layer.layer] || layer.layer;
    return {
      layer: layer.layer,
      label,
      status: done ? "done" : "missing",
      done,
      text: `${label}: ${done ? "готов" : "нет"}`,
      name: (layer.actual_names && layer.actual_names[0]) || layer.suggested_name || "",
      file: layer.target_file || "",
    };
  });
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || STATUS_LABELS.not_done;
}
