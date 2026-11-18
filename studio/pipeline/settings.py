"""Настройки конвейера проекта: режим участия, критерии ворот, модели ролей агентов.

Файл ``.studio/pipeline.json`` в папке проекта. Всё уже заполнено по умолчанию — править
необязательно. Критерии у каждых ворот свои и независимы друг от друга.
"""

from __future__ import annotations

import json
from pathlib import Path

from studio._atomic import atomic_write

from .errors import PipelineError
from .gates import DEFAULT_THRESHOLD, GATES, gate
from .roles import AUTHORS_BY_STAGE, ROLES, clean_agent, role

FILE = Path(".studio") / "pipeline.json"
MODES = ("manual", "gates", "autopilot")
MODE_NAMES = {"manual": "Вручную", "gates": "На воротах", "autopilot": "Автопилот"}
DEFAULT_MODE = "gates"
STALL_ROUNDS = 3  # кругов подряд без роста оценки, потом агент зовёт человека
MAX_ROUNDS = 12  # предел кругов доработки одних ворот, даже если оценка понемногу растёт
WAVE_THRESHOLD = 8  # сценариев, после которых система предлагает «волны»
QUICK_FIX_LIMIT = 2  # «мелкая правка»: затронуто не больше записей


def _threshold(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PipelineError("bad_threshold", "Порог — целое число от 0 до 10") from None
    if not 0 <= number <= 10:
        raise PipelineError("bad_threshold", "Порог — целое число от 0 до 10")
    return number


class PipelineSettings:
    def __init__(self, project: str | Path):
        self.path = Path(project) / FILE
        self.data = self._read()

    # ------------------------------------------------------------------ файл

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return raw if isinstance(raw, dict) else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(self.data, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------ режим и пороги

    @property
    def mode(self) -> str:
        mode = self.data.get("mode", DEFAULT_MODE)
        return mode if mode in MODES else DEFAULT_MODE

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise PipelineError("bad_mode", "Режим участия: " + ", ".join(MODE_NAMES.values()))
        self.data["mode"] = mode
        self.save()

    @property
    def stall_rounds(self) -> int:
        return STALL_ROUNDS

    @property
    def max_rounds(self) -> int:
        return MAX_ROUNDS

    @property
    def wave_threshold(self) -> int:
        return int(self.data.get("wave_threshold", WAVE_THRESHOLD))

    @property
    def quick_fix_limit(self) -> int:
        return int(self.data.get("quick_fix_limit", QUICK_FIX_LIMIT))

    def set_number(self, key: str, value) -> None:
        if key not in ("wave_threshold", "quick_fix_limit"):
            raise PipelineError("bad_setting", f"Неизвестная настройка: {key!r}")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise PipelineError("bad_setting", "Нужно целое число") from None
        if number < 1:
            raise PipelineError("bad_setting", "Число должно быть больше нуля")
        self.data[key] = number
        self.save()

    # ------------------------------------------------------------------ критерии ворот

    def gate_threshold(self, gate_key: str) -> int:
        """Порог приёмки ворот: оценка критерия не ниже этого числа (по умолчанию 8)."""
        gate(gate_key)
        over = (self.data.get("gates") or {}).get(gate_key) or {}
        return _threshold(over.get("threshold", DEFAULT_THRESHOLD))

    def set_gate_threshold(self, gate_key: str, value) -> None:
        """Один порог на все критерии ворот: личные пороги критериев сбрасываются."""
        number = _threshold(value)
        over = self._gate_over(gate_key)
        over["threshold"] = number
        for edit in (over.get("criteria") or {}).values():
            edit.pop("threshold", None)
        for custom in over.get("custom") or []:
            custom.pop("threshold", None)
        self.save()

    def criteria(self, gate_key: str) -> list[dict]:
        """Критерии ворот: умолчания студии плюс ваши правки и ваши собственные критерии."""
        spec = gate(gate_key)
        over = (self.data.get("gates") or {}).get(gate_key) or {}
        edits = over.get("criteria") or {}
        base = self.gate_threshold(gate_key)
        out = []
        for item in spec.criteria:
            edit = edits.get(item.key) or {}
            out.append(
                {
                    "key": item.key,
                    "title": edit.get("title") or item.title,
                    "hint": edit.get("hint") or item.hint,
                    "threshold": _threshold(edit.get("threshold", base)),
                    "enabled": bool(edit.get("enabled", True)),
                    "custom": False,
                }
            )
        for custom in over.get("custom") or []:
            out.append({**custom, "threshold": _threshold(custom.get("threshold", base)), "custom": True})
        return out

    def enabled_criteria(self, gate_key: str) -> list[dict]:
        return [c for c in self.criteria(gate_key) if c["enabled"]]

    def _gate_over(self, gate_key: str) -> dict:
        gate(gate_key)
        return self.data.setdefault("gates", {}).setdefault(gate_key, {})

    def set_criterion(
        self,
        gate_key: str,
        key: str,
        *,
        enabled: bool | None = None,
        threshold=None,
        title: str | None = None,
        hint: str | None = None,
    ) -> None:
        over = self._gate_over(gate_key)
        customs = {c["key"]: c for c in over.get("custom") or []}
        if key in customs:
            target = customs[key]
        elif key in {c.key for c in GATES[gate_key].criteria}:
            target = over.setdefault("criteria", {}).setdefault(key, {})
        else:
            raise PipelineError("unknown_criterion", f"У этих ворот нет критерия {key!r}")
        if enabled is not None:
            target["enabled"] = bool(enabled)
        if threshold is not None:
            target["threshold"] = _threshold(threshold)
        if title is not None:
            target["title"] = title.strip()
        if hint is not None:
            target["hint"] = hint.strip()
        self.save()

    def add_criterion(self, gate_key: str, title: str, hint: str = "", threshold=None) -> str:
        title = (title or "").strip()
        if not title:
            raise PipelineError("bad_criterion", "У критерия должно быть название")
        over = self._gate_over(gate_key)
        customs = over.setdefault("custom", [])
        key = f"custom-{len(customs) + 1}"
        while any(c["key"] == key for c in customs):
            key += "x"
        customs.append(
            {
                "key": key,
                "title": title[:120],
                "hint": (hint or "").strip()[:300],
                **({} if threshold is None else {"threshold": _threshold(threshold)}),
                "enabled": True,
            }
        )
        self.save()
        return key

    def remove_criterion(self, gate_key: str, key: str) -> None:
        over = self._gate_over(gate_key)
        customs = over.get("custom") or []
        if not any(c["key"] == key for c in customs):
            raise PipelineError(
                "not_custom", "Удалить можно только свой критерий; стандартный можно отключить"
            )
        over["custom"] = [c for c in customs if c["key"] != key]
        self.save()

    def reset_gate(self, gate_key: str) -> None:
        """«Вернуть по умолчанию» для одних ворот."""
        gate(gate_key)
        (self.data.get("gates") or {}).pop(gate_key, None)
        self.save()

    # ------------------------------------------------------------------ роли агентов

    def role(self, role_key: str) -> dict:
        role(role_key)
        item = (self.data.get("roles") or {}).get(role_key) or {}
        return {"agent": item.get("agent", ""), "model": item.get("model", "")}

    def set_role(self, role_key: str, agent: str = "", model: str = "") -> None:
        role(role_key)
        self.data.setdefault("roles", {})[role_key] = {
            "agent": clean_agent(agent),
            "model": (model or "").strip()[:120],
        }
        self.save()

    def roles(self) -> dict[str, dict]:
        return {key: self.role(key) for key in ROLES}

    def evaluator_warnings(self) -> list[str]:
        """Независимый оценщик должен быть другой моделью, чем автор: предупредим, если совпало."""
        ev = self.role("evaluator")
        if not ev["model"]:
            return []
        out = []
        for stage, authors in AUTHORS_BY_STAGE.items():
            for key in authors:
                other = self.role(key)
                if other["model"] and (other["agent"], other["model"]) == (
                    ev["agent"],
                    ev["model"],
                ):
                    out.append(
                        f"Этап {stage}: оценщик и «{ROLES[key].title}» на одной и той же модели"
                    )
        return out
