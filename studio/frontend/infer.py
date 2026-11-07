"""Вызовы эндпоинтов, найденные в коде фронтенда (по тексту, без выполнения).

Объявленные вызовы (``calls``) пишет человек или агент. Этот модуль находит вызовы в коде и
показывает, что страница или блок реально обращается к бэкенду — через цепочку
``блок → composable → действие → api``:

1. в каждом файле ищется ``<объект>.get|post|put|patch|delete('/путь', …)``; путь с
   подстановками ``${id}`` приводится к ``{param}``, строка запроса отбрасывается;
2. экспортируемые функции и константы (``export const fetchX = …``) становятся символами;
   символ «вызывает» то же, что и символы, которые он использует по имени;
3. composable отдаёт объект ``return { … }``: файл получает только вызовы тех его полей, имена
   которых сам использует (``const { login } = useSession()``), и то, что composable делает
   при создании; прочие импортированные символы дают всё, что вызывают;
4. файл «вызывает» то, что получилось из импортов, плюс свои прямые вызовы.

Это эвристика: вызов через переменную, динамический путь или свой клиент не найдётся. Поэтому
результат хранится отдельно от объявленного (``inferred_calls``) и ничего не ломает.
"""

from __future__ import annotations

import re
from pathlib import Path

METHODS = ("get", "post", "put", "patch", "delete")
MAX_ROUNDS = 8
_SUFFIXES = (".js", ".mjs", ".vue")

_EXPORT_RE = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\s*\*?\s*(\w+)|(?:const|let|var)\s+(\w+))",
    re.M,
)
_HTTP_RE = re.compile(
    r"\b[A-Za-z_$][\w$]*\s*\.\s*(get|post|put|patch|delete)\s*\(\s*"
    r"(`[^`]*`|'[^'\n]*'|\"[^\"\n]*\")",
)
_IMPORT_RE = re.compile(r"^import\s+([^;]*?)\s+from\s+['\"][^'\"]+['\"]", re.M | re.S)
_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*")
_SUBST_RE = re.compile(r"\$\{[^}]*\}")


def _clean_path(literal: str) -> str | None:
    """Путь из литерала запроса: ``/a/${id}${q}`` → ``/a/{param}``; не путь — None."""
    body = literal[1:-1]
    if not body.startswith("/"):
        return None
    out: list[str] = []
    last = 0
    for match in _SUBST_RE.finditer(body):
        out.append(body[last : match.start()])
        previous = "".join(out)
        # подстановка сразу после «/» — параметр пути, иначе (запрос, флаг) — отбрасывается
        out.append("{param}" if previous.endswith("/") else "")
        last = match.end()
    out.append(body[last:])
    path = "".join(out).split("?", 1)[0]
    return path.rstrip("/") or "/"


def direct_calls(text: str) -> list[str]:
    """Вызовы ``METHOD /path`` прямо в тексте файла."""
    found: list[str] = []
    for match in _HTTP_RE.finditer(text):
        path = _clean_path(match.group(2))
        call = f"{match.group(1).upper()} {path}" if path else None
        if call and call not in found:
            found.append(call)
    return found


def imported_names(text: str) -> set[str]:
    """Исходные имена, импортированные файлом (``a as b`` → ``a``)."""
    names: set[str] = set()
    for statement in _IMPORT_RE.finditer(text):
        head = statement.group(1)
        if "{" in head:
            inside = head.split("{", 1)[1].split("}", 1)[0]
            for item in inside.split(","):
                original = item.strip().split(" as ")[0].strip()
                if original:
                    names.add(original)
    return names


def _without_imports(text: str) -> str:
    return _IMPORT_RE.sub("", text)


def _matching(text: str, start: int) -> int:
    """Индекс скобки, закрывающей ту, что стоит в ``start`` (строки пропускаются); иначе -1."""
    depth, quote, index = 0, "", start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "'\"`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _split_top(text: str) -> list[str]:
    """Части по запятым верхнего уровня (вложенные скобки и строки не делятся)."""
    parts, depth, quote, last, index = [], 0, "", 0, 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "'\"`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[last:index])
            last = index + 1
        index += 1
    parts.append(text[last:])
    return [part.strip() for part in parts if part.strip()]


