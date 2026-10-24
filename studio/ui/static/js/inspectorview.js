import {api} from "./api.js";
import {
  $, S, anchorText, applyGraph, copyNodeLocation, esc, hooks, schedulePersist,
  nestedOf, notesOf, openEditorForNode, setSaveState, todoBadgeHtml,
  toast, KIND_NAMES, ROLE_NAMES,
} from "./core.js";
import {saveLabel} from "./savestate.js";
import {isTodo} from "./todo.js";

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
  h+=`<div class="kv"><span>якорь</span><b>${esc(anchorText(n))}</b></div>`;
  h+=`<div class="insp-actions"><button id="openEditorBtn" type="button">Открыть в VS Code</button><button id="copyLocBtn" type="button">Копировать путь:строка</button></div>`;
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
  h+=`<div class="field"><label>${isTodo(n)?"Что сделать":"Описание"}</label><textarea id="fDesc" rows="5">${esc(n.description)}</textarea></div>`;
  h+=`<div class="field"><label>${isTodo(n)?"Что сделать: критерии приёмки":"Критерии приёмки (по одному на строку)"}</label><textarea id="fAcc" rows="5">${esc((n.acceptance||[]).join("\n"))}</textarea></div>`;
  if(n.kind==="endpoint"){
    h+=`<div class="field"><label>Домен</label><input id="fDomain" value="${esc(n.domain)}" placeholder="например, orders"><div class="note">Домен назначается только эндпоинту; остальные узлы наследуют его автоматически.</div></div>`;
  }else{
    h+=`<div class="field"><label>Домен (наследуется)</label><input value="${esc(n.domain)}" disabled><div class="note">Домен нельзя назначить этому узлу: он наследуется от связанных эндпоинтов.</div></div>`;
  }
  if(n.planned){
    h+=`<div class="field"><div class="note">Этот узел — TODO: он заведён в «Создании». Правки сохраняются в .studio/metadata.json, код проекта не меняется.</div></div>`;
  }
  const nestedItems=nestedOf(n.id);
  if(nestedItems.length){
    h+=`<div class="field"><label>Вложенные узлы</label><div class="hint nested-inspector" style="line-height:1.6">`+
      nestedItems.map(p=>`<button type="button" class="link nested-insp" data-nested="${esc(p.id)}">${esc(p.label)}</button> — ${esc(ROLE_NAMES[(p.details||{}).role]||(p.details||{}).role||"")} · ${esc(p.file_path)}:${p.line}`).join("<br>")+
      `</div></div>`;
  }
  body.innerHTML=h;
  const openBtn=$("openEditorBtn");if(openBtn)openBtn.onclick=()=>openEditorForNode(n);
  const copyBtn=$("copyLocBtn");if(copyBtn)copyBtn.onclick=()=>copyNodeLocation(n);
  document.querySelectorAll(".nested-insp").forEach(button=>{
    button.onclick=()=>{S.selectedNode=button.dataset.nested;hooks.renderScenario();renderInspector()};
  });
  [["fDesc","description"],["fAcc","acceptance"],["fDomain","domain"]].forEach(([id,_f])=>{
    const el=$(id);if(!el)return;
    el.addEventListener("input",()=>{S.formDirty=true;setSaveState("edit")});
    el.addEventListener("blur",()=>saveMetadata());
    el.addEventListener("keydown",ev=>{if(ev.key==="Enter"&&(ev.ctrlKey||ev.metaKey))el.blur()});
  });
  S.formDirty=false;
  foot.textContent=saveLabel(S.saveState)||"Автосейв в .studio/metadata.json при потере фокуса.";
}
async function saveMetadata(force=false){
  const nodeId=S.selectedNode;
  const n=S.byId[nodeId];if(!n)return;
  const desc=$("fDesc")?$("fDesc").value:n.description;
  const acc=$("fAcc")?$("fAcc").value.split("\n").map(s=>s.trim()).filter(Boolean):n.acceptance;
  const dom=$("fDomain")?$("fDomain").value:(n.kind==="endpoint"?n.domain:"");
  const same=desc===n.description&&JSON.stringify(acc)===JSON.stringify(n.acceptance)&&(n.kind!=="endpoint"||dom===n.domain);
  if(same&&!force){S.formDirty=false;setSaveState("saved");return}
  setSaveState("save");
  try{
    const data=await api("/api/metadata/node","POST",{
      node_id:nodeId,description:desc,acceptance:acc,domain:dom,
    });
    applyGraph(data.graph);
    if(!S.byId[nodeId]){
      S.selectedNode=null;S.formDirty=false;setSaveState("reset");
      toast("Узел пропал из кода — выбор снят");
      hooks.renderScenario();schedulePersist();return;
    }
    S.selectedNode=nodeId;
    S.formDirty=false;setSaveState("saved");
    toast("метаданные сохранены");
    if(S.tab==="scenario"){hooks.renderScenario();renderInspector()}else hooks.renderMap();
    schedulePersist();
  }catch(e){
    setSaveState("fail");toast("ошибка сохранения: "+e.message);
  }
}

export {renderInspector, saveMetadata};


