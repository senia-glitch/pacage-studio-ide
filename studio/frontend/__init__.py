"""Сканер фронтенда: структура `frontend/`, правила слоёв и UI-стандарта.

Фронтенд проекта — Vue 3 + Pinia + Router + Vite с жёстким DAG слоёв:

    ui/pages, ui/blocks, ui/components → ui/composables → logic/actions
        → logic/api, logic/store, logic/utils, logic/constants

Как и сканер бэкенда, модуль только читает код и ничего не меняет.
Отчёт: структура по слоям, связи импортов, нарушения правил с файлом:строкой.
"""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from pathlib import Path

FRONTEND_DIR = "frontend"

# Эталонные каталоги: ключ слоя → путь в frontend/.
LAYER_PATHS = {
    "ui/pages": "src/ui/pages",
    "ui/blocks": "src/ui/blocks",
    "ui/components": "src/ui/components",
    "ui/composables": "src/ui/composables",
    "ui/app": "src/ui/app",
    "logic/actions": "src/logic/actions",
    "logic/api": "src/logic/api",
    "logic/store": "src/logic/store",
    "logic/utils": "src/logic/utils",
    "logic/constants": "src/logic/constants",
    "logic/logger": "src/logic/logger",
}

# Слои, где обязателен index.js — публичный контракт.
INDEX_LAYERS = {
    "ui/components",
    "ui/composables",
    "ui/app",
    "logic/actions",
    "logic/api",
    "logic/store",
    "logic/utils",
    "logic/constants",
}

SKIP_DIRS = {"node_modules", "dist", ".git", "coverage", ".vite"}

# Ожидаемый стек (мажоры по стандарту пакета).
STACK_WANTED = {
    "vue": "3",
    "vue-router": "4",
    "pinia": "4",
    "vite": "8",
    "vitest": "3",
}

SOURCE_SUFFIXES = {".js", ".mjs", ".vue"}
HEX_RE = re.compile(r"(?<![\w#])(#[0-9a-fA-F]{3,8})\b")
URL_HEX_RE = re.compile(r"url\(\s*['\"]?#[^)]*\)")
STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
IMPORT_RE = re.compile(r"^.*?\bfrom\s+[\"']([^\"']+)[\"']", re.M)
FULL_IMPORT_RE = re.compile(r"^import\s+[\"']([^\"']+)[\"']", re.M)
DYNAMIC_IMPORT_RE = re.compile(r"import\(\s*[\"']([^\"']+)[\"']\s*\)")
KEBAB_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# Следующие за parent → child рёбра графа, которыми карта и subtree строятся.
FLOW_KINDS = ("dispatch", "scenario_call", "port_call", "table", "uses")


def _classify(path: str) -> str | None:
    """Ключ слоя по пути внутри frontend/; None — вне эталонных слоёв."""
    for key, layer_path in LAYER_PATHS.items():
        if path.startswith(layer_path + "/"):
            return key
    if path == "src/main.js" or path == "src/ui/App.vue":
        return "bootstrap"
    if path.startswith("src/ui/styles/"):
        return "ui/styles"
    return None


def _walk(front: Path) -> list[Path]:
    """Все файлы frontend/ без node_modules/dist/.git/coverage."""
    files: list[Path] = []
    queue: deque[Path] = deque([front])
    while queue:
        current = queue.popleft()
        for child in sorted(current.iterdir()):
            if child.is_dir():
                if child.name not in SKIP_DIRS:
                    queue.append(child)
            else:
                files.append(child)
    return files


def _vue_style_blocks(text: str) -> str:
    return "\n".join(match.group(1) for match in STYLE_BLOCK_RE.finditer(text))


def _hex_in_css(text: str) -> tuple[int, int | None]:
    """Число hex-литералов цветов и строка первого вхождения (CSS-текст)."""
    found = 0
    first: int | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        cleaned = URL_HEX_RE.sub("", line)
        if HEX_RE.search(cleaned):
            found += 1
            if first is None:
                first = number
    return found, first


def _collect_imports(text: str) -> list[str]:
    targets: list[str] = []
    for match in IMPORT_RE.finditer(text):
        targets.append(match.group(1))
    for match in FULL_IMPORT_RE.finditer(text):
        targets.append(match.group(1))
    for match in DYNAMIC_IMPORT_RE.finditer(text):
        targets.append(match.group(1))
    return targets


