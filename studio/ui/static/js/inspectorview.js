import {api} from "./api.js";
import {
  $, S, anchorText, applyGraph, copyNodeLocation, esc, hooks, schedulePersist,
  nestedOf, notesOf, openEditorForNode, setSaveState, todoBadgeHtml,
  toast, KIND_NAMES, ROLE_NAMES,
} from "./core.js";
import {saveLabel} from "./savestate.js";
import {isTodo} from "./todo.js";

let saveInFlight=null;
function editableName(node){
  const details=node.details||{};
  return details.function_name||details.class_name||details.test_name||details.name||details.method||node.label;
}
function graphOwned(node){return !!node?.planned&&node.id.startsWith("studio:")}
function removablePlanBranch(rootId){
  const nodes=S.byId||{},edges=S.G?.edges||[],removable=new Set([rootId]);
  let changed=true;
  while(changed){
    changed=false;
    for(const edge of edges){
      const parent=edge.kind==="tests"?edge.to_node:edge.from_node;
      const childId=edge.kind==="tests"?edge.from_node:edge.to_node;
      if(!removable.has(parent)||removable.has(childId))continue;
      const child=nodes[childId];
      if(!graphOwned(child))continue;
      const parents=edges.filter(item=>(item.kind==="tests"?item.from_node:item.to_node)===childId);
      const onlyThisBranch=parents.every(item=>removable.has(item.kind==="tests"?item.to_node:item.from_node));
      if(onlyThisBranch){removable.add(childId);changed=true;}
    }
  }
  return [...removable].map(id=>nodes[id]).filter(Boolean);
}

