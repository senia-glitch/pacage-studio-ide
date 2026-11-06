import {api} from "./api.js";
import {$,S,applyGraph,esc,KIND_NAMES,hooks,openEditorForNode,toast,schedulePersist} from "./core.js";
import {openBriefDialog} from "./briefview.js";
import {CHILD_KINDS,relationFor} from "./relations.js";

const graphOwned=n=>!!n?.planned&&n.id.startsWith("studio:");
const acceptanceText=a=>Array.isArray(a)?a.join("\n"):"";
function selected(){return S.byId[S.selectedNode]||S.mapViewNodes?.[S.selectedNode]||null}
function renderInspector(){
  const chips=$("inspChips"),body=$("inspBody"),foot=$("inspFoot");if(!chips||!body)return;
  chips.innerHTML="";body.innerHTML="";const n=selected();
  if(S.selectedNode==="__new_domain__"){
    chips.innerHTML='<span class="chip acc">домен</span><span class="chip todo">план</span>';
    body.innerHTML=`<div class="node-origin plan-origin"><b>Новый домен</b><span>Будет добавлен в план и не изменит код проекта.</span></div><label class="field">Название домена<input id="domainName" maxlength="120" placeholder="например orders"></label><label class="field">Заметка<textarea id="domainNote" rows="3" placeholder="Назначение домена"></textarea></label><label class="field">Критерии приёмки<textarea id="domainAcceptance" rows="4"></textarea></label><button id="saveDomainPlan" class="primary">Создать плановый домен</button>`;
    bindDomainSave("",crypto.randomUUID());foot.textContent="Домен появится как отдельная группа на карте в режиме «План».";return;
  }
  if(!n){body.innerHTML='<p class="hint">Выберите узел на карте. Создавайте потомков кнопкой ＋ на карточке.</p>';foot.textContent="Фактические данные берутся из кода; план хранится в .studio/metadata.json.";return}
  chips.innerHTML=`<span class="chip acc">${esc(n.kind==="method"?"HTTP-метод":n.kind==="domain"?"домен":n.kind==="change"?"план правки":KIND_NAMES[n.kind]||n.kind)}</span>${n.planned?'<span class="chip todo">план</span>':n.synthetic?'<span class="chip">группа</span>':'<span class="chip">факт</span>'}${(n.details&&n.details.created_by==="agent")?'<span class="chip agent" title="Создано AI-агентом">агент</span>':""}`;
  if(n.kind==="change"&&n.synthetic){
    const source=S.byId[n.sourceNodeId],change=n.plannedChange||{};
    body.innerHTML=`<div class="node-origin plan-origin"><b>Плановая правка</b><span>Узел: ${esc(source?.label||n.details?.name||"не найден")}</span><span>Изменение не затрагивает код до реализации.</span></div><label class="field">Заметка об изменении<textarea id="changeNote" rows="5">${esc(change.note||"")}</textarea></label><label class="field">Критерии приёмки<textarea id="changeAcceptance" rows="4">${esc(acceptanceText(change.acceptance))}</textarea></label><div class="insp-actions"><button id="saveChange" class="primary">Сохранить правку</button><button id="clearChange" class="danger">Удалить пометку</button></div>`;
    $("saveChange").onclick=()=>source&&saveChange(source,false);$("clearChange").onclick=()=>source&&saveChange(source,true);foot.textContent="Плановая пометка хранится отдельно от фактического узла.";return;
  }
  if(n.kind==="method"&&n.synthetic){body.innerHTML=`<p class="hint">Группа создаётся автоматически по HTTP-методу и показывает домены с эндпоинтами.</p><button id="createDomainFromMethod" type="button" class="primary">＋ Создать домен</button>`;$("createDomainFromMethod").onclick=()=>{S.selectedNode="__new_domain__";renderInspector()};foot.textContent="Групповой узел · не сохраняется как сущность проекта.";return}
  if(n.kind==="domain"&&n.synthetic){
    const planned=!!n.domainPlanId,source=n.sourceDomain||"",existingChange=S.G.domain_changes?.[source]||n.domainRecord||{};
    body.innerHTML=`<div class="node-origin ${planned?"plan-origin":"code-origin"}"><b>${planned?"Новый плановый домен":source?"Плановое переименование домена":"Домен проекта"}</b><span>${source?`Фактическое имя «${esc(source)}» останется без изменений до реализации.`:planned?"Появится в структуре плана.":"Группа вычисляется по доменам эндпоинтов."}</span></div>
      ${source||planned?`<label class="field">Название домена<input id="domainName" maxlength="120" value="${esc(source?existingChange.name||n.label:n.label)}"></label><label class="field">Заметка<textarea id="domainNote" rows="3">${esc(existingChange.note||"")}</textarea></label><label class="field">Критерии приёмки<textarea id="domainAcceptance" rows="4">${esc(acceptanceText(existingChange.acceptance))}</textarea></label><div class="insp-actions"><button id="saveDomainPlan" class="primary">Сохранить план</button>${n.domainPlanId?'<button id="deleteDomainPlan" class="danger">Удалить домен и его плановые эндпоинты</button>':source?'<button id="deleteDomainRename" class="danger">Убрать переименование</button>':""}</div>`:`<p class="hint">Домен объединяет ${Number(n.details?.count)||0} эндпоинтов. Чтобы переименовать его, сохраните план изменения.</p><button id="renameDomain" class="primary">Переименовать в плане…</button>`}
      <button id="domainAddEndpoint" type="button">＋ Добавить эндпоинт</button>`;
    $("renameDomain")?.addEventListener("click",()=>{body.innerHTML=`<label class="field">Новое название домена<input id="domainName" maxlength="120" value="${esc(n.label)}"></label><label class="field">Заметка<textarea id="domainNote" rows="3"></textarea></label><label class="field">Критерии приёмки<textarea id="domainAcceptance" rows="4"></textarea></label><button id="saveDomainPlan" class="primary">Сохранить план переименования</button>`;bindDomainSave(n.label,"")});
    bindDomainSave(source,planned?n.domainPlanId:"",planned&&!n.domainPlanId?n.label:"");
    $("deleteDomainPlan")?.addEventListener("click",async()=>{if(!confirm(`Удалить плановый домен «${n.label}»?`))return;try{const d=await api("/api/metadata/domain-plan/delete","POST",{id:n.domainPlanId});applyGraph(d.graph);S.selectedNode=null;hooks.renderMap();renderInspector()}catch(e){toast(e.message)}});
    $("deleteDomainRename")?.addEventListener("click",async()=>{try{const d=await api("/api/metadata/domain-plan","POST",{source,name:source,note:"",acceptance:[]});applyGraph(d.graph);S.selectedNode=`@domain:${source}`;hooks.renderMap();renderInspector();toast("План переименования удалён")}catch(e){toast(e.message)}});
    $("domainAddEndpoint")?.addEventListener("click",()=>{body.innerHTML=endpointForm(n.label);bindCreate(null,n.label,n.domainPlanId||"")});
    foot.textContent="Домен — групповой узел. Его план не меняет текущие факты.";return;
  }
  if(S.selectedNode==="__new_domain__")return;
  const factual=!n.planned&&!n.synthetic;
  let h=`<div class="node-origin ${factual?"code-origin":"plan-origin"}"><b>${factual?"Узел из кода":"Плановый узел"}</b><span>${factual?`Источник: ${esc(n.file_path)}${n.line?":"+n.line:""}. Факты не редактируются здесь.`:"Узел и его связи сохранены в плане; код не меняется."}</span></div>`;
  if(factual){h+=`<div class="insp-actions">${n.file_path?'<button id="openNodeEditor">Открыть в редакторе</button>':""}<button id="nodeBrief" title="Собрать ТЗ для этого узла и всех его потомков">ТЗ узла и потомков</button></div>`}
  if(factual){h+=`<div class="field"><label>Плановая правка</label><textarea id="changeNote" rows="5" placeholder="Что предлагается изменить в этом узле">${esc(n.planned_change?.note||"")}</textarea></div><div class="field"><label>Критерии приёмки</label><textarea id="changeAcceptance" rows="4" placeholder="По одному критерию на строку">${esc(acceptanceText(n.planned_change?.acceptance))}</textarea></div><button id="saveChange" class="primary">Сохранить плановую правку</button>${n.planned_change&&Object.keys(n.planned_change).length?'<button id="clearChange" class="danger">Удалить пометку правки</button>':""}`}
  else{
    h+=`<label class="field">Название<input id="nodeName" maxlength="180" value="${esc((n.details||{}).function_name||(n.details||{}).class_name||(n.details||{}).method||(n.details||{}).name||n.label)}"></label><label class="field">Описание<textarea id="nodeDescription" rows="4">${esc(n.description||"")}</textarea></label><label class="field">Критерии приёмки<textarea id="nodeAcceptance" rows="4">${esc(acceptanceText(n.acceptance))}</textarea></label><div class="insp-actions"><button id="savePlanNode" class="primary">Сохранить узел</button><button id="nodeBrief" title="Собрать ТЗ для этого узла и всех его потомков">ТЗ узла и потомков</button>${graphOwned(n)?'<button id="deletePlanNode" class="danger">Удалить узел и плановых потомков</button>':""}</div>`;
    if(graphOwned(n)&&n.kind==="endpoint")h=h.replace('<label class="field">Описание',`<label class="field">HTTP-метод<input id="nodeHttpMethod" value="${esc((n.details||{}).http_method||"")}"></label><label class="field">Путь<input id="nodePath" value="${esc((n.details||{}).path||"")}"></label><label class="field">Домен<input id="nodeDomain" value="${esc(n.domain||"")}"></label><label class="field">Описание`);
  }
  body.innerHTML=h;
  $("openNodeEditor")?.addEventListener("click",()=>openEditorForNode(S.byId[n.id]));
  $("nodeBrief")?.addEventListener("click",()=>openBriefDialog({subtreeRoot:n.id}));
  $("saveChange")?.addEventListener("click",()=>saveChange(n,false));$("clearChange")?.addEventListener("click",()=>saveChange(n,true));
  $("savePlanNode")?.addEventListener("click",()=>saveNode(n));$("deletePlanNode")?.addEventListener("click",()=>deleteNode(n));
  foot.textContent="Изменения сохраняются в .studio/metadata.json. Код проекта не меняется.";
}
function endpointForm(domain){return `<form id="graphNodeCreate" class="graph-node-form"><input type="hidden" name="kind" value="endpoint"><label>Название эндпоинта<input name="name" required maxlength="180"></label><label>HTTP-метод<select name="http_method"><option>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></label><label>Путь<input name="path" placeholder="/orders"></label><label>Домен<input name="domain" value="${esc(domain)}"></label><label>Задача<textarea name="description" rows="3"></textarea></label><button class="primary" type="submit">＋ Добавить в план</button></form>`}
function bindCreate(parentId,domain,domainPlanId=""){const form=$("graphNodeCreate");if(!form)return;form.onsubmit=async ev=>{ev.preventDefault();const v=new FormData(form),kind=String(v.get("kind")||""),parentKind=S.byId[parentId]?.kind;const relation=relationFor(parentKind,kind);try{const d=await api("/api/metadata/graph-node","POST",{kind,name:v.get("name"),description:v.get("description"),acceptance:String(v.get("acceptance")||"").split(/\r?\n/).filter(Boolean),domain:v.get("domain")||domain,domain_plan_id:domainPlanId,http_method:v.get("http_method")||"",path:v.get("path")||"",parent_id:parentId||"",edge_kind:relation});applyGraph(d.graph);S.selectedNode=d.node_id;hooks.renderMap();renderInspector();toast("Плановый узел добавлен")}catch(e){toast("Не удалось добавить: "+e.message)}}}
function bindDomainSave(source,id,fromName=""){const domainId=id||(!source?crypto.randomUUID():"");$("saveDomainPlan")?.addEventListener("click",async()=>{const name=$("domainName")?.value.trim()||"",note=$("domainNote")?.value||"",acceptance=$("domainAcceptance")?.value||"";try{const d=await api("/api/metadata/domain-plan","POST",{source,from_name:fromName,name,note,acceptance,id:domainId});applyGraph(d.graph);S.selectedNode=source?`@domain:${name}`:`@domain-plan:${domainId}`;hooks.renderMap();renderInspector();toast("План домена сохранён")}catch(e){toast("Не удалось сохранить домен: "+e.message)}})}
async function saveChange(n,clear){try{const d=await api("/api/metadata/planned-change","POST",{node_id:n.id,note:clear?"":$("changeNote")?.value||"",acceptance:clear?[]:($("changeAcceptance")?.value||"").split(/\r?\n/).filter(Boolean)});applyGraph(d.graph);hooks.renderMap();renderInspector();toast(clear?"Пометка правки удалена":"Плановая правка сохранена")}catch(e){toast(e.message)}}
async function saveNode(n){try{const d=await api("/api/metadata/graph-node","POST",{node_id:n.id,kind:n.kind,name:$("nodeName").value,description:$("nodeDescription").value,acceptance:$("nodeAcceptance").value.split(/\r?\n/).filter(Boolean),domain:$("nodeDomain")?.value||n.domain||"",http_method:$("nodeHttpMethod")?.value||"",path:$("nodePath")?.value||""});applyGraph(d.graph);hooks.renderMap();renderInspector();toast("Плановый узел сохранён")}catch(e){toast(e.message)}}
let deletedBranchUndo=null;
async function deleteNode(n){if(!confirm(`Удалить «${n.label}» и его плановых потомков? Фактические узлы останутся.`))return;try{const d=await api("/api/metadata/graph-node/delete","POST",{node_id:n.id});deletedBranchUndo=d.undo&&d.undo.nodes&&Object.keys(d.undo.nodes).length?d.undo:null;S.selectedNode=null;hooks.renderMap();renderInspector();toast(deletedBranchUndo?"Плановая ветка удалена · Ctrl+Z — вернуть":"Плановая ветка удалена")}catch(e){toast(e.message)}}
document.addEventListener("keydown",async ev=>{
  if(S.tab!=="map"||!deletedBranchUndo)return;
  const tag=(document.activeElement?.tagName||"").toLowerCase();
  if(tag==="input"||tag==="textarea"||document.activeElement?.isContentEditable)return;
  if(document.querySelector(".studio-modal"))return;
  if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==="z"&&!ev.shiftKey){
    ev.preventDefault();
    const undo=deletedBranchUndo;deletedBranchUndo=null;
    try{
      const d=await api("/api/metadata/graph-node/restore","POST",{undo});
      applyGraph(d.graph);hooks.renderMap();renderInspector();
      toast("Плановая ветка восстановлена");
    }catch(e){toast("Не удалось восстановить: "+e.message)}
  }
});
export {renderInspector};
