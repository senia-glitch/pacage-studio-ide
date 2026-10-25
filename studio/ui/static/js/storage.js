export function hashText(value) {
  let hash = 5381;
  const text = String(value || "");
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 33) ^ text.charCodeAt(index);
  }
  return (hash >>> 0).toString(36);
}

export function storageKey(projectRoot) {
  return `studio:v2:${hashText(projectRoot)}`;
}

export function createStorage(store, key) {
  function load() {
    try {
      const raw = store.getItem(key);
      if (!raw) return null;
      const value = JSON.parse(raw);
      return value && typeof value === "object" ? value : null;
    } catch (_error) {
      return null;
    }
  }

  function save(value) {
    try {
      store.setItem(key, JSON.stringify(value));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function reset() {
    try {
      store.removeItem(key);
      return true;
    } catch (_error) {
      return false;
    }
  }

  return {load, save, reset};
}

export function cleanPositions(positions, knownIds, knownScopes) {
  const out = {};
  for (const [key, value] of Object.entries(positions || {})) {
    if (!value || typeof value.x !== "number" || typeof value.y !== "number") continue;
    const parts = key.split("|");
    const id = parts[parts.length - 1];
    const scope = parts.slice(0, -1).join("|");
    if (id && !knownIds.has(id) && !knownScopes.has(scope)) continue;
    out[key] = {x: value.x, y: value.y};
  }
  return out;
}
