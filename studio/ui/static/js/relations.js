/* Единые правила состава потомков и вида связи родитель → потомок.
   Используются формой создания в панели свойств, неопределённым узлом
   на канвасе и любыми будущими точками создания узлов. */

export const CHILD_KINDS = {
  endpoint: ["scenario"],
  scenario: ["scenario", "io", "test", "nested"],
  io: ["model", "nested"],
  model: ["nested"],
  test: ["nested"],
  nested: ["nested"],
};

export function relationFor(parentKind, childKind) {
  if (parentKind === "endpoint" && childKind === "scenario") return "dispatch";
  if (parentKind === "scenario" && childKind === "scenario") return "scenario_call";
  if (parentKind === "scenario" && childKind === "io") return "port_call";
  if (parentKind === "scenario" && childKind === "test") return "tests";
  if (parentKind === "io" && childKind === "model") return "table";
  return "uses";
}
