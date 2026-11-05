"""Тесты фронтенд-сканера: структура frontend/, DAG-правила, нейминг, hex."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.frontend import scan_frontend

GOOD_FRONT = {
    "index.html": "<!doctype html><html><body><div id=\"app\"></div></body></html>",
    "package.json": json.dumps(
        {
            "name": "demo-frontend",
            "type": "module",
            "dependencies": {"vue": "^3.5.0", "vue-router": "^4.4.0", "pinia": "^4.0.0"},
            "devDependencies": {"vite": "^8.0.0", "vitest": "^3.0.0"},
        }
    ),
    "vite.config.js": "import { defineConfig } from 'vite'\nexport default defineConfig({})",
    "public/favicon.svg": "<svg/>",
    "src/main.js": "import { createApp } from 'vue'\nimport App from './ui/App.vue'\ncreateApp(App)",
    "src/ui/App.vue": "<template><div/></template>\n<style>\n</style>",
    "src/ui/styles/tokens.css": ":root{--color-primary:#fx}\n:root{--gap:12px;color:#123456}",
    "src/ui/styles/base.css": ".btn{padding:8px 16px}",
    "src/ui/styles/icons.css": "/* nothing */",
    "src/ui/app/index.js": "export { merchantRouter } from './router.js'",
    "src/ui/app/router.js": "export const merchantRouter = []",
    "src/ui/composables/index.js": "export { useLogout } from './logout.js'",
    "src/ui/composables/logout.js": "export function useLogout(){ return ()=>{} }",
    "src/ui/components/index.js": "export { default as MainCard } from './MainCard.vue'",
    "src/ui/components/MainCard.vue": "<template><div/></template>",
    "src/ui/pages/booking-view/index.vue": "<template><MainCard/></template>",
    "src/logic/actions/index.js": "export { loadBooking } from './booking.js'",
    "src/logic/actions/booking.js": "import client from '../api/client.js'\nexport function loadBooking(id){ return client.get('/x', {id}) }",
    "src/logic/api/index.js": "export { client } from './client.js'",
    "src/logic/api/client.js": "export const client = { get(){}, post(){} }",
    "src/logic/store/index.js": "export { useBookingStore } from './booking.js'",
    "src/logic/store/booking.js": "import { defineStore } from 'pinia'\nexport const useBookingStore = defineStore('useBookingStore', {state:()=>({})})",
    "src/logic/utils/index.js": "export { pad } from './numbers.js'",
    "src/logic/utils/numbers.js": "export function pad(n){ return String(n).padStart(2,'0') }",
    "src/logic/constants/index.js": "export const WEEK_DAYS = ['пн']",
    "src/logic/logger/index.js": "export function logInfo(){ }",
    "src/logic/utils/numbers.test.js": "import { pad } from './numbers.js'\ntest('pad', ()=>{})",
}


@pytest.fixture
def good_project(tmp_path: Path) -> Path:
    for rel, text in GOOD_FRONT.items():
        target = tmp_path / "project" / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path / "project"


def test_отсутствие_фронтенда_мягко(tmp_path: Path):
    report = scan_frontend(tmp_path / "empty")
    assert report["present"] is False
    assert report["files"] == []


def test_эталонный_фронтенд_без_нарушений(good_project: Path):
    report = scan_frontend(good_project)
    assert report["present"] is True
    assert report["stats"]["files"] > 20
    stacks = {item["dep"]: item["status"] for item in report["stack"]}
    assert all(value == "ok" for value in stacks.values())
    dag_errors = [v for v in report["violations"] if v["severity"] == "error"]
    assert dag_errors == []


def test_ui_не_ходит_в_api_напрямую(tmp_path: Path):
    root = tmp_path / "project"
    for rel, text in GOOD_FRONT.items():
        target = root / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    page = root / "frontend/src/ui/pages/broken/index.vue"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("<script>\nimport client from '../../../logic/api/client.js'\nexport default {}\n</script>", encoding="utf-8")
    report = scan_frontend(root)
    codes = [violation["code"] for violation in report["violations"] if violation["severity"] == "error"]
    assert "dag.ui_api" in codes


def test_store_без_сети(tmp_path: Path):
    root = tmp_path / "project"
    for rel, text in GOOD_FRONT.items():
        target = root / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    store = root / "frontend/src/logic/store/bad.js"
    store.write_text("import { defineStore } from 'pinia'\nexport const useBadStore = defineStore('useBadStore',{state:()=>({}),actions:{load(){return fetch('/x')}}})", encoding="utf-8")
    report = scan_frontend(root)
    codes = [violation["code"] for violation in report["violations"] if violation["severity"] == "error"]
    # store импортирует только pinia — DAG не нарушен, но сеть внутри store запрещена
    assert "store_side_effects" in codes
    assert "dag.store_logic" not in codes


def test_hex_вне_tokens(tmp_path: Path):
    root = tmp_path / "project"
    for rel, text in GOOD_FRONT.items():
        target = root / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    comp = root / "frontend/src/ui/components/Badge.vue"
    comp.write_text("<template><b/></template>\n<style scoped>\n.badge{color:#ff8899;background:var(--color-primary);border:1px solid url(#mark)}\n</style>", encoding="utf-8")
    report = scan_frontend(root)
    hex_codes = [v for v in report["violations"] if v["code"] == "hex_outside_tokens"]
    assert len(hex_codes) == 1  # url(#mark) не считается цветом
    assert "один hex" not in hex_codes[0]["message"] or hex_codes[0]["line"] == 1  # первый встреченный файл


def test_нейминг_компонентов_и_страниц(tmp_path: Path):
    root = tmp_path / "project"
    for rel, text in GOOD_FRONT.items():
        target = root / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    low = root / "frontend/src/ui/components/lower.vue"
    low.write_text("<template/>", encoding="utf-8")
    page = root / "frontend/src/ui/pages/badFolder/index.vue"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("<template/>", encoding="utf-8")
    report = scan_frontend(root)
    codes = {v["code"] for v in report["violations"]}
    assert "naming_component" in codes
    assert "naming_page" in codes


def test_client_не_знает_логики(tmp_path: Path):
    root = tmp_path / "project"
    for rel, text in GOOD_FRONT.items():
        target = root / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    client = root / "frontend/src/logic/api/client.js"
    client.write_text("import { WEEK_DAYS } from '../constants/index.js'\nexport const client = { get(){} }", encoding="utf-8")
    report = scan_frontend(root)
    codes = [v["code"] for v in report["violations"] if v["severity"] == "error"]
    assert "dag.client_deps" in codes


def test_версии_стека__ignore(tmp_path: Path):
    root = tmp_path / "project"
    for rel, text in GOOD_FRONT.items():
        target = root / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    pkg = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    pkg["dependencies"]["pinia"] = "^3.2.0"
    (root / "frontend/package.json").write_text(json.dumps(pkg), encoding="utf-8")
    report = scan_frontend(root)
    pinia = next(item for item in report["stack"] if item["dep"] == "pinia")
    assert pinia["status"] == "mismatch"
    codes = {v["code"] for v in report["violations"]}
    assert "stack_mismatch" in codes


def test_ui_вкладка_фронтенд():
    from tests.test_ui import HTML, JS_DIR
    js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(JS_DIR.glob("*.js"))
    )
    assert 'data-tab="frontend"' in HTML
    assert 'id="tab-frontend"' in HTML
    assert 'id="tab-frontend-button"' in HTML
    assert 'aria-labelledby="tab-frontend-button"' in HTML
    assert 'id="tab-frontend"' in HTML
    assert "renderFrontend" in js
    assert "/api/frontend" in js
    assert "frontendview.js" in "\n".join(str(path.name) for path in JS_DIR.glob("*.js"))

