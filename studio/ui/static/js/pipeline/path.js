/* Экран «Путь»: пять этапов с шагами и воротами, действия человека и решения на воротах. */

import {GATE_STATE_NAMES, STATUS_NAMES, chip, h, statusChip} from "./dom.js";

const STEP_STATE = {done: "сделано", available: "можно", blocked: "ждёт", planned: "будет"};

export function renderPath(ctx, body) {
  const state = ctx.state;
  if (!state.started) {
    body.append(
      h(
        "div",
        {class: "pl-hero"},
        h("h2", null, "Начните с идеи"),
        h("p", null, "Конвейер ведёт проект от идеи до работающего ПО: пять этапов, ворота качества между ними и один банк знаний."),
        h("button", {class: "pl-primary", onclick: () => ctx.act("/api/pipeline/start")}, "Начать с идеи"),
        state.entry === "develop"
          ? h("div", {class: "pl-panel"}, h("p", null, "В этой папке уже есть код. Режим «Развитие»: студия прочитает код, положит найденное в банк с меткой «из кода», а агент восстановит замысел."), h("button", {class: "pl-primary", onclick: async () => { await ctx.act("/api/pipeline/develop/scan"); await ctx.act("/api/pipeline/develop/reconstruct"); }}, "Подключить существующий проект"))
          : null,
      ),
    );
    return;
  }
  body.append(nextBar(ctx));
  if (state.warnings && state.warnings.length) {
    body.append(h("div", {class: "pl-warn"}, state.warnings.map((text) => h("div", null, "⚠ ", text))));
  }
  for (const stage of state.stages) body.append(stageCard(ctx, stage));
}

function nextBar(ctx) {
  const state = ctx.state;
  const running = state.job && state.job.running;
  const next = state.next;
  const bar = h("div", {class: "pl-next"});
  if (!next) {
    bar.append(h("b", null, "Сейчас: "), "все подключённые шаги пройдены или ждут вашего решения на воротах.");
  } else {
    bar.append(h("b", null, "Дальше: "), `${next.num && next.num !== "gate" ? next.num + " " : ""}${next.title}`);
    if (next.hint) bar.append(h("span", {class: "hint"}, " — ", next.hint));
    if (next.key !== "area") {
      bar.append(
        h(
          "button",
          {class: "pl-primary", disabled: running, onclick: () => runStep(ctx, next.stage, next.key)},
          next.key.startsWith("gate_") ? "Запустить ворота" : "Выполнить",
        ),
      );
    }
  }
  if (state.mode === "autopilot") {
    bar.append(
      h(
        "button",
        {class: "pl-auto", disabled: running, title: "Агенты пройдут шаги сами; вас позовут на итоговой приёмке и когда агент застрянет", onclick: () => ctx.act("/api/pipeline/autopilot")},
        "▶ Автопилот",
      ),
    );
  }
  return bar;
}

function runStep(ctx, stage, step) {
  return ctx.act("/api/pipeline/run", {stage, step});
}

function stageCard(ctx, stage) {
  const counts = Object.entries(stage.counts || {})
    .filter(([key]) => key !== "rejected")
    .map(([key, n]) => h("span", {class: "pl-count st-" + key, title: STATUS_NAMES[key]}, `${STATUS_NAMES[key] || key} ${n}`));
  const state = !stage.wired ? "будет подключён" : stage.open ? "открыт" : `ждёт этап ${stage.n - 1}`;
  const card = h(
    "section",
    {class: "pl-stage" + (stage.open ? " open" : "") + (stage.wired ? "" : " planned")},
    h(
      "header",
      null,
      h("span", {class: "pl-num"}, stage.n),
      h("h3", null, stage.name),
      h("span", {class: "pl-stage-state"}, state),
      h("span", {class: "spacer"}),
      counts,
    ),
    stepsRow(ctx, stage),
    gatesRow(ctx, stage),
  );
  const panel = stagePanel(ctx, stage);
  if (panel) card.append(panel);
  const gate = stage.gates.find((g) => g.key === ctx.gate);
  if (gate) card.append(h("div", {class: "pl-gatepanel", "data-gate": gate.key}, "Загрузка…"));
  if (gate) loadGate(ctx, gate.key, card.querySelector(".pl-gatepanel"));
  return card;
}

