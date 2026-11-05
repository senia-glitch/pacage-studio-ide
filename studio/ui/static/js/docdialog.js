import {api} from "./api.js";
import {esc, openEditorForNode, toast} from "./core.js";
import {createSyncAcceptPane} from "./syncapply.js";

export function saveBlob(text, type, name) {
    const blob = new Blob([text], {type});
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

/* Диалог документа (ТЗ или отчёт): выбор разделов, перегенерация, правка текста.
   «Скопировать» и «Скачать» берут текст из поля — с вашими правками; файл в .studio
   хранит исходный сгенерированный текст. */
export async function documentDialog({title, endpoint, base, sections, json, jsonName, scopes, accept}) {
    document.querySelectorAll(".studio-modal").forEach((el) => el.remove());
    const overlay = document.createElement("div");
    overlay.className = "studio-modal frontend-overlay";
    overlay.innerHTML = `<div class="modal-box brief-box"><h3>${esc(title)}</h3>
        ${accept ? `<div class="sync-steps" role="tablist">
            <button type="button" class="on" data-step="doc" role="tab">ТЗ</button>
            <button type="button" data-step="accept" role="tab">Принять отчёт агента</button>
        </div>` : ""}
        <div id="frontDocMain">
        <div class="brief-controls front-sections">
            ${scopes ? `<label class="hint">Область:
                <select id="frontDocScope">${scopes.map(([value, label]) => `<option value="${esc(value)}"${value === base.scope ? " selected" : ""}>${esc(label)}</option>`).join("")}</select>
            </label>` : ""}
            <span class="hint">Разделы:</span>
            ${sections.map(([key, label]) => `<label class="hint"><input type="checkbox" class="front-section" value="${esc(key)}" checked> ${esc(label)}</label>`).join("")}
        </div>
        <p class="hint" id="frontDocHint">Собираю…</p>
        <textarea id="frontBriefText" class="prompt-editor-text" rows="16" spellcheck="false"></textarea>
        <div class="pre-actions"></div>
        </div>
        <div id="frontDocAccept" hidden></div></div>`;
    if (accept) {
        const main = overlay.querySelector("#frontDocMain");
        const host = overlay.querySelector("#frontDocAccept");
        host.appendChild(createSyncAcceptPane(accept));
        const closeRow = document.createElement("div");
        closeRow.className = "pre-actions";
        const closeButton = document.createElement("button");
        closeButton.type = "button";
        closeButton.className = "primary";
        closeButton.textContent = "Закрыть";
        closeButton.onclick = () => overlay.querySelector("#frontDocMain .pre-actions .primary").click();
        closeRow.appendChild(closeButton);
        host.appendChild(closeRow);
        overlay.querySelectorAll(".sync-steps button").forEach((tab) => {
            tab.onclick = () => {
                overlay.querySelectorAll(".sync-steps button").forEach((b) => b.classList.toggle("on", b === tab));
                main.hidden = tab.dataset.step !== "doc";
                host.hidden = tab.dataset.step !== "accept";
            };
        });
    }
    const text = overlay.querySelector("#frontBriefText");
    const hint = overlay.querySelector("#frontDocHint");
    const actions = overlay.querySelector(".pre-actions");
    const state = {path: "", data: null, dirty: false};
    const picked = () => [...overlay.querySelectorAll(".front-section:checked")].map((box) => box.value);
    const button = (label, action, cls) => {
        const element = document.createElement("button");
        element.type = "button";
        element.textContent = label;
        if (cls) element.className = cls;
        element.onclick = action;
        actions.appendChild(element);
        return element;
    };
    const generate = async () => {
        hint.textContent = "Собираю…";
        try {
            const payload = {...base, sections: picked()};
            const scopeSelect = overlay.querySelector("#frontDocScope");
            if (scopeSelect) payload.scope = scopeSelect.value;
            if (payload.scope !== "node") delete payload.nodeId;
            const data = await api(endpoint, "POST", payload);
            state.path = data.path;
            state.data = data.data || null;
            state.dirty = false;
            text.value = data.markdown;
            hint.textContent = `Файл сохранён: ${data.path}. Правки в поле влияют на копирование и скачивание, но не на файл.`;
            return true;
        } catch (error) {
            hint.textContent = "Ошибка: " + (error.message || "");
            return false;
        }
    };
    text.addEventListener("input", () => { state.dirty = true; });
    const scopeBox = overlay.querySelector("#frontDocScope");
    if (scopeBox) {
        let previous = scopeBox.value;
        scopeBox.onchange = async () => {
            if (state.dirty && !window.confirm("Ваши правки в тексте будут заменены. Продолжить?")) {
                scopeBox.value = previous;
                return;
            }
            previous = scopeBox.value;
            await generate();
        };
    }
    overlay.querySelectorAll(".front-section").forEach((box) => {
        box.onchange = async () => {
            if (state.dirty && !window.confirm("Ваши правки в тексте будут заменены. Продолжить?")) {
                box.checked = !box.checked;
                return;
            }
            if (!picked().length && json) {
                box.checked = true;
                toast("Выберите хотя бы один раздел");
                return;
            }
            await generate();
        };
    });
    button("Скопировать", () => copyFront(text.value, "Скопировано"));
    button("Скачать .md", () => saveBlob(text.value, "text/markdown;charset=utf-8", (state.path || "document.md").split("/").pop()));
    if (json) {
        button("Скачать .json", () => saveBlob(JSON.stringify(state.data || {}, null, 2), "application/json;charset=utf-8", jsonName || "report.json"));
    }
    button("Открыть файл", () => openEditorForNode({file_path: state.path, planned: false, line: 1}));
    const close = () => {
        overlay.remove();
        document.removeEventListener("keydown", onKey);
    };
    const onKey = (event) => { if (event.key === "Escape") close(); };
    button("Закрыть", close, "primary");
    overlay.addEventListener("click", (event) => {
        if (event.target === overlay) close();
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    await generate();
}

export function copyText(text, message) {
    if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(() => toast(message)).catch(() => toast("Не удалось скопировать"));
        return;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
        document.execCommand("copy");
        toast(message);
    } catch (_error) {
        toast("Не удалось скопировать");
    }
    area.remove();
}

