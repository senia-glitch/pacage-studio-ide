"""«Волны» (по желанию): этапы 3–4 проходят по очереди для пакетов сценариев.

По умолчанию волн нет: один пакет — это все сценарии. Если сценариев больше порога из настроек,
система предлагает разбить их на пакеты. Код группирует сценарии по общим таблицам БД, чтобы
сценарии, которые трогают одни и те же данные, шли вместе. Каждая волна — рабочий срез: свои
страницы, тесты, эндпоинты и свои ворота; после бэкенда волны — «ещё волна?»: да — следующий
пакет, нет — этап 5.

Пока идёт волна, этапы 3–4 видят только записи этой волны (``records``). Когда все волны
пройдены, область снимается и видно всё.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from studio._atomic import atomic_write
from studio.pipeline.graph import ACCEPTED

from .engine import Pipeline
from .errors import PipelineError

FILE = Path(".studio") / "waves.json"
SCOPED = frozenset({"scenario", "criterion", "page", "call", "test", "endpoint"})
WAVE_GATES = ("frontend", "backend")  # ворота, которые каждая волна проходит заново

_cache: dict = {}


def _read(pl: Pipeline) -> dict:
    try:
        raw = json.loads((pl.project / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _write(pl: Pipeline, data: dict) -> None:
    path = pl.project / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))
    _cache.clear()


def enabled(pl: Pipeline) -> bool:
    return bool(_read(pl).get("on"))


def current(pl: Pipeline) -> int:
    """Номер идущей волны с нуля; ``-1`` — волн нет."""
    data = _read(pl)
    return int(data.get("current", 0)) if data.get("on") else -1


# ---------------------------------------------------------------------- пакеты


def groups(pl: Pipeline, scenarios: list[dict] | None = None) -> list[list[str]]:
    """Пакеты сценариев: связанные общими таблицами идут вместе, размер — не больше порога."""
    items = scenarios if scenarios is not None else pl.bank.records("scenario", status=ACCEPTED)
    ids = [s["id"] for s in items]
    if not ids:
        return []
    parent = {i: i for i in ids}

    def find(i: str) -> str:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for table in pl.bank.records("table"):
        linked = [p["id"] for p in pl.bank.parents(table["id"]) if p["id"] in parent]
        for other in linked[1:]:
            parent[find(other)] = find(linked[0])
    parts: dict[str, list[str]] = {}
    for i in ids:  # порядок записей в банке сохраняется
        parts.setdefault(find(i), []).append(i)
    threshold = max(1, pl.settings.wave_threshold)
    count = max(1, math.ceil(len(ids) / threshold))
    target = math.ceil(len(ids) / count)
    packages: list[list[str]] = [[]]
    for part in parts.values():
        if packages[-1] and len(packages[-1]) + len(part) > target:
            packages.append([])
        packages[-1].extend(part)
    return packages


def suggestion(pl: Pipeline) -> dict | None:
    """Когда сценариев больше порога, а волны выключены, система предлагает их включить."""
    if enabled(pl) or _read(pl).get("declined"):
        return None
    total = len(pl.bank.records("scenario", status=ACCEPTED))
    if total <= pl.settings.wave_threshold:
        return None
    packages = groups(pl)
    if len(packages) < 2:
        return None
    return {"scenarios": total, "packages": len(packages), "threshold": pl.settings.wave_threshold}


def enable(pl: Pipeline) -> dict:
    """Включить волны: составить пакеты и начать с первого. Уже пройденные волны остаются."""
    data = _read(pl)
    done = [list(p) for p in data.get("packages", [])[: int(data.get("current", 0))]]
    used = {i for p in done for i in p}
    rest = [s for s in pl.bank.records("scenario", status=ACCEPTED) if s["id"] not in used]
    packages = groups(pl, rest)
    if not packages and not done:
        raise PipelineError("no_scenarios", "Волны строятся из принятых сценариев: их пока нет")
    state = {"on": True, "packages": [*done, *packages], "current": len(done)}
    _write(pl, {**state, "finished": not packages})
    return info(pl)


def disable(pl: Pipeline) -> dict:
    """Выключить волны: этапы 3–4 снова видят все сценарии сразу."""
    _write(pl, {"on": False, "declined": True})
    return info(pl)


def advance(pl: Pipeline) -> dict | None:
    """«Ещё волна?» после принятого бэкенда: да — сбросить ворота и перейти к следующему пакету."""
    data = _read(pl)
    if not data.get("on") or data.get("finished"):
        return None
    current = int(data.get("current", 0))
    if current + 1 >= len(data.get("packages", [])):
        data["finished"] = True
        _write(pl, data)
        return None
    data["current"] = current + 1
    _write(pl, data)
    pl.keeper.reset(WAVE_GATES)
    from . import stage3

    (pl.project / stage3.RESULTS).unlink(missing_ok=True)  # результаты тестов — волны прежней
    return info(pl)


# ---------------------------------------------------------------------- область волны


def _scope_of(pl: Pipeline, scenario_ids: list[str]) -> set[str]:
    ids: set[str] = set()
    for sid in scenario_ids:
        ids.add(sid)
        owners = [sid]
        for req in pl.bank.parents(sid):
            if req["kind"] != "requirement":
                continue
            ids.add(req["id"])
            for card in pl.bank.children(req["id"]):
                if card["kind"] == "criterion":  # критерий требования проверяют и его сценарии
                    ids.add(card["id"])
                    owners.append(card["id"])
        for owner in owners:
            ids.update(d["id"] for d in pl.bank.descendants(owner))
    for card in pl.bank.records("criterion"):  # критерии ограничений (права, объём) общие
        if any(p["kind"] == "constraint" for p in pl.bank.parents(card["id"])):
            ids.add(card["id"])
            ids.update(d["id"] for d in pl.bank.descendants(card["id"]))
    return ids


def scope(pl: Pipeline) -> frozenset[str] | None:
    """Коды записей текущей волны; ``None`` — волн нет или все пройдены, видно всё."""
    data = _read(pl)
    if not data.get("on") or data.get("finished"):
        return None
    key = (pl.bank.store.revision, data.get("current"), json.dumps(data.get("packages")))
    if _cache.get("key") != key:
        packages = data.get("packages", [])
        index = min(int(data.get("current", 0)), max(len(packages) - 1, 0))
        _cache["key"] = key
        _cache["value"] = frozenset(_scope_of(pl, packages[index] if packages else []))
    return _cache["value"]


def records(pl: Pipeline, kind: str, **kwargs) -> list[dict]:
    """Записи вида ``kind``; пока идёт волна — только записи этой волны."""
    cards = pl.bank.records(kind, **kwargs)
    ids = scope(pl)
    if ids is None or kind not in SCOPED:
        return cards
    return [c for c in cards if c["id"] in ids]


def facts(pl: Pipeline) -> dict:
    """Что передать проверкам ворот, чтобы они смотрели только на текущую волну."""
    ids = scope(pl)
    return {} if ids is None else {"scope": sorted(ids)}


def in_scope(facts_: dict, card: dict) -> bool:
    """Входит ли запись в область, которую смотрят ворота (без волн — всегда входит)."""
    ids = facts_.get("scope")
    return ids is None or card["id"] in ids


# ---------------------------------------------------------------------- сводка


def info(pl: Pipeline) -> dict:
    """Для экрана: включены ли волны, пакеты и какой идёт сейчас, предложение системы."""
    data = _read(pl)
    on = bool(data.get("on"))
    current = int(data.get("current", 0))
    finished = bool(data.get("finished"))
    packages = []
    for index, ids in enumerate(data.get("packages", []) if on else []):
        cards = [pl.bank.get(i) for i in ids]
        state = "done" if index < current or finished else "current" if index == current else "next"
        packages.append(
            {
                "index": index + 1,
                "state": state,
                "scenarios": [{"id": c["id"], "title": c["title"]} for c in cards],
            }
        )
    return {
        "on": on,
        "finished": finished,
        "current": current + 1 if on else 0,
        "total": len(packages),
        "packages": packages,
        "suggestion": suggestion(pl),
        "threshold": pl.settings.wave_threshold,
    }


__all__ = [
    "advance",
    "current",
    "disable",
    "enable",
    "enabled",
    "facts",
    "groups",
    "in_scope",
    "info",
    "records",
    "scope",
    "suggestion",
]
