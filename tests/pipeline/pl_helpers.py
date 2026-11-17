"""Общие помощники тестов конвейера (conftest.py в подпапке tests запрещён — он занял бы имя)."""

from studio.pipeline import ChangeLog, GateKeeper, PipelineSettings, ProjectBank


def make(tmp_path):
    bank = ProjectBank.at(tmp_path)
    settings = PipelineSettings(tmp_path)
    return bank, settings, GateKeeper(bank, settings, tmp_path), ChangeLog(bank, settings, tmp_path)


def idea(bank):
    """Маленькая идея: область → граница → проблема → требование → критерий (черновики)."""
    area = bank.add("area", "Запись к врачу")
    boundary = bank.add("boundary", "Только запись, без оплаты", parents=[area["id"]])
    problem = bank.add("problem", "Не дозвониться в регистратуру", parents=[boundary["id"]])
    req = bank.add("requirement", "Система должна записывать пациента", parents=[problem["id"]])
    crit = bank.add("criterion", "Когда выбрано время, запись создана", parents=[req["id"]])
    return {"area": area, "boundary": boundary, "problem": problem, "req": req, "crit": crit}
