/* Экран «Путь»: пять этапов с шагами и воротами, действия человека и решения на воротах. */

import {GATE_STATE_NAMES, STATUS_NAMES, chip, h, statusChip} from "./dom.js";
import {renderMap} from "./maps.js";

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
  const debt = debtBar(ctx);
  if (debt) body.append(debt);
  if (state.warnings && state.warnings.length) {
    body.append(h("div", {class: "pl-warn"}, state.warnings.map((text) => h("div", null, "⚠ ", text))));
  }
  if (ctx.openGateOnce) {
    ctx.openGateOnce = false;
    const gates = state.stages.flatMap((stage) => stage.gates);
    const failed = gates.find((gate) => gate.state === "returned") || gates.find((gate) => gate.state === "ready" && gate.human);
    if (failed) ctx.gate = failed.key;
  }
  for (const stage of state.stages) body.append(stageCard(ctx, stage));
}

function nextBar(ctx) {
  const state = ctx.state;
  const running = state.job && state.job.running;
  const next = state.next;
  const bar = h("div", {class: "pl-next"});
  if (!next) {
    const asked = (state.questions || []).length;
    bar.append(h("b", null, "Сейчас: "), asked ? "у студии есть вопросы к вам — ответьте на них ниже, и она пойдёт дальше." : "все подключённые шаги пройдены или ждут вашего решения на воротах.");
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
  if (gate) ctx.track(loadGate(ctx, gate.key, card.querySelector(".pl-gatepanel")));
  return card;
}

function stepsRow(ctx, stage) {
  const running = ctx.state.job && ctx.state.job.running;
  const items = stage.steps.filter((s) => s.num !== "gate");
  return h(
    "div",
    {class: "pl-steps"},
    items.map((step) => {
      const agent = step.role && !["area"].includes(step.key) && step.key !== "questions";  // «1.2» — диалог, не повторяется
      const runnable = step.key !== "area" && !step.key.startsWith("planned_");
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
        gate.state === "returned" && gate.why ? h("span", {class: "pl-gwhy"}, gate.why) : null,
        gate.human ? h("span", {class: "pl-flag", title: "Нужно ваше решение"}, "!") : null,
      ),
    ),
  );
}

// ------------------------------------------------------------------ шаги с вводом человека

function stagePanel(ctx, stage) {
  if (stage.n === 1) {
    const parts = [areaPanel(ctx), questionsPanel(ctx), reviewPanel(ctx)].filter(Boolean);
    return parts.length ? h("div", {class: "pl-panel"}, parts) : null;
  }
  if (stage.n === 2) {
    const unchosen = ctx.state.unchosen || [];
    return unchosen.length ? h("div", {class: "pl-panel"}, variantsPanel(ctx, unchosen)) : null;
  }
  if (stage.n === 3) {
    const style = stage.steps.find((step) => step.key === "style");
    const parts = [wavesPanel(ctx), style && style.state === "available" ? stylePanel(ctx) : null].filter(Boolean);
    return parts.length ? h("div", {class: "pl-panel"}, parts) : null;
  }
  return null;
}

// ------------------------------------------------------------------ волны (по желанию)

function wavesPanel(ctx) {
  const waves = ctx.state.waves;
  if (!waves) return null;
  const running = ctx.state.job && ctx.state.job.running;
  if (waves.on) {
    const title = waves.finished ? "Волны: все пройдены" : `Волны: идёт ${waves.current} из ${waves.total}`;
    return h(
      "div",
      {class: "pl-block"},
      h("div", {class: "pl-label"}, title),
      h(
        "div",
        {class: "pl-waves"},
        waves.packages.map((pack) =>
          h("span", {class: "pl-wave " + pack.state, title: pack.scenarios.map((s) => s.title).join("; ")}, `Волна ${pack.index} · сценариев ${pack.scenarios.length}`),
        ),
      ),
      h("div", {class: "hint"}, "Этапы 3–4 идут по очереди для каждой волны со своими воротами; после бэкенда волны — следующая, после последней — этап 5. Сценарии с общими таблицами идут вместе."),
      h(
        "div",
        {class: "pl-actions"},
        waves.finished ? null : h("button", {disabled: running, onclick: () => ctx.act("/api/pipeline/waves/enable")}, "Пересобрать пакеты"),
        h("button", {class: "ghost", disabled: running, onclick: () => ctx.act("/api/pipeline/waves/disable")}, "Выключить волны"),
      ),
    );
  }
  const offer = waves.suggestion;
  if (!offer) return null;
  return h(
    "div",
    {class: "pl-block"},
    h("div", {class: "pl-label"}, "Волны"),
    h("p", null, `Сценариев ${offer.scenarios} — больше порога ${offer.threshold}. Разбить этапы 3–4 на ${offer.packages} волны? Каждая волна — рабочий срез: свои страницы, тесты и ворота.`),
    h(
      "div",
      {class: "pl-actions"},
      h("button", {class: "pl-primary", disabled: running, onclick: () => ctx.act("/api/pipeline/waves/enable")}, "Включить волны"),
      h("button", {class: "ghost", disabled: running, onclick: () => ctx.act("/api/pipeline/waves/disable")}, "Не нужно, всё сразу"),
    ),
  );
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
  const studio = questions.some((q) => q.attrs && q.attrs.source);
  const running = ctx.state.job && ctx.state.job.running;
  const queued = ctx.state.queued || 0;
  const label = studio ? "Вопрос студии — следующий вопрос зависит от вашего ответа" + (queued ? ` (в очереди ещё: ${queued})` : "") : "1.2 Уточняющие вопросы — ответьте";
  const wrap = h("div", {class: "pl-block"}, h("div", {class: "pl-label"}, label));
  for (const q of questions) {
    const input = h("input", {type: "text", placeholder: "или свой ответ"});
    const send = (answer) => ctx.act("/api/pipeline/answer", {id: q.id, answer});
    const topic = q.attrs.topic || "";
    const finish = topic && !topic.startsWith("confirm.") && q.attrs.source !== "author"
      ? h("button", {class: "ghost", disabled: running, title: "Хватит вопросов по этой теме: студия пойдёт дальше по тому, что уже сказано", onclick: () => ctx.act("/api/pipeline/interview/finish", {topic})}, topic.startsWith("gate.") ? "Достаточно, дорабатывай" : "Достаточно")
      : null;
    wrap.append(
      h(
        "div",
        {class: "pl-question"},
        h("div", {class: "q"}, q.body || q.title),
        h(
          "div",
          {class: "opts"},
          (q.attrs.options || []).map((option) => h("button", {class: q.attrs.recommended === option ? "rec" : "", title: q.attrs.recommended === option ? "студия считает этот вариант наиболее вероятным по вашему диалогу" : "", onclick: () => send(option)}, option)),
          input,
          h("button", {class: "ghost", onclick: () => input.value.trim() && send(input.value.trim())}, "Ответить"),
          h("button", {class: "ghost", disabled: running, title: "Студия ответит сама по своим правилам", onclick: () => ctx.act("/api/pipeline/answer/auto", {ids: [q.id]})}, "Решить за меня"),
          q.attrs.topic && !q.attrs.topic.startsWith("confirm.") ? h("button", {class: "ghost", disabled: running, title: "По этому пункту требований нет: студия не будет о нём спрашивать", onclick: () => send("Не важно")}, "Не важно") : null,
          finish,
        ),
      ),
    );
  }
  return wrap;
}

let reviewDraft = "";
const ACTION_NAMES = {revise: "Изменить", add: "Добавить", reject: "Отклонить"};

function reviewPanel(ctx) {
  const review = ctx.state.review;
  if (!review || !review.digest) return null;
  const running = ctx.state.job && ctx.state.job.running;
  const wrap = h("div", {class: "pl-block pl-review"}, h("div", {class: "pl-label"}, "Что сейчас в «Идее» — простыми словами (обновляется после каждого шага)"));
  if (review.digest.text) wrap.append(...review.digest.text.split(String.fromCharCode(10, 10)).map((part) => h("p", {class: "pl-quote"}, part)));
  const details = h("details", null, h("summary", null, "Все записи по разделам"));
  for (const section of review.digest.sections || []) {
    details.append(h("div", {class: "pl-label"}, section.title), h("ul", {class: "lines"}, section.items.map((item) => h("li", null, `[${item.id}] ${item.text}`, item.criteria && item.criteria.length ? h("ul", null, item.criteria.map((c) => h("li", {class: "hint"}, `критерий [${c.id}]: ${c.text}`))) : null))));
  }
  wrap.append(details);
  const send = async (text) => { if (await ctx.act("/api/pipeline/review/say", {text})) reviewDraft = ""; };
  const thread = review.thread || [];
  thread.forEach((message, index) => {
    if (message.who === "user") {
      wrap.append(h("div", {class: "pl-quote"}, h("b", null, "Вы: "), message.text));
      return;
    }
    const box = h("div", {class: "pl-advice sub"});
    if (message.understanding) box.append(h("div", null, h("b", null, "Как я вас понял: "), message.understanding));
    if (message.impact.length) box.append(h("div", {class: "pl-label"}, "Как это повлияет на систему"), h("ul", {class: "lines"}, message.impact.map((i) => h("li", null, i))));
    if (message.conflicts.length) box.append(h("div", {class: "pl-warn"}, h("b", null, "Противоречия, которые нужно снять: "), message.conflicts.map((c) => h("div", null, "• " + c.what + (c.records.length ? ` (${c.records.join(", ")})` : "")))));
    for (const q of message.questions) {
      box.append(h("div", {class: "pl-question"}, h("div", {class: "q"}, q.text), h("div", {class: "opts"}, q.options.map((option) => h("button", {disabled: running, onclick: () => send(`${q.text} — ${option}`)}, option)))));
    }
    if (message.plan.length) {
      box.append(h("div", {class: "pl-label"}, "План правок (ничего не изменится без вашего «Применить»)"), h("ul", {class: "lines"}, message.plan.map((step) => h("li", null, h("b", null, ACTION_NAMES[step.action] + " "), step.action === "add" ? `${step.title} (под ${step.parent})` : `[${step.id}] ${step.label}${step.title ? " → " + step.title : ""}`, step.why ? h("div", {class: "hint"}, step.why) : null))));
    }
    if (index === thread.length - 1) {
      box.append(h("div", {class: "acts"},
        review.ready ? h("button", {class: "pl-primary", disabled: running, onclick: () => ctx.act("/api/pipeline/review/apply")}, "Применить правки") : h("span", {class: "hint"}, "Применить можно, когда противоречия сняты и вопросы закрыты: ответьте или уточните ещё."),
        h("button", {class: "ghost", disabled: running, onclick: () => ctx.act("/api/pipeline/review/cancel")}, "Отменить обсуждение")));
    }
    wrap.append(box);
  });
  const area = h("textarea", {rows: 3, placeholder: "Свободное уточнение (необязательно): что бы вы изменили? Студия обсудит, как это повлияет на систему, и ничего не изменит без вашего согласия.", "data-focus": "review-note", oninput: () => { reviewDraft = area.value; }});
  area.value = reviewDraft;
  wrap.append(area, h("div", {class: "acts"}, h("button", {disabled: running, onclick: () => area.value.trim() && send(area.value.trim())}, thread.length ? "Ответить студии" : "Обсудить уточнение")));
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
  const mapKind = {2: "er", 3: "front", 4: "back", 5: "trace"}[data.gate.stage];
  if (mapKind) {
    const holder = h("div", {class: "pl-map", hidden: !ctx.showMap});
    out.push(h("div", null, h("button", {class: "ghost", onclick: () => { ctx.showMap = !ctx.showMap; ctx.go("path"); }}, ctx.showMap ? "Скрыть карту этапа" : "Показать карту этапа"), holder));
    if (ctx.showMap) ctx.track(renderMap(ctx, holder, mapKind));
  }
  if (run && run.state !== "passed" && data.advice) out.push(adviceBlock(ctx, data, run, running));
  out.push(h("div", {class: "pl-layers"}, layer("1 · Проверки кодом", run ? (run.code.length ? "bad" : "ok") : ""), layer("2 · Независимая оценка", run && run.ratings ? (run.failing.length ? "bad" : "ok") : ""), layer("3 · Ваше решение", run && run.state === "passed" ? "ok" : "")));
  if (!run) {
    out.push(criteriaList(data.criteria));
    return out;
  }
  if (run.code.length) out.push(findings(ctx, data, running));
  if (run.ratings) out.push(ratingTable(data.criteria, run));
  else out.push(criteriaList(data.criteria));
  if ((run.failing || []).length) out.push(criteriaAdvice(ctx, data, run, running));
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

function adviceButton(ctx, option, run, running, record) {
  const attrs = {class: option.recommended ? "pl-primary" : "", disabled: running, title: option.hint || ""};
  const act = (path, body) => () => ctx.act(path, body);
  if (option.key === "rework") return h("button", {...attrs, onclick: act("/api/pipeline/gate/return", {run: run.id})}, option.label);
  if (option.key === "reject_all") return h("button", {...attrs, onclick: act("/api/pipeline/gate/fix", {run: run.id, action: "reject_all"})}, option.label);
  if (option.key === "rerate") return h("button", {...attrs, onclick: act("/api/pipeline/gate/fix", {run: run.id, action: "rerate"})}, option.label);
  if (option.key === "integrate") return h("button", {...attrs, onclick: act("/api/pipeline/gate/fix", {run: run.id, action: "integrate"})}, option.label);
  if (option.key === "dedupe") return h("button", {...attrs, onclick: act("/api/pipeline/gate/fix", {run: run.id, action: "dedupe"})}, option.label);
  if (option.key === "settings") return h("button", {...attrs, onclick: () => ctx.go("settings")}, option.label);
  if (option.key === "accept") return h("button", {...attrs, onclick: act("/api/pipeline/gate/decide", {run: run.id, decision: "accept"})}, option.label);
  const manual = ctx.state.mode === "manual"; // руками — только в «Вручную»
  if (!manual && ["accept_below", "open", "link", "reject"].includes(option.key)) return null;
  if (option.key === "accept_below") {
    return h("button", {...attrs, class: "warn", onclick: () => { if (confirm("Принять ниже порога? Записи получат пометку.")) ctx.act("/api/pipeline/gate/decide", {run: run.id, decision: "accept_below"}); }}, option.label);
  }
  if (option.key === "open") return h("button", {...attrs, class: "ghost", onclick: () => ctx.go("bank", record ? {record: record.id} : undefined)}, option.label);
  if (option.key === "link" && record) return h("button", {...attrs, onclick: () => linkDialog(ctx, record, run)}, `${option.label} «${record.title}»…`);
  if (option.key === "reject" && record) return h("button", {...attrs, class: "danger", onclick: async () => { if (await ctx.act("/api/pipeline/record/reject", {id: record.id})) recheck(ctx, run); }}, `${option.label} «${record.title}»`);
  return h("span", {class: "pl-hintopt", title: option.hint || ""}, option.label, option.hint ? " — " + option.hint : "");
}

function adviceBlock(ctx, data, run, running) {
  const advice = data.advice;
  const title = run.state === "ready" ? "Ворота пройдены, ждут вашего решения" : "Почему ворота не пройдены и что делать";
  const box = h("div", {class: "pl-advice"}, h("div", {class: "pl-label"}, title));
  if (advice.summary) box.append(h("div", {class: "summary"}, advice.summary));
  if (advice.lines.length) {
    const more = advice.lines.length > 8 ? h("li", {class: "hint"}, `…и ещё ${advice.lines.length - 8} (ниже, в списках)`) : null;
    box.append(h("ul", {class: "lines"}, advice.lines.slice(0, 8).map((line) => h("li", null, line)), more));
  }
  if (advice.audit) box.append(auditBlock(ctx, advice.audit, run, running));
  if (advice.actions.length) box.append(h("div", {class: "acts"}, h("span", {class: "hint"}, "Варианты: "), advice.actions.map((option) => adviceButton(ctx, option, run, running, null))));
  if (run.stuck) box.append(h("div", {class: "pl-warn"}, "Оценка не растёт несколько кругов подряд — нужно ваше решение: поправьте записи сами, согласуйте правку выше по цепочке или (на ваше усмотрение) примите ниже порога."));
  return box;
}

function debtBar(ctx) {
  const debt = ctx.state.debt;
  if (!debt || !debt.count) return null;
  const running = ctx.state.job && ctx.state.job.running;
  const where = debt.gates.map((g) => `${g.title}: ${g.count}`).join("; ");
  const auto = ctx.state.mode === "autopilot";
  const box = h("div", {class: "pl-advice"}, h("div", {class: "pl-label"}, `Записей, принятых ниже порога: ${debt.count}`), h("div", {class: "summary"}, where));
  box.append(h("div", {class: "hint"}, "Это долг качества: всё выше по цепочке опирается на сомнительное. Агент доведёт каждую запись до порога через аудит либо запись отклонят. Идёт сверху вниз, с самых ранних ворот."));
  if (auto) box.append(h("div", {class: "hint"}, "В режиме «Автопилот» он начнёт с этого при запуске."));
  box.append(h("div", {class: "acts"}, h("button", {class: "pl-primary", disabled: running, title: "Записи станут «устарело», зависимое принятое тоже, ворота пройдут заново с аудитом", onclick: () => ctx.act("/api/pipeline/debt/reopen")}, "Довести до порога")));
  return box;
}

function auditBlock(ctx, audit, run, running) {
  const box = h("div", {class: "pl-audit"}, h("div", {class: "pl-label"}, "Аудит: в чём причина и что исправить"));
  if (audit.summary) box.append(h("div", {class: "summary"}, audit.summary));
  for (const item of audit.items || []) {
    const where = item.records && item.records.length ? ` (записи ${item.records.join(", ")})` : "";
    const head = item.cause === "upstream" ? `${item.criterion}: причина выше — запись ${item.target}` : `${item.criterion}${where}`;
    box.append(h("div", {class: "finding"}, h("b", null, head), h("div", null, item.defect), h("div", {class: "hint"}, item.cause === "upstream" ? (ctx.state.mode === "manual" ? "Подан запрос на изменение принятой записи (вкладка «Запросы»): решите его сами." : "Правка принятой записи решается вопросом студии: ответьте на него вверху экрана «Путь».") : `Что исправить: ${item.fix}`), item.evaluator_ok === false ? h("div", {class: "pl-warn"}, "Аудитор сомневается в замечании оценщика: по записям оно подтверждается не полностью.") : null, item.cause === "ask" ? h("div", {class: "hint"}, "Студия задала вопросы — ответьте на них вверху экрана «Путь»; дальше она сама доработает этап.") : null, ctx.state.mode === "manual" && item.action === "reject" && item.records && item.records.length ? h("div", {class: "acts"}, h("span", {class: "hint"}, "Аудитор считает, что довести нельзя: "), h("button", {class: "danger", disabled: running, onclick: async () => { for (const id of item.records) { if (!(await ctx.act("/api/pipeline/record/reject", {id, note: "аудит: довести до порога нельзя"}))) return; } recheck(ctx, run); }}, `Отклонить (${item.records.length})`)) : null));
  }
  return box;
}

function criteriaAdvice(ctx, data, run, running) {
  const box = h("div", {class: "pl-advice sub"}, h("div", {class: "pl-label"}, "Критерии ниже порога — что можно сделать"));
  for (const item of run.failing) {
    const options = (data.advice.criteria || {})[item.key] || [];
    const text = item.missing ? "оценщик не вернул оценку" : `${item.score} из ${item.threshold || "?"} — ${item.why}`;
    box.append(h("div", {class: "finding"}, h("span", null, `«${item.title}»: ${text}`), h("span", {class: "acts"}, options.map((option) => adviceButton(ctx, option, run, running, null)))));
  }
  return box;
}

function findings(ctx, data, running) {
  const run = data.run;
  const box = h("div", {class: "pl-findings"}, h("div", {class: "pl-label"}, "Проверки кодом не пройдены — это блокирует ворота"));
  for (const finding of data.findings) {
    const item = h("div", {class: "finding"}, h("span", null, finding.message));
    const acts = h("span", {class: "acts"});
    const records = finding.records || [];
    for (const option of finding.advice || []) {
      if (option.key === "link") records.filter((rec) => rec.parent_kinds && rec.parent_kinds.length).forEach((rec) => acts.append(adviceButton(ctx, option, run, running, rec)));
      else if (option.key === "reject") (finding.code === "duplicate" ? records.slice(1) : records).filter((rec) => rec.status !== "accepted").forEach((rec) => acts.append(adviceButton(ctx, option, run, running, rec)));
      else acts.append(adviceButton(ctx, option, run, running, records[0]));
    }
    item.append(acts);
    box.append(item);
  }
  return box;
}

/* После правки записи ворота проверяются заново: иначе на экране остаётся прежний результат,
   и кажется, что ничего не произошло. */
function recheck(ctx, run) {
  if (run) ctx.act("/api/pipeline/gate/fix", {run: run.id, action: "rerate"});
}

async function linkDialog(ctx, rec, run) {
  const candidates = [];
  for (const kind of rec.parent_kinds) {
    const data = await ctx.api("/api/pipeline/bank?kind=" + kind);
    candidates.push(...data.records.filter((r) => r.status !== "rejected"));
  }
  if (!candidates.length) return ctx.toast("Подходящих родителей пока нет", "err");
  const select = h("select", null, candidates.map((c) => h("option", {value: c.id}, `${c.kind_title}: ${c.title}`)));
  const dialog = h("div", {class: "pl-dialog"}, h("div", null, `Привязать «${rec.title}» к:`), select, h("button", {class: "pl-primary", onclick: async () => { dialog.remove(); if (await ctx.act("/api/pipeline/record/link", {child: rec.id, parent: select.value})) recheck(ctx, run); }}, "Привязать"), h("button", {class: "ghost", onclick: () => dialog.remove()}, "Отмена"));
  ctx.host.append(dialog);
}

function decision(ctx, data, run, running) {
  const box = h("div", {class: "pl-decision"});
  if (run.state === "passed") {
    box.append(h("span", {class: "ok"}, "Принято", run.below ? " ниже порога (записи помечены)" : "", run.by ? ` · решил: ${run.by}` : ""));
    return box;
  }
  if (run.stuck) box.append(h("div", {class: "pl-warn"}, "Оценка не растёт несколько кругов подряд — нужно ваше решение: поправьте записи сами, согласуйте правку выше по цепочке или (на ваше усмотрение) примите ниже порога."));
  const note = h("input", {type: "text", class: "pl-note", placeholder: "замечание агентам (необязательно)"});
  if (run.state === "ready") {
    box.append(h("button", {class: "pl-primary", disabled: running, onclick: () => ctx.act("/api/pipeline/gate/decide", {run: run.id, decision: "accept"})}, "Принять"));
  }
  if (ctx.state.mode === "manual" && run.state === "returned" && !run.code.length && run.failing && run.failing.length) {
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
