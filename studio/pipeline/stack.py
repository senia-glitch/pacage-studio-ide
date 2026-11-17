"""Профиль стека: студия заточена под него, а не под «любой стек».

Бэкенд: ``fastbase`` (HTTP) → ``core-package`` (сценарии) → ``event-infra`` (PostgreSQL).
Фронтенд: Vue 3, Vite, Pinia, Vue Router, тесты Vitest, жёсткий DAG слоёв ``logic/`` и ``ui/``.
Правила взяты из документации самого стека (``docs/`` проекта после ``fb init``) и из сканеров
студии, которые проверяют то же самое.

Здесь одно место для трёх вещей:

* текст правил — он идёт в «Конституцию», в подсказки ролей и в ТЗ исполнителя;
* каркас кодом, без агента: ``models.py`` из схемы БД, сценарий и роут на каждый эндпоинт,
  каталог ``frontend/`` с обязательными файлами;
* подготовка проекта: генераторы самого стека (``fb init``, ``infra-init``, ``core-init``).

Проверки того, что получилось, — в ``stackcheck.py`` (сканеры студии).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from studio.agent import projectenv
from studio.env import DEFAULT_API_PREFIX, read_env

from .errors import PipelineError

STACK_LINE = (
    "Windows; Python 3.12 или новее; стек fastbase, core-package, event-infra; интерфейс на Vue"
)
INSTALL_HINT = (
    'pip install "fastbase[full] @ git+https://github.com/senia-glitch/fastapi-transport.git" '
    "(или studio install-full)"
)

BACKEND_GUIDE = """\
СТЕК БЭКЕНДА: fastbase + core-package + event-infra (документация стека — в docs/ проекта).
Поток: HTTP → роут (fastbase) → run("сценарий", dto) → сценарий (core-package) → self._db → \
event-infra → PostgreSQL.
1. models.py в корне — единственный источник схемы БД: SQLModel-классы с __tablename__ и \
первичным ключом. Миграции создаются сами при запуске, alembic руками не трогай.
2. Роут app/api/v1/routes/<домен>.py: router = APIRouter(tags=["<домен>"]); путь эндпоинта в \
декораторе целиком, без префикса API (@router.post("/appointments/{id}/cancel")). Функция роута \
только собирает DTO и возвращает await run("имя", dto). Логики в роуте нет.
3. Сценарий app/scenarios/<домен>/<имя>.py: DTO (наследник BaseDTO), модель ответа (pydantic), \
класс-наследник BaseScenario с @register_scenario("имя") и @tracked_scenario("имя") и \
async def execute(self, dto). Имя в run(...) — строковый литерал, совпадающий с регистрацией. \
Сценарий не знает про HTTP. core обходит только пакеты первого уровня, поэтому каждый сценарий \
подключён строкой from app.scenarios.<домен> import <имя> в app/scenarios/<домен>/__init__.py.
4. БД — только через self._db: create/read/update/delete(таблица, ...) и query/custom(SQL, \
параметры). Чтение идёт в read-канал; изменения — только create/update/delete или \
query(..., channel="write"). Таблицы и колонки — ровно как в models.py. Многошаговые операции \
не атомарны: при частичном сбое откатывай вручную (удали созданное).
5. Ошибки — исключения core: NotFoundError, ValidationError, ConflictError. fastbase сам \
превращает их в единый конверт {"success": false, "error": {...}}. HTTPException в сценариях не \
используй.
6. Чистая логика без ввода-вывода — в app/utils/*.py (и тесты на неё отдельно).
7. Эндпоинт принимает и отдаёт ровно то, что в контракте: метод, путь (без префикса API — его \
добавляет fastbase), запрос, ответ.
8. Нельзя: логика в роутах; прямая работа с БД мимо self._db; мутации через query без \
channel="write"; свои числовые коды ошибок в коде (они только в FAT_ERROR_CODES); правки \
app/main.py и alembic/.
9. Каркас сценария и роута уже создан студией: допиши execute, структуру файлов не меняй."""

FRONTEND_GUIDE = """\
СТЕК ФРОНТЕНДА: Vue 3, Vite, Pinia, Vue Router, тесты Vitest. Работай только в frontend/.
Две ветки: logic/ (без интерфейса) и ui/. Зависимость строго ui → logic, обратно нельзя.
src/main.js — только bootstrap. logic/: api (один эндпоинт — одна функция, не знает про store), \
actions (один пользовательский сценарий — одна функция: зовёт api, пишет в store, ошибку \
возвращает значением), store (Pinia: только состояние и синхронные мутации, \
defineStore('use<Имя>Store'), без сети и DOM), utils (чистые функции), constants (справочники \
SCREAMING_SNAKE). ui/: App.vue (только <router-view/>), app/ (router.js и guard), \
pages/<kebab-case>/index.vue, blocks/ (составные блоки без импорта logic: данные через props и \
emits), composables/ (use*.js), components/ (PascalCase, UI-кит без бизнеса), styles/ \
(tokens.css — единственный источник цветов, base.css — общие классы).
Зависимости только сверху вниз: pages → blocks, composables, components, logic; composables → \
components, logic/actions; actions → api, store, utils, constants; api → utils, constants. \
ui никогда не ходит в logic/api напрямую (только через actions); logic не импортирует ui.
Каждая папка-слой (кроме pages/<страница>/) имеет index.js — публичный контракт; снаружи папки \
импорт только через него.
Цвета — только var(--*) из tokens.css, hex вне tokens.css запрещён. Один .vue = разметка + \
scoped-стили + UI-логика, на части не рвать. Страницы и блоки не делают сетевых вызовов.
До появления бэкенда данные — из моков по контракту в frontend/src/logic/api/mocks/; когда бэкенд \
готов, моки заменяются реальными вызовами (шаг «Замена моков»). Путь вызова — без префикса API.
Тесты: *.test.js (Vitest); тесты logic не зависят от Vue и DOM. Каркас frontend/ уже создан \
студией: добавляй файлы по слоям и не ломай index.js."""

BACKEND_TEST_GUIDE = (
    "Тесты бэкенда — pytest, файлы tests/pipeline/test_<имя>.py. Корень проекта уже в PYTHONPATH: "
    "импортируй app.* и core напрямую. Сценарий проверяй без сервера: создай экземпляр "
    "Сценарий(db=Stub) со stub-БД (маленький класс с async create/read/update/delete/query, "
    "хранящий данные в списках) и выполни asyncio.run(сценарий.execute(DTO(...))). Ошибки — "
    "pytest.raises(NotFoundError/ValidationError/ConflictError из core). Базу данных тест не "
    "поднимает."
)
FRONTEND_TEST_GUIDE = (
    "Тесты интерфейса — Vitest на JavaScript (ES-модули), файлы "
    "frontend/tests/pipeline/<имя>.test.js. Импортируй код только через index.js слоя "
    "(../../src/logic/actions/index.js и т.п.). Тесты logic не требуют Vue и DOM; сеть подменяй "
    "через vi.mock или vi.stubGlobal('fetch', ...)."
)
E2E_TEST_GUIDE = (
    "Сквозные тесты — pytest, файлы tests/pipeline/e2e/test_<имя>.py: настоящие HTTP-запросы к "
    "запущенному API (адрес из FAT_HOST/FAT_PORT, по умолчанию http://127.0.0.1:8000, префикс "
    "/api/v1), проверка конверта ответа и ошибок."
)

# Что должно быть в проекте после генераторов стека.
STACK_FILES = (".env.fastbase", ".infra.env", "models.py", "app/main.py")
GENERATORS = (
    ("fb", ["init"]),
    ("infra-init", ["-y"]),  # без -y он спрашивает подтверждение и без ответа отменяется
    ("core-init", ["--target-dir", "app"]),
)

_PARAM_RE = re.compile(r"\{([^}/]+)\}|<([^>/]+)>|:([A-Za-z_]\w*)")
SQL_TYPES = {
    "int": "int", "text": "str", "bool": "bool", "float": "float", "date": "date",
    "datetime": "datetime", "json": "dict", "uuid": "UUID",
}  # fmt: skip


# ---------------------------------------------------------------------- имена


def slug(text: str, fallback: str = "item") -> str:
    """snake_case из любого текста: латиница, цифры и «_», не начинается с цифры."""
    out = re.sub(r"[^0-9a-zA-Z]+", "_", str(text or "")).strip("_").lower()
    if not out:
        return fallback
    return out if not out[0].isdigit() else f"n_{out}"


def pascal(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[_\W]+", name) if part) or "Item"


def api_prefix(project: Path) -> str:
    return read_env(Path(project)).api_prefix or DEFAULT_API_PREFIX


def strip_prefix(path: str, prefix: str) -> str:
    """Путь без префикса API: ``/api/v1/users`` и ``/api/users`` → ``/users``."""
    path = "/" + str(path or "").strip().split("?", 1)[0].lstrip("/")
    for cut in (prefix.rstrip("/"), "/api"):
        if cut and (path == cut or path.startswith(cut + "/")):
            return path[len(cut) :] or "/"
    return path


def normalize_params(path: str) -> str:
    """Параметры пути в виде ``{name}``: ``:id`` и ``<id>`` тоже понимаем."""
    return _PARAM_RE.sub(lambda m: "{" + (m.group(1) or m.group(2) or m.group(3)) + "}", path)


def split_path(path: str, prefix: str) -> tuple[str, str]:
    """Путь контракта → (домен, остаток пути роутера): ``/users/{id}/x`` → (users, /{id}/x)."""
    clean = normalize_params(strip_prefix(path, prefix)).rstrip("/")
    parts = [p for p in clean.split("/") if p]
    if not parts or parts[0].startswith("{"):
        return "main", clean
    return slug(parts[0], "main"), ("/" + "/".join(parts[1:])) if len(parts) > 1 else ""


def route_path(path: str, prefix: str) -> str:
    """Путь для декоратора роута: целиком, без префикса API и с параметрами ``{name}``."""
    return normalize_params(strip_prefix(path, prefix)).rstrip("/") or "/"


def path_params(path: str) -> list[str]:
    return [slug(m, "id") for m in re.findall(r"\{([^}/]+)\}", path)]


def scenario_name(method: str, path: str, prefix: str, taken: set[str], hint: str = "") -> str:
    """Имя сценария: подсказка проектировщика, иначе глагол по методу и существительное по пути."""
    base = slug(hint, "") if hint else ""
    if not base:
        domain, rest = split_path(path, prefix)
        tail = [p for p in rest.split("/") if p and not p.startswith("{")]
        has_param = "{" in rest
        method = method.upper()
        if tail:  # действие над сущностью: /users/{id}/cancel → cancel_users
            base = f"{slug(tail[-1])}_{domain}"
        elif method == "GET":
            base = f"get_{domain}" if has_param else f"list_{domain}"
        elif method == "POST":
            base = f"create_{domain}"
        elif method in ("PUT", "PATCH"):
            base = f"update_{domain}"
        elif method == "DELETE":
            base = f"delete_{domain}"
        else:
            base = f"{method.lower()}_{domain}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


# ---------------------------------------------------------------------- подготовка проекта


def missing_files(project: Path) -> list[str]:
    return [name for name in STACK_FILES if not (Path(project) / name).exists()]


def _tool(project: Path, name: str) -> str | None:
    base = projectenv.venv_dir(project)
    if base is not None:
        for folder in ("Scripts", "bin"):
            for suffix in (".exe", ""):
                candidate = base / folder / (name + suffix)
                if candidate.is_file():
                    return str(candidate)
    return shutil.which(name)


def init_stack(project: Path) -> list[str]:
    """Запустить генераторы самого стека, если проекта на стеке ещё нет; вернуть, что запускали."""
    project = Path(project)
    if not missing_files(project):
        return []
    ran = []
    # генераторы печатают дерево файлов: без UTF-8 они падают на консоли с кодовой страницей cp1251
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    projectenv.activate(env, project)
    for name, args in GENERATORS:
        exe = _tool(project, name)
        if exe is None:
            raise PipelineError(
                "stack_not_installed",
                f"Не найден {name}: сначала установите стек — {INSTALL_HINT}",
            )
        try:
            done = subprocess.run(  # noqa: S603 — генераторы самого стека, без оболочки
                [exe, *args], cwd=str(project), capture_output=True, text=True, timeout=180,
                encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=env,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PipelineError("stack_init_failed", f"{name} не запустился: {error}") from None
        if done.returncode != 0:
            tail = ((done.stdout or "") + (done.stderr or ""))[-400:].strip()
            hint = ""
            if "No module named" in tail:  # на PATH нашёлся «осиротевший» запускатель без пакета
                hint = f" Стек не установлен в окружении, которое нашла студия ({exe}). {INSTALL_HINT}"
            raise PipelineError("stack_init_failed", f"{name} вернул ошибку: {tail}{hint}")
        ran.append(" ".join([name, *args]))
    left = missing_files(project)
    if left:
        raise PipelineError(
            "stack_init_failed",
            f"Генераторы отработали, но не создали: {', '.join(left)}. Запустите их вручную в папке проекта.",
        )
    configure_env(project)
    return ran


def configure_env(project: Path) -> bool:
    """В ``.env.fastbase`` включить связку трёх пакетов и поиск сценариев (стек просит это вручную)."""
    path = Path(project) / ".env.fastbase"
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    want = {"FAT_INTEGRATIONS": "core,event-infra", "FAT_CORE_DISCOVER": "app.scenarios"}
    changed = False
    for key, value in want.items():
        pattern = re.compile(rf"^{key}=.*$", re.M)
        if pattern.search(text):
            new = pattern.sub(f"{key}={value}", text)
        else:
            new = text.rstrip("\n") + f"\n{key}={value}\n"
        changed |= new != text
        text = new
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


# ---------------------------------------------------------------------- models.py

MODELS_BEGIN = "# >>> студия: модели из схемы БД (блок перезаписывается; свои модели — вне блока)"
MODELS_END = "# <<< студия: конец блока моделей"
_EXAMPLES_RE = re.compile(
    r"# =+\n# ПРИМЕРЫ МОДЕЛЕЙ[^\n]*\n# =+\n.*?(?=# =+\n# АВТОМАТИЧЕСКАЯ РЕГИСТРАЦИЯ)", re.S
)
_MODELS_HEADER = '''\
"""SQLModel-модели: единственный источник истины о схеме БД (event-infra)."""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import JSON
from sqlmodel import SQLModel, Field

metadata = SQLModel.metadata

'''
_MODELS_FOOTER = '''

def get_all_schemas():
    """Возвращает все SQLModel-таблицы как dict {"tablename": Model}."""
    import sys

    module = sys.modules[__name__]
    schemas = {}
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and issubclass(attr, SQLModel) and attr != SQLModel and hasattr(attr, "__table__"):
            schemas[attr.__tablename__] = attr
    return schemas
'''


def _column(col: dict) -> str:
    name = slug(col.get("name"), "field")
    py = SQL_TYPES.get(col.get("type"), "str")
    key = col.get("key")
    nullable = bool(col.get("null")) or key == "pk"
    ann = f"Optional[{py}]" if nullable else py
    if key == "pk":
        field = "Field(default=None, primary_key=True)"
    elif key == "fk":
        tail = ", default=None)" if nullable else ")"
        field = f'Field(foreign_key="{col.get("ref", "")}"{tail}'
    elif col.get("type") == "json":
        field = "Field(default=None, sa_type=JSON)" if nullable else "Field(sa_type=JSON)"
    else:
        field = "Field(default=None)" if nullable else "Field()"
    return f"    {name}: {ann} = {field}"


def render_models(tables: list[dict]) -> str:
    """Классы SQLModel по схеме БД (без заголовка файла): в том виде, как их пишет сам стек."""
    out = []
    for table in tables:
        attrs = table.get("attrs") or {}
        name = attrs.get("name") or table["title"]
        out += [f"class {pascal(name)}(SQLModel, table=True):", f'    __tablename__ = "{name}"']
        cols = attrs.get("columns") or []
        out += [_column(c) for c in cols] or ["    pass"]
        out += ["", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def write_models(project: Path, tables: list[dict]) -> str:
    """Записать модели в ``models.py`` корня проекта: блок студии, остальное в файле не трогаем."""
    path = Path(project) / "models.py"
    block = f"{MODELS_BEGIN}\n{render_models(tables)}{MODELS_END}\n"
    if not path.is_file():
        path.write_text(_MODELS_HEADER + "\n" + block + _MODELS_FOOTER, encoding="utf-8")
        return "models.py"
    text = path.read_text(encoding="utf-8")
    if MODELS_BEGIN in text and MODELS_END in text:
        head = text[: text.index(MODELS_BEGIN)]
        tail = text[text.index(MODELS_END) + len(MODELS_END) :].lstrip("\n")
        new = head + block + ("\n" + tail if tail else "")
    elif _EXAMPLES_RE.search(text):  # образцы из infra-init (users, posts) спорят с нашими таблицами
        new = _EXAMPLES_RE.sub(lambda _m: block + "\n\n", text, count=1)
    elif "def get_all_schemas" in text:
        marker = text.index("def get_all_schemas")
        new = text[:marker].rstrip("\n") + "\n\n\n" + block + "\n\n" + text[marker:]
    else:
        new = text.rstrip("\n") + "\n\n\n" + block
    path.write_text(new, encoding="utf-8")
    return "models.py"


# ---------------------------------------------------------------------- сценарии и роуты


def _py_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "Optional[Any]"


def _fields(sample, skip: set[str], required: bool) -> list[str]:
    lines = []
    for key, value in sample.items() if isinstance(sample, dict) else []:
        name = slug(key, "")
        if not name or name in skip or str(key) != name:
            continue
        kind = _py_type(value)
        if required and kind != "Optional[Any]":
            lines.append(f"    {name}: {kind}")
        else:
            inner = kind if kind.startswith("Optional") else f"Optional[{kind}]"
            lines.append(f"    {name}: {inner} = None")
    return lines


def _param_type(name: str) -> str:
    return "int" if name == "id" or name.endswith("_id") else "str"


def render_scenario(item: dict) -> str:
    name, title = item["scenario"], pascal(item["scenario"])
    params = path_params(item["path"])
    body = item["method"].upper() in ("POST", "PUT", "PATCH")
    dto = [f"    {p}: Optional[{_param_type(p)}] = None" for p in params]
    dto += _fields(item.get("request"), set(params), required=body)
    response = item.get("response")
    has_model = isinstance(response, dict) and bool(response)
    lines = [
        f'"""{item.get("summary") or name}. Каркас создан студией: допишите execute по ТЗ."""',
        "",
        "from typing import Any, Optional",
        "",
        "from pydantic import BaseModel",
        "",
        "from core import BaseDTO, BaseScenario, register_scenario, tracked_scenario",
        "",
        "",
        f"class {title}DTO(BaseDTO):",
        *(dto or ["    pass"]),
        "",
        "",
    ]
    if has_model:
        fields = _fields(response, set(), True) or ["    pass"]
        lines += [f"class {title}Response(BaseModel):", *fields, "", ""]
        decorator = f'@register_scenario("{name}", response={title}Response, dto={title}DTO)'
        returns = f"{title}Response"
    else:
        decorator = f'@register_scenario("{name}", dto={title}DTO)'
        returns = "list" if isinstance(response, list) else "dict"
    lines += [
        decorator,
        f'@tracked_scenario("{name}")',
        f"class {title}Scenario(BaseScenario):",
        f"    async def execute(self, dto: {title}DTO) -> {returns}:",
        f'        raise NotImplementedError("{name}: допишите сценарий по ТЗ")',
        "",
    ]
    return "\n".join(lines)


def _route_function(item: dict) -> str:
    name, title = item["scenario"], pascal(item["scenario"])
    params = path_params(item["path"])
    method = item["method"].lower()
    args = [f"{p}: {_param_type(p)}" for p in params]
    if method in ("post", "put", "patch"):
        args.append(f"dto: {title}DTO")
        body = [f"    dto.{p} = {p}" for p in params]
        call = "dto"
    else:
        body = []
        call = f"{title}DTO({', '.join(f'{p}={p}' for p in params)})"
    summary = str(item.get("summary") or "").replace("\n", " ").replace('"', "'")
    extra = f', summary="{summary}"' if summary else ""
    body.append(f'    return await run("{name}", {call})')
    return (
        f'@router.{method}("{item["rest"]}"{extra})\n'
        f"async def {name}({', '.join(args)}):\n" + "\n".join(body) + "\n"
    )


def _register(init: Path, domain: str, name: str) -> None:
    """Подключить сценарий в ``__init__.py`` домена: core обходит только пакеты первого уровня."""
    line = f"from app.scenarios.{domain} import {name}"
    text = init.read_text(encoding="utf-8") if init.exists() else ""
    if line not in text:
        head = text.rstrip("\n") + "\n" if text.strip() else ""
        init.write_text(head + line + "\n", encoding="utf-8")


def _add_import(text: str, line: str) -> str:
    if line in text:
        return text
    rows = text.split("\n")
    last = max((i for i, r in enumerate(rows) if re.match(r"(from|import) ", r)), default=-1)
    rows.insert(last + 1, line)
    return "\n".join(rows)


def scaffold_backend(project: Path, endpoints: list[dict]) -> dict:
    """Сценарий и роут на каждый эндпоинт. Существующие файлы не перезаписываются.

    ``endpoints``: ``method``, ``path``, ``scenario`` (имя), ``summary``, ``request``, ``response``.
    Возвращает ``{"scenarios": [...], "routes": [...]}`` — какие файлы созданы или дополнены.
    """
    project = Path(project)
    prefix = api_prefix(project)
    created: dict[str, list[str]] = {"scenarios": [], "routes": []}
    by_domain: dict[str, list[dict]] = {}
    for raw in endpoints:
        domain, _rest = split_path(raw["path"], prefix)
        full = route_path(raw["path"], prefix)
        by_domain.setdefault(domain, []).append({**raw, "domain": domain, "rest": full})
    for domain, items in by_domain.items():
        folder = project / "app" / "scenarios" / domain
        folder.mkdir(parents=True, exist_ok=True)
        for init in (project / "app" / "scenarios" / "__init__.py", folder / "__init__.py"):
            if not init.exists():
                init.write_text("", encoding="utf-8")
        route_file = project / "app" / "api" / "v1" / "routes" / f"{domain}.py"
        route_file.parent.mkdir(parents=True, exist_ok=True)
        for item in items:
            target = folder / f"{item['scenario']}.py"
            _register(folder / "__init__.py", domain, item["scenario"])
            if not target.exists():
                target.write_text(render_scenario(item), encoding="utf-8")
                created["scenarios"].append(target.relative_to(project).as_posix())
            text = route_file.read_text(encoding="utf-8") if route_file.exists() else ""
            if f'run("{item["scenario"]}"' in text:
                continue
            if not text:
                text = (
                    "from fastapi import APIRouter\n\nfrom core import run\n\n"
                    f'router = APIRouter(tags=["{domain}"])\n'
                )
            module = f"app.scenarios.{domain}.{item['scenario']}"
            text = _add_import(text, f"from {module} import {pascal(item['scenario'])}DTO")
            text = text.rstrip("\n") + "\n\n\n" + _route_function(item)
            route_file.write_text(text, encoding="utf-8")
            rel = route_file.relative_to(project).as_posix()
            if rel not in created["routes"]:
                created["routes"].append(rel)
    return created


# ---------------------------------------------------------------------- frontend/

FRONT_THEMES = {
    "light": {"bg": "#f8f9fa", "surface": "#ffffff", "text": "#212529", "muted": "#868e96",
              "accent": "#3a7abd", "accent_dark": "#2a5a8d", "border": "#dee2e6"},
    "dark": {"bg": "#16181c", "surface": "#1f2228", "text": "#e6e8eb", "muted": "#9aa0a8",
             "accent": "#5b9bd5", "accent_dark": "#3f7fb8", "border": "#343a42"},
    "business": {"bg": "#eef1f4", "surface": "#ffffff", "text": "#1a2733", "muted": "#6b7a89",
                 "accent": "#1a3a4a", "accent_dark": "#12293a", "border": "#cfd6dd"},
}  # fmt: skip

_PACKAGE_JSON = {
    "name": "frontend-app",
    "private": True,
    "version": "0.0.0",
    "type": "module",
    "scripts": {
        "dev": "vite",
        "build": "vite build",
        "preview": "vite preview",
        "test": "vitest run",
    },
    "dependencies": {"pinia": "^4.0.3", "vue": "^3.5.42", "vue-router": "^4.6.4"},
    "devDependencies": {"@vitejs/plugin-vue": "^6.0.8", "vite": "^8.3.0", "vitest": "^3.2.7"},
}
_VITE = """\
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
"""
_INDEX_HTML = """\
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>frontend-app</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
"""
_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="currentColor"/></svg>\n'
)
_MAIN_JS = """\
// Bootstrap: собирает logic/ и ui/. Вне веток. Без бизнес-логики.
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './ui/App.vue'
import { createAppRouter } from './ui/app/index.js'
import './ui/styles/tokens.css'
import './ui/styles/base.css'

const app = createApp(App)
app.use(createPinia())
app.use(createAppRouter())
app.mount('#app')
"""
_CLIENT_JS = """\
// Транспорт: единственная fetch-обёртка проекта. Знает только fetch и адрес API.
// Путь вызова пишется без префикса API — он добавляется здесь.
export const API_PREFIX = '/api/v1'

export class ApiError extends Error {
  constructor(status, message, payload) {
    super(message)
    this.status = status
    this.payload = payload
  }
}

export function withQuery(path, params) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') q.append(k, String(v))
  }
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

export async function apiFetch(path, { method = 'GET', body, timeout = 30000 } = {}) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeout)
  try {
    const res = await fetch(API_PREFIX + path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    })
    let data = null
    try {
      data = await res.json()
    } catch {
      data = null
    }
    if (!res.ok) {
      throw new ApiError(res.status, data?.error?.message || `Request failed (${res.status})`, data)
    }
    return data
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(0, error?.message || 'Network error')
  } finally {
    clearTimeout(timer)
  }
}
"""
_CLIENT_TEST = """\
import { describe, expect, it } from 'vitest'
import { withQuery } from './client.js'

describe('withQuery', () => {
  it('добавляет только заданные параметры', () => {
    expect(withQuery('/items', { a: 1, b: '', c: null })).toBe('/items?a=1')
    expect(withQuery('/items', {})).toBe('/items')
  })
})
"""
_BASE_CSS = """\
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--font-family); font-size: 16px; line-height: 1.5; color: var(--color-text); background: var(--color-bg); }
a { color: var(--color-accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.btn { display: inline-flex; align-items: center; justify-content: center; padding: 8px 16px; border: none; border-radius: var(--radius-md); font-size: 14px; cursor: pointer; }
.btn--primary { background: var(--color-accent); color: var(--color-white); }
.btn--primary:hover { background: var(--color-accent-dark); }
.card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: var(--spacing-4); }
"""
_ROUTER_JS = """\
// Маршруты и guard. Из logic/ здесь только store и доступовые действия.
import { createRouter, createWebHistory } from 'vue-router'

export const routes = []

export function createAppRouter() {
  return createRouter({ history: createWebHistory(), routes })
}
"""


def _tokens(theme: str) -> str:
    c = FRONT_THEMES.get(theme) or FRONT_THEMES["light"]
    return (
        ":root {\n"
        f"  --color-bg: {c['bg']};\n  --color-surface: {c['surface']};\n"
        f"  --color-text: {c['text']};\n  --color-muted: {c['muted']};\n"
        f"  --color-accent: {c['accent']};\n  --color-accent-dark: {c['accent_dark']};\n"
        f"  --color-border: {c['border']};\n"
        "  --color-danger: #dc3545;\n  --color-success: #28a745;\n  --color-white: #ffffff;\n"
        "  --font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Roboto', sans-serif;\n"
        "  --spacing-2: 8px;\n  --spacing-4: 16px;\n  --spacing-6: 24px;\n"
        "  --radius-md: 6px;\n  --radius-lg: 12px;\n}\n"
    )


def _contract(layer: str, text: str = "") -> str:
    return f"// Контракт {layer}. Снаружи — только отсюда.\n{text}"


def scaffold_frontend(project: Path, theme: str = "light") -> list[str]:
    """Каркас ``frontend/`` с обязательными файлами стека. Существующие файлы не трогаем."""
    front = Path(project) / "frontend"
    files = {
        "package.json": json.dumps(_PACKAGE_JSON, ensure_ascii=False, indent=2) + "\n",
        "vite.config.js": _VITE,
        "index.html": _INDEX_HTML,
        "public/favicon.svg": _FAVICON,
        "src/main.js": _MAIN_JS,
        "src/ui/App.vue": "<template>\n  <router-view />\n</template>\n",
        "src/ui/app/index.js": _contract(
            "ui/app", "export { routes, createAppRouter } from './router.js'\n"
        ),
        "src/ui/app/router.js": _ROUTER_JS,
        "src/ui/styles/tokens.css": _tokens(theme),
        "src/ui/styles/base.css": _BASE_CSS,
        "src/ui/composables/index.js": _contract("ui/composables"),
        "src/ui/components/index.js": _contract("ui/components (UI-кит)"),
        "src/ui/blocks/.gitkeep": "",
        "src/ui/pages/.gitkeep": "",
        "src/logic/api/client.js": _CLIENT_JS,
        "src/logic/api/client.test.js": _CLIENT_TEST,
        "src/logic/api/index.js": _contract(
            "logic/api", "export { ApiError, apiFetch, withQuery } from './client.js'\n"
        ),
        "src/logic/api/mocks/.gitkeep": "",
        "src/logic/actions/index.js": _contract("logic/actions"),
        "src/logic/store/index.js": _contract("logic/store"),
        "src/logic/utils/index.js": _contract("logic/utils"),
        "src/logic/constants/index.js": _contract(
            "logic/constants", "export const APP_NAME = 'frontend-app'\n"
        ),
        "src/logic/logger/index.js": (
            "// Логгер интерфейса.\nexport function logInfo(scope, message, data) {\n"
            "  console.info(`[${scope}] ${message}`, data ?? '')\n}\n"
        ),
    }
    created = []
    for rel, content in files.items():
        target = front / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append("frontend/" + rel)
    return created


__all__ = [
    "BACKEND_GUIDE",
    "BACKEND_TEST_GUIDE",
    "E2E_TEST_GUIDE",
    "FRONTEND_GUIDE",
    "FRONTEND_TEST_GUIDE",
    "FRONT_THEMES",
    "STACK_LINE",
    "api_prefix",
    "configure_env",
    "init_stack",
    "missing_files",
    "normalize_params",
    "pascal",
    "render_models",
    "render_scenario",
    "route_path",
    "scaffold_backend",
    "scaffold_frontend",
    "scenario_name",
    "slug",
    "split_path",
    "strip_prefix",
    "write_models",
]
