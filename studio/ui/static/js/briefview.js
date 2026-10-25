import {api} from "./api.js";
import {
  briefRequest, defaultMapMode, plannedNodes, plannedStats, planStates, shortPrompt,
  statusText, verifyLabel,
} from "./briefmodel.js";
import {
  $, S, anchorText, esc, flushPending, modal, openEditorForNode, schedulePersist,
  toast, KIND_NAMES,
} from "./core.js";

function updateBriefBadge(){
  const button=$("briefBtn");if(!button)return;
  const label=verifyLabel(S.briefReport,S.G);
  const stats=plannedStats(S.G);
  button.textContent=label.text;
  button.title=label.title+(stats.unready?` · без описания: ${stats.unready}`:"");
  button.classList.toggle("err",label.urgent);
  button.classList.toggle("warn",!label.urgent&&stats.unready>0);
  button.classList.toggle("on",S.todoHighlight);
}
function applyTodoHighlight(){
  document.body.classList.toggle("todo-highlight",S.todoHighlight);
  const button=$("briefBtn");if(button)button.classList.toggle("on",S.todoHighlight);
}
function toggleTodoHighlight(){
  S.todoHighlight=!S.todoHighlight;
  applyTodoHighlight();
  schedulePersist();
}
function scheduleBriefStatus(){
  if(S.briefStatusTimer)clearTimeout(S.briefStatusTimer);
  S.briefStatusTimer=setTimeout(refreshBriefStatus,300);
}
async function refreshBriefStatus(){
  try{
    S.briefReport=await api("/api/verify?brief=last");
  }catch(e){
    if(e.status===404)S.briefReport=null;else return;
  }
  updateBriefBadge();
}
function briefPlanChoice(plan,selected){
  const ready=!!String(plan.description||"").trim();
  const hint=ready?`слоёв: ${(plan.layers||[]).length}`:"нет описания — заполните в студии";
  return `<label class="brief-node${ready?"":" disabled"}" title="${esc(plan.slug)}">
    <input type="checkbox" data-plan="${esc(plan.slug)}" ${selected.has(plan.slug)&&ready?"checked":""} ${ready?"":"disabled"}>
    <span><b>план ${esc(plan.title||plan.slug)}</b>
    <small>${esc(plan.slug)} · ${esc(hint)}</small></span></label>`;
}
function briefNodeChoice(node,selected){
  const ready=!!String(node.description||"").trim();
  const hint=ready?"готов к реализации":"нет описания — заполните в студии";
  return `<label class="brief-node${ready?"":" disabled"}" title="${esc(node.id)}">
    <input type="checkbox" data-bid="${esc(node.id)}" ${selected.has(node.id)&&ready?"checked":""} ${ready?"":"disabled"}>
    <span><b>${esc(KIND_NAMES[node.kind]||node.kind)} ${esc(anchorText(node))}</b>
    <small>${esc(node.file_path)} · ${esc(hint)}</small></span></label>`;
}
function briefStatusList(report){
  if(!report||!report.items||!report.items.length)return '<p class="hint">Отчёт пуст.</p>';
  return report.items.map(item=>{
    const cls=item.status==="выполнено"?"ok":item.status==="частично"?"warn":"err";
    const where=item.file?`<button class="link diag-open" data-file="${esc(item.file)}" data-line="${item.line||1}">${esc(item.file)}:${esc(item.line||1)}</button>`:"";
    return `<div class="brief-status-row ${cls}"><span class="chip">T${esc(item.number)}</span><div><b>${esc(KIND_NAMES[item.kind]||item.kind)} ${esc(item.anchor)}</b> — ${esc(item.status)} ${where}<div class="hint">${esc(item.message||"")}</div></div></div>`;
  }).join("");
}
function openBriefStatus(){
  if(!S.briefReport){openBriefDialog();return}
  modal("ТЗ для AI-агента",`<div class="brief-stats">Выполнено ${S.briefReport.done} из ${S.briefReport.total} · ${esc(statusText(S.briefReport))}</div>${briefStatusList(S.briefReport)}`,[
    {label:"Открыть ТЗ",action:()=>openBriefDialog()},
    {label:"Закрыть",cls:"primary",action:()=>{}},
  ]);
  document.querySelectorAll(".diag-open").forEach(button=>{
    button.onclick=()=>openEditorForNode({file_path:button.dataset.file,line:Number(button.dataset.line)||1,planned:false});
  });
}
async function openBriefDialog(options={}){
  await flushPending();
  const nodes=plannedNodes(S.G);
  const plans=planStates(S.G);
  const onlyPlans=Array.isArray(options.plans)?new Set(options.plans.filter(Boolean)):null;
  const includeEmpty=false;
  const selected=new Set(onlyPlans?[]:nodes.filter(node=>String(node.description||"").trim()).map(node=>node.id));
  const selectedPlans=new Set(onlyPlans||plans.filter(plan=>String(plan.description||"").trim()).map(plan=>plan.slug));
  const mapMode=defaultMapMode(((S.G&&S.G.nodes)||[]).length);
  const overlay=document.createElement("div");
  overlay.className="studio-modal";
  overlay.innerHTML=`<div class="modal-box brief-box"><h3>ТЗ для AI-агента</h3>
    <div class="brief-layout">
      <div class="brief-left">
        <div class="brief-controls">
          <label class="hint">Карта:
            <select id="briefMap">
              <option value="related">связанная</option>
              <option value="full">вся</option>
              <option value="compact">кратко</option>
              <option value="none">нет</option>
            </select>
          </label>
          <label class="hint"><input type="checkbox" id="briefEmpty"> включать без описания</label>
          <div class="hint">Планы: ${plans.length} · старые планируемые узлы: ${nodes.length} · отмечены готовые.</div>
        </div>
        <div id="briefPlans">${plans.length?plans.map(plan=>briefPlanChoice(plan,selectedPlans)).join(""):'<p class="hint">Планов нет. Создайте сценарий во вкладке «Создание».</p>'}</div>
        <div id="briefNodes">${nodes.length?nodes.map(node=>briefNodeChoice(node,selected)).join(""):'<p class="hint">Старых планируемых узлов нет.</p>'}</div>
      </div>
      <div class="brief-preview">
        <div class="brief-stats" id="briefStats">Подготовка…</div>
        <pre id="briefPreview">Собираю ТЗ…</pre>
        <div class="hint">ТЗ содержит фрагменты вашего кода и описания. Перед отправкой во внешний AI-сервис это решает пользователь.</div>
        <div class="brief-actions">
          <button type="button" id="briefCopy">Скопировать ТЗ</button>
          <button type="button" id="briefPrompt">Скопировать короткий промпт</button>
          <button type="button" id="briefDownload">Скачать .md</button>
          <button type="button" class="primary" id="briefSave">Сохранить в проект</button>
          <button type="button" id="briefClose">Закрыть</button>
        </div>
      </div>
    </div></div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener("click",ev=>{if(ev.target===overlay)overlay.remove()});
  const mapSelect=overlay.querySelector("#briefMap");
  mapSelect.value=S.briefLast&&S.briefLast.options&&S.briefLast.options.map||mapMode;
  const emptyBox=overlay.querySelector("#briefEmpty");
  let includeEmptyValue=includeEmpty;
  emptyBox.checked=includeEmptyValue;
  const preview=overlay.querySelector("#briefPreview");
  const statsBox=overlay.querySelector("#briefStats");
  S.briefPreview=preview;
  let currentMarkdown="";
  let currentPath=".studio/agent-brief.md";
  const collect=()=>{
    const chosen=[...overlay.querySelectorAll('#briefNodes input[data-bid]:checked')].map(input=>input.dataset.bid);
    const chosenPlans=[...overlay.querySelectorAll('#briefPlans input[data-plan]:checked')].map(input=>input.dataset.plan);
    return briefRequest({selected:chosen,plans:chosenPlans,map:mapSelect.value,includeEmpty:includeEmptyValue});
  };
  const generate=async()=>{
    statsBox.textContent="Собираю ТЗ…";
    try{
      const data=await api("/api/brief","POST",collect());
      currentMarkdown=data.markdown||"";
      S.briefLast=data;
      preview.textContent=currentMarkdown;
      const stats=data.stats||{};
      statsBox.textContent=`Задач: ${stats.tasks||0} · не готово: ${stats.unready||0} · ${stats.chars||0} символов (~${stats.approx_tokens||0} токенов)`+
        ((data.warnings||[]).length?` · предупреждений: ${data.warnings.length}`:"");
      if((data.warnings||[]).length){
        statsBox.title=data.warnings.join("\n");
        statsBox.textContent+=" · подробности в подсказке";
      }
      if(data.brief_id){
        try{S.briefReport=await api("/api/verify?brief="+encodeURIComponent(data.brief_id));}catch(_){}
      }
      updateBriefBadge();
    }catch(e){
      statsBox.textContent="Ошибка: "+e.message;
    }
  };
  const scheduleGenerate=()=>{clearTimeout(overlay._timer);overlay._timer=setTimeout(generate,220)};
  overlay.querySelectorAll('#briefNodes input[data-bid],#briefPlans input[data-plan]').forEach(input=>input.onchange=scheduleGenerate);
  mapSelect.onchange=scheduleGenerate;
  emptyBox.onchange=()=>{
    includeEmptyValue=emptyBox.checked;
    nodes.forEach(node=>{
      const input=[...overlay.querySelectorAll("#briefNodes input[data-bid]")].find(el=>el.dataset.bid===node.id);
      if(!input)return;
      const ready=!!String(node.description||"").trim();
      if(!ready){
        input.disabled=!includeEmptyValue;
        if(includeEmptyValue)input.checked=true;
      }
    });
    plans.forEach(plan=>{
      const input=[...overlay.querySelectorAll("#briefPlans input[data-plan]")].find(el=>el.dataset.plan===plan.slug);
      if(!input)return;
      const ready=!!String(plan.description||"").trim();
      if(!ready){
        input.disabled=!includeEmptyValue;
        if(includeEmptyValue)input.checked=true;
      }
    });
    scheduleGenerate();
  };
  const copyText=(text,message)=>{
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(()=>toast(message)).catch(()=>toast("Не удалось скопировать"));
      return;
    }
    const area=document.createElement("textarea");
    area.value=text;area.style.position="fixed";area.style.opacity="0";document.body.appendChild(area);area.select();
    try{document.execCommand("copy");toast(message)}catch(_){toast("Не удалось скопировать")}
    area.remove();
  };
  overlay.querySelector("#briefCopy").onclick=()=>copyText(currentMarkdown||preview.textContent,"ТЗ скопировано");
  overlay.querySelector("#briefPrompt").onclick=()=>copyText(shortPrompt(currentPath),"Промпт скопирован");
  overlay.querySelector("#briefDownload").onclick=()=>{
    const blob=new Blob([currentMarkdown||preview.textContent],{type:"text/markdown;charset=utf-8"});
    const link=document.createElement("a");
    link.href=URL.createObjectURL(blob);link.download="agent-brief.md";document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(link.href),1000);
  };
  overlay.querySelector("#briefSave").onclick=async()=>{
    try{
      const data=await api("/api/brief/save","POST",collect());
      currentPath=data.path||currentPath;
      toast("ТЗ сохранено: "+currentPath);
      if(data.brief_id){
        try{S.briefReport=await api("/api/verify?brief="+encodeURIComponent(data.brief_id));}catch(_){}
        updateBriefBadge();
      }
    }catch(e){toast("Ошибка сохранения ТЗ: "+e.message)}
  };
  overlay.querySelector("#briefClose").onclick=()=>{clearTimeout(overlay._timer);overlay.remove()};
  await generate();
}

function setTodoMenu(open){
  const panel=$("todoMenuPanel");if(!panel)return;
  panel.hidden=!open;
}
function initTodoChip(){
  const chip=$("briefBtn"),menu=$("todoMenuBtn"),action=$("todoBriefAction"),status=$("todoStatusAction");
  if(chip)chip.onclick=()=>{if(S.briefReport&&Number(S.briefReport.total)>0)openBriefStatus();else openBriefDialog()};
  if(menu)menu.onclick=ev=>{ev.stopPropagation();setTodoMenu($("todoMenuPanel").hidden)};
  if(action)action.onclick=()=>{setTodoMenu(false);openBriefDialog()};
  if(status)status.onclick=()=>{setTodoMenu(false);openBriefStatus()};
  document.addEventListener("click",ev=>{
    if(!ev.target.closest||!ev.target.closest(".todo-chip-wrap"))setTodoMenu(false);
  });
}


export {updateBriefBadge, scheduleBriefStatus, refreshBriefStatus, openBriefDialog, initTodoChip};