def resolve_import(rel: str, target: str, internal_paths: set[str]) -> str | None:
    """Относительный или @-импорт → внутренний src-путь; иначе None (внешний пакет)."""
    if target.startswith("@/"):
        candidate = "src/" + target[2:]
        if candidate in internal_paths or candidate in _without_suffixes(internal_paths):
            return candidate if candidate in internal_paths else None
        return None
    if not target.startswith("."):
        return None
    folder = rel.rsplit("/", 1)[0]
    parts: list[str] = []
    for part in (folder + "/" + target).split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    candidate = "/".join(parts)
    if candidate in internal_paths:
        return candidate
    without = re.sub(r"\.(js|mjs|vue|css)$", "", candidate)
    for path in internal_paths:
        if re.sub(r"\.(js|mjs|vue|css)$", "", path) == without:
            return path
    return None


def _without_suffixes(paths: set[str]) -> set[str]:
    return {re.sub(r"\.(js|mjs|vue|css)$", "", path) for path in paths}


def is_source(name: str) -> bool:
    return name.endswith(tuple(SOURCE_SUFFIXES))


def scan_frontend(project_root: str | Path) -> dict:
    """Отчёт по frontend/ проекта: структура, DAG, правила, нарушения."""
    root = Path(project_root)
    front = root / FRONTEND_DIR
    violations: list[dict] = []
    edges_counter: Counter[tuple[str, str]] = Counter()
    edge_examples: dict[tuple[str, str], list[str]] = {}

    def warn(code: str, file_path: str, line: int, message: str) -> None:
        violations.append({
            "code": code, "severity": "warning",
            "file": file_path, "line": line, "message": message,
        })

    def error(code: str, file_path: str, line: int, message: str) -> None:
        violations.append({
            "code": code, "severity": "error",
            "file": file_path, "line": line, "message": message,
        })

    rel_files: list[str] = []
    if front.is_dir():
        rel_files = sorted(path.relative_to(front).as_posix() for path in _walk(front))

    report: dict = {
        "present": front.is_dir(),
        "root": FRONTEND_DIR,
        "files": rel_files,
        "layers": [],
        "edges": [],
        "violations": violations,
        "package": None,
        "stack": [],
        "routes": {},
        "pagesGraph": [],
        "stats": {
            "files": len(rel_files),
            "pages": 0,
            "components": 0,
            "stores": 0,
            "actions": 0,
            "utils": 0,
            "tests": 0,
            "errors": 0,
            "warnings": 0,
        },
    }
    if not front.is_dir():
        return report

    # --- package.json и стек ---
    package: dict | None = None
    if "package.json" in rel_files:
        try:
            package = json.loads((front / "package.json").read_text(encoding="utf-8"))
            report["package"] = package
        except (json.JSONDecodeError, OSError):
            warn("package_unreadable", "package.json", 1, "package.json не читается")
    deps: dict[str, str] = {}
    if isinstance(package, dict):
        deps.update(package.get("dependencies") or {})
        deps.update(package.get("devDependencies") or {})
    for name, wanted in STACK_WANTED.items():
        matched = next(
            (
                key
                for key in deps
                if key == name or key.startswith(name + "@") or key.startswith(name + "-")
            ),
            "",
        )
        got = deps[matched] if matched else ""
        status = check_stack_version(got, wanted)
        report["stack"].append(
            {"dep": name, "want": wanted + ".x", "got": got or "", "status": status}
        )
        if got and status != "ok":
            warn(
                "stack_mismatch",
                "package.json",
                1,
                f"{name}: ожидается {wanted}.x, фактически {got}",
            )
    if not isinstance(package, dict):
        warn(
            "package_missing",
            "package.json",
            1,
            "frontend/package.json не найден — ровно один package.json в frontend/",
        )

    # --- эталонные прочие файлы ---
    routes_map: dict = {}
    router_rel = next((rel for rel in rel_files if rel.endswith("src/ui/app/router.js")), "")
    if router_rel:
        try:
            routes_map = parse_routes((front / router_rel).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            routes_map = {}
    report["routes"] = routes_map
    page_facts: dict[str, dict] = {}
    REQUIRED = {
        "index.html": "Точка входа index.html",
        "vite.config.js": "vite.config.js — конфигурация Vite",
        "public/favicon.svg": "public/favicon.svg",
        "src/main.js": "src/main.js — только bootstrap, без бизнес-логики",
        "src/ui/App.vue": "src/ui/App.vue — корень интерфейса",
        "src/ui/styles/tokens.css": "src/ui/styles/tokens.css — единственный источник цветов",
        "src/ui/styles/base.css": "src/ui/styles/base.css — общие классы UI",
    }
    for required, message in REQUIRED.items():
        if required not in rel_files:
            warn("structure_missing", required, 1, f"{message} не найден")

    # --- слои и index.js-контракты ---
    layer_infos: list[dict] = []
    for key, layer_path in sorted(LAYER_PATHS.items()):
        layer_files = [rel for rel in rel_files if rel.startswith(layer_path + "/")]
        info = {
            "key": key,
            "path": layer_path,
            "exists": bool(layer_files),
            "index": (layer_path + "/index.js") in layer_files,
            "fileCount": len(layer_files),
        }
        if not info["exists"]:
            warn("layer_missing", layer_path + "/", 1, f"Слой {key} не заведён — папки нет")
        elif key in INDEX_LAYERS and not info["index"]:
            warn(
                "index_missing",
                layer_path + "/",
                1,
                f"Слой {key}: нет index.js — публичный контракт",
            )
        layer_infos.append(info)
    report["layers"] = layer_infos

    # --- прохождение исходников ---
    internal_paths = {rel for rel in rel_files if rel.startswith("src/")}
    stats = report["stats"]
    for rel in rel_files:
        layer = _classify(rel)
        if layer == "ui/pages" and rel.endswith("/index.vue"):
            stats["pages"] += 1
        if layer == "ui/components" and rel.endswith(".vue"):
            stats["components"] += 1
        if layer == "logic/store" and rel.endswith(".js"):
            stats["stores"] += 1
        if layer == "logic/actions" and rel.endswith(".js"):
            stats["actions"] += 1
        if layer == "logic/utils" and rel.endswith(".js"):
            stats["utils"] += 1
        if rel.endswith(".test.js") or "/__tests__/" in rel:
            stats["tests"] += 1
        if not is_source(rel):
            continue
        path = front / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        check_naming_and_styles(rel, layer, text, warn, error)
        # Фактические блоки страницы: компоненты по именам привязок, реально упомянутые в тексте.
        if layer == "ui/pages" and rel.endswith("/index.vue"):
            folder = rel[len("src/ui/pages/") :].split("/", 1)[0]
            names: list[str] = []
            for statement in re.finditer(r"^import\s+([^;]+?)\s*(?:$|from\s+)", text, re.M):
                head = statement.group(1)
                if "{" in head:
                    for item in re.findall(r"(\w+)(?:\s+as\s+\w+)?", head.split("{", 1)[1]):
                        names.append(item)
                else:
                    standalone = re.match(r"import\s+(\w+)", statement.group(0))
                    if standalone:
                        names.append(standalone.group(1))
            block_fact: list[dict] = []
            for name in names:
                stem = name
                candidates = [
                    path
                    for path in internal_paths
                    if (
                        path.startswith("src/ui/components/")
                        or path.startswith("src/ui/blocks/")
                    )
                    and path.endswith((".vue", ".js"))
                    and stem.lower() == path.rsplit("/", 1)[-1][: -len(".vue")].lower()
                ]
                if not candidates:
                    continue
                if not re.search(rf"<\s*{stem}\b", text):
                    continue
                block_fact.append({"name": stem, "file": candidates[0]})
            page_facts[folder] = {
                "folder": folder,
                "file": rel,
                "route": routes_map.get(folder, ""),
                "components": block_fact,
            }
        targets = _collect_imports(text)
        seen: set[str] = set()
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            resolved = resolve_import(rel, target, internal_paths)
            if resolved is None:
                continue
            to_layer = _classify(resolved)
            if to_layer is None:
                continue
            key = (layer or "other", to_layer)
            edges_counter[key] += 1
            examples = edge_examples.setdefault(key, [])
            if rel not in examples:
                examples.append(rel)
            check_dag(rel, layer, to_layer, resolved, error)

    # --- тесты ---
    if not stats["tests"]:
        warn("tests_missing", "src", 1, "Тестов нет — *.test.js рядом с кодом (Vitest)")

    report["edges"] = [
        {
            "from": pair[0],
            "to": pair[1],
            "count": count,
            "example": edge_examples[pair][0],
        }
        for pair, count in DiffSort(edges_counter)
    ]
    report["pagesGraph"] = [page_facts[folder] for folder in sorted(page_facts)]
    for violation in violations:
        bucket = "errors" if violation["severity"] == "error" else "warnings"
        stats[bucket] += 1
    return report


def DiffSort(counter: Counter) -> list:
    """Пары счётчика упорядоченно: по убыванию числа переходов, потом алфавит."""
    return sorted(counter.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))


