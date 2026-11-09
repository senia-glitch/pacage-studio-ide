"""Цепочка «Продукт → Фронтенд»: проверки артефактов, слияние исследования, установка в OpenCode."""

from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from pathlib import Path

import pytest

from studio import cli, productchain
from studio.productchain import check
from studio.productchain.install import ASSETS, asset_files

ROOT = Path(__file__).resolve().parent.parent


def write(root: Path, name: str, value: dict) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def edit(root: Path, name: str, fn) -> None:
    path = root / name
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def good_chain(root: Path) -> None:
    write(root, "domain.json", {"version": 1, "raw_idea": "сайт для автосервиса", "domain": "автосервисы",
                                "roles": [{"id": "role.1", "name": "Клиент", "description": "", "run": 1}],
                                "search_directions": []})
    write(root, "research.json", {"version": 1,
        "sources": [{"id": "src.1", "url": "https://example.com/r", "title": "Отзывы", "kind": "review",
                     "accessed": "2026-11-09", "run": 2}],
        "facts": [{"id": "fact.1", "stream": "needs", "kind": "pain", "role_id": "role.1",
                   "text": "не дозвониться", "evidence": "", "source_ids": ["src.1"], "run": 2}],
        "competitors": [{"id": "comp.1", "name": "А", "url": "https://example.com/a", "segment": "",
                         "source_ids": ["src.1"], "run": 2,
                         "features": [{"id": "feat.1", "name": "Запись", "description": "",
                                       "source_ids": ["src.1"]}]}]})
    write(root, "needs.json", {"version": 1, "needs": [
        {"id": "need.1", "role_id": "role.1", "statement": "записаться без звонка", "fact_ids": ["fact.1"],
         "frequency": "high", "severity": "high", "coverage": "partial", "covered_by": ["comp.1"],
         "required_by_norm": False, "rank": 1, "status": "active", "run": 3}]})
    write(root, "product.json", {"version": 1, "variants": [
        {"id": "var.1", "run": 4, "statement": "запись онлайн", "role_ids": ["role.1"], "need_ids": ["need.1"],
         "differentiation": "", "out_of_scope": [], "differs_from_raw_idea": ""}],
        "selected": "var.1", "decision_id": "dec.1"})
    write(root, "requirements.json", {"version": 1, "round": 1,
        "glossary": [{"id": "term.1", "term": "Запись", "definition": "", "forbidden_synonyms": ["заявка"]}],
        "entities": [{"id": "ent.1", "name": "Запись", "term_id": "term.1", "states": ["new"]}],
        "areas": [{"id": "area.1", "name": "Запись", "domain": "booking"}],
        "items": [{"id": "req.1", "area_id": "area.1", "type": "mandatory", "title": "Онлайн-запись",
                   "description": "клиент создаёт запись", "role_ids": ["role.1"], "need_ids": ["need.1"],
                   "entity_ids": ["ent.1"], "benefit": "без звонка",
                   "justification": [{"basis": "pain", "text": "жалобы", "source_ids": ["src.1"]}],
                   "acceptance": ["занятый слот выбрать нельзя"], "no_ui": False, "status": "active",
                   "run": 5}],
        "journeys": [{"id": "journey.1", "role_id": "role.1", "name": "записаться",
                      "steps": [{"n": 1, "action": "выбрать время", "req_id": "req.1"}]}]})
    write(root, "decisions.json", {"version": 1, "decisions": [
        {"id": "dec.1", "step": 4, "target_id": "var.1", "decision": "select", "by": "human",
         "contested": False, "reason": "закрывает главную потребность", "overridden_by": None},
        {"id": "dec.2", "step": 8, "target_id": "req.1", "decision": "accept", "by": "decider",
         "contested": False, "reason": "обязательно", "overridden_by": None}]})


@pytest.fixture
def chain(tmp_path: Path) -> Path:
    root = tmp_path / ".studio" / "chain"
    good_chain(root)
    return root


