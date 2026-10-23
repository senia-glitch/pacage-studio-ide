export const state = {
  graph: null,
  byId: {},
  tab: "map",
  selectedScenario: null,
  selectedNode: null,
  hideMeta: false,
  editor: "vscode",
  editorTemplate: "",
  views: {
    map: {x: 30, y: 30, k: 1},
    scenario: {x: 30, y: 30, k: 1},
    create: {x: 30, y: 30, k: 1},
  },
  positions: {
    map: new Map(),
    scenario: new Map(),
    periph: new Map(),
    zones: new Map(),
  },
  live: {revision: 0, online: false, updatedAt: ""},
};

export function replaceGraph(graph) {
  state.graph = graph || {};
  state.byId = {};
  for (const node of state.graph.nodes || []) {
    state.byId[node.id] = node;
  }
  if (state.selectedScenario && !(state.graph.scenario_paths || {})[state.selectedScenario]) {
    state.selectedScenario = null;
  }
  if (state.selectedNode && !state.byId[state.selectedNode]) {
    state.selectedNode = null;
  }
}