def check_stack_version(got: str, wanted: str) -> str:
    if not got:
        return "missing"
    match = re.match(r"[^0-9]*(\d+)(?:\.(\d+))?", str(got))
    if not match:
        return "mismatch"
    got_major = int(match.group(1))
    wanted_major = int(wanted)
    return "ok" if got_major >= wanted_major else "mismatch"


def parse_routes(text: str) -> dict:
    """Маршруты из src/ui/app/router.js: name → path (read-only факты)."""
    routes: dict
    routes = {}
    for match in re.finditer(r"\{[^{}]*?\}", text):
        block = match.group(0)
        name = re.search(r"name\s*:\s*[\"']([^\"']+)[\"']", block)
        path = re.search(r"path\s*:\s*[\"']([^\"']+)[\"']", block)
        if name:
            routes[name.group(1)] = path.group(1) if path else ""
    return routes


def scan_page_folder(rel: str, front: Path) -> dict:
    """Факт-данные страницы: файлы папки, компоненты, плановые хинты."""
    folder = rel[len("src/ui/pages/") :]
    folder = folder.split("/", 1)[0]
    return {"folder": folder, "file": rel}


def check_naming_and_styles(
    rel: str,
    layer: str | None,
    text: str,
    warn,
    error,
) -> None:
    """Нейминг файлов и hex-цвета вне tokens.css."""
    if (rel.endswith(".vue") or rel.endswith(".css")) and rel != "src/ui/styles/tokens.css":
        css = _vue_style_blocks(text) if rel.endswith(".vue") else text
        count, first = _hex_in_css(css)
        if count:
            error(
                "hex_outside_tokens",
                rel,
                first or 1,
                f"Hex-цвет ×{count} вне tokens.css — цвет берётся var(--*) из tokens",
            )
    if layer == "ui/components" and rel.endswith(".vue"):
        stem = rel.rsplit("/", 1)[-1][: -len(".vue")]
        if stem and not stem[0].isupper():
            warn("naming_component", rel, 1, f"Компонент `{stem}.vue` не в PascalCase")
    if layer == "ui/pages" and rel.endswith("/index.vue"):
        folder = rel.split("/")[3] if rel.count("/") >= 4 else ""
        if folder and not KEBAB_RE.fullmatch(folder):
            warn("naming_page", rel, 1, f"Папка страницы `{folder}` не в kebab-case")
    store_js = layer == "logic/store" and rel.endswith(".js")
    store_named_bad = store_js and "defineStore(" in text and not re.search(
        r"defineStore\(\s*[\"']use[A-Z]", text
    )
    if store_named_bad:
        warn("naming_store", rel, 1, f"Стор `{rel}`: сторы называются use<Name>Store")
    if store_js and _has_store_side_effects(text):
        error(
            "store_side_effects",
            rel,
            1,
            "В store обнаружены сеть/DOM — только состояние и синхронные мутации",
        )