def levels(root: Path, level: str) -> set[str]:
    return {f.check for f in productchain.validate(root).findings if f.level == level}


# ---------------------------------------------------------------- проверки


def test_чистая_цепочка_без_находок(chain):
    report = productchain.validate(chain)
    assert [f.as_dict() for f in report.findings] == []
    assert productchain.exit_code(report) == 0
    assert "ошибок: 0" in productchain.format_report(report)


def test_пустая_папка_без_находок(tmp_path):
    assert productchain.validate(tmp_path).findings == []


def test_битая_ссылка(chain):
    edit(chain, "needs.json", lambda d: d["needs"][0]["fact_ids"].append("fact.99"))
    assert "ref" in levels(chain, "error")


def test_дубль_и_неверный_идентификатор(chain):
    edit(chain, "needs.json", lambda d: d["needs"].append({**d["needs"][0]}))
    edit(chain, "domain.json", lambda d: d["roles"].append({"id": "role-2", "name": "x"}))
    errors = levels(chain, "error")
    assert {"duplicate-id", "id"} <= errors


def test_неверный_json_и_версия(chain):
    (chain / "needs.json").write_text("{", encoding="utf-8")
    edit(chain, "domain.json", lambda d: d.update(version=2))
    errors = levels(chain, "error")
    assert {"json", "version"} <= errors


def test_факт_конкурент_и_функция_без_источника(chain):
    def fn(d):
        d["facts"][0]["source_ids"] = []
        d["facts"][0]["stream"] = "other"
        d["competitors"][0]["source_ids"] = []
        d["competitors"][0]["features"][0]["source_ids"] = []
        d["sources"].append({"id": "src.2", "url": "ftp://x", "title": ""})
        d["sources"].append({"id": "src.3", "url": "https://example.com/r", "title": ""})
    edit(chain, "research.json", fn)
    report = productchain.validate(chain)
    sources = [f for f in report.findings if f.check == "source"]
    assert len(sources) == 4
    assert "enum" in levels(chain, "error")
    assert "duplicate-source" in levels(chain, "warning")


def test_потребность_без_фактов_и_оценок(chain):
    edit(chain, "needs.json", lambda d: d["needs"][0].update(fact_ids=[], severity="huge"))
    errors = levels(chain, "error")
    assert {"fact", "enum"} <= errors


def test_вариант_с_непринятой_потребностью(chain):
    def fn(d):
        d["decisions"].append({"id": "dec.3", "step": 3, "target_id": "need.1", "decision": "reject",
                               "by": "human", "reason": "не наше"})
    edit(chain, "decisions.json", fn)
    edit(chain, "product.json", lambda d: d["variants"].append(
        {"id": "var.2", "statement": "", "role_ids": [], "need_ids": []}))
    assert "need" in levels(chain, "warning")
    assert "need" in levels(chain, "error")


def test_требование_без_полей(chain):
    def fn(d):
        d["items"][0].update(type="must", need_ids=[], benefit="", justification=[])
    edit(chain, "requirements.json", fn)
    errors = levels(chain, "error")
    assert {"enum", "need", "field", "justification"} <= errors


def test_обоснование_logic_без_источника_допустимо(chain):
    edit(chain, "requirements.json", lambda d: d["items"][0].update(
        justification=[{"basis": "logic", "text": "без этого путь рвётся"}]))
    assert "source" not in levels(chain, "error")


def test_обоснование_без_источника_и_неверный_basis(chain):
    edit(chain, "requirements.json", lambda d: d["items"][0].update(
        justification=[{"basis": "pain", "text": "x"}, {"basis": "magic", "text": "y"}]))
    errors = levels(chain, "error")
    assert {"source", "enum"} <= errors


def test_незакрытая_потребность_и_рваный_путь(chain):
    edit(chain, "requirements.json", lambda d: d["items"][0].update(status="removed"))
    assert {"coverage", "journey"} <= levels(chain, "blocking")


