import {S, $, esc, modal, openEditorForNode, toast} from "./core.js";

function updateDiagBadge(){
  const badge=$("diagBtn");if(!badge)return;
  const diagnostics=(S.G&&S.G.diagnostics)||[];
  const errors=diagnostics.filter(d=>d.level==="error").length;
  const warnings=diagnostics.filter(d=>d.level==="warning").length;
  const total=errors+warnings;
  badge.textContent=total?`⚠ ${total}`:"⚠ 0";
  badge.classList.toggle("err",errors>0);
  badge.classList.toggle("warn",errors===0&&warnings>0);
  badge.title=total?`Диагностика: ${errors} ошибок · ${warnings} предупреждений`:"Диагностики нет";
}
function openDiagnostics(){
  const diagnostics=(S.G&&S.G.diagnostics)||[];
  const levels=[["error","Ошибки"],["warning","Предупреждения и неточности"],["info","Информация"]];
  modal("Диагностика",
    `<div class="hint" style="margin-bottom:8px">Жёлтые пометки — это неточности: студия не поняла конструкцию и говорит почему.</div>`+
    `<input id="diagFilter" placeholder="Фильтр по коду или тексту" style="width:100%;margin-bottom:8px">`+
    `<div id="diagList"></div>`,[
    {label:"Копировать всё",cls:"",action:()=>{
      const markdown=diagnostics.map(d=>`- ${d.level === "warning" ? "неточность " : ""}${d.code} ${d.file}:${d.line} — ${d.message}${d.hint?" ("+d.hint+")":""}`).join("\n");
      navigator.clipboard&&navigator.clipboard.writeText(markdown||"Диагностики нет").then(()=>toast("Скопировано")).catch(()=>{});
    }},
    {label:"Закрыть",cls:"primary",action:()=>{}},
  ]);
  const filter=$("diagFilter"),list=$("diagList");
  const draw=()=>{
    const query=String(filter&&filter.value||"").trim().toLowerCase();
    const matches=d=>!query||`${d.code} ${d.message} ${d.hint} ${d.file}`.toLowerCase().includes(query);
    const shown=diagnostics.filter(matches);
    if(!shown.length){
      list.innerHTML='<p class="hint">'+(diagnostics.length?"Ничего не найдено по фильтру.":"Диагностики нет: все найденные конструкции поддержаны.")+'</p>';
      return;
    }
    list.innerHTML=levels.map(([level,title])=>{
      const items=shown.filter(d=>d.level===level);
      if(!items.length)return "";
      return `<h4>${esc(title)} <span class="hint">· ${items.length}</span></h4>`+
        items.map(d=>{
          const where=d.file?`${esc(d.file)}:${d.line}`:"";
          const mark=d.level==="warning"?'<span class="chip warn">неточность</span>':"";
          return `<div class="diag-row ${esc(d.level)}">${mark}<span class="chip">${esc(d.code)}</span>`+
            `<div><div>${esc(d.message)}</div>${where?`<button class="link diag-open" data-file="${esc(d.file)}" data-line="${d.line}">${where}</button>`:""}`+
            `${d.hint?`<div class="hint">${esc(d.hint)}</div>`:""}</div></div>`;
        }).join("");
    }).join("");
    list.querySelectorAll(".diag-open").forEach(button=>{
      button.onclick=()=>openEditorForNode({file_path:button.dataset.file,line:Number(button.dataset.line)||1,planned:false});
    });
  };
  if(filter)filter.addEventListener("input",draw);
  draw();
}

export {updateDiagBadge, openDiagnostics};