function stepsRow(ctx, stage) {
  const running = ctx.state.job && ctx.state.job.running;
  const items = stage.steps.filter((s) => s.num !== "gate");
  return h(
    "div",
    {class: "pl-steps"},
    items.map((step) => {
      const agent = step.role && !["area"].includes(step.key) && step.key !== "questions";
      const runnable = !["area", "questions"].includes(step.key) && !step.key.startsWith("planned_");
      const clickable = step.state === "available" && runnable && !running;
      return h(
        "div",
        {class: "pl-step " + step.state, title: STEP_STATE[step.state] + (step.hint ? " — " + step.hint : "")},
        h("span", {class: "n"}, step.num),
        h("span", {class: "t"}, step.title),
        clickable ? h("button", {onclick: () => runStep(ctx, stage.n, step.key)}, "▶") : null,
        step.state === "done" && agent && !running && step.key !== "gate" && repeatable(step.key)
          ? h("button", {class: "ghost", title: "Выполнить ещё раз (добавит новое, ничего не затрёт)", onclick: () => runStep(ctx, stage.n, step.key)}, "↻")
          : null,
      );
    }),
  );
}

function repeatable(key) {
  return ["competitors", "domain", "problems", "constraints", "dictionary", "boundaries"].includes(key);
}

function gatesRow(ctx, stage) {
  return h(
    "div",
    {class: "pl-gates"},
    stage.gates.map((gate) =>
      h(
        "button",
        {
          class: "pl-gate " + gate.state + (ctx.gate === gate.key ? " on" : ""),
          title: GATE_STATE_NAMES[gate.state] || gate.state,
          onclick: () => {
            ctx.gate = ctx.gate === gate.key ? null : gate.key;
            ctx.go("path");
          },
        },
        h("span", {class: "diamond"}, "◇"),
        gate.title,
        h("span", {class: "pl-gstate"}, GATE_STATE_NAMES[gate.state] || gate.state),
        gate.human ? h("span", {class: "pl-flag", title: "Нужно ваше решение"}, "!") : null,
      ),
    ),
  );
}

// ------------------------------------------------------------------ шаги с вводом человека

function stagePanel(ctx, stage) {
  if (stage.n === 1) {
    const parts = [areaPanel(ctx), questionsPanel(ctx)].filter(Boolean);
    return parts.length ? h("div", {class: "pl-panel"}, parts) : null;
  }
  if (stage.n === 2) {
    const unchosen = ctx.state.unchosen || [];
    return unchosen.length ? h("div", {class: "pl-panel"}, variantsPanel(ctx, unchosen)) : null;
  }
  if (stage.n === 3) {
    const style = stage.steps.find((step) => step.key === "style");
    return style && style.state === "available" ? h("div", {class: "pl-panel"}, stylePanel(ctx)) : null;
  }
  return null;
}

function areaPanel(ctx) {
  const stage = ctx.state.stages[0];
  const done = stage.steps.find((s) => s.key === "area").state === "done";
  const box = h("textarea", {class: "pl-area", rows: 3, placeholder: "О чём система и для кого? Опишите идею своими словами."});
  const wrap = h("div", {class: "pl-block"}, h("div", {class: "pl-label"}, "1.1 Область — ваша идея"));
  if (done && !ctx.editArea) {
    wrap.append(
      h("div", {class: "pl-quote"}, ctx.areaText || "Идея записана."),
      h("button", {class: "ghost", onclick: () => { ctx.editArea = true; ctx.go("path"); }}, "Изменить"),
    );
    ctx.api("/api/pipeline/bank?kind=area").then((data) => {
      const area = data.records[0];
      const quote = wrap.querySelector(".pl-quote");
      if (area && quote) { ctx.areaText = area.body || area.title; quote.textContent = ctx.areaText; box.value = ctx.areaText; }
    }).catch(() => {});
    return wrap;
  }
  if (ctx.areaText) box.value = ctx.areaText;
  wrap.append(
    box,
    h("button", {class: "pl-primary", onclick: async () => {
      const text = box.value.trim();
      if (!text) return ctx.toast("Опишите идею", "err");
      ctx.areaText = text; ctx.editArea = false;
      await ctx.act("/api/pipeline/area", {text});
    }}, done ? "Сохранить" : "Записать идею"),
  );
  return wrap;
}

function questionsPanel(ctx) {
  const questions = ctx.state.questions || [];
  if (!questions.length) return null;
  const wrap = h("div", {class: "pl-block"}, h("div", {class: "pl-label"}, "1.2 Уточняющие вопросы — ответьте"));
  for (const q of questions) {
    const input = h("input", {type: "text", placeholder: "или свой ответ"});
    const send = (answer) => ctx.act("/api/pipeline/answer", {id: q.id, answer});
    wrap.append(
      h(
        "div",
        {class: "pl-question"},
        h("div", {class: "q"}, q.title),
        h(
          "div",
          {class: "opts"},
          (q.attrs.options || []).map((option) => h("button", {onclick: () => send(option)}, option)),
          input,
          h("button", {class: "ghost", onclick: () => input.value.trim() && send(input.value.trim())}, "Ответить"),
        ),
      ),
    );
  }
  return wrap;
}