@pytest.mark.parametrize("title", ["Онлайн-заявки на ремонт", "Заявка", "Приём заявкой"])
def test_запрещённый_синоним_в_любом_падеже(chain, title):
    edit(chain, "requirements.json", lambda d: d["items"][0].update(title=title))
    assert "glossary" in levels(chain, "blocking")


def test_основа_слова():
    assert check.word_stem("заявка") == "заявк"
    assert check.word_stem("бронь") == "брон"  # найдёт «брони», «бронью»
    assert check.word_stem("ось") == "ось"  # короткая основа не обрезается
    assert check.word_stem("личный кабинет") == "личный кабинет"


def test_роль_без_сквозного_пути(chain):
    edit(chain, "domain.json", lambda d: d["roles"].append(
        {"id": "role.2", "name": "Мастер", "description": "", "run": 1}))
    edit(chain, "product.json", lambda d: d["variants"][0]["role_ids"].append("role.2"))
    assert "journey" in levels(chain, "warning")


def test_замечания(chain):
    write(chain, "remarks.json", {"version": 1, "remarks": [
        {"id": "rem.1", "step": 6, "severity": "blocking", "target_ids": ["req.1"], "text": "нет источника",
         "status": "open"},
        {"id": "rem.2", "step": 6, "severity": "minor", "target_ids": ["req.1"], "text": "x",
         "status": "rejected", "resolution": ""}]})
    assert "remark" in levels(chain, "blocking")
    assert "remark" in levels(chain, "error")


def test_решения(chain):
    def fn(d):
        d["decisions"][1].update(contested=True)
        d["decisions"].append({"id": "dec.3", "step": 8, "target_id": "req.1", "decision": "maybe",
                               "by": "robot", "reason": ""})
    edit(chain, "decisions.json", fn)
    errors = levels(chain, "error")
    assert {"contested", "enum", "field"} <= errors


def test_переигранное_решение_не_действует(chain):
    def fn(d):
        d["decisions"][1]["overridden_by"] = "dec.3"
        d["decisions"].append({"id": "dec.3", "step": 8, "target_id": "req.1", "decision": "reject",
                               "by": "human", "reason": "передумал", "overridden_by": None})
    edit(chain, "decisions.json", fn)
    assert check.accepted(json_all(chain), 8) == set()


def json_all(root: Path) -> dict:
    return check.load_all(root, check.Report())


def test_контракт(chain):
    write(chain, "contract.json", {"version": 1, "status": "draft", "frozen_at": None,
        "pages": [{"name": "booking", "req_ids": [], "calls": ["GET /x"],
                   "blocks": [{"name": "SlotPicker", "req_ids": [], "calls": ["GET /slots"]}]}],
        "endpoints": [{"call": "GET /slots"}]})
    report = productchain.validate(chain)
    messages = [f.message for f in report.findings if f.check == "contract"]
    assert any("GET /x" in m for m in messages)
    assert any("mock" in m for m in messages)
    assert any("req.1" in m for m in messages)


def test_контракт_покрывает_выбранное(chain):
    write(chain, "contract.json", {"version": 1, "status": "draft", "frozen_at": None,
        "pages": [{"name": "booking", "req_ids": ["req.1"], "calls": [],
                   "blocks": [{"name": "SlotPicker", "req_ids": ["req.1"], "calls": ["GET /slots"]}]}],
        "endpoints": [{"call": "GET /slots", "mock": "frontend/src/mocks/slots.json"}]})
    assert "contract" not in levels(chain, "error")


def test_ссылки_на_тестовые_замечания(chain):
    write(chain, "test-report.json", {"version": 1, "runs": [{"run": 1, "issues": [
        {"id": "iss.1", "journey_id": "journey.1", "req_ids": ["req.1"]}]}]})
    assert levels(chain, "error") == set()


def test_report_json(chain):
    edit(chain, "requirements.json", lambda d: d["items"][0].update(title="заявка"))
    report = productchain.validate(chain)
    data = productchain.report_json(report)
    assert data["blocking"] == 1 and data["errors"] == 0
    assert productchain.exit_code(report) == 2


