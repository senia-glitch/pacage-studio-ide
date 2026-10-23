export const SAVE_STATES = ["idle", "dirty", "saving", "saved", "error", "conflict"];

const TRANSITIONS = {
  idle: ["dirty", "saving", "saved"],
  dirty: ["saving", "idle", "conflict"],
  saving: ["saved", "error", "conflict", "dirty"],
  saved: ["dirty", "saving", "idle"],
  error: ["dirty", "saving", "idle"],
  conflict: ["saving", "dirty", "idle"],
};

export function nextState(current, event) {
  if (!SAVE_STATES.includes(current)) return "idle";
  const mapping = {
    edit: "dirty",
    save: "saving",
    saved: "saved",
    fail: "error",
    conflict: "conflict",
    reset: "idle",
    force: "saving",
  };
  const target = mapping[event];
  if (!target) return current;
  return TRANSITIONS[current].includes(target) ? target : current;
}

export function saveLabel(state) {
  return {
    idle: "",
    dirty: "Не сохранено",
    saving: "Сохранение…",
    saved: "Сохранено ✓",
    error: "Ошибка — повторить",
    conflict: "Конфликт: файл изменён",
  }[state] || "";
}
