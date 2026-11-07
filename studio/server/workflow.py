"""Дорожная карта цепочки «агент ↔ человек»: этап работы и допустимые переходы.

Цепочка (путь «планирование» / путь «реализация»)::

    sync → mode → ideas → review → confirm → select → implement → report → (заново)
    sync → mode ─────────────────────────→ select → implement → report → (заново)

* ``sync`` — агент подключён (вычисляется по его обращениям, в файле не хранится);
* ``mode`` — человек выбирает «планирование» или «реализация»;
* ``ideas`` — агент присылает идеи со сценариями; ``review`` — человек свайпает карточки;
* ``confirm`` — «перенести принятое на карту?»; ``select`` — чекбоксы задач на реализацию;
* ``implement`` — агент пишет код по сохранённому ТЗ; ``report`` — человеческий отчёт.

Состояние лежит в ``.studio/workflow.json`` (переживает перезапуск). Права агента зависят
от этапа: писать идеи он может только на этапах ``ideas``/``review``, отчёт — только на
``implement``/``report``. Решения по переходам принимает человек (UI); агент двигает
цепочку двумя действиями: «идеи готовы» и «отчёт отправлен».
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from studio._atomic import atomic_write
from studio.metadata import MetadataError

STAGES = ("mode", "ideas", "review", "confirm", "select", "implement", "report")
MODES = ("plan", "implement")
TRANSITIONS: dict[str, frozenset[str]] = {
    "mode": frozenset({"ideas", "select"}),
    "ideas": frozenset({"review", "mode"}),
    "review": frozenset({"confirm", "ideas", "mode"}),
    "confirm": frozenset({"select", "review", "ideas", "mode"}),
    "select": frozenset({"implement", "mode"}),
    "implement": frozenset({"report", "select", "mode"}),
    "report": frozenset({"mode", "select", "implement"}),
}
STEP_TITLES = {
    "sync": "Синхронизация",
    "mode": "Режим",
    "ideas": "Идеи агента",
    "review": "Валидация",
    "confirm": "Перенос на карту",
    "select": "Выбор задач",
    "implement": "Реализация",
    "report": "Отчёт",
}
STEP_ORDER = tuple(STEP_TITLES)
PLANNING_ONLY = frozenset({"ideas", "review", "confirm"})
# Этапы, на которых агенту разрешена запись соответствующего режима.
AGENT_WRITE_STAGES = {
    "plan": frozenset({"ideas", "review"}),
    "implement": frozenset({"implement", "report"}),
}
SEEN_TTL = 75.0  # агент «подключён», если обращался к студии не позже этого срока (с)
FILENAME = "workflow.json"
_MAX_TASK = 4000
_MAX_SUMMARY = 20000


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _blank() -> dict:
    return {
        "version": 1,
        "revision": 0,
        "cycle": 1,
        "entries": 0,
        "stage": "mode",
        "mode": "",
        "task": "",
        "summary": "",
        "report_id": "",
        "selected": {"plans": 0, "nodes": 0, "pages": 0},
        "updated_at": _now(),
    }


class Workflow:
    """Состояние дорожной карты проекта; все изменения идут под замком состояния студии."""

    def __init__(self, root: Path, lock: threading.RLock, cond: threading.Condition):
        self.root = Path(root)
        self.lock = lock
        self.cond = cond
        self._seen: float | None = None
        self._seen_mode = ""
        # Слушатели входа на этап (автозапуск агента) и признак «агент запускается сам».
        self.listeners: list = []
        self.auto_agent = False
        self.data = self._load()

    # ------------------------------------------------------------------ хранение

    @property
    def path(self) -> Path:
        return self.root / ".studio" / FILENAME

    def _load(self) -> dict:
        data = _blank()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(raw, dict):
            return data
        if raw.get("stage") in STAGES:
            data["stage"] = raw["stage"]
        if raw.get("mode") in MODES:
            data["mode"] = raw["mode"]
        for key in ("task", "summary", "report_id", "updated_at"):
            if isinstance(raw.get(key), str):
                data[key] = raw[key]
        for key in ("revision", "cycle", "entries"):
            if isinstance(raw.get(key), int) and not isinstance(raw.get(key), bool):
                data[key] = max(0, raw[key])
        selected = raw.get("selected")
        if isinstance(selected, dict):
            for key in data["selected"]:
                value = selected.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    data["selected"][key] = value
        return data

    def _save(self) -> None:
        self.data["revision"] += 1
        self.data["updated_at"] = _now()
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not atomic_write(self.path, payload):
            raise MetadataError("not_writable", "Не удалось сохранить дорожную карту")
        self.cond.notify_all()

    def _emit(self) -> None:
        for listener in list(self.listeners):
            try:
                listener(self.data["stage"], self.data["entries"])
            except Exception:  # noqa: BLE001 — сбой слушателя не должен ломать переход
                continue

    # ----------------------------------------------------------------- присутствие

    def seen(self, mode: str) -> None:
        """Агент обратился к студии: он подключён (в файл не пишется)."""
        self._seen = time.monotonic()
        self._seen_mode = mode

    def connected(self) -> bool:
        return self._seen is not None and time.monotonic() - self._seen <= SEEN_TTL

    # ------------------------------------------------------------------ переходы

    def _go(self, stage: str) -> None:
        allowed = TRANSITIONS.get(self.data["stage"], frozenset())
        if stage not in allowed:
            raise MetadataError(
                "wrong_stage",
                f"Из этапа «{STEP_TITLES.get(self.data['stage'], self.data['stage'])}» "
                f"нельзя перейти к «{STEP_TITLES.get(stage, stage)}»",
            )
        self.data["stage"] = stage
        self.data["entries"] += 1  # счётчик входов на этапы: по нему UI открывает окно заново

    def start(self, mode: str, task: str = "") -> dict:
        with self.lock:
            if mode not in MODES:
                raise MetadataError("invalid_input", "Режим: plan (планирование) или implement")
            if self.data["stage"] != "mode":
                raise MetadataError(
                    "wrong_stage", "Цепочка уже идёт: сначала завершите или сбросьте её"
                )
            self._go("ideas" if mode == "plan" else "select")
            self.data["mode"] = mode
            self.data["task"] = str(task or "").strip()[:_MAX_TASK]
            self.data["summary"] = ""
            self.data["report_id"] = ""
            self._save()
            self._emit()
            return self.data

    def advance(self, stage: str, **fields) -> dict:
        with self.lock:
            if stage not in STAGES:
                raise MetadataError("invalid_input", f"Неизвестный этап: {stage}")
            self._go(stage)
            if stage == "mode":
                self._reset_cycle()
            self.data.update(fields)
            self._save()
            self._emit()
            return self.data

    def touch(self) -> None:
        """Поднять ревизию без смены этапа: ждущие клиенты видят изменение (например, свайп)."""
        with self.lock:
            self._save()

    def _reset_cycle(self) -> None:
        self.data.update(
            {"mode": "", "task": "", "summary": "", "report_id": "",
             "selected": {"plans": 0, "nodes": 0, "pages": 0}}
        )

    def finish(self) -> dict:
        """Завершить цикл: отчёт принят, цепочку можно начать заново."""
        with self.lock:
            if self.data["stage"] != "report":
                raise MetadataError("wrong_stage", "Завершить можно только на этапе «Отчёт»")
            self._go("mode")
            self._reset_cycle()
            self.data["cycle"] += 1
            self._save()
            self._emit()
            return self.data

    def reset(self) -> dict:
        """Начать заново из любого этапа (цикл не считается завершённым)."""
        with self.lock:
            if self.data["stage"] != "mode":
                self._go("mode")
                self._reset_cycle()
                self._save()
                self._emit()
            return self.data

    def report_received(self, report_id: str, summary: str) -> None:
        with self.lock:
            self.data["summary"] = str(summary or "").strip()[:_MAX_SUMMARY]
            self.data["report_id"] = report_id
            moved = self.data["stage"] == "implement"
            if moved:
                self._go("report")
            self._save()
            if moved:
                self._emit()  # прогон тестов и другие слушатели входа на этап «Отчёт»

    # ---------------------------------------------------------------------- ожидание

    def wait(self, revision: int, timeout: float) -> int:
        with self.lock:
            if self.data["revision"] <= revision:
                self.cond.wait_for(lambda: self.data["revision"] > revision, timeout=timeout)
            return self.data["revision"]

    # ------------------------------------------------------------------ права агента

    def agent_gate(self, mode: str, writes: frozenset[str], method: str, path: str) -> str:
        """Причина отказа агенту режима ``mode`` или пустая строка.

        Чтение всегда открыто; запись режима разрешена только на его этапах.
        """
        if method != "POST" or path not in writes:
            return ""
        stage = self.data["stage"]
        if stage in AGENT_WRITE_STAGES.get(mode, frozenset()):
            return ""
        needed = " и ".join(
            f"«{STEP_TITLES[item]}»"
            for item in STEP_ORDER
            if item in AGENT_WRITE_STAGES.get(mode, ())
        )
        return (
            f"Сейчас этап «{STEP_TITLES[stage]}»: запись в режиме «{mode}» разрешена на этапах "
            f"{needed}. Дождитесь, пока человек дойдёт до него в дорожной карте "
            "(инструмент studio_workflow)."
        )

    # ---------------------------------------------------------------------- описание

    def steps(self) -> list[dict]:
        stage, mode = self.data["stage"], self.data["mode"]
        connected = self.connected() or self.auto_agent
        skip = PLANNING_ONLY if mode == "implement" else frozenset()
        out = []
        # До выбора режима активна «Синхронизация», пока агент не подключился.
        current = STEP_ORDER.index("sync" if stage == "mode" and not connected else stage)
        for index, step in enumerate(STEP_ORDER):
            if step in skip:
                state = "skipped"
            elif index < current:
                state = "done"
            elif index == current:
                state = "active"
            else:
                state = "todo"
            out.append({"id": step, "title": STEP_TITLES[step], "state": state})
        return out

    def hint(self, ctx: dict) -> dict:
        """Что делать агенту сейчас: ``action`` — машинное слово, ``text`` — пояснение."""
        stage = self.data["stage"]
        if stage == "ideas":
            task = self.data["task"]
            tail = f" Задача человека: {task}" if task else " Задача не указана — спроси человека."
            return {
                "action": "propose",
                "text": "Изучи проект и пришли идеи со сценариями (studio_idea_add: scenarios и, "
                "если нужен интерфейс, pages), затем вызови studio_ideas_ready." + tail,
            }
        if stage == "implement":
            return {
                "action": "implement",
                "text": "Возьми ТЗ (studio_brief), реализуй задачи, проверь студией "
                "(studio_verify) и отправь человеческий отчёт (studio_report с полем summary).",
            }
        waits = {
            "mode": "человек ещё не выбрал режим работы в дорожной карте студии",
            "review": "человек валидирует карточки "
            f"(осталось: {(ctx.get('cards') or {}).get('pending', 0)})",
            "confirm": "человек решает, переносить ли принятое на карту",
            "select": "человек выбирает задачи на реализацию",
            "report": "человек читает твой отчёт; цепочка завершится после его решения",
        }
        return {"action": "wait", "text": "Ждём: " + waits.get(stage, stage) + "."}

    def describe(self, ctx: dict) -> dict:
        with self.lock:
            data = dict(self.data)
            data["selected"] = dict(self.data["selected"])
            return {
                **data,
                "agent": {
                    "connected": self.connected(),
                    "auto": self.auto_agent,
                    "mode": self._seen_mode,
                },
                "steps": self.steps(),
                "hint": self.hint(ctx),
                **ctx,
            }


__all__ = [
    "AGENT_WRITE_STAGES",
    "FILENAME",
    "MODES",
    "STAGES",
    "STEP_ORDER",
    "STEP_TITLES",
    "TRANSITIONS",
    "Workflow",
]