def _has_store_side_effects(text: str) -> bool:
    """Сеть/DOM внутри store — нарушение «store = только state»."""
    return bool(re.search(r"(?<![\w$])(?:fetch\(|window\.|document\.)", text))


def check_dag(
    rel: str,
    from_layer: str | None,
    to_layer: str | None,
    resolved: str,
    error,
) -> None:
    """Жёсткие правила зависимостей DAG. При нарушении — ошибка."""
    if from_layer is None or to_layer is None or from_layer == to_layer:
        return
    if rel == "src/logic/api/client.js":
        external = resolved.startswith("src/") and to_layer != "logic/api"
        if external:
            # перенаправления внутри api — разрешены (транспорт client_used)
            error(
                "dag.client_deps",
                rel,
                1,
                "client.js знает только fetch — связи снаружи у transports",
            )
        return
    if from_layer.startswith("ui") and to_layer == "logic/api":
        error(
            "dag.ui_api",
            rel,
            1,
            "ui/* не ходит в logic/api напрямую — сценарий в logic/actions",
        )
        return
    crosses = to_layer.startswith("logic/") or to_layer.startswith("ui/")
    if from_layer == "logic/store" and crosses:
        error(
            "dag.store_logic",
            rel,
            1,
            "logic/store/* импортирует только pinia — состояние + синхронные мутации",
        )
        return
    if from_layer == "logic/utils" and to_layer in (
        "logic/actions",
        "logic/api",
        "logic/store",
        "logic/logger",
        "ui/app",
    ):
        error(
            "dag.utils_deps",
            rel,
            1,
            "logic/utils/* — чистые функции: только logic/constants и другие utils",
        )
        return
    if from_layer == "logic/actions" and to_layer.startswith("ui/"):
        error(
            "dag.actions_ui",
            rel,
            1,
            "logic/actions/* не тянут интерфейс — сценарии без DOM",
        )
        return
    if from_layer == "logic/constants" and to_layer.startswith("logic/"):
        error(
            "dag.constants_deps",
            rel,
            1,
            "logic/constants/* — чистые данные (SCREAMING_SNAKE) без логики",
        )
        return


def _has_hex(text: str) -> bool:
    return bool(_hex_in_css(text)[0])


__all__ = ["FRONTEND_DIR", "scan_frontend"]
