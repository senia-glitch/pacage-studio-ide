"""Механические проверки артефактов цепочки «Продукт → Фронтенд» (без LLM).

Проверяет файлы `.studio/chain/*.json`: ссылки, источники, покрытие потребностей, словарь,
сквозные пути, решения и контракт фронтенда. Сливает параллельные черновики исследования
с перенумерацией идентификаторов. CLI: `studio chain check | merge-research N | summary`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CHAIN_DIR = Path(".studio") / "chain"

FILES = (
    "chain",
    "domain",
    "research",
    "needs",
    "product",
    "requirements",
    "remarks",
    "decisions",
    "contract",
    "test-report",
    "acceptance",
)

# Префикс идентификатора → где такие объекты живут: (файл, путь к списку).
COLLECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "role": ("domain", ("roles",)),
    "src": ("research", ("sources",)),
    "fact": ("research", ("facts",)),
    "comp": ("research", ("competitors",)),
    "need": ("needs", ("needs",)),
    "var": ("product", ("variants",)),
    "term": ("requirements", ("glossary",)),
    "ent": ("requirements", ("entities",)),
    "area": ("requirements", ("areas",)),
    "req": ("requirements", ("items",)),
    "journey": ("requirements", ("journeys",)),
    "rem": ("remarks", ("remarks",)),
    "dec": ("decisions", ("decisions",)),
}
# feat.N живут внутри конкурентов, iss.N — внутри прогонов теста: собираются отдельно.
ID_RE = re.compile(
    r"^(role|src|fact|comp|feat|need|var|term|ent|area|req|journey|rem|dec|iss)\.\d+$"
)
REF_KEY_RE = re.compile(r"(_id|_ids)$")
EXTRA_REF_KEYS = {"selected", "overridden_by"}


@dataclass
class Finding:
    level: str  # error | blocking | warning
    check: str
    where: str
    message: str

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "check": self.check,
            "where": self.where,
            "message": self.message,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, check: str, where: str, message: str) -> None:
        self.findings.append(Finding(level, check, where, message))

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)


# ---------------------------------------------------------------- загрузка


def load_all(root: Path, report: Report) -> dict[str, dict]:
    data: dict[str, dict] = {}
    for name in FILES:
        path = root / f"{name}.json"
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            report.add("error", "json", f"{name}.json", f"файл не читается как JSON: {exc}")
            continue
        if not isinstance(value, dict):
            report.add("error", "json", f"{name}.json", "в корне ожидается объект")
            continue
        if value.get("version") != 1:
            report.add("error", "version", f"{name}.json", 'в корне нужен "version": 1')
        data[name] = value
    return data


def items(data: dict[str, dict], file: str, *path: str) -> list[dict]:
    node = data.get(file)
    for key in path:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    if not isinstance(node, list):
        return []
    return [x for x in node if isinstance(x, dict)]


def all_ids(data: dict[str, dict], report: Report) -> dict[str, str]:
    """id → файл. Попутно ловит дубли и неверный вид идентификатора."""
    known: dict[str, str] = {}

    def register(obj: dict, file: str, prefix: str) -> None:
        oid = obj.get("id")
        if not isinstance(oid, str) or not ID_RE.match(oid) or not oid.startswith(prefix + "."):
            report.add(
                "error", "id", file, f"неверный идентификатор {oid!r}, ожидается {prefix}.<номер>"
            )
            return
        if oid in known:
            report.add("error", "duplicate-id", file, f"идентификатор {oid} встречается дважды")
            return
        known[oid] = file

    for prefix, (file, path) in COLLECTIONS.items():
        for obj in items(data, file, *path):
            register(obj, f"{file}.json", prefix)
    for comp in items(data, "research", "competitors"):
        for feat in comp.get("features") or []:
            if isinstance(feat, dict):
                register(feat, "research.json", "feat")
    for run in items(data, "test-report", "runs"):
        for issue in run.get("issues") or []:
            if isinstance(issue, dict):
                register(issue, "test-report.json", "iss")
    return known


# ---------------------------------------------------------------- проверки


def check_refs(data: dict[str, dict], known: dict[str, str], report: Report) -> None:
    """Поля *_id / *_ids, selected, overridden_by со значением вида prefix.N должны существовать."""

    def walk(node, file: str, trail: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{trail}.{key}" if trail else key
                if REF_KEY_RE.search(key) or key in EXTRA_REF_KEYS:
                    values = value if isinstance(value, list) else [value]
                    for v in values:
                        if isinstance(v, str) and ID_RE.match(v) and v not in known:
                            report.add(
                                "error", "ref", f"{file}: {here}", f"ссылка на несуществующий {v}"
                            )
                walk(value, file, here)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                label = (
                    value.get("id") if isinstance(value, dict) and value.get("id") else str(index)
                )
                walk(value, file, f"{trail}[{label}]")

    for name, value in data.items():
        walk(value, f"{name}.json", "")


def active(obj: dict) -> bool:
    return obj.get("status", "active") != "removed"


def latest_decisions(data: dict[str, dict], step: int) -> dict[str, dict]:
    """target_id → действующее решение шага (последнее, не переигранное)."""
    out: dict[str, dict] = {}
    for dec in items(data, "decisions", "decisions"):
        if dec.get("step") != step or dec.get("overridden_by"):
            continue
        target = dec.get("target_id")
        if isinstance(target, str):
            out[target] = dec
    return out


def accepted(data: dict[str, dict], step: int) -> set[str] | None:
    """Принятые на шаге объекты; None — решений этого шага ещё нет."""
    decs = latest_decisions(data, step)
    if not decs:
        return None
    return {t for t, d in decs.items() if d.get("decision") in ("accept", "select")}


def check_research(data: dict[str, dict], report: Report) -> None:
    for fact in items(data, "research", "facts"):
        if not fact.get("source_ids"):
            report.add(
                "error", "source", f"research.json: {fact.get('id')}", "у факта нет source_ids"
            )
        if fact.get("stream") not in ("needs", "competitors", "norms"):
            report.add(
                "error",
                "enum",
                f"research.json: {fact.get('id')}",
                "stream: needs | competitors | norms",
            )
    for comp in items(data, "research", "competitors"):
        if not comp.get("source_ids"):
            report.add(
                "error", "source", f"research.json: {comp.get('id')}", "у конкурента нет source_ids"
            )
        for feat in comp.get("features") or []:
            if isinstance(feat, dict) and not feat.get("source_ids"):
                report.add(
                    "error",
                    "source",
                    f"research.json: {feat.get('id')}",
                    "у функции конкурента нет source_ids",
                )
    seen: dict[str, str] = {}
    for src in items(data, "research", "sources"):
        url = str(src.get("url") or "")
        if not url.startswith(("http://", "https://")):
            report.add(
                "error",
                "source",
                f"research.json: {src.get('id')}",
                "url должен начинаться с http(s)://",
            )
        if url in seen:
            report.add(
                "warning",
                "duplicate-source",
                f"research.json: {src.get('id')}",
                f"тот же url, что у {seen[url]}",
            )
        seen.setdefault(url, str(src.get("id")))


def check_needs(data: dict[str, dict], report: Report) -> None:
    for need in items(data, "needs", "needs"):
        where = f"needs.json: {need.get('id')}"
        if not need.get("fact_ids"):
            report.add("error", "fact", where, "у потребности нет fact_ids")
        for key, allowed in (
            ("frequency", {"high", "medium", "low"}),
            ("severity", {"high", "medium", "low"}),
            ("coverage", {"none", "partial", "full"}),
        ):
            if need.get(key) not in allowed:
                report.add("error", "enum", where, f"{key}: {' | '.join(sorted(allowed))}")


def selected_variant(data: dict[str, dict]) -> dict | None:
    sel = (data.get("product") or {}).get("selected")
    for var in items(data, "product", "variants"):
        if var.get("id") == sel:
            return var
    return None


def check_product(data: dict[str, dict], report: Report) -> None:
    ok_needs = accepted(data, 3)
    for var in items(data, "product", "variants"):
        if not var.get("need_ids"):
            report.add("error", "need", f"product.json: {var.get('id')}", "у варианта нет need_ids")
        if ok_needs is not None:
            for nid in var.get("need_ids") or []:
                if nid not in ok_needs:
                    report.add(
                        "warning",
                        "need",
                        f"product.json: {var.get('id')}",
                        f"{nid} не принята на шаге 3",
                    )


def word_stem(word: str) -> str:
    """Грубая основа слова, чтобы ловить падежи: «заявка» → «заявк» (найдёт «заявки», «заявкой»).

    Для фраз основа берётся только у последнего слова; короткие основы не обрезаются,
    чтобы не ловить лишнее.
    """
    word = word.strip()
    head, _, last = word.rpartition(" ")
    stem = re.sub(r"[аяоеёыиуюйьъ]{1,2}$", "", last, flags=re.IGNORECASE)
    if len(stem) < 4:
        stem = last
    return f"{head} {stem}" if head else stem


def check_requirements(data: dict[str, dict], report: Report) -> None:
    reqs = [r for r in items(data, "requirements", "items") if active(r)]
    for req in reqs:
        where = f"requirements.json: {req.get('id')}"
        if req.get("type") not in ("mandatory", "proposed"):
            report.add("error", "enum", where, "type: mandatory | proposed")
        if not req.get("need_ids"):
            report.add("error", "need", where, "у требования нет need_ids")
        for key in ("title", "benefit"):
            if not str(req.get(key) or "").strip():
                report.add("error", "field", where, f"пустое поле {key}")
        just = req.get("justification") or []
        if not just:
            report.add("error", "justification", where, "нет обоснования")
        for j in just:
            if not isinstance(j, dict):
                continue
            basis = j.get("basis")
            if basis not in ("competitors", "pain", "norm", "logic"):
                report.add("error", "enum", where, "basis: competitors | pain | norm | logic")
            elif basis != "logic" and not j.get("source_ids"):
                report.add("error", "source", where, f"обоснование «{basis}» без source_ids")

    # покрытие потребностей выбранного варианта
    var = selected_variant(data)
    if var is not None and "requirements" in data:
        covered = {nid for r in reqs for nid in r.get("need_ids") or []}
        for nid in var.get("need_ids") or []:
            if nid not in covered:
                report.add(
                    "blocking",
                    "coverage",
                    "requirements.json",
                    f"потребность {nid} выбранного варианта не закрыта ни одним требованием",
                )

    # запрещённые синонимы словаря
    for term in items(data, "requirements", "glossary"):
        for syn in term.get("forbidden_synonyms") or []:
            if not isinstance(syn, str) or not syn.strip():
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(word_stem(syn))}\w*", re.IGNORECASE)
            for req in reqs:
                text = f"{req.get('title', '')} {req.get('description', '')}"
                if pattern.search(text):
                    report.add(
                        "blocking",
                        "glossary",
                        f"requirements.json: {req.get('id')}",
                        f"«{syn}» запрещён словарём, используйте термин «{term.get('term')}»",
                    )

    # сквозные пути
    active_ids = {r.get("id") for r in reqs}
    journeys = items(data, "requirements", "journeys")
    for journey in journeys:
        for step in journey.get("steps") or []:
            if isinstance(step, dict) and step.get("req_id") not in active_ids:
                report.add(
                    "blocking",
                    "journey",
                    f"requirements.json: {journey.get('id')}",
                    f"шаг {step.get('n')} ссылается на неактивное или отсутствующее "
                    f"требование {step.get('req_id')}",
                )
    if var is not None and journeys:
        with_path = {j.get("role_id") for j in journeys}
        for rid in var.get("role_ids") or []:
            if rid not in with_path:
                report.add(
                    "warning",
                    "journey",
                    "requirements.json",
                    f"у роли {rid} нет ни одного сквозного пути",
                )


def check_remarks(data: dict[str, dict], report: Report) -> None:
    for rem in items(data, "remarks", "remarks"):
        if rem.get("severity") == "blocking" and rem.get("status", "open") == "open":
            report.add(
                "blocking",
                "remark",
                f"remarks.json: {rem.get('id')}",
                f"открытое блокирующее замечание шага {rem.get('step')}: {rem.get('text')}",
            )
        if rem.get("status") == "rejected" and not str(rem.get("resolution") or "").strip():
            report.add(
                "error",
                "remark",
                f"remarks.json: {rem.get('id')}",
                "отклонённое замечание без resolution",
            )


def check_decisions(data: dict[str, dict], report: Report) -> None:
    for dec in items(data, "decisions", "decisions"):
        where = f"decisions.json: {dec.get('id')}"
        if dec.get("decision") not in ("accept", "reject", "select"):
            report.add("error", "enum", where, "decision: accept | reject | select")
        if dec.get("by") not in ("human", "decider"):
            report.add("error", "enum", where, "by: human | decider")
        if not str(dec.get("reason") or "").strip():
            report.add("error", "field", where, "решение без reason")
        if dec.get("contested"):
            for key in ("pros", "cons", "extra_check"):
                if not dec.get(key):
                    report.add("error", "contested", where, f"спорное решение без {key}")


def check_contract(data: dict[str, dict], report: Report) -> None:
    contract = data.get("contract")
    if contract is None:
        return
    pages = items(data, "contract", "pages")
    endpoints = {e.get("call"): e for e in items(data, "contract", "endpoints")}
    in_ui: set[str] = set()
    for page in pages:
        in_ui.update(page.get("req_ids") or [])
        calls = list(page.get("calls") or [])
        for block in page.get("blocks") or []:
            if isinstance(block, dict):
                in_ui.update(block.get("req_ids") or [])
                calls += block.get("calls") or []
        for call in calls:
            if call not in endpoints:
                report.add(
                    "error",
                    "contract",
                    f"contract.json: {page.get('name')}",
                    f"вызова {call} нет в endpoints",
                )
    for call, ep in endpoints.items():
        if not ep.get("mock"):
            report.add("error", "contract", f"contract.json: {call}", "у эндпоинта нет mock")
    chosen = accepted(data, 8)
    if chosen is not None:
        reqs = {r.get("id"): r for r in items(data, "requirements", "items")}
        for rid in sorted(chosen):
            req = reqs.get(rid)
            if req and not req.get("no_ui") and rid not in in_ui:
                report.add(
                    "error",
                    "contract",
                    "contract.json",
                    f"выбранное требование {rid} не попало ни в одну страницу или блок",
                )


CHECKS = (
    check_research,
    check_needs,
    check_product,
    check_requirements,
    check_remarks,
    check_decisions,
    check_contract,
)


def validate(root: Path) -> Report:
    report = Report()
    data = load_all(root, report)
    known = all_ids(data, report)
    check_refs(data, known, report)
    for check in CHECKS:
        check(data, report)
    return report


# ---------------------------------------------------------------- слияние исследования


def merge_research(root: Path, run: int) -> dict:
    """Влить .studio/chain/research/run-<run>-<stream>.json в research.json с новой нумерацией.

    Черновики прогона пишут исследователи параллельно, поэтому их идентификаторы локальные
    (src.1, fact.1 в каждом файле). Здесь они перенумеровываются и ссылки внутри черновика
    переписываются; источники с уже известным url не дублируются.
    """
    target = root / "research.json"
    base = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"version": 1}
    for key in ("sources", "facts", "competitors"):
        base.setdefault(key, [])

    def next_number(prefix: str, pool: list[dict]) -> int:
        nums = [
            int(x["id"].split(".")[1])
            for x in pool
            if str(x.get("id", "")).startswith(prefix + ".")
        ]
        return max(nums, default=0) + 1

    feats = [f for c in base["competitors"] for f in c.get("features") or []]
    counters = {
        "src": next_number("src", base["sources"]),
        "fact": next_number("fact", base["facts"]),
        "comp": next_number("comp", base["competitors"]),
        "feat": next_number("feat", feats),
    }
    by_url = {s.get("url"): s.get("id") for s in base["sources"]}
    added = {"src": 0, "fact": 0, "comp": 0, "feat": 0}
    drafts = sorted((root / "research").glob(f"run-{run}-*.json"))
    if not drafts:
        raise ValueError(f"нет черновиков {root / 'research'}/run-{run}-*.json")

    for draft_path in drafts:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        stream = draft_path.stem.split("-", 2)[2]
        mapping: dict[str, str] = {}

        def fresh(prefix: str) -> str:
            value = f"{prefix}.{counters[prefix]}"
            counters[prefix] += 1
            added[prefix] += 1
            return value

        for src in draft.get("sources") or []:
            if src.get("url") in by_url:
                mapping[src["id"]] = by_url[src["url"]]
                continue
            new_id = fresh("src")
            mapping[src["id"]] = new_id
            by_url[src.get("url")] = new_id
            base["sources"].append({**src, "id": new_id, "run": run})

        def remap(ids, mapping=mapping):
            return [mapping.get(i, i) for i in ids or []]

        for fact in draft.get("facts") or []:
            base["facts"].append(
                {
                    **fact,
                    "id": fresh("fact"),
                    "stream": fact.get("stream") or stream,
                    "source_ids": remap(fact.get("source_ids")),
                    "run": run,
                }
            )
        for comp in draft.get("competitors") or []:
            features = [
                {**f, "id": fresh("feat"), "source_ids": remap(f.get("source_ids"))}
                for f in comp.get("features") or []
            ]
            base["competitors"].append(
                {
                    **comp,
                    "id": fresh("comp"),
                    "source_ids": remap(comp.get("source_ids")),
                    "features": features,
                    "run": run,
                }
            )

    target.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "run": run,
        "drafts": [p.name for p in drafts],
        "added": added,
        "new_total": added["fact"] + added["comp"] + added["feat"],
    }


# ---------------------------------------------------------------- вывод


def summary(root: Path) -> dict:
    report = Report()
    data = load_all(root, report)
    out: dict[str, dict] = {}
    for prefix, (file, path) in COLLECTIONS.items():
        lst = items(data, file, *path)
        if lst:
            out.setdefault(file, {})[prefix] = len(lst)
    return out


def format_report(report: Report) -> str:
    """Отчёт проверки для терминала: ошибки, блокирующие, предупреждения и итог."""
    labels = {"error": "ОШИБКА", "blocking": "БЛОКИРУЕТ", "warning": "предупр."}
    lines = [
        f"{labels[level]:9} [{f.check}] {f.where}: {f.message}"
        for level in ("error", "blocking", "warning")
        for f in report.findings
        if f.level == level
    ]
    lines.append(
        f"ошибок: {report.count('error')}, блокирующих: {report.count('blocking')}, "
        f"предупреждений: {report.count('warning')}"
    )
    return "\n".join(lines)


def exit_code(report: Report) -> int:
    """0 — чисто, 1 — есть ошибки, 2 — ошибок нет, но есть блокирующие замечания."""
    if report.count("error"):
        return 1
    return 2 if report.count("blocking") else 0


def report_json(report: Report) -> dict:
    return {
        "findings": [f.as_dict() for f in report.findings],
        "errors": report.count("error"),
        "blocking": report.count("blocking"),
        "warnings": report.count("warning"),
    }