# ---------------------------------------------------------------- слияние исследования


def test_слияние_перенумеровывает_и_не_дублирует(chain):
    write(chain, "research/run-2-needs.json", {
        "sources": [{"id": "src.1", "url": "https://example.com/r", "title": "дубль"},
                    {"id": "src.2", "url": "https://example.com/new", "title": "новый"}],
        "facts": [{"id": "fact.1", "kind": "pain", "role_id": "role.1", "text": "долго ждать",
                   "evidence": "", "source_ids": ["src.1", "src.2"]}]})
    write(chain, "research/run-2-competitors.json", {
        "sources": [{"id": "src.1", "url": "https://example.com/b", "title": "Б"}],
        "competitors": [{"id": "comp.1", "name": "Б", "url": "https://example.com/b", "segment": "",
                         "source_ids": ["src.1"],
                         "features": [{"id": "feat.1", "name": "Статус", "source_ids": ["src.1"]}]}]})
    result = productchain.merge_research(chain, 2)
    assert result["added"] == {"src": 2, "fact": 1, "comp": 1, "feat": 1}
    assert result["new_total"] == 3
    data = json.loads((chain / "research.json").read_text(encoding="utf-8"))
    fact = data["facts"][-1]
    assert fact["id"] == "fact.2" and fact["stream"] == "needs"
    # черновики вливаются по алфавиту: src.2 достался конкуренту, новый источник фактов — src.3
    assert fact["source_ids"] == ["src.1", "src.3"]
    assert data["competitors"][-1]["features"][0]["id"] == "feat.2"
    assert productchain.validate(chain).findings == []