function stylePanel(ctx) {
  const wrap = h("div", {class: "pl-block"}, h("div", {class: "pl-label"}, "3.1 Стиль и каркас — один раз, выберите тему"));
  const themes = ctx.state.themes || {};
  wrap.append(
    h(
      "div",
      {class: "opts"},
      Object.entries(themes).map(([key, name]) => h("button", {onclick: () => ctx.act("/api/pipeline/style", {theme: key})}, name)),
      h("button", {class: "ghost", title: "Дизайнер предложит тему, меню и каркас", onclick: () => ctx.act("/api/pipeline/run", {stage: 3, step: "style"})}, "Поручить дизайнеру"),
    ),
    h("div", {class: "hint"}, "В автопилоте берётся стандартная тема без вопросов. Стиль входит в каждое ТЗ интерфейса."),
  );
  return wrap;
}

function variantsPanel(ctx, unchosen) {
  const wrap = h("div", {class: "pl-block"}, h("div", {class: "pl-label"}, "2.2 Варианты — выберите по одному"));
  for (const req of unchosen) {
    wrap.append(
      h(
        "div",
        {class: "pl-question"},
        h("div", {class: "q"}, req.title),
        h(
          "div",
          {class: "opts"},
          req.variants.map((v) => h("button", {title: v.body, onclick: () => ctx.act("/api/pipeline/choose", {id: v.id})}, v.title)),
        ),
      ),
    );
  }
  return wrap;
}

// ------------------------------------------------------------------ ворота

async function loadGate(ctx, key, panel) {
  try {
    const data = await ctx.api("/api/pipeline/gate?key=" + encodeURIComponent(key));
    panel.replaceChildren(...gateBody(ctx, data));
  } catch (error) {
    panel.textContent = error.message;
  }
}

function gateBody(ctx, data) {
  const run = data.run;
  const running = ctx.state.job && ctx.state.job.running;
  const out = [];
  const stage = ctx.state.stages.find((s) => s.n === data.gate.stage);
  out.push(
    h(
      "div",
      {class: "pl-ghead"},
      h("b", null, data.gate.title),
      h("span", {class: "pl-gstate"}, run ? GATE_STATE_NAMES[run.state] + (run.round > 1 ? ` · круг ${run.round}` : "") : GATE_STATE_NAMES.none),
      h("span", {class: "spacer"}),
      stage && stage.wired
        ? h("button", {class: "pl-primary", disabled: running, onclick: () => runStep(ctx, data.gate.stage, "gate_" + data.gate.key)}, run ? "Запустить заново" : "Запустить ворота")
        : h("span", {class: "hint"}, "этап ещё не подключён"),
    ),
  );
  out.push(h("div", {class: "pl-layers"}, layer("1 · Проверки кодом", run ? (run.code.length ? "bad" : "ok") : ""), layer("2 · Независимая оценка", run && run.ratings ? (run.failing.length ? "bad" : "ok") : ""), layer("3 · Ваше решение", run && run.state === "passed" ? "ok" : "")));
  if (!run) {
    out.push(criteriaList(data.criteria));
    return out;
  }
  if (run.code.length) out.push(findings(ctx, data));
  if (run.ratings) out.push(ratingTable(data.criteria, run));
  else out.push(criteriaList(data.criteria));
  out.push(decision(ctx, data, run, running));
  return out;
}

function layer(name, status) {
  return h("span", {class: "pl-layer " + status}, name);
}

function criteriaList(criteria) {
  return h(
    "div",
    {class: "pl-criteria"},
    h("div", {class: "pl-label"}, "Критерии независимой оценки (правятся в Настройках; нужен каждый ≥ порога)"),
    criteria.filter((c) => c.enabled).map((c) => h("div", {class: "row"}, h("b", null, c.title), h("span", {class: "hint"}, " — ", c.hint), chip("≥ " + c.threshold))),
  );
}

