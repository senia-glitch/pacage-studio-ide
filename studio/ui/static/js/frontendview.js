import {api} from "./api.js";
import {$,esc,S,toast,modal,schedulePersist,openEditorForNode,enableNodeDrag} from "./core.js";
import {createSyncAcceptPane, promptTotal} from "./syncapply.js";
import {copyText as copyFront, documentDialog} from "./docdialog.js";
import {bindViewport, applyTransform, fitView} from "./viewport.js";
import {clearStage, measure, sizeStage} from "./edges.js";
import {drawEdges} from "./edges.js";

/* Вкладка «Фронтенд» — аналог «Карты проекта» для frontend/: канвас
   домен → страница → блок → правило, правая панель «Свойства | Нарушения»,
   дерево структуры, ТЗ и отчёт. Полностью независим от бэкенд-канваса:
   своя камера, режимы, позиции, выделение и метаданные
   (.studio/frontend.json через /api/frontend/*). */

const S0 = {
    view: {x: 40, y: 40, k: 1},
    mode: "fact",
    graph: null,
    loading: false,
    error: "",
    selected: null,
    propsTab: "props",
    stickyViol: false,
    boxes: {},
    positions: new Map(),
    positionsCache: new Map(),
    nodes: [],
    edges: [],
    children: new Map(),
    filterSev: "all",
    query: "",
    openGroups: new Set(),
    undoDelete: null,
    justDraggedAt: 0,
    treeCollapsed: new Set(),
    treeMode: "all",
    pendingDomain: false,
    anim: null,
};

const FR_COLORS = {uses: "#5aa0ff", rule: "#79b8f5"};
const FR_EDGE_NAMES = {uses: "содержит", rule: "правило"};
const WIDTHS = {domain: 236, page: 216, block: 190, rule: 180};
const HEIGHTS = {domain: 92, page: 112, block: 104, rule: 96};
const GAP_Y = 54;
const GAP_X = 30;
const PAD = 60;

/* ---------- состояние UI (localStorage отдельно от метаданных) ---------- */

function saveFrontUi() {
    try {
        localStorage.setItem("studio:front-ui", JSON.stringify({
            view: S0.view,
            mode: S0.mode,
            collapsed: [...S0.treeCollapsed],
            treeMode: S0.treeMode,
            positions: Object.fromEntries([...S0.positions.entries()]),
        }));
    } catch (_error) {
        /* localStorage недоступен */
    }
}

function loadFrontUi() {
    try {
        const raw = localStorage.getItem("studio:front-ui");
        if (!raw) return;
        const data = JSON.parse(raw);
        if (data.view && Number.isFinite(data.view.x) && Number.isFinite(data.view.y) && Number.isFinite(data.view.k)) {
            S0.view = data.view;
        }
        if (data.mode === "fact" || data.mode === "plan") S0.mode = data.mode;
        if (["all", "fact", "plan"].includes(data.treeMode)) S0.treeMode = data.treeMode;
        if (Array.isArray(data.collapsed)) {
            for (const id of data.collapsed) if (String(id)) S0.treeCollapsed.add(String(id));
        }
        for (const [key, value] of Object.entries(data.positions || {})) {
            if (Number.isFinite(value?.x) && Number.isFinite(value?.y)) S0.positions.set(key, value);
        }
    } catch (_error) {
        /* дефолтное состояние */
    }
}

/* ---------- грамматика ---------- */

function pluralFiles(n) {
    if (n === 0) return "0 файлов";
    return n + " " + pluralRu(n, "файл", "файла", "файлов");
}

function pluralRu(n, one, two, many) {
    const modTen = Math.abs(n) % 10;
    const modHundred = Math.abs(n) % 100;
    if (n === 0) return many;
    if (modTen === 1 && modHundred !== 11) return one;
    if (modTen >= 2 && modTen <= 4 && (modHundred < 10 || modHundred >= 20)) return two;
    return many;
}

function renderHeading(graph) {
    const heading = $("frontHeading");
    const caption = $("frontCaption");
    const stats = $("frontStats");
    if (!heading || !caption || !stats) return;
    if (!graph) {
        caption.textContent = "";
        stats.innerHTML = "";
        return;
    }
    const statsData = graph.stats || {};
    const stackBad = (graph.stack || []).filter((item) => item.status !== "ok");
    heading.textContent = "Фронтенд";
    caption.textContent = "frontend/";
    const bits = [
        statsData.pages ? pluralFiles(statsData.pages) : "фронтенда нет",
        statsData.errors ? `${statsData.errors} ${pluralRu(statsData.errors, "ошибка", "ошибки", "ошибок")}` : "ошибок нет",
        statsData.warnings ? `${statsData.warnings} ${pluralRu(statsData.warnings, "предупреждение", "предупреждения", "предупреждений")}` : "",
        stackBad.length ? `стек: ${stackBad.length} откл.` : "стек ✓",
    ].filter(Boolean);
    stats.innerHTML = bits.map((bit) => `<span class="chip">${esc(bit)}</span>`).join("");
}

/* ---------- граф → узлы канваса ---------- */

function buildFrontNodes(graph) {
    const nodes = [];
    const edges = [];
    const children = new Map();
    const shelves = new Map();

    const push = (id, kind, data, parentId) => {
        nodes.push({...data, id, kind, parentId, planned: !!data.planned});
        if (parentId) {
            edges.push({from_node: parentId, to_node: id, kind});
            if (!children.has(parentId)) children.set(parentId, []);
            children.get(parentId).push(id);
        }
    };
    const shelfFor = (domainName) => {
        const key = "domain:" + domainName;
        if (!shelves.has(key)) {
            const shelf = {id: key, kind: "domain", name: domainName, planned: false};
            shelves.set(key, shelf);
            nodes.push(shelf);
        }
        return shelves.get(key);
    };
    for (const domain of graph.domains || []) shelfFor(domain.name);
    for (const page of graph.pages || []) {
        const domainName = page.domain || "Без домена";
        const shelf = shelfFor(domainName);
        push(page.id, "page", {
            name: page.name,
            route: page.route,
            file: page.file,
            domain: page.domain,
            description: page.description,
            acceptance: page.acceptance,
            calls: page.calls,
            createdBy: page.created_by,
            deletion: page.deletion,
            planned: page.planned,
        }, shelf.id);
        for (const block of page.blocks || []) {
            push(block.id, "block", {
                name: block.name,
                file: block.file,
                description: block.description,
                acceptance: block.acceptance,
                calls: block.calls,
                createdBy: block.created_by,
                deletion: block.deletion,
                planned: block.planned,
            }, page.id);
            for (const rule of block.rules || []) {
                push(rule.id, "rule", {
                    name: rule.name,
                    description: rule.description,
                    acceptance: rule.acceptance,
                    note: rule.note,
                    done: rule.done,
                }, block.id);
            }
        }
        for (const rule of page.rules || []) {
            push(rule.id, "rule", {
                name: rule.name,
                description: rule.description,
                acceptance: rule.acceptance,
                note: rule.note,
                done: rule.done,
            }, page.id);
        }
    }
    return {nodes, edges: dedupeEdges(edges), children};
}

