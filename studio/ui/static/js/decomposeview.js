import {studioToken} from "./api.js";
import {$ , applyGraph, toast} from "./core.js";

async function decomposePost(path, body){
  const response=await fetch(path,{method:"POST",headers:{"X-Studio-Token":studioToken(),"Content-Type":"application/json"},body:JSON.stringify(body)});
  let payload=null;
  try{payload=await response.json()}catch(_){payload=null}
  if(!response.ok){
    let message=`HTTP ${response.status}`;
    let code=`http_${response.status}`;
    if(payload&&typeof payload.error==="string"){message=payload.error;code=payload.code||code}
    else if(payload&&payload.error&&typeof payload.error==="object"){message=payload.error.message||message;code=payload.error.code||code}
    const error=new Error(message);
    error.code=code;error.status=response.status;
    throw error;
  }
  return payload;
}

function copyText(text){
  if(navigator.clipboard&&navigator.clipboard.writeText){
    return navigator.clipboard.writeText(text).then(()=>toast("Скопировано")).catch(()=>toast("Не удалось скопировать"));
  }
  const area=document.createElement("textarea");
  area.value=text;area.style.position="fixed";area.style.opacity="0";document.body.appendChild(area);area.select();
  try{document.execCommand("copy");toast("Скопировано")}catch(_){toast("Не удалось скопировать")}
  area.remove();
  return Promise.resolve();
}

export function openDecomposeDialog(){
  const overlay=document.createElement("div");
  overlay.className="studio-modal";
  overlay.innerHTML=`<div class="modal-box decomp-box"><h3>Декомпозиция задачи</h3>
    <div class="field"><label>Задача</label><textarea id="decompTask" rows="4" placeholder="Опишите задачу, которую нужно разбить на сценарии"></textarea></div>
    <div style="display:flex;gap:8px;margin:10px 0"><button type="button" id="decompBuildBtn">Сгенерировать промпт для декомпозиции</button><button type="button" id="decompCopyBtn" disabled>Копировать промпт</button></div>
    <div class="field"><label>Промпт</label><textarea id="decompPrompt" rows="10" readonly></textarea></div>
    <div class="field"><label>JSON от LLM</label><textarea id="decompJson" rows="8" placeholder='{"scenarios": []}'></textarea></div>
    <div class="modal-actions"><button type="button" id="decompApplyBtn" class="primary" disabled>Сгенерировать новые сценарии</button><button type="button" id="decompCloseBtn">Закрыть</button></div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener("click",ev=>{if(ev.target===overlay)overlay.remove()});
  const task=overlay.querySelector("#decompTask");
  const build=overlay.querySelector("#decompBuildBtn");
  const prompt=overlay.querySelector("#decompPrompt");
  const copy=overlay.querySelector("#decompCopyBtn");
  const json=overlay.querySelector("#decompJson");
  const apply=overlay.querySelector("#decompApplyBtn");
  overlay.querySelector("#decompCloseBtn").onclick=()=>overlay.remove();
  build.onclick=async()=>{
    if(!task.value.trim()){toast("Введите задачу");return}
    build.disabled=true;build.textContent="Собираю…";
    try{
      const data=await decomposePost("/api/decompose/prompt",{task:task.value});
      prompt.value=data.prompt;
      copy.disabled=false;
    }catch(e){toast(e.message)}
    finally{build.disabled=false;build.textContent="Сгенерировать промпт для декомпозиции"}
  };
  copy.onclick=()=>copyText(prompt.value);
  json.addEventListener("input",()=>{
    if(!json.value.trim()){apply.disabled=true;json.style.borderColor="";return}
    try{JSON.parse(json.value);apply.disabled=false;json.style.borderColor=""}
    catch(_){apply.disabled=true;json.style.borderColor="var(--acc)"}
  });
  apply.onclick=async()=>{
    let payload;
    try{payload=JSON.parse(json.value)}catch(e){toast("Невалидный JSON: "+e.message);return}
    apply.disabled=true;apply.textContent="Создаю…";
    try{
      const data=await decomposePost("/api/decompose",{scenarios:payload.scenarios});
      applyGraph(data.graph);
      toast("Создано "+data.plans.length+" планов");
      task.value="";prompt.value="";json.value="";
      copy.disabled=true;
      overlay.remove();
    }catch(e){
      toast(e.message);
      if(overlay.isConnected){apply.disabled=false;apply.textContent="Сгенерировать новые сценарии"}
    }
  };
}

export function initDecompose(){
  const button=$("decompBtn");
  if(button)button.onclick=openDecomposeDialog;
}