def test_слияние_с_нуля(tmp_path):
    write(tmp_path, "research/run-1-norms.json", {
        "sources": [{"id": "src.1", "url": "https://example.com/law", "title": "закон"}],
        "facts": [{"id": "fact.1", "kind": "norm", "role_id": None, "text": "согласие на ПДн",
                   "source_ids": ["src.1"]}]})
    result = productchain.merge_research(tmp_path, 1)
    assert result["added"]["fact"] == 1
    data = json.loads((tmp_path / "research.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["facts"][0]["stream"] == "norms"


def test_слияние_без_черновиков(tmp_path):
    with pytest.raises(ValueError):
        productchain.merge_research(tmp_path, 5)


def test_summary(chain):
    data = productchain.summary(chain)
    assert data["requirements"]["req"] == 1
    assert data["decisions"]["dec"] == 2


# ---------------------------------------------------------------- установка и ассеты


def test_установка_и_повтор(tmp_path):
    dest = tmp_path / ".opencode"
    first = productchain.install(dest)
    assert len(first.written) == len(asset_files()) >= 18
    assert (dest / "agents" / "chain.md").is_file()
    assert (dest / "skills" / "chain-formats" / "SKILL.md").is_file()
    second = productchain.install(dest)
    assert second.written == [] and len(second.unchanged) == len(first.written)


def test_правка_пользователя_не_перезаписывается_без_force(tmp_path):
    dest = tmp_path / ".opencode"
    productchain.install(dest)
    target = dest / "agents" / "critic.md"
    target.write_text("моя правка", encoding="utf-8")
    assert productchain.install(dest).skipped == [target]
    assert target.read_text(encoding="utf-8") == "моя правка"
    assert productchain.install(dest, force=True).written == [target]
    assert target.read_text(encoding="utf-8") != "моя правка"


def frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, path
    return match.group(1)


def test_агенты_и_скиллы_согласованы():
    skills = {p.parent.name for p in (ASSETS / "skills").glob("*/SKILL.md")}
    agents = {p.stem for p in (ASSETS / "agents").glob("*.md")}
    assert agents == {"chain", "researcher", "analyst", "product", "critic", "reviewer", "decider"}
    used: set[str] = set()
    for path in sorted(ASSETS.rglob("*.md")):
        head = frontmatter(path)
        # описание в кавычках: двоеточия внутри иначе ломают YAML
        assert re.search(r'^description: ".+"$', head, re.M), path
        if path.parent.name != "agents":
            continue
        # права в формате V1 (permission: bash/task/skill): его понимают и OpenCode 1.x, и V2
        assert "permission:" in head and "permissions:" not in head, path
        rules = permission_rules(head)
        assert rules["skill"].get("*") == "deny", path
        for name, effect in rules["skill"].items():
            if name != "*":
                assert name in skills and effect == "allow", (path, name)
                used.add(name)
        for name in rules["task"]:
            assert name == "*" or name in agents, (path, name)
        if path.stem != "chain":
            assert rules["bash"] == {"*": "deny"} and rules["task"] == {"*": "deny"}, path
    assert used == skills


def permission_rules(head: str) -> dict[str, dict[str, str]]:
    """Разобрать блок permission: ключ → {шаблон: действие} (строка «key: deny» = {"*": deny})."""
    rules: dict[str, dict[str, str]] = {"bash": {}, "task": {}, "skill": {}}
    block = head.split("permission:\n", 1)[1]
    current = None
    for line in block.splitlines():
        if not line.startswith("  "):
            break
        if not line.startswith("    "):
            key, _, value = line.strip().partition(":")
            current = key
            if value.strip():
                rules[key] = {"*": value.strip()}
            continue
        name, _, effect = line.strip().rpartition(":")
        rules[current][name.strip().strip('"')] = effect.strip()
    return rules


def test_инструкции_ссылаются_на_команды_студии():
    text = (ASSETS / "agents" / "chain.md").read_text(encoding="utf-8")
    assert "studio chain check" in text and "studio chain merge-research" in text
    assert "scripts/" not in text


def test_ассеты_упакованы():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["studio.productchain"]
    for relative in asset_files():
        name = f"assets/opencode/{relative.as_posix()}"
        assert any(fnmatch.fnmatch(name, pattern) for pattern in patterns), name


# ---------------------------------------------------------------- CLI


def test_cli_install_project(tmp_path, capsys):
    assert cli.main(["chain", "install", "--project", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "записано: agents/chain.md" in out
    (tmp_path / ".opencode" / "agents" / "chain.md").write_text("x", encoding="utf-8")
    assert cli.main(["chain", "install", "--project", str(tmp_path)]) == 0
    assert "пропущено" in capsys.readouterr().out


def test_cli_install_dest(tmp_path, capsys):
    dest = tmp_path / "opencode"
    assert cli.main(["chain", "install", "--dest", str(dest)]) == 0
    assert (dest / "skills" / "decision-making" / "SKILL.md").is_file()
    assert "Папка OpenCode" in capsys.readouterr().out


def test_cli_check_merge_summary(tmp_path, monkeypatch, capsys):
    root = tmp_path / ".studio" / "chain"
    good_chain(root)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["chain", "check"]) == 0
    assert "ошибок: 0" in capsys.readouterr().out
    edit(root, "requirements.json", lambda d: d["items"][0].update(title="заявка"))
    assert cli.main(["chain", "check", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["blocking"] == 1
    assert cli.main(["chain", "summary"]) == 0
    assert '"req": 1' in capsys.readouterr().out
    assert cli.main(["chain", "merge-research", "7"]) == 1
    assert "нет черновиков" in capsys.readouterr().out
    write(root, "research/run-7-needs.json", {"sources": [], "facts": []})
    assert cli.main(["chain", "merge-research", "7"]) == 0


def test_cli_chain_без_подкоманды(capsys):
    assert cli.main(["chain"]) == 0
    assert "studio chain check" in capsys.readouterr().out


def test_cli_help_упоминает_chain(capsys):
    assert cli.main(["help"]) == 0
    assert "chain install" in capsys.readouterr().out
