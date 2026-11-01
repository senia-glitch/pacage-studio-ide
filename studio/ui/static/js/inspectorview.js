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

function renderInspector(){
  const chips=$("inspChips"),body=$("inspBody"),foot=$("inspFoot");
  chips.innerHTML="";body.innerHTML="";
  if(!S.selectedNode||!S.byId[S.selectedNode]){
    body.innerHTML='<p class="hint">Выберите узел на карте. Описание, критерии и домен хранятся в .studio/metadata.json; код проекта не изменяется.</p>';
    foot.textContent="Правки сохраняются при потере фокуса.";return;
  }
  const n=S.byId[S.selectedNode];
  chips.innerHTML=`<span class="chip acc">${esc(KIND_NAMES[n.kind]||n.kind)}</span>${todoBadgeHtml(n)}${notesOf(n).length?'<span class="chip warn">неточно</span>':""}<span class="chip">${esc(n.file_path)}${n.planned?"":":"+n.line}</span>`;
  let h="";
  if(n.planned)h+=`<div class="insp-todo">Этот узел — TODO. Кода пока нет. Файл: <b>${esc((n.details||{}).target_file||n.file_path)}</b></div>`;
  h+=`<div class="kv"><span>${n.planned?"узел":"якорь"}</span><b>${esc(anchorText(n))}</b></div>`;
  h+=`<div class="insp-actions"><button id="openEditorBtn" type="button">Открыть в VS Code</button><button id="copyLocBtn" type="button">Копировать путь:строка</button></div>`;
  h+=`<details class="field graph-add"><summary>＋ Добавить связанный узел</summary><form id="graphNodeCreate" class="graph-node-form">
    <label>Тип узла<select name="kind"><option value="scenario">Сценарий</option><option value="io">IO</option><option value="model">Модель</option><option value="test">Тест</option><option value="nested">Дополнительный узел</option></select></label>
    <label>Связь<select name="edge_kind"><option value="uses">использует</option><option value="dispatch">вызывает endpoint → scenario</option><option value="scenario_call">вызывает сценарий</option><option value="port_call">вызов IO</option><option value="table">работает с таблицей</option><option value="tests">проверяется тестом</option><option value="depends">зависит от</option></select></label>
    <label>Название<input name="name" required maxlength="180" placeholder="Например: LoadOrderIO"></label>
    <label>Описание<textarea name="description" rows="3" placeholder="Назначение узла"></textarea></label>
    <div class="graph-form-actions"><button type="submit" class="primary">Создать и связать</button><span class="hint">Направление связи определяется её типом.</span></div>
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
  if(graphOwned(n))h+=`<button id="deleteGraphNode" class="danger graph-delete" type="button">Удалить узел и его плановую ветку</button>`;
  body.innerHTML=h;
  const openBtn=$("openEditorBtn");if(openBtn)openBtn.onclick=()=>openEditorForNode(n);
  const copyBtn=$("copyLocBtn");if(copyBtn)copyBtn.onclick=()=>copyNodeLocation(n);
  document.querySelectorAll(".nested-insp").forEach(button=>{
    button.onclick=()=>{S.selectedNode=button.dataset.nested;hooks.renderScenario();renderInspector()};
  });
  const createForm=$("graphNodeCreate");
  if(createForm){
    const kindField=createForm.elements.kind,edgeField=createForm.elements.edge_kind;
    const preferred={endpoint:"scenario",scenario:"io",io:"model",model:"nested",test:"nested",nested:"nested"}[n.kind];
    if(preferred)kindField.value=preferred;
    const applySuggestedEdge=()=>{
      const child=kindField.value;
      const allowed={endpoint:["scenario","nested"],scenario:["scenario","io","test","nested"],io:["model","nested"],model:["nested"],test:["nested"],nested:["nested"]}[n.kind]||["nested"];
      [...kindField.options].forEach(option=>{option.hidden=!allowed.includes(option.value);option.disabled=!allowed.includes(option.value)});
      if(!allowed.includes(child))kindField.value=allowed[0];
      const selectedKind=kindField.value;
      const suggested=n.kind==="endpoint"&&selectedKind==="scenario"?"dispatch":n.kind==="scenario"&&selectedKind==="scenario"?"scenario_call":n.kind==="scenario"&&selectedKind==="io"?"port_call":n.kind==="io"&&selectedKind==="model"?"table":selectedKind==="test"?"tests":"uses";
      edgeField.value=suggested;
    };
    kindField.onchange=applySuggestedEdge;applySuggestedEdge();
    createForm.onsubmit=async event=>{
      event.preventDefault();const values=new FormData(createForm);const submit=createForm.querySelector('[type="submit"]');
      submit.disabled=true;submit.textContent="Создаю…";
      try{
        if(!(await saveMetadata())){submit.disabled=false;submit.textContent="Создать и связать";return}
        const data=await api("/api/metadata/graph-node","POST",{kind:values.get("kind"),name:values.get("name"),description:values.get("description"),parent_id:n.id,edge_kind:values.get("edge_kind"),target_file:n.file_path});
        applyGraph(data.graph);S.selectedNode=data.node_id;hooks.renderScenario();renderInspector();schedulePersist();toast("Узел добавлен в ветку");
      }catch(error){submit.disabled=false;submit.textContent="Создать и связать";toast("Не удалось добавить узел: "+error.message)}
    };
  }
  $("deleteGraphNode")?.addEventListener("click",async()=>{
    if(!confirm("Удалить этот узел и все связанные с ним плановые узлы? Реальные узлы проекта останутся на месте."))return;
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