function renderInspector(){
  const chips=$("inspChips"),body=$("inspBody"),foot=$("inspFoot");
  chips.innerHTML="";body.innerHTML="";
  if(!S.selectedNode||!S.byId[S.selectedNode]){
    body.innerHTML='<p class="hint">Выберите узел на карте, чтобы добавить к нему плановый узел. Структура найденных в коде узлов меняется в IDE; описания и плановые карточки студия хранит в .studio/metadata.json.</p><button id="emptyInspectorPlans" class="primary" type="button">Создать сценарий целиком →</button>';
    $("emptyInspectorPlans")?.addEventListener("click",()=>$("workspace-plans-button")?.click());
    foot.textContent="Правки сохраняются при потере фокуса.";return;
  }
  const n=S.byId[S.selectedNode];
  chips.innerHTML=`<span class="chip acc">${esc(KIND_NAMES[n.kind]||n.kind)}</span>${todoBadgeHtml(n)}${notesOf(n).length?'<span class="chip warn">неточно</span>':""}<span class="chip">${esc(n.file_path)}${n.planned?"":":"+n.line}</span>`;
  let h="";
  h+=n.planned
    ?`<div class="node-origin plan-origin"><b>Плановый узел</b><span>Кода пока нет · описание и связи хранятся в .studio/metadata.json.</span><span>${graphOwned(n)?"Название, задача и критерии редактируются здесь.":"Описание и критерии редактируются здесь; файлы создаются в «Планы и создание»."}</span></div>`
    :`<div class="node-origin code-origin"><b>Узел из кода</b><span>Источник структуры — ${esc(n.file_path)}.</span><span>Код меняется в IDE; здесь редактируются описание и критерии.</span></div>`;
  if(n.planned)h+=`<div class="insp-todo">Этот узел — TODO · Файл для реализации: <b>${esc((n.details||{}).target_file||n.file_path)}</b></div>`;
  h+=`<div class="kv"><span>${n.planned?"узел":"якорь"}</span><b>${esc(anchorText(n))}</b></div>`;
  h+=`<div class="insp-actions"><button id="openEditorBtn" type="button">Открыть в VS Code</button><button id="copyLocBtn" type="button">Копировать путь:строка</button></div>`;
  h+=`<details class="field graph-add" open><summary>＋ Добавить узел в эту ветку</summary>
    <p class="graph-add-help">Создаст плановую карточку и связь с «${esc(n.label)}». Для нового сценария целиком используйте «Планы и создание».</p>
    <button type="button" class="link graph-plans-link" id="graphPlansLink">Создать сценарий целиком →</button>
    <form id="graphNodeCreate" class="graph-node-form">
    <label>Какой узел добавить<select name="kind"><option value="scenario">Сценарий</option><option value="io">IO</option><option value="model">Модель</option><option value="test">Тест</option><option value="nested">Дополнительный узел</option></select></label>
    <div class="graph-relation" id="graphRelation" role="status"></div>
    <label>Название<input name="name" required maxlength="180" placeholder="Например: LoadOrderIO"></label>
    <label>Описание<textarea name="description" rows="3" placeholder="Назначение узла"></textarea></label>
    <div class="graph-form-actions"><button type="submit" class="primary">＋ Создать плановый узел</button></div>
  </form></details>`;
  if(notesOf(n).length){
    h+=`<div class="field yellow-block"><label>Почему жёлтый</label>`+
      notesOf(n).map(x=>`<div class="yellow-note"><b>${esc(x.code)}</b> — ${esc(x.message)}${x.hint?`<div class="hint">${esc(x.hint)}</div>`:""}</div>`).join("")+
      `</div>`;
  }
  const extraRoutes=(n.details&&n.details.extra_routes)||[];
  if(extraRoutes.length){
    h+=`<div class="field"><label>Ещё маршруты</label><div class="hint" style="line-height:1.6">`+
      extraRoutes.map(route=>`${esc(String(route.http_method||"").toUpperCase())} ${esc(route.url_path||route.path||"")}`).join("<br>")+
      `</div></div>`;
  }
  if(graphOwned(n)){
    h+=`<div class="field"><label>Название узла</label><input id="fName" maxlength="180" value="${esc(editableName(n))}"></div>`;
    if(n.kind==="endpoint")h+=`<div class="field"><label>HTTP-метод</label><input id="fHttpMethod" value="${esc((n.details||{}).http_method||"")}" placeholder="GET"><label style="margin-top:8px">Путь</label><input id="fHttpPath" value="${esc((n.details||{}).path||"")}" placeholder="/orders/{id}"></div>`;
  }
  h+=`<div class="field"><label>${isTodo(n)?"Что сделать":"Описание"}</label><textarea id="fDesc" rows="5">${esc(n.description)}</textarea></div>`;
  h+=`<div class="field"><label>${isTodo(n)?"Что сделать: критерии приёмки":"Критерии приёмки (по одному на строку)"}</label><textarea id="fAcc" rows="5">${esc((n.acceptance||[]).join("\n"))}</textarea></div>`;
  if(n.kind==="endpoint"){
    h+=`<div class="field"><label>Домен</label><input id="fDomain" value="${esc(n.domain)}" placeholder="например, orders"><div class="note">Домен назначается только эндпоинту; остальные узлы наследуют его автоматически.</div></div>`;
  }else{
    h+=`<div class="field"><label>Домен (наследуется)</label><input value="${esc(n.domain)}" disabled><div class="note">Домен нельзя назначить этому узлу: он наследуется от связанных эндпоинтов.</div></div>`;
  }
  if(n.planned){
    h+=`<div class="field"><div class="note">Это визуальный TODO-узел. Правки сохраняются в .studio/metadata.json; исходный код не меняется.</div></div>`;
  }
  const nestedItems=nestedOf(n.id);
  if(nestedItems.length){
    h+=`<div class="field"><label>Вложенные узлы</label><div class="hint nested-inspector" style="line-height:1.6">`+
      nestedItems.map(p=>`<button type="button" class="link nested-insp" data-nested="${esc(p.id)}">${esc(p.label)}</button> — ${esc(ROLE_NAMES[(p.details||{}).role]||(p.details||{}).role||"")} · ${esc(p.file_path)}:${p.line}`).join("<br>")+
      `</div></div>`;
  }
  if(graphOwned(n))h+=`<button id="deleteGraphNode" class="danger graph-delete" type="button">Удалить плановый узел и ветку…</button>`;
  body.innerHTML=h;
  const openBtn=$("openEditorBtn");if(openBtn)openBtn.onclick=()=>openEditorForNode(n);
  const copyBtn=$("copyLocBtn");if(copyBtn)copyBtn.onclick=()=>copyNodeLocation(n);
  document.querySelectorAll(".nested-insp").forEach(button=>{
    button.onclick=()=>{S.selectedNode=button.dataset.nested;hooks.renderScenario();renderInspector()};
  });
  const createForm=$("graphNodeCreate");
  $("graphPlansLink")?.addEventListener("click",()=>$("workspace-plans-button")?.click());
  if(createForm){
    const kindField=createForm.elements.kind,relation=$("graphRelation");
    const preferred={endpoint:"scenario",scenario:"io",io:"model",model:"nested",test:"nested",nested:"nested"}[n.kind];
    if(preferred)kindField.value=preferred;
    const relations={
      endpoint:{scenario:["dispatch","эндпоинт вызывает сценарий"],nested:["uses","дополнительный узел относится к эндпоинту"]},
      scenario:{scenario:["scenario_call","сценарий вызывает сценарий"],io:["port_call","сценарий вызывает IO"],test:["tests","тест проверяет сценарий"],nested:["uses","дополнительный узел относится к сценарию"]},
      io:{model:["table","IO работает с моделью / таблицей"],nested:["uses","дополнительный узел относится к IO"]},
      model:{nested:["uses","дополнительный узел относится к модели"]},
      test:{nested:["uses","дополнительный узел относится к тесту"]},
      nested:{nested:["uses","дополнительный узел относится к этому узлу"]},
    };
    const applySuggestedEdge=()=>{
      const choices=relations[n.kind]||{};
      const allowed=Object.keys(choices);
      [...kindField.options].forEach(option=>{option.hidden=!allowed.includes(option.value);option.disabled=!allowed.includes(option.value)});
      if(!allowed.includes(kindField.value))kindField.value=allowed[0];
      const selected=choices[kindField.value]||choices[allowed[0]];
      relation.textContent=`Связь: ${selected?.[1]||"использует"}`;
      createForm.dataset.edgeKind=selected?.[0]||"uses";
    };
    kindField.onchange=applySuggestedEdge;applySuggestedEdge();
    createForm.onsubmit=async event=>{
      event.preventDefault();const values=new FormData(createForm);const submit=createForm.querySelector('[type="submit"]');
      submit.disabled=true;submit.textContent="Создаю…";
      try{
        if(!(await saveMetadata())){submit.disabled=false;submit.textContent="＋ Создать плановый узел";return}
        const data=await api("/api/metadata/graph-node","POST",{kind:values.get("kind"),name:values.get("name"),description:values.get("description"),parent_id:n.id,edge_kind:createForm.dataset.edgeKind||"uses",target_file:n.file_path});
        applyGraph(data.graph);S.selectedNode=data.node_id;hooks.renderScenario();renderInspector();schedulePersist();toast("Узел добавлен в ветку");
      }catch(error){submit.disabled=false;submit.textContent="＋ Создать плановый узел";toast("Не удалось добавить узел: "+error.message)}
    };
  }
  $("deleteGraphNode")?.addEventListener("click",async()=>{
    const branch=removablePlanBranch(n.id);
    const names=branch.map(node=>`• ${KIND_NAMES[node.kind]||node.kind}: ${node.label}`);
    const message=`Удалить плановую ветку из ${branch.length} ${branch.length===1?"узла":"узлов"}?\n\n${names.join("\n")}\n\nСвязи затронутых узлов будут удалены. Узлы кода и плановые узлы, используемые другими ветками, сохранятся.`;
    if(!confirm(message))return;
    const button=$("deleteGraphNode");button.disabled=true;
    try{if(!(await saveMetadata())){button.disabled=false;return}const data=await api("/api/metadata/graph-node/delete","POST",{node_id:n.id});applyGraph(data.graph);S.selectedNode=null;hooks.renderScenario();renderInspector();schedulePersist();toast("Плановая ветка удалена")}
    catch(error){button.disabled=false;toast("Не удалось удалить узел: "+error.message)}
  });
  [["fName","name"],["fHttpMethod","http_method"],["fHttpPath","path"],["fDesc","description"],["fAcc","acceptance"],["fDomain","domain"]].forEach(([id,_f])=>{
    const el=$(id);if(!el)return;
    el.addEventListener("input",()=>{S.formDirty=true;setSaveState("edit")});
    el.addEventListener("blur",()=>saveMetadata());
    el.addEventListener("keydown",ev=>{if(ev.key==="Enter"&&(ev.ctrlKey||ev.metaKey))el.blur()});
  });
  S.formDirty=false;
  foot.textContent=saveLabel(S.saveState)||"Автосейв в .studio/metadata.json при потере фокуса.";
}
async function saveMetadata(force=false){
  if(saveInFlight)return saveInFlight;
  const nodeId=S.selectedNode;
  const n=S.byId[nodeId];if(!n)return true;
  const canEditGraphNode=graphOwned(n);
  const name=$("fName")?$("fName").value.trim():n.label;
  const desc=$("fDesc")?$("fDesc").value:n.description;
  const acc=$("fAcc")?$("fAcc").value.split("\n").map(s=>s.trim()).filter(Boolean):n.acceptance;
  const dom=$("fDomain")?$("fDomain").value:(n.kind==="endpoint"?n.domain:"");
  const same=(!canEditGraphNode||name===editableName(n))&&desc===n.description&&JSON.stringify(acc)===JSON.stringify(n.acceptance)&&(n.kind!=="endpoint"||dom===n.domain)&&(!canEditGraphNode||($("fHttpMethod")?.value||"")===(n.details?.http_method||"")&&($("fHttpPath")?.value||"")===(n.details?.path||""));
  if(same&&!force){S.formDirty=false;setSaveState("saved");return true}
  setSaveState("save");
  saveInFlight=(async()=>{
    try{
      const payload=canEditGraphNode?{
        node_id:nodeId,kind:n.kind,name,description:desc,acceptance:acc,domain:dom,
        http_method:$("fHttpMethod")?.value||"",path:$("fHttpPath")?.value||"",
      }:{node_id:nodeId,description:desc,acceptance:acc,domain:dom};
      const data=await api(canEditGraphNode?"/api/metadata/graph-node":"/api/metadata/node","POST",payload);
      applyGraph(data.graph);
      if(!S.byId[nodeId]){
        S.selectedNode=null;S.formDirty=false;setSaveState("reset");
        toast("Узел пропал из кода — выбор снят");
        hooks.renderScenario();schedulePersist();return true;
      }
      S.selectedNode=nodeId;
      S.formDirty=false;setSaveState("saved");
      toast("метаданные сохранены");
      if(S.tab==="scenario"){hooks.renderScenario();renderInspector()}else hooks.renderMap();
      schedulePersist();
      return true;
    }catch(e){
      setSaveState("fail");toast("ошибка сохранения: "+e.message+" — текст остался в форме");
      return false;
    }finally{
      saveInFlight=null;
    }
  })();
  return saveInFlight;
}

export {renderInspector, saveMetadata};