function dedupeEdges(edges) {
    const seen = new Set();
    return edges.filter((edge) => {
        const key = `${edge.from_node}→${edge.to_node}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

/* ---------- разметка статусов ---------- */

function renderStatus(html) {
    const box = $("frontEmpty");
    if (box) box.innerHTML = html;
}

function renderLoading() {
    renderStatus('<p class="hint">Читаю frontend/…</p>');
}

function renderFrontendError(message) {
    renderStatus(`<div class="front-empty"><div class="front-empty-icon">⚠</div>
        <h3>Вкладка недоступна</h3><p class="hint">${esc(message)}</p></div>`);
}

function renderNoFrontend() {
    renderStatus(`<div class="front-empty">
        <div class="front-empty-icon">∅</div>
        <h3>frontend/ не заведён</h3>
        <p class="hint">В проекте нет папки frontend/. Кнопка «⇄ ТЗ…» сгенерирует промпт-скелет по стандарту,
        чтобы фронтенд был заведён ровно по правилам пакета.</p></div>`);
    drawFrontCanvas(null);
    renderTree(null);
}

function renderErrorMessage(message) {
    renderStatus(`<div class="front-empty"><div class="front-empty-icon">⚠</div>
        <h3>frontend/ есть, но вкладку собрать не удалось</h3><p class="hint">${esc(message)}</p></div>`);
}

/* ---------- layout канваса ---------- */

function layoutFront(nodes, children) {
    const positions = new Map();
    const sizes = new Map();
    const byId = new Map(nodes.map((node) => [node.id, node]));

    const measure = (id, seen = new Set()) => {
        if (sizes.has(id)) return sizes.get(id);
        seen.add(id);
        const kids = children.get(id) || [];
        const own = {width: WIDTHS[byId.get(id)?.kind] || 180, height: HEIGHTS[byId.get(id)?.kind] || 104};
        let treeHeight = 0;
        let treeWidth = 0;
        for (const kid of kids) {
            const child = measure(kid, seen);
            treeHeight = Math.max(treeHeight, child.height);
            treeWidth = Math.max(treeWidth, child.width);
        }
        const result = {
            width: Math.max(own.width, treeWidth),
            height: own.height + (kids.length ? GAP_Y + treeHeight : 0),
        };
        sizes.set(id, result);
        return result;
    };

    const shelves = nodes.filter((node) => node.kind === "domain");
    if (S0.pendingDomain) {
        shelves.push({id: "domain-pending", kind: "domain", name: "Новый домен…", planned: true});
    }
    let x = PAD + 20;
    let y = PAD + 30;
    for (const shelf of shelves) {
        const size = measure(shelf.id);
        if (x > PAD + 20 && x + size.width > 2200) {
            x = PAD + 20;
            y += 200;
        }
        positions.set(shelf.id, {x, y});
        place(shelf.id, x, y, children, byId, positions);
        x += size.width + 70;
    }
    for (const node of nodes) {
        const saved = S0.positions.get(node.id);
        if (saved) positions.set(node.id, {...saved});
    }
    return {positions, byId};
}

function place(id, x, y, children, byId, positions) {
    positions.set(id, {x, y});
    const kids = children.get(id) || [];
    const childWidths = kids.map((kid) => WIDTHS[byId.get(kid)?.kind] || 180);
    const total = childWidths.reduce((sum, item) => sum + item + GAP_X, -GAP_X);
    let cursor = x + (WIDTHS[byId.get(id)?.kind] || 180) / 2 - total / 2;
    for (const kid of kids) {
        const cw = WIDTHS[byId.get(kid)?.kind] || 180;
        const cx = Math.round(cursor);
        place(kid, cx, y + (HEIGHTS[byId.get(id)?.kind] || 104) + GAP_Y, children, byId, positions);
        cursor += cw + GAP_X;
    }
}

/* ---------- рисование канваса ---------- */

function drawFrontCanvas(graph) {
    const stage = $("frontStage");
    const svg = $("frontEdges");
    if (!stage || !svg) return;
    clearStage(stage, svg);
    if (!graph) {
        sizeStage(stage, svg);
        applyTransform("frontView", S0.view);
        return;
    }
    const built = buildFrontNodes({domains: graph.domains || [], pages: graph.pages || []});
    S0.nodes = built.nodes;
    S0.edges = built.edges;
    S0.children = built.children;
    let highlight = null;
    let dimmed = null;
    if (S0.selected && S0.nodes.some((node) => node.id === S0.selected)) {
        highlight = new Set([S0.selected]);
        const stack = [S0.selected];
        while (stack.length) {
            for (const kid of S0.children.get(stack.pop()) || []) {
                if (highlight.has(kid)) continue;
                highlight.add(kid);
                stack.push(kid);
            }
        }
        dimmed = new Set(S0.nodes.map((node) => node.id).filter((id) => !highlight.has(id)));
    }
    if (S0.pendingDomain && !S0.nodes.some((node) => node.id === "domain-pending")) {
        S0.nodes.push({id: "domain-pending", kind: "domain", name: "Новый домен…", planned: true});
    }
    const byId = new Map(S0.nodes.map((node) => [node.id, node]));
    S0.nodes.forEach((node) => byId.set(node.id, node));
    const violationByFile = new Map();
    for (const violation of graph.violations || []) {
        violationByFile.set(violation.file, (violationByFile.get(violation.file) || 0) + 1);
    }
    const {positions} = layoutFront(S0.nodes, S0.children);
    S0.positionsCache = positions;
    for (const node of S0.nodes) {
        const pos = positions.get(node.id) || S0.positions.get(node.id) || {x: PAD, y: PAD};
        const card = buildCard(node, graph, violationByFile, highlight, dimmed);
        card.dataset.id = node.id;
        card.style.left = pos.x + "px";
        card.style.top = pos.y + "px";
        card.style.width = WIDTHS[node.kind] + "px";
        card.style.minHeight = HEIGHTS[node.kind] + "px";
        stage.appendChild(card);
        attachDrag(card, node);
        wireCard(card, node, graph);
    }
    const boxes = measure(stage);
    S0.boxes = boxes;
    drawEdges(svg, boxes, frontEdgeModel(S0.edges), FR_COLORS, FR_EDGE_NAMES, esc);
    sizeStage(stage, svg);
    applyTransform("frontView", S0.view);
}
function buildCard(node, graph, violationByFile, highlight, dimmed) {
    const card = document.createElement("article");
    const faded = S0.mode === "plan" && !node.planned && node.kind !== "domain" ? " faded" : "";
    const dimPlanned = S0.mode === "fact" && node.planned ? " faded" : "";
    const kin = highlight && highlight.has(node.id) ? " kin" : "";
    const dimByBranch = dimmed && dimmed.has(node.id) ? " dim" : "";
    card.className =
        "card front-node k-" + node.kind +
        (node.planned ? " planned" : "") +
        (node.kind === "domain" ? " synthetic" : "") +
        faded + dimPlanned + kin + dimByBranch +
        (node.deletion ? " node--delete" : "") +
        (S0.selected === node.id ? " sel" : "");
    card.dataset.id = node.id;
    const captions = {
        domain: "домен · роль",
        page: node.planned ? "страница · план" : "страница",
        block: node.planned ? "блок · план" : "блок",
        rule: "правило",
    };
    const chips = [`<span class="chip">${esc(captions[node.kind])}</span>`];
    if (node.deletion) chips.push('<span class="chip no">на удаление</span>');
    let own = "";
    if (node.kind === "page") {
        const count = violationByFile.get(node.file) || 0;
        if (count) chips.push(`<span class="chip no">${count}</span>`);
        own = `<div class="mt">маршрут: <b>${node.route ? esc(node.route) : "—"}</b></div>`;
    }
    if (node.kind === "block") {
        const count = violationByFile.get(node.file) || 0;
        if (count) chips.push(`<span class="chip no">${count}</span>`);
        own = `<div class="mt">${node.file ? esc(node.file) : "файл после реализации"}</div>`;
    }
    if (node.kind === "rule") {
        own = `<div class="mt">${node.description ? esc(node.description) : "описание в свойствах"}</div>`;
        if (node.done) chips.push('<span class="chip ok">готово</span>');
    }
    const plus = node.kind === "rule"
        ? ""
        : `<button type="button" class="front-add" data-act="create" title="${node.kind === "domain" ? "Плановая страница в домене" : "Плановое правило"}">＋</button>`;
    const actionable = node.planned || node.kind === "page" || node.kind === "block";
    const del = actionable && !node.deletion
        ? node.planned
            ? `<button type="button" class="front-delete" data-act="delete" title="Удалить плановый узел (Ctrl+Z вернёт)">🗑</button>`
            : `<button type="button" class="front-delete-note" data-act="delete-note" title="Пометка на удаление (код не меняется — задача в ТЗ)">🗑</button>`
        : node.planned && node.deletion
            ? "" : "";
    const deleteChip = node.deletion ? "" : "";
    card.innerHTML =
        `<div class="hd"><span class="grip" title="Перетащить узел">⠿</span>${del}` +
        `<span class="chip">${esc(captions[node.kind])}</span><span class="t">${esc(node.name)}</span>${plus}</div>` +
        own;
    void graph;
    void deleteChip;
    return card;
}

function frontEdgeModel(edges) {
    return edges.map((edge) => ({
        from_node: edge.from_node,
        to_node: edge.to_node,
        kind: edge.kind === "rule" ? "rule" : "uses",
    }));
}

function wireCard(card, node, graph) {
    const add = card.querySelector(".front-add");
    if (add) {
        add.addEventListener("click", (event) => {
            event.stopPropagation();
            S0.selected = node.id;
            if (node.kind === "domain") openCreateForm(node, "page");
            else openCreateForm(node, "rule");
        });
    }
    const del = card.querySelector(".front-delete");
    if (del) {
        del.addEventListener("click", async (event) => {
            event.stopPropagation();
            await deletePlannedNode(node);
        });
    }
    const delNote = card.querySelector(".front-delete-note");
    if (delNote) {
        delNote.addEventListener("click", (event) => {
            event.stopPropagation();
            S0.selected = node.id;
            switchRightTab("props");
            drawFrontCanvas(graph);
            renderProps();
            if (S0.deletionFocus !== 0) {
                requestAnimationFrame(() => {
                    const field = document.getElementById("fpDelNote");
                    if (field) {
                        field.closest(".front-delete-block")?.scrollIntoView({block: "nearest"});
                        field.focus({preventScroll: true});
                    }
                });
            }
        });
    }
    card.addEventListener("click", (event) => {
        if (event.target.closest("button")) return;
        if (Date.now() - S0.justDraggedAt < 250) return;
        S0.selected = node.id;
        if (!S0.stickyViol) switchRightTab("props");
        drawFrontCanvas(graph);
        renderProps();
    });
}

/* ---------- drag узлов ---------- */

function frontDropTarget(node, centerX, centerY) {
    // Страница падает в домен (роль); блок — на другую страницу.
    const candidates = [];
    const shelfWidth = WIDTHS.domain || 200;
    const blockSizeW = WIDTHS.block || 190;
    if (node.kind === "page") {
        for (const candidate of S0.nodes) {
            const box = S0.boxes[candidate.id];
            if (!box || candidate.kind !== "domain" || candidate.id === node.parentId) continue;
            candidates.push({id: candidate.id, name: candidate.name, box, width: shelfWidth, height: HEIGHTS.domain || 104});
        }
        const shelf = S0.nodes.find((item) => item.id === "domain:Без домена");
        if (shelf) {
            const box = S0.boxes[shelf.id];
            if (box && node.parentId !== shelf.id) candidates.push({id: shelf.id, name: "Без домена", box, width: shelfWidth, height: HEIGHTS.domain || 104});
        }
    } else if (node.kind === "block") {
        for (const candidate of S0.nodes) {
            const box = S0.boxes[candidate.id];
            if (!box || candidate.kind !== "page" || candidate.id === node.parentId) continue;
            candidates.push({id: candidate.id, name: candidate.name, box, width: WIDTHS.page, height: HEIGHTS.page});
        }
    }
    for (const candidate of candidates) {
        if (
            centerX >= candidate.box.x
            && centerX <= candidate.box.x + (candidate.width || 200)
            && centerY >= candidate.box.y
            && centerY <= candidate.box.y + (candidate.height || 110)
        ) {
            return candidate;
        }
    }
    return null;
}

let frontDropId = null;

function frontSetDropTarget(id) {
    if (frontDropId === id) return;
    const stage = $("frontStage");
    if (!stage) return;
    const previous = stage.querySelector(`.front-node.drop-target`);
    if (previous) previous.classList.remove("drop-target");
    frontDropId = id || null;
    if (frontDropId) {
        const element = stage.querySelector(`[data-id="${CSS.escape(frontDropId)}"]`);
        if (element) element.classList.add("drop-target");
    }
}

function attachDrag(card, node) {
    const origin = S0.positionsCache.get(node.id) || {x: PAD, y: PAD};
    const moving = new Set([node.id]);
    const queue = [node.id];
    while (queue.length) {
        for (const childId of S0.children.get(queue.pop()) || []) {
            if (!moving.has(childId)) {
                moving.add(childId);
                queue.push(childId);
            }
        }
    }
    const targets = [...moving].map((id) => ({id, from: {...(S0.positionsCache.get(id) || origin)}}));
    enableNodeDrag(
        card,
        "front",
        node.id,
        origin.x,
        origin.y,
        S0.view,
        (nx, ny) => {
            const dx = nx - origin.x;
            const dy = ny - origin.y;
            for (const target of targets) {
                const next = {x: target.from.x + dx, y: target.from.y + dy};
                S0.positions.set(target.id, next);
                S0.positionsCache.set(target.id, next);
                const element = $("frontStage").querySelector(`[data-id="${CSS.escape(target.id)}"]`);
                if (element) {
                    element.style.left = next.x + "px";
                    element.style.top = next.y + "px";
                }
            }
            S0.justDraggedAt = Date.now();
            const boxes = measure($("frontStage"));
            drawEdges($("frontEdges"), boxes, frontEdgeModel(S0.edges), FR_COLORS, FR_EDGE_NAMES, esc);
            if (node.kind === "page" || node.kind === "block") {
                const dropNode = frontDropTarget(node, nx + (WIDTHS[node.kind] || 180) / 2, ny + (HEIGHTS[node.kind] || 104) / 2);
                frontSetDropTarget(dropNode ? dropNode.id : null);
            }
        },
        async (cancelled) => {
            frontSetDropTarget(null);
            if (cancelled) {
                for (const target of targets) {
                    const original = S0.positions.get(target.id);
                    if (original) S0.positions.set(target.id, original);
                    else S0.positions.delete(target.id);
                }
                saveFrontUi();
                drawFrontCanvas(S0.graph);
                schedulePersist();
                return;
            }
            // Drop: страница → домен; блок → страница.
            const dropNode = frontDropTarget(
                node,
                origin.x + (WIDTHS[node.kind] || 180) / 2 + (S0.positionsCache.get(node.id)?.x ?? origin.x - origin.x),
                origin.y + (HEIGHTS[node.kind] || 104) / 2 + (S0.positionsCache.get(node.id)?.y ?? origin.y - origin.y),
            );
            const finalDrop = (() => {
                const movedBox = S0.positionsCache.get(node.id);
                if (!movedBox) return frontDropTarget(node, origin.x + WIDTHS[node.kind] / 2, origin.y + HEIGHTS[node.kind] / 2);
                return frontDropTarget(node, movedBox.x + (WIDTHS[node.kind] || 180) / 2, movedBox.y + (HEIGHTS[node.kind] || 104) / 2);
            })();
            void dropNode;
            if (finalDrop) {
                if (node.kind === "page" && node.parentId !== finalDrop.id) {
                    const domainName = finalDrop.name === "Без домена" ? "" : finalDrop.name;
                    if ((node.domain || "") !== domainName) {
                        frontSetDropTarget(null);
                        const ok = await metaPost({
                            action: "save_page",
                            id: node.id,
                            name: node.name,
                            description: node.description,
                            acceptance: node.acceptance || [],
                            domain: domainName,
                            route: node.route || "",
                        });
                        if (ok) toast(`Страница переведена в домен «${domainName || "Без домена"}»`);
                        pageDragEnded(node);
                        return;
                    }
                }
                if (node.kind === "block" && node.parentId !== finalDrop.id) {
                    frontSetDropTarget(null);
                    const ok = await metaPost({
                        action: "move_block",
                        blockId: node.id,
                        fromPageId: node.parentId,
                        toPageId: finalDrop.id,
                        name: node.name,
                    });
                    if (ok) {
                        if (node.planned) toast(`Блок «${node.name}» перенесён на «${finalDrop.name}»`);
                        else toast(`Создана задача переноса «${node.name}»: убрать со старой страницы, плановый блок — на «${finalDrop.name}»`);
                    }
                    pageDragEnded(node);
                    return;
                }
            }
            saveFrontUi();
            drawFrontCanvas(S0.graph);
            schedulePersist();
        });
}

function pageDragEnded(node) {
    // После мета-операции: перерисовка уже выполнена; позиции переносимых узлов обновить.
}

/* ---------- панель «Свойства» ---------- */

function propsById(node) {
    return S0.nodes.find((item) => item.id === node.parentId) || null;
}

function propsPageId(node) {
    if (node.kind === "page") return node.id;
    const parent = S0.nodes.find((item) => item.id === node.parentId);
    if (parent?.kind === "block") return parent.parentId;
    return parent?.id || "";
}

function renderProps() {
    const box = $("frontProps");
    if (!box) return;
    const node = S0.nodes.find((item) => item.id === S0.selected) || null;
    if (!node) {
        box.innerHTML = '<p class="hint">Выберите узел на канвасе. «＋» на карточке — плановый потомок.</p>';
        return;
    }
    const built = {
        domain: propsDomain,
        page: propsPage,
        block: propsBlock,
        rule: propsRule,
    }[node.kind](node);
    box.innerHTML = `<div class="front-props">
        <div class="insp-head-row"><h3>${esc(built.title)}</h3></div>
        <div class="front-props-body">${built.body}</div></div>`;
    wireProps(node);
}

function propsDomainField(node) {
    return `<label>Домен (роль)<input id="fpDomain" value="${esc(node.domain || "")}" maxlength="60" placeholder="клиент / админ / публичный"></label>`;
}

function propsDomain(node) {
    return {title: "Домен · роль", body: `
        <p class="hint">Ролевая группировка («клиент», «админ»). Страницы без домена — в полке «Без домена».</p>
        <label>Название<input id="fpName" value="${esc(node.name)}" maxlength="60"></label>
        <div class="insp-actions"><button type="button" class="primary" id="fpDomainSave">Сохранить название</button></div>`};
}

function callsField(node) {
    const rows = ((S0.graph || {}).links || {}).rows || [];
    const mine = rows.filter((row) => row.node === node.id);
    const status = mine.length
        ? `<div class="hint calls-status">${mine
              .map((row) => (row.status === "ok"
                  ? `<span class="calls-ok">✓ ${esc(row.call)} — есть в бэкенде</span>`
                  : `<span class="calls-missing">✗ ${esc(row.call)} — эндпоинта в бэкенде нет</span>`))
              .join("<br>")}${mine.some((row) => row.status !== "ok")
              ? "<br>Добавьте недостающий эндпоинт на карту бэкенда (в идее — «На карту ↗»)."
              : ""}</div>`
        : "";
    return `<label>Вызывает эндпоинты бэкенда<textarea id="fpCalls" rows="2" placeholder="GET /lessons&#10;POST /orders/create">${esc((node.calls || []).join("\n"))}</textarea></label>
        <div class="hint">По одному на строку: «МЕТОД /путь». Префикс API можно не писать.</div>${status}`;
}

function agentMark(item) {
    return item.created_by === "agent" ? " · агент" : "";
}

function propsPage(node) {
    const title = (node.planned ? "Плановая страница" : "Страница") + (node.createdBy === "agent" ? " · создано агентом" : "");
    const body = node.planned
        ? `
        <label>Название (папка kebab-case)<input id="fpName" value="${esc(node.name)}" maxlength="60"></label>
        ${propsDomainField(node)}
        <label>Маршрут (path)<input id="fpRoute" value="${esc(node.route)}" maxlength="120" placeholder="/example"></label>
        <label>Описание<textarea id="fpDesc" rows="3">${esc(node.description || "")}</textarea></label>
        <label>Критерии приёмки<textarea id="fpAccept" rows="3">${esc((node.acceptance || []).join("\n"))}</textarea></label>
        ${callsField(node)}
        <div class="insp-actions"><button type="button" class="primary" id="fpSave">Сохранить страницу</button>
        <button type="button" class="danger" id="fpDeletePlanned">Удалить страницу</button></div>`
        : `
        <div class="node-origin code-origin"><b>Страница из кода</b><span>${esc(node.file)}</span></div>
        ${propsDomainField(node)}
        <label>Маршрут (факт router.js, read-only)<input value="${esc(node.route)}" readonly></label>
        <label>Описание (метаданные)<textarea id="fpDesc" rows="3">${esc(node.description || "")}</textarea></label>
        <label>Критерии приёмки<textarea id="fpAccept" rows="3">${esc((node.acceptance || []).join("\n"))}</textarea></label>
        ${callsField(node)}
        <div class="insp-actions"><button type="button" class="primary" id="fpSave">Сохранить метаданные</button></div>
        ${deletionBlock(node)}`;
    return {title, body};
}

function propsBlock(node) {
    const title = (node.planned ? "Плановый блок" : "Блок") + (node.createdBy === "agent" ? " · создано агентом" : "");
    const parent = S0.nodes.find((item) => item.id === node.parentId);
    const body = node.planned
        ? `
        <label>Название (компонент PascalCase)<input id="fpName" value="${esc(node.name)}" maxlength="60"></label>
        <label>Страница<input value="${esc(parent?.name || "")}" readonly></label>
        <label>Описание<textarea id="fpDesc" rows="3">${esc(node.description || "")}</textarea></label>
        <label>Критерии приёмки<textarea id="fpAccept" rows="3">${esc((node.acceptance || []).join("\n"))}</textarea></label>
        ${callsField(node)}
        <div class="insp-actions"><button type="button" class="primary" id="fpSave">Сохранить блок</button>
        <button type="button" class="danger" id="fpDeletePlanned">Удалить блок</button></div>`
        : `
        <div class="node-origin code-origin"><b>Блок из кода</b><span>${esc(node.file)}</span></div>
        <label>Описание (что блок делает)<textarea id="fpDesc" rows="3">${esc(node.description || "")}</textarea></label>
        <label>Критерии приёмки<textarea id="fpAccept" rows="3">${esc((node.acceptance || []).join("\n"))}</textarea></label>
        ${callsField(node)}
        <div class="insp-actions"><button type="button" class="primary" id="fpSave">Сохранить метаданные</button></div>
        ${deletionBlock(node)}`;
    return {title, body};
}

function propsRule(node) {
    const parent = S0.nodes.find((item) => item.id === node.parentId);
    const binding = parent?.kind === "block" ? `блока «${esc(parent.name)}»` : `страницы «${esc(parent?.name || "")}»`;
    return {title: "Правило", body: `
        <p class="hint">Плановая спецификация ${binding}.</p>
        <label>Название<input id="fpName" value="${esc(node.name)}" maxlength="120"></label>
        <label>Задача (описание)<textarea id="fpDesc" rows="3">${esc(node.description || "")}</textarea></label>
        <label>Критерии приёмки<textarea id="fpAccept" rows="3">${esc((node.acceptance || []).join("\n"))}</textarea></label>
        <label>Примечание<input id="fpNote" value="${esc(node.note || "")}" maxlength="200"></label>
        <label class="canvas-toggle" style="margin-top:6px"><input type="checkbox" id="fpDone" ${node.done ? "checked" : ""}> Реализовано</label>
        <div class="insp-actions"><button type="button" class="primary" id="fpSave">Сохранить правило</button>
        <button type="button" class="danger" id="fpDeletePlanned">Удалить правило</button></div>`};
}

/* ---------- операции меты ---------- */

async function metaPost(payload) {
    try {
        const data = await api("/api/frontend/meta", "POST", payload);
        S0.graph = data.graph;
        S0.graphJson = JSON.stringify(data.graph);
        S0.selected = null;
        drawFrontCanvas(data.graph);
        renderHeading(data.graph);
        renderTree(data.graph);
        renderViolCount(data.graph);
        renderViolationsPanel(data.graph);
        renderProps();
        return true;
    } catch (error) {
        toast("Ошибка: " + (error.message || ""));
        return false;
    }
}

function wireProps(node) {
    $("fpDomainSave")?.addEventListener("click", async () => {
        const name = ($("fpName") || {}).value.trim();
        if (!name) {
            toast("Имя обязательно");
            return;
        }
        const ok = await metaPost({action: "save_domain", op: "rename", id: node.id, name});
        if (ok) toast("Домен переименован");
    });
    $("fpSave")?.addEventListener("click", async () => {
        const payload = {action: "", id: node.id, pageId: propsPageId(node)};
        if (node.kind === "page") {
            payload.action = "save_page";
            payload.name = ($("fpName") || {}).value;
            payload.description = valueById("fpDesc");
            payload.acceptance = linesById("fpAccept");
            payload.domain = valueById("fpDomain");
            payload.route = valueById("fpRoute");
            payload.calls = linesById("fpCalls");
        } else if (node.kind === "block") {
            payload.action = "save_block";
            payload.pageId = node.parentId;
            payload.id = node.id;
            payload.name = valueById("fpName");
            payload.description = valueById("fpDesc");
            payload.acceptance = linesById("fpAccept");
            payload.calls = linesById("fpCalls");
        } else {
            const parent = propsById(node);
            payload.action = "save_rule";
            payload.pageId = parent?.kind === "block" ? parent.parentId : node.parentId;
            payload.blockId = parent?.kind === "block" ? parent.id : "";
            payload.id = node.id;
            payload.name = valueById("fpName");
            payload.description = valueById("fpDesc");
            payload.acceptance = linesById("fpAccept");
            payload.note = valueById("fpNote");
            payload.done = !!(document.getElementById("fpDone") || {}).checked;
        }
        const ok = await metaPost(payload);
        if (ok) toast("Сохранено в .studio/frontend.json");
    });
    $("fpDeletePlanned")?.addEventListener("click", async () => {
        const payload = {action: "delete_planned", kind: "", pageId: "", blockId: "", ruleId: ""};
        if (node.kind === "page") {
            payload.kind = "page";
            payload.pageId = node.id;
        } else if (node.kind === "block") {
            payload.kind = "block";
            payload.pageId = node.parentId;
            payload.blockId = node.id;
        } else {
            const parent = propsById(node);
            payload.kind = parent?.kind === "block" ? "blockRule" : "pageRule";
            payload.pageId = node.parentId;
            payload.blockId = parent?.kind === "block" ? parent.id : "";
            payload.ruleId = node.id;
        }
        if (!confirm("Удалить плановый узел? Ctrl+Z вернёт.")) return;
        try {
            const data = await api("/api/frontend/meta", "POST", payload);
            S0.undoDelete = data.undo || null;
            S0.graph = data.graph;
            updateUndoPanel();
            if (S0.selected === node.id) S0.selected = null;
            drawFrontCanvas(data.graph);
            renderHeading(data.graph);
            renderTree(data.graph);
            renderViolCount(data.graph);
            renderViolationsPanel(data.graph);
            renderProps();
            toast("Плановый узел удалён · Ctrl+Z вернёт");
        } catch (error) {
            toast("Ошибка: " + (error.message || ""));
        }
    });
    $("fpDeleteNote")?.addEventListener("click", async () => {
        const isBlock = node.kind === "block";
        const payload = {
            action: "save_delete_note",
            pageId: propsPageId(node),
            blockId: isBlock ? node.id : "",
            note: valueById("fpDelNote"),
            acceptance: (node.acceptance || []).slice(), // критерии общей приёмки узла
        };
        const ok = await metaPost(payload);
        if (ok) toast("Пометка сохранена — попадёт в ТЗ");
    });
    $("fpClearDelete")?.addEventListener("click", async () => {
        const ok = await metaPost({
            action: "save_delete_note",
            pageId: propsPageId(node),
            blockId: node.kind === "block" ? node.id : "",
            note: "",
            acceptance: [],
        });
        if (ok) toast("Пометка снята");
    });
}

function deletionBlock(node) {
    return `<div class="front-delete-block">
        <label>Пометка на удаление — что и почему убираем (код не меняется; попадёт в ТЗ)<textarea id="fpDelNote" rows="2">${esc(node.deletion?.note || "")}</textarea></label>
        <div class="insp-actions">
            <button type="button" id="fpDeleteNote">${node.deletion ? "Изменить пометку" : "Пометить на удаление"}</button>
            ${node.deletion ? '<button type="button" id="fpClearDelete">Снять пометку</button>' : ""}
        </div></div>`;
}

function valueById(id) {
    const element = document.getElementById(id);
    if (!element) return "";
    return element.value ?? "";
}

function linesById(id) {
    const element = document.getElementById(id);
    if (!element) return [];
    return String(element.value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function updateUndoPanel() {
    const panel = document.getElementById("frontUndo");
    if (panel) panel.hidden = !S0.undoDelete;
}

async function restoreLastDelete() {
    if (!S0.undoDelete) return;
    try {
        const data = await api("/api/frontend/meta", "POST", {action: "restore_planned", ...S0.undoDelete});
        S0.undoDelete = null;
        S0.graph = data.graph;
        updateUndoPanel();
        drawFrontCanvas(data.graph);
        renderHeading(data.graph);
        renderTree(data.graph);
        renderViolCount(data.graph);
        renderViolationsPanel(data.graph);
        renderProps();
        toast("Восстановлено");
    } catch (error) {
        toast("Ошибка: " + (error.message || ""));
    }
}

/* ---------- панель «Нарушения» ---------- */

function renderViolCount(graph) {
    const chip = $("frontViolCount");
    if (!chip) return;
    const stats = graph.stats || {};
    const total = (stats.errors || 0) + (stats.warnings || 0);
    chip.textContent = total ? String(total) : "";
    chip.className = "chip" + (stats.errors ? " no" : total ? " warn" : "");
}

function renderViolationsPanel(graph) {
    const box = $("frontViolBody");
    if (!box) return;
    const violations = graph.violations || [];
    const query = S0.query.trim();
    let items = [...violations];
    if (S0.filterSev !== "all") items = items.filter((v) => v.severity === S0.filterSev);
    if (query) items = items.filter((v) => v.file.includes(query) || v.message.includes(query) || v.code.includes(query));
    const controls = `<div class="front-v-head">
        <div class="rail-title">НАРУШЕНИЯ · ${items.length}</div>
        <div class="front-v-filters">
            <button type="button" data-sev="all" class="${S0.filterSev === "all" ? "on" : ""}">все</button>
            <button type="button" data-sev="error" class="${S0.filterSev === "error" ? "on" : ""}">ошибки</button>
            <button type="button" data-sev="warning" class="${S0.filterSev === "warning" ? "on" : ""}">замечания</button>
            <button type="button" data-sev="copy" title="Скопировать видимый список для агента">Копировать</button>
        </div>
        <input id="frontQuery" type="search" placeholder="Файл, правило, текст…">
    </div>`;
    if (!items.length) {
        box.innerHTML = controls + '<div class="front-v-body"><p class="hint">Нарушений нет — фронтенд чистый.</p></div>';
        wireFilters(box);
        return;
    }
    const byCode = new Map();
    for (const item of items) {
        if (!byCode.has(item.code)) byCode.set(item.code, []);
        byCode.get(item.code).push(item);
    }
    const groups = [...byCode.entries()].map(([code, rows]) => {
        const open = S0.openGroups.has(code);
        const errs = rows.filter((row) => row.severity === "error").length;
        return `<div class="front-v-group">
            <div class="front-v-head-row" data-code="${esc(code)}">
                <span class="t">${esc(code)}</span>
                <span class="chip${errs ? " no" : " warn"}">${rows.length}</span></div>
            <div class="front-v-rows" ${open ? "" : "hidden"}>
                ${rows.map((row) => `<div class="front-v-row ${esc(row.severity)}" data-file="${esc(row.file)}" data-line="${row.line || 1}" title="Открыть в редакторе">
                    <span class="front-v-file">${esc(row.file)}:${row.line || 1}</span>
                    <span class="hint">${esc(row.message)}</span></div>`).join("")}
            </div></div>`;
    }).join("");
    box.innerHTML = controls + `<div class="front-v-body">${groups}</div>`;
    wireFilters(box);
}

function wireFilters(box) {
    box.querySelectorAll(".front-v-filters button").forEach((button) => {
        button.onclick = () => {
            S0.filterSev = button.dataset.sev;
            renderViolationsPanel(S0.graph);
        };
    });
    const search = box.querySelector("#frontQuery");
    if (search) {
        search.value = S0.query;
        search.oninput = () => {
            S0.query = search.value || "";
            renderViolationsPanel(S0.graph);
        };
    }
}

/* ---------- дерево структуры ---------- */

function renderTree(graph) {
    const body = $("frontTreeBody");
    if (!body) return;
    if (!graph) {
        body.innerHTML = '<p class="hint">Загрузка…</p>';
        return;
    }
    const mode = S0.treeMode;
    const collapsed = S0.treeCollapsed;
    const rows = [];
    const twist = (id, hasKids, open) =>
        hasKids
            ? `<button type="button" class="front-twist" data-twist="${esc(id)}" title="${open ? "Свернуть" : "Развернуть"}">${open ? "▾" : "▸"}</button>`
            : '<span class="front-twist leaf">·</span>';
    const row = (id, kind, label, depth, hasKids, open) =>
        `<div class="front-tree-row" data-id="${esc(id)}" data-front-kind="${kind}" style="padding-left:${6 + depth * 12}px">
            ${twist(id, hasKids, open)}
            <span class="t" title="${esc(label)}">${esc(label)}</span>
            ${kind === "page" && hasKids && collapsed.has(id) ? "" : ""}
        </div>`;

    const nodeLabel = (node) => {
        const kind = node.kind === "page" ? "[ПЛАН] страница" : node.kind === "block" ? "[ПЛАН] блок" : node.kind === "rule" ? "правило" : "домен";
        return node.kind === "page" || node.kind === "block"
            ? (node.planned ? `[ПЛАН] ${node.name}` : node.name)
            : node.name;
    };

    const walkBlock = (block, depth) => {
        rows.push(row(block.id, "block", "[ПЛАН] ".repeat(block.planned) + block.name + agentMark(block), depth, (block.rules || []).length > 0, !collapsed.has(block.id)));
        if (collapsed.has(block.id)) return;
        if (mode === "fact") return; // правила — всегда план
        for (const rule of block.rules || []) {
            rows.push(row(rule.id, "rule", rule.name || "", depth + 1, false, true));
        }
    };
    const walkPage = (page, depth) => {
        const kidsCount = (page.blocks || []).length + (page.rules || []).length;
        rows.push(row(page.id, "page", "[ПЛАН] ".repeat(page.planned) + page.name + agentMark(page), depth, kidsCount > 0, !collapsed.has(page.id)));
        if (collapsed.has(page.id)) return;
        for (const block of page.blocks || []) {
            if (mode === "fact" && block.planned) continue; // плановые блоки — в режиме «план»
            if (mode === "plan" && !block.planned && !(block.rules || []).length) continue;
            walkBlock(block, depth + 1);
        }
        if (mode !== "fact") {
            for (const rule of page.rules || []) {
                rows.push(row(rule.id, "rule", rule.name || "", depth + 1, false, true));
            }
        }
    };

    for (const domain of graph.domains || []) {
        const domainPages = (graph.pages || []).filter(
            (page) => page.domain === domain.name && treePageVisible(page, mode),
        );
        rows.push(row(domain.id, "domain", domain.name, 0, domainPages.length > 0, !collapsed.has(domain.id)));
        if (collapsed.has(domain.id)) continue;
        domainPages.forEach((page) => walkPage(page, 1));
    }
    const orphans = (graph.pages || []).filter((page) => !page.domain && treePageVisible(page, mode));
    if (orphans.length) {
        rows.push(row("domain:Без домена", "domain", "Без домена", 0, true, !collapsed.has("domain:Без домена")));
        if (!collapsed.has("domain:Без домена")) {
            orphans.forEach((page) => walkPage(orphans.length ? page : page, 1));
        }
    }
    body.innerHTML = rows.join("");
}

function treePageVisible(page, mode) {
    if (mode === "all") return true;
    if (mode === "fact") return !page.planned;
    // план: страница видна, если сама плановая или у неё есть плановое содержимое
    return page.planned
        || (page.blocks || []).some((block) => block.planned || (block.rules || []).length)
        || (page.rules || []).length > 0;
}

/* ---------- ТЗ и отчёт ---------- */

const VERIFY_KINDS = {
    page: "страница",
    block: "блок",
    page_deletion: "удаление страницы",
    block_deletion: "удаление блока",
    rule: "правило",
    violation: "нарушение",
};
const VERIFY_MARKS = {"выполнено": "✓", "частично": "◐", "не сделано": "✗"};

function verifyBody(report) {
    const rows = (report.items || []).map((item) => `<li>
        <b>${VERIFY_MARKS[item.status] || "?"}</b> T${esc(String(item.number))} ${esc(VERIFY_KINDS[item.kind] || item.kind)}
        «${esc(item.name)}» — ${esc(item.message)}</li>`).join("");
    return `<p class="hint">ТЗ ${esc(report.brief_id || "?")}: выполнено ${report.done} из ${report.total} · ${esc(report.status)}.
        Проверка идёт по текущему коду frontend/ и объявленным вызовам эндпоинтов.</p>
        <ul class="verify-list">${rows || "<li>В ТЗ нет задач.</li>"}</ul>`;
}

async function openVerify() {
    try {
        const report = await api("/api/frontend/verify?brief=last");
        modal("Проверка ТЗ фронтенда", verifyBody(report), [
            {label: "Проверить снова", action: openVerify},
            {label: "Закрыть", cls: "primary"},
        ]);
    } catch (error) {
        toast(error.status === 404
            ? "ТЗ фронтенда ещё не собрано: нажмите «⇄ ТЗ…»"
            : "Не удалось проверить ТЗ: " + error.message);
    }
}

const BRIEF_SECTIONS = [
    ["outline", "Область работ"],
    ["links", "Связи с бэкендом"],
    ["violations", "Нарушения правил"],
    ["verify", "Проверка"],
];
const REPORT_SECTIONS = [
    ["summary", "Стек и сводка"],
    ["notes", "Замечания проекта"],
    ["structure", "Структура"],
    ["coverage", "Покрытие правил"],
    ["violations", "Нарушения"],
    ["dag", "Связи импортов (DAG)"],
];

async function requestBrief(scope, nodeId) {
    const base = {scope};
    if (scope === "node" && nodeId) base.nodeId = nodeId;
    const scopes = [["all", "весь фронтенд"], ["violations", "только нарушения"]];
    if (scope === "node" && nodeId) scopes.unshift(["node", "выбранный узел и потомки"]);
    await documentDialog({
        title: "ТЗ для AI-агента — фронтенд",
        endpoint: "/api/frontend/brief",
        base,
        sections: BRIEF_SECTIONS,
        json: false,
        scopes,
        accept: {
            endpoint: "/api/frontend/sync/apply",
            intro: "Вставьте итоговый JSON-отчёт агента по этому ТЗ (подойдёт и весь ответ с блоком json). Студия сверит записи с кодом и покажет отличия; описания и критерии страниц и блоков пишутся только отмеченные и только по кнопке «Применить».",
            onApplied: (data) => applySyncedGraph(data.graph),
        },
    });
}

/* ---------- синхронизация описаний с кодом (⇄ как на карте) ---------- */

let syncOpen = false;

async function renderSyncPrompt(syncState) {
    const preview = $("frontSyncPreview");
    const stats = $("frontSyncStats");
    if (!preview) return;
    preview.textContent = "Собираю промпт…";
    try {
        const data = await api("/api/frontend/sync", "POST", {
            scope: syncState.scope,
            domain: syncState.domain,
            pageId: syncState.pageId,
        });
        syncState.markdown = data.markdown || "";
        preview.textContent = syncState.markdown;
        stats.textContent = "Узлов в области: " + promptTotal(syncState.markdown);
    } catch (error) {
        preview.textContent = "Ошибка: " + error.message;
    }
}

function applySyncedGraph(graph) {
    if (!graph) return;
    S0.graph = graph;
    drawFrontCanvas(graph);
    renderHeading(graph);
    renderTree(graph);
    renderViolCount(graph);
    renderViolationsPanel(graph);
    renderProps();
}

function openSyncDialog() {
    if (syncOpen) return;
    syncOpen = true;
    const domains = [...new Set(
        (S0.graph?.pages || [])
            .filter((page) => !page.planned)
            .map((page) => String(page.domain || "").trim())
            .filter(Boolean)
    )];
    const state = {scope: "all", domain: "", pageId: "", markdown: ""};
    const overlay = document.createElement("div");
    overlay.className = "studio-modal frontend-overlay";
    overlay.innerHTML = `<div class="modal-box brief-box"><h3>Синхронизация описаний фронтенда с кодом</h3>
        <div class="brief-layout">
            <div class="brief-left">
                <div class="brief-controls">
                    <label class="hint">Область:
                        <select id="frontSyncScope">
                            <option value="all">весь фронтенд</option>
                            <option value="domain">конкретный домен (роль)</option>
                            <option value="page">конкретная страница</option>
                            <option value="unready">только узлы без описания</option>
                        </select>
                    </label>
                    <label class="hint" id="frontSyncDomainWrap" hidden>Домен:
                        <select id="frontSyncDomain">${domains.map((d) => `<option>${esc(d)}</option>`).join("")}</select>
                    </label>
                    <label class="hint" id="frontSyncPageWrap" hidden>Страница:
                        <select id="frontSyncPage">${(S0.graph?.pages || [])
                            .filter((page) => !page.planned)
                            .map((page) => `<option value="${esc(page.id)}">${esc(page.name)}</option>`).join("")}</select>
                    </label>
                </div>
                <p class="hint">Шаг 1. Промпт для AI-агента с доступом к папке проекта: агент прочитает реальный
                код страниц и блоков и вернёт <b>описания и критерии</b> одним JSON-блоком. Файлы агент не правит.
                Плановые узлы и правила в промпт не попадают.</p>
                <p class="hint">Шаг 2. Вставьте JSON-ответ на вкладке «Принять ответ»: студия покажет отличия,
                и вы сами решите, что записать в .studio/frontend.json. Код frontend/, домены и связи не меняются.</p>
            </div>
            <div class="brief-preview">
                <div class="sync-steps" role="tablist">
                    <button type="button" class="on" data-step="prompt" role="tab">1. Промпт</button>
                    <button type="button" data-step="accept" role="tab">2. Принять ответ</button>
                </div>
                <div class="sync-step" id="frontSyncStepPrompt">
                    <div class="brief-stats" id="frontSyncStats">Подготовка…</div>
                    <pre id="frontSyncPreview">Собираю промпт…</pre>
                    <div class="hint">Промпт содержит узлы фронтенда. Перед отправкой во внешний AI-сервис это решает пользователь.</div>
                    <div class="brief-actions">
                        <button type="button" id="frontSyncCopy">Скопировать промпт</button>
                        <button type="button" id="frontSyncDownload">Скачать .md</button>
                    </div>
                </div>
                <div class="sync-step" id="frontSyncStepAccept" hidden></div>
                <div class="brief-actions"><button type="button" class="primary" id="frontSyncClose">Закрыть</button></div>
            </div>
        </div></div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (event) => {
        if (event.target === overlay) {
            overlay.remove();
            syncOpen = false;
        }
    });
    const acceptHost = overlay.querySelector("#frontSyncStepAccept");
    acceptHost.appendChild(createSyncAcceptPane({
        endpoint: "/api/frontend/sync/apply",
        onApplied: (data) => applySyncedGraph(data.graph),
    }));
    overlay.querySelectorAll(".sync-steps button").forEach((button) => {
        button.onclick = () => {
            overlay.querySelectorAll(".sync-steps button").forEach((b) => b.classList.toggle("on", b === button));
            overlay.querySelector("#frontSyncStepPrompt").hidden = button.dataset.step !== "prompt";
            acceptHost.hidden = button.dataset.step !== "accept";
        };
    });
    const scopeSelect = overlay.querySelector("#frontSyncScope");
    const domainWrap = overlay.querySelector("#frontSyncDomainWrap");
    const pageWrap = overlay.querySelector("#frontSyncPageWrap");
    const domainSelect = overlay.querySelector("#frontSyncDomain");
    const pageSelect = overlay.querySelector("#frontSyncPage");
    const syncTabs = () => {
        domainWrap.hidden = state.scope !== "domain";
        pageWrap.hidden = state.scope !== "page";
    };
    syncTabs();
    scopeSelect.onchange = () => {
        state.scope = scopeSelect.value;
        syncTabs();
        void renderSyncPrompt(state);
    };
    domainSelect.onchange = () => {
        state.domain = domainSelect.value;
        void renderSyncPrompt(state);
    };
    pageSelect.onchange = () => {
        state.pageId = pageSelect.value;
        void renderSyncPrompt(state);
    };
    overlay.querySelector("#frontSyncCopy").onclick = () => copyFront(state.markdown, "Промпт скопирован");
    overlay.querySelector("#frontSyncDownload").onclick = () => {
        const blob = new Blob([state.markdown], {type: "text/markdown;charset=utf-8"});
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "sync-frontend.md";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    };
    overlay.querySelector("#frontSyncClose").onclick = () => {
        overlay.remove();
        syncOpen = false;
    };
    void renderSyncPrompt(state);
}

async function requestReport() {
    await documentDialog({
        title: "Отчёт о существующем фронтенде",
        endpoint: "/api/frontend/report",
        base: {},
        sections: REPORT_SECTIONS,
        json: true,
        jsonName: "frontend-report.json",
    });
}

/* ---------- формы создания (домен/плановый потомок) ---------- */

function openDomainForm() {
    switchRightTab("props");
    const box = $("frontProps");
    if (!box) return;
    box.innerHTML = `<div class="front-props">
        <div class="insp-head-row"><h3>Новый домен (роль)</h3></div>
        <div class="front-props-body">
        <label>Название<input id="fpNewDomain" maxlength="60" placeholder="клиент / админ / публичный"></label>
        <div class="insp-actions">
            <button type="button" class="primary" id="fpDomainCreate">Создать домен</button>
            <button type="button" id="fpDomainCancel">Отмена</button></div></div></div>`;
    document.getElementById("fpDomainCreate")?.addEventListener("click", async () => {
        const name = valueById("fpNewDomain").trim();
        if (!name) {
            toast("Имя домена обязательно");
            return;
        }
        const ok = await metaPost({action: "save_domain", op: "create", name});
        if (!ok) return;
        S0.pendingDomain = false;
        drawFrontCanvas(S0.graph);
        renderTree(S0.graph);
        toast("Домен создан");
    });
    document.getElementById("fpDomainCancel")?.addEventListener("click", () => {
        S0.pendingDomain = false;
        drawFrontCanvas(S0.graph);
        renderProps();
    });
}

function openCreateForm(node, kind) {
    switchRightTab("props");
    const box = $("frontProps");
    if (!box) return;
    const isRule = kind === "rule";
    const title = isRule ? `Новое правило → «${node.name}»` : `Новая плановая страница в «${node.name}»`;
    box.innerHTML = `<div class="front-props">
        <div class="insp-head-row"><h3>${esc(title)}</h3></div>
        <div class="front-props-body">
        <input type="hidden" id="fpNewKind" value="${isRule ? "rule" : "page"}">
        <label>Название<input id="fpNewName" maxlength="120" placeholder="${isRule ? "Например: блок показывает сетку недели" : "Например: booking-view"}"></label>
        ${isRule
            ? `<label>Задача (описание)<textarea id="fpNewDesc" rows="3"></textarea></label>
               <label>Критерии (по строке)<textarea id="fpNewAcc" rows="2"></textarea></label>`
            : `<label>Маршрут<input id="fpNewRoute" maxlength="120" placeholder="/example"></label>
               <label>Описание<textarea id="fpNewDesc" rows="2"></textarea></label>
               <label>Критерии (по строке)<textarea id="fpNewAcc" rows="2"></textarea></label>`}
        <div class="insp-actions"><button type="button" class="primary" id="fpNewSave">Создать в плане</button>
        <button type="button" id="fpNewCancel">Отмена</button></div></div></div>`;
    document.getElementById("fpNewCancel")?.addEventListener("click", () => drawFrontCanvas(S0.graph));
    document.getElementById("fpNewSave")?.addEventListener("click", async () => {
        if (isRule) {
            const payload = {
                action: "save_rule",
                name: valueById("fpNewName").trim(),
                description: valueById("fpNewDesc"),
                acceptance: linesById("fpNewAcc"),
            };
            if (node.kind === "block") {
                payload.pageId = node.parentId;
                payload.blockId = node.id;
            } else {
                payload.pageId = node.id;
            }
            if (!payload.name) {
                toast("Название обязательно");
                return;
            }
            if (await metaPost(payload)) {
                document.getElementById("frontNewOk")?.click();
                toast("Правило создано");
            }
            return;
        }
        const name = valueById("fpNewName").trim();
        if (!name) {
            toast("Название обязательно");
            return;
        }
        const payload = {
            action: "save_page",
            id: "page-plan:" + crypto.randomUUID(),
            plannedNew: true,
            name,
            domain: node.name === "Без домена" ? "" : node.name,
            route: valueById("fpNewRoute"),
            description: valueById("fpNewDesc"),
            acceptance: linesById("fpNewAcc"),
        };
        if (await metaPost(payload)) toast("Плановая страница создана");
    });
}

/* ---------- init ---------- */

function applyFrontView() {
    applyTransform("frontView", S0.view);
}

let frontAnim = null;

function stopFrontAnim() {
    if (frontAnim) {
        cancelAnimationFrame(frontAnim.raf);
        frontAnim = null;
    }
}

function animateFrontTo(target) {
    stopFrontAnim();
    const canvas = $("frontCanvas");
    const from = {x: S0.view.x, y: S0.view.y, k: S0.view.k};
    const dx = target.x - from.x;
    const dy = target.y - from.y;
    const dk = target.k - from.k;
    const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const distance = Math.hypot(dx, dy) + Math.abs(dk) * 500;
    const duration = reduced || !Number.isFinite(distance) || distance < 2
        ? 0
        : Math.round(Math.min(420, 180 + distance * 0.3));
    if (!duration) {
        S0.view = {x: target.x, y: target.y, k: target.k};
        applyFrontView();
        return;
    }
    frontAnim = {t0: performance.now(), raf: 0};
    const ease = (t) => 1 - Math.pow(1 - t, 3);
    const step = (now) => {
        if (!frontAnim) return;
        if (canvas && canvas.classList.contains("grabbing")) {
            frontAnim = null;
            return;
        }
        const t = Math.min(1, (now - frontAnim.t0) / duration);
        const eased = ease(t);
        S0.view.x = from.x + dx * eased;
        S0.view.y = from.y + dy * eased;
        S0.view.k = from.k + dk * eased;
        applyFrontView();
        if (t < 1) frontAnim.raf = requestAnimationFrame(step);
        else frontAnim = null;
    };
    frontAnim.raf = requestAnimationFrame(step);
}

/* Гибридное вписывание на фронт-канвасе (логика «Карты проекта»):
   камера центрируется на узле; масштаб уменьшается, только когда узел
   с потомками не влезает в кадр; приближения не делается. */
function centerFrontNode(id) {
    const canvas = $("frontCanvas");
    if (!canvas || !S0.graph) return;
    let box = (S0.positionsCache || new Map()).get(id);
    if (!box) {
        drawFrontCanvas(S0.graph);
        box = (S0.positionsCache || new Map()).get(id);
        if (!box) return;
    }
    const node = S0.nodes.find((item) => item.id === id);
    if (!node) return;
    const width = WIDTHS[node.kind] || 180;
    const height = HEIGHTS[node.kind] || 104;
    const frame = {x: box.x, y: box.y, right: box.x + width, bottom: box.y + height};
    let hasDescendants = false;
    const seen = new Set([id]);
    const stack = [id];
    while (stack.length) {
        for (const kid of S0.children.get(stack.pop()) || []) {
            if (seen.has(kid)) continue;
            seen.add(kid);
            const kbox = (S0.positionsCache || new Map()).get(kid);
            if (kbox) {
                frame.x = Math.min(frame.x, kbox.x);
                frame.y = Math.min(frame.y, kbox.y);
                frame.right = Math.max(frame.right, kbox.x + (WIDTHS[(S0.nodes.find((n) => n.id === kid) || {}).kind] || 180));
                frame.bottom = Math.max(frame.bottom, kbox.y + (HEIGHTS[(S0.nodes.find((n) => n.id === kid) || {}).kind] || 104));
                hasDescendants = true;
            }
            if (!S0.treeCollapsed.has(kid)) stack.push(kid);
        }
    }
    const bw = Math.max(1, frame.right - frame.x);
    const bh = Math.max(1, frame.bottom - frame.y);
    const cw = canvas.clientWidth || 900;
    const ch = canvas.clientHeight || 600;
    const margin = 72;
    const fitK = Math.min(2.5, Math.max(0.2, Math.min((cw - margin * 2) / bw, (ch - margin * 2) / bh)));
    const k = hasDescendants ? Math.min(S0.view.k, fitK) : S0.view.k;
    animateFrontTo({x: (cw - bw * k) / 2 - frame.x * k, y: (ch - bh * k) / 2 - frame.y * k, k});
}

function switchRightTab(tab) {
    S0.propsTab = tab;
    S0.stickyViol = tab === "viol";
    $("frontTabProps")?.classList.toggle("on", tab === "props");
    $("frontTabViol")?.classList.toggle("on", tab === "viol");
    const props = $("frontProps");
    const wrap = $("frontViolWrap");
    if (props) props.hidden = tab !== "props";
    if (wrap) wrap.hidden = tab !== "viol";
    if (tab === "viol" && S0.graph) renderViolationsPanel(S0.graph);
    if (tab === "props") renderProps();
}

function initFrontend() {
    loadFrontUi();
    document.addEventListener("keydown", (event) => {
        if (S.tab !== "frontend") return;
        const tag = (document.activeElement?.tagName || "").toLowerCase();
        const typing = tag === "input" || tag === "textarea" || tag === "select" || document.activeElement?.isContentEditable;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && !event.shiftKey && !typing) {
            event.preventDefault();
            restoreLastDelete();
        }
        if ((event.key === "f" || event.key === "F" || event.key === "а" || event.key === "А") && !event.ctrlKey && !event.metaKey && !event.altKey && !typing) {
            event.preventDefault();
            const canvas = $("frontCanvas");
            const stage = canvas?.querySelector(".stage");
            if (canvas && stage) {
                stopFrontAnim();
                fitView(canvas, S0.view, applyFrontView);
                saveFrontUi();
            }
        }
    });
    document.getElementById("frontRescan")?.addEventListener("click", async () => {
        await loadFrontGraph();
        toast("frontend/ перечитан");
    });
    document.getElementById("frontTreeFold")?.addEventListener("click", () => {
        // Свернуть всё: домены с содержимым и каждую страницу с потомками.
        let changed = false;
        const graph = S0.graph;
        if (!graph) return;
        for (const domain of graph.domains || []) {
            const hasPages = (graph.pages || []).some((page) => page.domain === domain.name && treePageVisible(page, S0.treeMode));
            if (hasPages && !S0.treeCollapsed.has(domain.id)) {
                S0.treeCollapsed.add(domain.id);
                changed = true;
            }
        }
        for (const page of graph.pages || []) {
            const kids = (page.blocks || []).filter(
                (block) => (S0.treeMode === "all" || S0.treeMode === "fact" || block.planned || (block.rules || []).length),
            ).length + (S0.treeMode !== "fact" ? (page.rules || []).length : 0);
            if (kids && !S0.treeCollapsed.has(page.id)) {
                S0.treeCollapsed.add(page.id);
                changed = true;
            }
        }
        if (changed) {
            saveFrontUi();
            renderTree(graph);
        }
    });
    document.getElementById("frontDomainBtn")?.addEventListener("click", () => {
        if (S0.pendingDomain) {
            // Обезличенный узел уже есть — просто камера к нему.
            centerFrontNode("domain-pending");
            switchRightTab("props");
            openDomainForm();
            return;
        }
        S0.pendingDomain = true;
        drawFrontCanvas(S0.graph);
        switchRightTab("props");
        openDomainForm();
    });
    document.getElementById("frontSyncBtn")?.addEventListener("click", () => openSyncDialog());
    document.getElementById("frontBriefBtn")?.addEventListener("click", async () => {
        const target = S0.selected ? S0.nodes.find((item) => item.id === S0.selected) : null;
        if (target && target.kind === "rule") {
            await requestBrief("node", target.parentId);
            return;
        }
        await requestBrief(target?.kind === "page" || target?.kind === "block" ? "node" : "all", target?.id || null);
    });
    document.getElementById("frontUndoBtn")?.addEventListener("click", () => restoreLastDelete());
    document.getElementById("frontFitBtn")?.addEventListener("click", () => {
        stopFrontAnim();
        const canvas = $("frontCanvas");
        const stage = canvas?.querySelector(".stage");
        if (!canvas || !stage) return;
        fitView(canvas, S0.view, applyFrontView);
        saveFrontUi();
    });
    document.getElementById("frontReportBtn")?.addEventListener("click", requestReport);
    document.getElementById("frontVerifyBtn")?.addEventListener("click", openVerify);
    document.querySelectorAll("[data-front-mode]").forEach((button) => {
        button.addEventListener("click", () => {
            S0.mode = button.dataset.frontMode === "plan" ? "plan" : "fact";
            document.querySelectorAll("[data-front-mode]").forEach((item) => item.classList.toggle("on", item.dataset.frontMode === S0.mode));
            drawFrontCanvas(S0.graph);
            saveFrontUi();
        });
    });
    document.querySelectorAll("[data-tree-mode]").forEach((button) => {
        button.addEventListener("click", () => {
            const mode = button.dataset.treeMode;
            if (!["all", "fact", "plan"].includes(mode)) return;
            S0.treeMode = mode;
            document.querySelectorAll("[data-tree-mode]").forEach((item) => item.classList.toggle("on", item.dataset.treeMode === S0.treeMode));
            renderTree(S0.graph);
            saveFrontUi();
        });
    });
    const propsButton = document.getElementById("frontTabProps");
    const violButton = document.getElementById("frontTabViol");
    propsButton?.addEventListener("click", () => switchRightTab("props"));
    violButton?.addEventListener("click", () => switchRightTab("viol"));
    bindViewport("frontCanvas", S0.view, applyFrontView);
    document.getElementById("frontCanvas")?.addEventListener("click", (event) => {
        if (event.target.closest(".card")) return;
        if (Date.now() - S0.justDraggedAt < 250) return;
        if (S0.selected !== null) {
            S0.selected = null;
            drawFrontCanvas(S0.graph);
            renderProps();
        }
    });
    document.addEventListener("click", (event) => {
        const twistButton = event.target.closest("#frontTreeBody .front-twist[data-twist]");
        if (twistButton) {
            const id = twistButton.dataset.twist;
            if (!id) return;
            if (S0.treeCollapsed.has(id)) S0.treeCollapsed.delete(id);
            else S0.treeCollapsed.add(id);
            saveFrontUi();
            drawFrontCanvas(S0.graph);
            renderTree(S0.graph);
            return;
        }
        const treeFile = event.target.closest("#frontTreeBody .front-tree-row");
        if (treeFile) {
            S0.selected = treeFile.dataset.id;
            drawFrontCanvas(S0.graph);
            renderProps();
            centerFrontNode(treeFile.dataset.id);
            return;
        }
        const sevButton = event.target.closest("#frontViolBody .front-v-filters button");
        if (sevButton && sevButton.dataset.sev === "copy") {
            const list = (S0.graph.violations || [])
                .filter((item) => (S0.filterSev === "all" || item.severity === S0.filterSev))
                .filter((item) => !S0.query.trim() || item.file.includes(S0.query.trim()) || item.message.includes(S0.query.trim()) || item.code.includes(S0.query.trim()))
                .map((item) => `${item.file}:${item.line || 1} — ${item.code} — ${item.message}`);
            copyFront(list.join("\n") || "нарушений нет", "Список нарушений скопирован");
            return;
        }
        if (sevButton) {
            S0.filterSev = sevButton.dataset.sev;
            renderViolationsPanel(S0.graph);
            return;
        }
        const groupHead = event.target.closest("#frontViolBody .front-v-head-row");
        if (groupHead) {
            const code = groupHead.dataset.code;
            if (!code) return;
            if (S0.openGroups.has(code)) S0.openGroups.delete(code);
            else S0.openGroups.add(code);
            renderViolationsPanel(S0.graph);
            return;
        }
        const violationRow = event.target.closest("#frontViolBody .front-v-row");
        if (violationRow?.dataset.file) {
            openEditorForNode({
                file_path: "frontend/" + violationRow.dataset.file,
                line: Number(violationRow.dataset.line) || 1,
                planned: false,
            });
        }
    });
    document.getElementById("frontTabViol")?.addEventListener("click", () => {
        S0.stickyViol = true;
        switchRightTab("viol");
    });
    document.getElementById("frontTabProps")?.addEventListener("click", () => {
        S0.stickyViol = false;
        switchRightTab("props");
    });
    document.addEventListener("mousedown", () => { S0.mouseDown = true; }, true);
    document.addEventListener("mouseup", () => { S0.mouseDown = false; }, true);
    if (S.tab === "frontend") void loadFrontGraph();
}

