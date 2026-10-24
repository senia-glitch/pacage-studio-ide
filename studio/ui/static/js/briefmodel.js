const MAP_MODES = ["related", "full", "compact", "none"];

export function plannedNodes(graph) {
  return ((graph && graph.nodes) || [])
    .filter((node) => node && node.planned)
    .slice()
    .sort((a, b) => {
      const kinds = {model: 0, io: 1, scenario: 2, endpoint: 3, test: 4};
      return (kinds[a.kind] ?? 9) - (kinds[b.kind] ?? 9) || String(a.id).localeCompare(String(b.id));
    });
}

export function plannedStats(graph) {
  const nodes = plannedNodes(graph);
  const ready = nodes.filter((node) => String(node.description || "").trim()).length;
  return {tasks: ready, unready: nodes.length - ready, total: nodes.length};
}

export function defaultMapMode(nodeCount) {
  return Number(nodeCount) > 150 ? "related" : "full";
}

export function briefRequest({selected, map, includeEmpty, scenario}) {
  const payload = {
    only: Array.isArray(selected) ? selected : [...(selected || [])],
    map: MAP_MODES.includes(map) ? map : "full",
    include_empty: !!includeEmpty,
  };
  if (scenario) payload.scenario = scenario;
  return payload;
}

export function verifyLabel(report, graph) {
  if (report && Number(report.total) > 0) {
    return {
      text: `Выполнено ${report.done}/${report.total}`,
      urgent: Number(report.partial) + Number(report.not_done) > 0,
      title: "Проверка ТЗ для AI-агента",
    };
  }
  const stats = plannedStats(graph);
  return {
    text: `К реализации: ${stats.tasks}`,
    urgent: stats.unready > 0,
    title: stats.unready
      ? `К реализации: ${stats.tasks} · не готово: ${stats.unready}`
      : "ТЗ для AI-агента",
  };
}

export function shortPrompt(path) {
  return `Прочитай и выполни ТЗ из файла ${path}, следуй его правилам`;
}

export function statusText(report) {
  if (!report) return "ТЗ ещё не проверялось";
  if (report.total === 0) return "В ТЗ нет задач";
  return `выполнено ${report.done}, частично ${report.partial}, не сделано ${report.not_done}`;
}
