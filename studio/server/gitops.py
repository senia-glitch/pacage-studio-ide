"""Git-чекпоинты работы агента: коммит после этапа, откат, опциональный пуш.

Git делает студия, а не агент: у агента нет доступа к ``git push`` (запрещён в его правах),
поэтому всё, что попадает в историю или на GitHub, проходит через эти функции. Работа идёт
в отдельной ветке ``studio/c<цикл>``; пока в рабочем дереве есть ваши несохранённые
изменения, ветку не создаём и ничего не коммитим, чтобы не смешать их с работой агента.
Состояние чекпоинта лежит в ``.studio/agent-git.json``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from studio._atomic import atomic_write

STATE_FILE = "agent-git.json"
BRANCH_PREFIX = "studio/"
_EXCLUDE = (".", ":(exclude).studio")
_TIMEOUT = 60


class GitError(Exception):
    """Команда git не выполнилась; ``str(exc)`` — понятное объяснение."""


def _git(root: Path, *args: str, check: bool = True) -> str:
    exe = shutil.which("git")
    if exe is None:
        raise GitError("git не найден в PATH")
    try:
        done = subprocess.run(  # noqa: S603 — аргументы собираем сами, без оболочки
            [exe, *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_TIMEOUT, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {args[0]}: {exc}") from exc
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise GitError(detail or f"git {args[0]}: код {done.returncode}")
    return done.stdout.strip()


def is_repo(root: Path) -> bool:
    """Корень проекта сам является корнем git-репозитория.

    Проект внутри чужого (более крупного) репозитория не подходит: чекпоинт переключал бы
    ветку и откатывал бы файлы всего репозитория, а не только проекта.
    """
    root = Path(root)
    try:
        top = _git(root, "rev-parse", "--show-toplevel", check=False)
    except GitError:
        return False
    if not top:
        return False
    try:
        return Path(top).resolve() == root.resolve()
    except OSError:
        return False


def _state_path(root: Path) -> Path:
    return Path(root) / ".studio" / STATE_FILE


def load_state(root: Path) -> dict:
    try:
        raw = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(root: Path, data: dict) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def branch(root: Path) -> str:
    return _git(Path(root), "rev-parse", "--abbrev-ref", "HEAD")


def head(root: Path) -> str:
    return _git(Path(root), "rev-parse", "HEAD")


def dirty_files(root: Path) -> list[str]:
    out = _git(Path(root), "status", "--porcelain", "--", *_EXCLUDE)
    return [line[3:] for line in out.splitlines() if line.strip()]


def status(root: Path) -> dict:
    """Что показать в интерфейсе: репозиторий, ветка, чекпоинт, чистота дерева."""
    root = Path(root)
    if not is_repo(root):
        return {"repo": False}
    state = load_state(root)
    try:
        current = branch(root)
        dirty = dirty_files(root)
    except GitError as exc:
        return {"repo": True, "error": str(exc)}
    return {
        "repo": True,
        "branch": current,
        "clean": not dirty,
        "dirty_count": len(dirty),
        "before": state.get("before", ""),
        "checkpoint": state.get("checkpoint", ""),
        "checkpoint_message": state.get("message", ""),
        "pushed": bool(state.get("pushed")),
        "can_rollback": bool(state.get("before")) and current.startswith(BRANCH_PREFIX),
    }


def begin(root: Path, cycle: int) -> dict:
    """Перед работой агента: запомнить точку отката и перейти в ветку цикла.

    Возвращает ``{"ok": bool, "reason": str}``. При несохранённых изменениях или
    отсутствии репозитория чекпоинт отключается на этот запуск (работа агента не блокируется).
    """
    root = Path(root)
    if not is_repo(root):
        return {"ok": False, "reason": "Проект не в git-репозитории: откат недоступен"}
    try:
        if dirty_files(root):
            _save_state(root, {})
            return {
                "ok": False,
                "reason": "В проекте есть несохранённые изменения: чекпоинт не создан, "
                "сохраните их (commit) перед запуском агента",
            }
        before = head(root)
        target = f"{BRANCH_PREFIX}c{int(cycle)}"
        if branch(root) != target:
            ref = f"refs/heads/{target}"
            exists = _git(root, "rev-parse", "--verify", "--quiet", ref, check=False)
            if exists:
                _git(root, "checkout", target)
            else:
                _git(root, "checkout", "-b", target)
        _save_state(root, {"before": before, "branch": target, "checkpoint": "", "message": ""})
    except GitError as exc:
        return {"ok": False, "reason": f"git: {exc}"}
    return {"ok": True, "reason": ""}


def checkpoint(root: Path, message: str, push: bool = False, remote: str = "origin") -> dict:
    """После работы агента: закоммитить изменения (кроме .studio) и при желании запушить."""
    root = Path(root)
    state = load_state(root)
    if not state.get("before"):
        return {"ok": False, "reason": "Точка отката не записана", "committed": False}
    try:
        if not dirty_files(root):
            return {"ok": True, "reason": "Изменений нет", "committed": False}
        _git(root, "add", "-A", "--", *_EXCLUDE)
        _git(root, "commit", "-m", message, "--no-verify")
        state.update({"checkpoint": head(root), "message": message})
        _save_state(root, state)
        pushed = ""
        if push:
            try:
                _git(root, "push", "-u", remote, branch(root))
                state["pushed"] = True
                _save_state(root, state)
            except GitError as exc:
                pushed = f"Коммит сделан, но пуш не удался: {exc}"
        return {"ok": True, "reason": pushed, "committed": True, "checkpoint": state["checkpoint"]}
    except GitError as exc:
        return {"ok": False, "reason": f"git: {exc}", "committed": False}


def rollback(root: Path) -> dict:
    """Вернуть проект к состоянию до работы агента (только в ветке ``studio/*``)."""
    root = Path(root)
    state = load_state(root)
    before = state.get("before")
    if not before:
        return {"ok": False, "reason": "Нечего откатывать: точка отката не записана"}
    try:
        if not branch(root).startswith(BRANCH_PREFIX):
            return {"ok": False, "reason": "Откат разрешён только в ветке studio/*"}
        _git(root, "reset", "--hard", before)
        _git(root, "clean", "-fd", "--", *_EXCLUDE)
        state.update({"checkpoint": "", "message": "", "pushed": False})
        _save_state(root, state)
    except GitError as exc:
        return {"ok": False, "reason": f"git: {exc}"}
    return {"ok": True, "reason": ""}


__all__ = [
    "BRANCH_PREFIX",
    "GitError",
    "begin",
    "branch",
    "checkpoint",
    "is_repo",
    "load_state",
    "rollback",
    "status",
]