function userIsEditing() {
    const active = document.activeElement;
    return !!active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName)
        && !!active.closest("#tab-frontend");
}

/* Отложенное «живое» обновление: ждём, пока пользователь закончит ввод. Опрос, а не
   событие focusout: оно не приходит, если окно браузера не в фокусе. */
function settleFrontRefresh() {
    if (!S0.refreshPending) return;
    if (userIsEditing() || S0.mouseDown) {
        setTimeout(settleFrontRefresh, 400);
        return;
    }
    S0.refreshPending = false;
    void loadFrontGraph({soft: true});
}

async function loadFrontGraph(options = {}) {
    if (S0.loading) return;
    if (options.soft && (userIsEditing() || S0.mouseDown)) {
        if (!S0.refreshPending) {
            S0.refreshPending = true;
            setTimeout(settleFrontRefresh, 400);
        }
        return;
    }
    S0.loading = true;
    if (!S0.graph) {
        renderTree(null);
        renderHeading(null);
        renderViolationsPanel({violations: []});
        renderProps();
        renderLoading();
    }
    try {
        const graph = await api("/api/frontend/graph");
        if (!graph.present) {
            S0.graph = {present: false, pages: [], domains: [], stats: {}, violations: []};
            renderHeading(S0.graph);
            renderNoFrontend();
            switchRightTab("props");
            return;
        }
        const json = JSON.stringify(graph);
        if (options.soft && json === S0.graphJson) return;
        S0.graphJson = json;
        S0.graph = graph;
        renderHeading(graph);
        drawFrontCanvas(graph);
        renderTree(graph);
        renderViolCount(graph);
        renderViolationsPanel(graph);
        renderStatus("");
        switchRightTab(S0.propsTab);
    } catch (error) {
        S0.error = error.message;
        renderHeading(null);
        renderFrontendError(error.message);
        renderTree(null);
        toast("Вкладка фронтенда: " + error.message);
    } finally {
        S0.loading = false;
    }
}

export {initFrontend, loadFrontGraph as renderFrontend};