_RETURN_RE = re.compile(r"\breturn\s*\{")
_STATEMENT_RE = re.compile(r"\n\s*(?:const|let|var|function|async function|return)\b")
_KEY_RE = re.compile(r"^([A-Za-z_$][\w$]*)\s*:(.*)$", re.S)


def _local_definition(body: str, name: str) -> str:
    """Текст локального ``const name = …`` / ``function name`` до следующего оператора."""
    found = re.search(rf"(?:const|let|var|function)\s+{re.escape(name)}\b", body)
    if found is None:
        return ""
    after = _STATEMENT_RE.search(body, found.end())
    end = after.start() if after else len(body)
    return body[found.start() : end]


def split_members(body: str) -> tuple[str, dict[str, str]] | None:
    """Тело composable → (код создания без ``return``, ``{поле: код поля}``); None — нет объекта."""
    marks = list(_RETURN_RE.finditer(body))
    if not marks:
        return None
    open_at = marks[-1].end() - 1
    close_at = _matching(body, open_at)
    if close_at < 0:
        return None
    members: dict[str, str] = {}
    setup = body[: marks[-1].start()] + body[close_at + 1 :]
    for entry in _split_top(body[open_at + 1 : close_at]):
        keyed = _KEY_RE.match(entry)
        if entry.startswith("..."):
            setup += " " + entry[3:]
        elif keyed:
            members[keyed.group(1)] = keyed.group(2)
        elif _WORD_RE.fullmatch(entry):
            local = _local_definition(body, entry)
            members[entry] = entry + " " + local
            if local:
                setup = setup.replace(local, " ")
    return setup, members


def _symbols(text: str) -> dict[str, str]:
    """Экспортируемые символы файла → текст их определения (до следующего экспорта)."""
    marks = [(m.start(), m.group(1) or m.group(2)) for m in _EXPORT_RE.finditer(text)]
    out: dict[str, str] = {}
    for index, (start, name) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        out[name] = out.get(name, "") + text[start:end]
    return out


def infer_calls(front_dir: Path, rel_files: list[str]) -> dict[str, list[str]]:
    """``{путь файла относительно frontend/: [вызовы]}`` для файлов с найденными вызовами."""
    texts: dict[str, str] = {}
    for rel in rel_files:
        if not rel.startswith("src/") or not rel.endswith(_SUFFIXES):
            continue
        if rel.endswith((".test.js", ".spec.js")) or "/__tests__/" in rel:
            continue
        try:
            texts[rel] = (Path(front_dir) / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

    symbol_calls: dict[str, set[str]] = {}
    symbol_words: dict[str, set[str]] = {}
    shapes: dict[str, tuple[str, dict[str, str]]] = {}
    for text in texts.values():
        for name, body in _symbols(text).items():
            symbol_calls.setdefault(name, set()).update(direct_calls(body))
            symbol_words.setdefault(name, set()).update(_WORD_RE.findall(_without_imports(body)))
            shape = split_members(body)
            if shape is not None:
                shapes[name] = shape
    for _ in range(MAX_ROUNDS):
        changed = False
        for name, words in symbol_words.items():
            gained: set[str] = set()
            for word in words:
                if word != name and word in symbol_calls:
                    gained |= symbol_calls[word]
            if not gained <= symbol_calls[name]:
                symbol_calls[name] |= gained
                changed = True
        if not changed:
            break

    def words_calls(code: str, own: str) -> set[str]:
        out = set(direct_calls(code))
        for word in set(_WORD_RE.findall(_without_imports(code))) - {own}:
            out |= symbol_calls.get(word, set())
        return out

    result: dict[str, list[str]] = {}
    for rel, text in texts.items():
        calls = set(direct_calls(text))
        used = set(_WORD_RE.findall(_without_imports(text)))
        for name in imported_names(text) & used:
            if name in shapes:
                setup, members = shapes[name]
                calls |= words_calls(setup, name)
                for key in used & members.keys():
                    calls |= words_calls(members[key], name)
            else:
                calls |= symbol_calls.get(name, set())
        if calls:
            result[rel] = sorted(calls)
    return result


__all__ = ["direct_calls", "imported_names", "infer_calls", "split_members"]
