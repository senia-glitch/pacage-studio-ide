const SAVE_STATES = ["idle", "dirty", "saving", "saved", "error"];

const TRANSITIONS = {
  idle: ["dirty", "saving", "saved"],
  dirty: ["saving", "idle"],
  saving: ["saved", "error", "dirty"],
  saved: ["dirty", "saving", "idle"],
  error: ["dirty", "saving", "idle"],
};

export function nextState(current, event) {
  if (!SAVE_STATES.includes(current)) return "idle";
  const mapping = {
    edit: "dirty",
    save: "saving",
    saved: "saved",
    fail: "error",
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
  }[state] || "";
}
