"""Вызовы эндпоинтов, найденные в коде фронтенда: путь, цепочка, поля composable, интеграция."""

from __future__ import annotations

from pathlib import Path

from studio.frontend import infer
from studio.frontend.brief import _found_line
from studio.frontend.graph import build_front_graph
from studio.frontend.links import check_links
from studio.frontend.verify import verify_front_task
from studio.metamodel import Node

FILES = {
    "src/logic/api/endpoints.js": """import { api } from './http.js'
export const fetchLessons = (p) => api.get(`/lessons${toQuery(p)}`)
export const fetchLesson = (id) => api.get(`/lessons/${id}`)
export const postLesson = (b) => api.post('/lessons', b)
export const removeLesson = (id, force) => api.delete(`/lessons/${id}${force ? '?force=true' : ''}`)
export const fetchGhost = () => api.get("/ghost")
""",
    "src/logic/actions/lessons.js": """import { fetchLessons, postLesson, removeLesson } from '../api'
export async function loadLessons(p) { return fetchLessons(p) }
export async function createLesson(b) { await postLesson(b); return loadLessons() }
export async function deleteLesson(id) { return removeLesson(id) }
""",
    "src/ui/composables/useLessons.js": """import { loadLessons, createLesson, deleteLesson } from '@/logic/actions'
export function useLessons() {
  const load = () => loadLessons()
  return {
    items: [],
    load,
    add: (body) => createLesson(body),
    remove: (id) => deleteLesson(id),
  }
}
""",
    "src/ui/blocks/LessonList.vue": """<template><ul/></template>
<script setup>
import { useLessons } from '@/ui/composables'
const { load } = useLessons()
</script>
""",
    "src/ui/blocks/LessonForm.vue": """<template><form/></template>
<script setup>
import { useLessons } from '@/ui/composables'
const lessons = useLessons()
const save = (body) => lessons.add(body)
</script>
""",
    "src/ui/pages/lessons/index.vue": """<template><LessonList/><LessonForm/></template>
<script setup>
import LessonList from '@/ui/blocks/LessonList.vue'
import LessonForm from '@/ui/blocks/LessonForm.vue'
import { useLessons } from '@/ui/composables'
const { remove } = useLessons()
</script>
""",
    "src/ui/pages/about/index.vue": "<template><p/></template>\n",
    "src/ui/composables/useLessons.test.js": "import { api } from 'x'\napi.get('/not-counted')\n",
}


def _project(tmp_path: Path) -> Path:
    front = tmp_path / "frontend"
    for rel, text in FILES.items():
        target = front / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------ разбор


def test_путь_запроса_подстановки_и_строка_запроса() -> None:
    text = """
    api.get(`/a/${id}/b${toQuery(p)}`)
    http.post('/x?y=1', body)
    client.delete("/z/")
    api.get(`/m/${id}${force ? '?force=true' : ''}`)
    api.get(url)
    api.get('relative/path')
    cache.get('/same')
    """
    assert infer.direct_calls(text) == [
        "GET /a/{param}/b", "POST /x", "DELETE /z", "GET /m/{param}", "GET /same"]


def test_импорты_с_переименованием() -> None:
    text = "import a, { b, c as d } from './x.js'\nimport * as all from 'y'\nimport 'z.css'\n"
    assert infer.imported_names(text) == {"b", "c"}


def test_поля_composable_делятся_на_создание_и_возвращаемое() -> None:
    body = infer._symbols(FILES["src/ui/composables/useLessons.js"])["useLessons"]
    setup, members = infer.split_members(body)
    assert set(members) == {"items", "load", "add", "remove"}
    assert "loadLessons" in members["load"] and "createLesson" in members["add"]
    assert "createLesson" not in setup and "return" not in setup
    assert infer.split_members("export const x = 1") is None
    assert infer.split_members("return { a: 1") is None  # скобки не закрыты
    spread = infer.split_members("function f() { const q = 1; return { ...other(), q } }")
    assert "other" in spread[0] and set(spread[1]) == {"q"}


def test_цепочка_блок_composable_действие_api(tmp_path: Path) -> None:
    root = _project(tmp_path)
    found = infer.infer_calls(root / "frontend", sorted(FILES))
    # блок берёт только то поле composable, которое использует
    assert found["src/ui/blocks/LessonList.vue"] == ["GET /lessons"]
    assert found["src/ui/blocks/LessonForm.vue"] == ["GET /lessons", "POST /lessons"]
    assert found["src/ui/pages/lessons/index.vue"] == ["DELETE /lessons/{param}"]
    assert "src/ui/pages/about/index.vue" not in found
    assert "GET /not-counted" not in sum(found.values(), [])  # тесты не считаются
    assert infer.infer_calls(root / "frontend", ["README.md", "src/x.css"]) == {}
    assert infer.infer_calls(root / "frontend", ["src/ui/нет.vue"]) == {}


# ------------------------------------------------------------- интеграция


def test_граф_ТЗ_сверка_и_verify(tmp_path: Path) -> None:
    root = _project(tmp_path)
    graph = build_front_graph(root)
    page = next(p for p in graph["pages"] if p["name"] == "lessons")
    blocks = {b["name"]: b for b in page["blocks"]}
    assert page["inferred_calls"] == ["DELETE /lessons/{param}"]
    assert blocks["LessonForm"]["inferred_calls"] == ["GET /lessons", "POST /lessons"]
    assert next(p for p in graph["pages"] if p["name"] == "about")["inferred_calls"] == []

    def endpoint(method: str, path: str) -> Node:
        return Node(id=f"e:{method}{path}", kind="endpoint", label=path, file_path="a.py",
                    details={"http_method": method, "url_path": path})

    backend = type("G", (), {"nodes": [endpoint("GET", "/lessons"), endpoint("POST", "/lessons")]})
    blocks["LessonForm"]["calls"] = ["POST /lessons"]  # объявлено: в «в коде» не дублируется
    links = check_links(graph, backend, "")
    seen = {(r["name"], r["call"]): r["status"] for r in links["in_code"]}
    assert ("LessonForm", "POST /lessons") not in seen
    assert seen[("LessonForm", "GET /lessons")] == "ok"
    assert seen[("lessons", "DELETE /lessons/{param}")] == "missing"
    assert links["in_code_missing"] == 1 and links["missing"] == 0 and links["rows"][0]["call"]

    assert _found_line(blocks["LessonForm"], "- ").endswith("GET /lessons")
    assert _found_line(blocks["LessonList"]).endswith("GET /lessons")
    assert _found_line({"calls": ["GET /lessons"], "inferred_calls": ["GET /lessons"]}) == ""

    front = {"pages": [page]}
    task = {"kind": "block", "id": "x", "name": "LessonForm", "page_id": page["id"],
            "calls": ["POST /lessons", "PUT /lessons/{param}"]}
    result = verify_front_task(task, front)
    assert result["status"] == "выполнено"
    assert "PUT /lessons/{param} в коде не найден" in result["message"]
    assert "POST /lessons в коде" not in result["message"]
    quiet = verify_front_task({**task, "calls": ["POST /lessons"]}, front)
    assert quiet["message"] == "блок есть на странице"
    page_task = {"kind": "page", "name": "lessons", "calls": ["GET /nope"]}
    assert "GET /nope в коде не найден" in verify_front_task(page_task, front)["message"]