function ratingTable(criteria, run) {
  const failing = new Set((run.failing || []).map((f) => f.key));
  const rows = criteria.filter((c) => c.enabled).map((c) => {
    const r = (run.ratings || {})[c.key];
    const bad = failing.has(c.key);
    return h(
      "tr",
      {class: bad ? "bad" : ""},
      h("td", null, c.title),
      h("td", {class: "num"}, r ? r.score : "—"),
      h("td", {class: "num"}, "≥ " + c.threshold),
      h("td", null, r ? r.evidence || "нет доказательства" : "нет оценки"),
    );
  });
  return h("table", {class: "pl-table"}, h("thead", null, h("tr", null, h("th", null, "Критерий"), h("th", null, "Оценка"), h("th", null, "Порог"), h("th", null, "Доказательство"))), h("tbody", null, rows));
}

function findings(ctx, data) {
  const box = h("div", {class: "pl-findings"}, h("div", {class: "pl-label"}, "Проверки кодом не пройдены — это блокирует ворота"));
  for (const finding of data.findings) {
    const item = h("div", {class: "finding"}, h("span", null, finding.message));
    if (finding.code === "orphan" || finding.code === "duplicate" || finding.code === "unknown_participant" || finding.code === "stale") {
      for (const rec of finding.records) {
        item.append(h("span", {class: "acts"}, ...recordActions(ctx, rec, finding.code)));
      }
    }
    box.append(item);
  }
  return box;
}

function recordActions(ctx, rec, code) {
  const buttons = [];
  if (code === "orphan" && rec.parent_kinds.length) {
    buttons.push(h("button", {title: "Привязать к родителю: запись станет частью цепочки", onclick: () => linkDialog(ctx, rec)}, "Внедрить…"));
  }
  if (rec.status !== "accepted" && code !== "unknown_participant") {
    buttons.push(h("button", {class: "danger", title: "Отклонить: уйдёт в архив, принять её как есть нельзя", onclick: () => ctx.act("/api/pipeline/record/reject", {id: rec.id})}, "Отклонить"));
  }
  buttons.push(h("button", {class: "ghost", onclick: () => ctx.go("bank", {record: rec.id})}, "Открыть"));
  return buttons;
}

async function linkDialog(ctx, rec) {
  const candidates = [];
  for (const kind of rec.parent_kinds) {
    const data = await ctx.api("/api/pipeline/bank?kind=" + kind);
    candidates.push(...data.records.filter((r) => r.status !== "rejected"));
  }
  if (!candidates.length) return ctx.toast("Подходящих родителей пока нет", "err");
  const select = h("select", null, candidates.map((c) => h("option", {value: c.id}, `${c.kind_title}: ${c.title}`)));
  const dialog = h("div", {class: "pl-dialog"}, h("div", null, `Привязать «${rec.title}» к:`), select, h("button", {class: "pl-primary", onclick: async () => { dialog.remove(); await ctx.act("/api/pipeline/record/link", {child: rec.id, parent: select.value}); }}, "Привязать"), h("button", {class: "ghost", onclick: () => dialog.remove()}, "Отмена"));
  ctx.host.append(dialog);
}

function decision(ctx, data, run, running) {
  const box = h("div", {class: "pl-decision"});
  if (run.state === "passed") {
    box.append(h("span", {class: "ok"}, "Принято", run.below ? " ниже порога (записи помечены)" : "", run.by ? ` · решил: ${run.by}` : ""));
    return box;
  }
  if (run.stuck) box.append(h("div", {class: "pl-warn"}, "Круги доработки закончились — решайте сами: правьте записи или принимайте ниже порога."));
  const note = h("input", {type: "text", class: "pl-note", placeholder: "замечание агентам (необязательно)"});
  if (run.state === "ready") {
    box.append(h("button", {class: "pl-primary", disabled: running, onclick: () => ctx.act("/api/pipeline/gate/decide", {run: run.id, decision: "accept"})}, "Принять"));
  }
  if (run.state === "returned" && !run.code.length && run.failing && run.failing.length) {
    box.append(h("button", {class: "warn", disabled: running, title: "Записи получат пометку «принято ниже порога»", onclick: () => { if (confirm("Принять ниже порога? Записи получат пометку.")) ctx.act("/api/pipeline/gate/decide", {run: run.id, decision: "accept_below"}); }}, "Принять ниже порога"));
  }
  if (["ready", "returned"].includes(run.state)) {
    box.append(h("button", {disabled: running, title: "Агент-автор поправит черновики по замечаниям и ворота пройдут заново", onclick: () => ctx.act("/api/pipeline/gate/return", {run: run.id})}, "Доработать агентом"));
  }
  if (run.state === "ready") {
    box.append(note, h("button", {class: "ghost", disabled: running, onclick: () => ctx.act("/api/pipeline/gate/decide", {run: run.id, decision: "return", note: note.value})}, "Вернуть"));
  }
  return box;
}

export {statusChip};
