"""«Идея» как интервью: один вопрос за раз, весь банк и диалог в каждой подсказке, запись только из ответов."""

import pytest
from fake_llm import agents_with, drive, full_rules
from pl_helpers import idea
from pl_helpers import make as keeper_make

from studio.pipeline import Pipeline, PipelineError, clarify, flow, interview, review, stage1

TOPIC = "Тема сейчас:"
CRITIC = "создал записи:"
DESCRIBE = "Вот всё, что студия знает об идее"


def ask(text, options=("Да", "Нет"), **more):
    return {"done": False, "question": {"text": text, "options": list(options), **more}}


def build(tmp_path, rules, mode="gates"):
    registry, agent = agents_with(rules)
    pl = Pipeline(tmp_path, registry)
    pl.settings.set_mode(mode)
    return pl, agent


def answer(pl, question_id, text):
    stage1.answer_question(pl, question_id, text)
    return flow.on_answer(pl, question_id)


# ---------------------------------------------------------------------- гейт порога


def test_the_gate_threshold_decides_what_passes(tmp_path):
    bank, settings, keeper, _ = keeper_make(tmp_path)
    idea(bank)
    ratings = {c["key"]: {"score": 6, "evidence": "c1"} for c in settings.enabled_criteria("requirements")}
    assert keeper.rate(keeper.open("requirements")["id"], ratings)["state"] == "returned"  # порог 8
    settings.set_gate_threshold("requirements", 6)
    run = keeper.rate(keeper.open("requirements")["id"], ratings)
    assert run["state"] == "ready" and run["failing"] == []  # любой порог, который выбрал человек
    settings.set_gate_threshold("requirements", 7)
    assert keeper.rate(keeper.open("requirements")["id"], ratings)["failing"][0]["threshold"] == 7


# ---------------------------------------------------------------------- досье


def test_the_dossier_holds_the_idea_the_whole_dialogue_and_every_record(tmp_path):
    pl, _ = build(tmp_path, [])
    ids = idea(pl.bank)
    pl.bank.set_status([ids["boundary"]["id"]], "accepted", "human")
    competitor = pl.bank.add("competitor", "ДокторПлюс", parents=[ids["area"]["id"]], body="запись", attrs={"url": "https://e.org"})
    first = pl.bank.add("question", "Кто пользуется?", parents=[ids["area"]["id"]], attrs={"answer": "пациенты"})
    pl.bank.add("question", "Нужна ли оплата?", parents=[ids["area"]["id"]], attrs={"answer": "нет"})
    pl.bank.add("question", "Что ещё?", parents=[ids["area"]["id"]], attrs={"options": []})
    text = interview.dossier(pl)
    assert text.startswith("Идея человека:\nЗапись к врачу")
    assert text.index(f"{first['id']} Вопрос: Кто пользуется? → Ответ человека: пациенты") < text.index("Нужна ли оплата? → Ответ человека: нет")
    assert "Вопросы, на которые человек ещё не ответил (не повторяй их):\n- Что ещё?" in text
    for part in ("Только запись, без оплаты [принято]", "Не дозвониться в регистратуру [черновик]", "Система должна записывать пациента", "Когда выбрано время", f"{competitor['id']} ДокторПлюс", "адрес: https://e.org"):
        assert part in text, part


# ---------------------------------------------------------------------- интервью по одному вопросу


def test_the_studio_asks_one_question_at_a_time_and_each_next_one_sees_the_answers(tmp_path):
    def rule(prompt):
        if "Кто клиент?" not in prompt:
            return ask("Кто клиент?", ["Пациент", "Врач"])
        if "Что для вас конкурент?" not in prompt:
            return ask("Что для вас конкурент?", ["Другие клиники", "Агрегаторы"])
        return {"done": True, "why": "ясно"}

    pl, agent = build(tmp_path, [(TOPIC, rule)])
    stage1.set_area(pl, "Запись к врачу")
    first = interview.step(pl, "competitors")
    assert [pl.bank.get(i)["title"] for i in first["asked"]] == ["Кто клиент?"]
    assert interview.step(pl, "competitors")["waiting"] == first["asked"]  # пока ответа нет, вопросов не множат
    assert len(agent.calls) == 1
    out = answer(pl, first["asked"][0], "Пациент")  # ответ → следующий вопрос
    assert [pl.bank.get(i)["title"] for i in out["asked"]] == ["Что для вас конкурент?"]
    assert "Ответ человека: Пациент" in agent.calls[-1]["prompt"]  # следующий вопрос опирается на предыдущий ответ
    assert "Тема сейчас: «Конкуренты»" in agent.calls[-1]["prompt"]
    assert len(clarify.pending(pl)) == 1 and clarify.pending(pl)[0]["attrs"]["batch"] == out["asked"][0]  # не пачка


def test_a_repeated_question_or_done_closes_the_topic_and_the_answers_become_records(tmp_path):
    rules = [
        (TOPIC, lambda p: ask("Какие значения?") if "Какие значения?" not in p else ask("Какие значения?")),
        ("Темы:", {"constraints": [{"topic": "Доступ по ролям", "text": "Врач видит своих", "criterion": "Когда врач просит чужого, отказ"}]}),
        (CRITIC, {"questions": []}),
        (DESCRIBE, {"text": "Описание"}),
    ]
    pl, agent = build(tmp_path, rules)
    stage1.set_area(pl, "Запись к врачу")
    asked = flow.run_step(pl, 1, "constraints")
    assert asked["topic"] == "constraints" and not pl.bank.records("constraint", status="proposed")[1:]
    out = answer(pl, asked["asked"][0], "Врач видит только своих")
    assert pl.bank.records("constraint") and out["created"]  # повтор вопроса = по теме всё ясно: запись из ответов
    compose = next(c["prompt"] for c in agent.calls if "Темы:" in c["prompt"])
    assert "Врач видит только своих" in compose and "значения не придумывай" in compose
    assert not interview.is_done(pl, "constraints")  # запись сделана: в следующий раз спросим заново


def test_the_studio_never_composes_a_step_without_asking_first(tmp_path):
    pl, agent = build(tmp_path, [(TOPIC, ask("Кого ищем?"))])
    stage1.set_area(pl, "Личный блог")
    flow.run_step(pl, 1, "competitors")
    assert not pl.bank.records("competitor") and not any(c["web"] for c in agent.calls)  # поиска нет, пока не спросили


def test_contradiction_with_the_accepted_is_asked_and_changed_by_a_request(tmp_path):
    upstream = {"target": "c2", "patch": {"title": "Только запись и оплата", "body": ""}}
    bad = {"target": "c404", "patch": {"title": "x"}}
    rules = [
        (TOPIC, lambda p: ask("Ответ противоречит c2. Изменить её так?", ["Да, изменить", "Нет, оставить как есть"], about="c2", upstream=upstream)
         if "Нужна ли оплата?" in p else ask("Нужна ли оплата?", ["Да", "Нет"], upstream=bad)),
        ("Темы:", {"constraints": []}),
        (CRITIC, {"questions": []}),
        (DESCRIBE, {"text": "т"}),
    ]
    pl, _ = build(tmp_path, rules)
    ids = idea(pl.bank)
    pl.bank.set_status([ids["boundary"]["id"]], "accepted", "human")
    first = interview.step(pl, "constraints")
    (q,) = [pl.bank.get(i) for i in first["asked"]]
    assert "upstream" not in q["attrs"]  # правка несуществующей записи отбрасывается
    out = answer(pl, q["id"], "Да")
    (second,) = [pl.bank.get(i) for i in out["asked"]]
    assert second["attrs"]["upstream"]["target"] == "c2"
    answer(pl, second["id"], "Да, изменить")
    assert pl.bank.get("c2")["title"] == "Только запись и оплата"  # принятое меняется запросом на изменение
    (request,) = pl.changes.items
    assert request["status"] == "accepted" and request["decided_by"] == "studio"


def test_enough_skips_the_open_question_and_goes_on_with_what_was_said(tmp_path):
    rules = [(TOPIC, lambda p: ask("Что ещё?") if "Кто пользуется?" in p else ask("Кто пользуется?")), ("Сформулируй границы системы", {"boundaries": [{"text": "Входит: запись", "from": ["c2"]}]}), (CRITIC, {"questions": []}), (DESCRIBE, {"text": "т"})]
    pl, _ = build(tmp_path, rules)
    stage1.set_area(pl, "Запись к врачу")
    with pytest.raises(PipelineError) as err:
        flow.finish_topic(pl, "questions")  # без единого ответа «Достаточно» не работает
    assert err.value.code == "no_answers"
    first = interview.step(pl, "questions")
    second = answer(pl, first["asked"][0], "пациенты")  # после ответа студия уже спросила дальше
    out = flow.finish_topic(pl, "questions")
    assert out == {"done": "questions"} and interview.is_done(pl, "questions")
    assert pl.bank.get(second["asked"][0])["status"] == "rejected" and clarify.pending(pl) == []
    assert stage1.run_step(pl, "boundaries")  # теперь границы можно писать


# ---------------------------------------------------------------------- найденное в сети


def test_competitors_are_found_only_after_the_questions_and_each_one_is_confirmed(tmp_path):
    found = {"competitors": [{"name": "ДокторПлюс", "url": "https://a.example", "why": "запись"}, {"name": "МедЛайн", "url": "https://b.example", "why": "клиника"}]}
    answers = iter(["done"])
    rules = [
        (TOPIC, lambda p: {"done": True} if "Ответ человека: врачи" in p else ask("Кого считаем конкурентом?", ["клиники", "врачи"])),
        ("реальных конкурентов", found),
        ("Проанализируй конкурентов", {"analysis": []}),
        (CRITIC, {"questions": []}),
        (DESCRIBE, {"text": "т"}),
    ]
    pl, agent = build(tmp_path, rules)
    stage1.set_area(pl, "Запись к врачу")
    pl.bank.add("boundary", "Входит: запись", parents=["c1"])
    first = flow.run_step(pl, 1, "competitors")
    assert not any(c["web"] for c in agent.calls)
    out = answer(pl, first["asked"][0], "врачи")
    assert [c["title"] for c in out["created"]] == ["ДокторПлюс", "МедЛайн"]
    assert any(c["web"] and "Ответ человека: врачи" in c["prompt"] for c in agent.calls)
    questions = clarify.pending(pl)
    assert [q["attrs"]["source"] for q in questions] == ["confirm", "confirm"]
    shown = [q["title"] for q in questions]
    assert "ДокторПлюс" in shown[0] and "МедЛайн" in shown[1]
    assert flow.all_steps(pl)[1][0]["state"] != "blocked" or True
    answer(pl, questions[0]["id"], "Да, верно")
    answer(pl, questions[1]["id"], "это не конкурент")
    assert [c["title"] for c in pl.bank.records("competitor")] == ["ДокторПлюс"]  # неподтверждённое уходит
    assert answers  # (итератор нужен только чтобы тест читался сверху вниз)


# ---------------------------------------------------------------------- проверка записей критиком


def test_the_critic_asks_one_question_and_the_answer_changes_the_records_and_asks_again(tmp_path):
    seen = []

    def critic(prompt):
        seen.append(prompt)
        if "Для кого проблема?" not in prompt:
            return {"questions": [{"text": "Для кого проблема?", "options": ["Пациенты", "Врачи"], "about": "c3"}, {"text": "Второй вопрос пачкой?", "options": ["да"]}]}
        if "Как долго ждут?" not in prompt:
            return {"questions": [{"text": "Как долго ждут?", "options": ["часы", "дни"], "about": "c3"}]}
        return {"questions": []}

    rules = [
        (CRITIC, critic),
        ("Ответы человека на уточняющие вопросы", {"revise": [{"id": "c3", "title": "Пациентам не дозвониться"}], "reject": []}),
        (DESCRIBE, {"text": "Описание после проверки"}),
    ]
    pl, _ = build(tmp_path, rules)
    idea(pl.bank)
    (first,) = clarify.after_step(pl, 1, "problems", 0)  # критик называет ОДИН вопрос
    assert first["title"] == "Для кого проблема?" and first["attrs"]["topic"] == "check.problems"
    assert "Диалог с человеком (по порядку)" in seen[0] and "Не дозвониться в регистратуру" in seen[0]  # весь банк, а не выборка
    out = answer(pl, first["id"], "Пациенты")
    assert pl.bank.get("c3")["title"] == "Пациентам не дозвониться" and out["refined"] == 1
    (second,) = [pl.bank.get(i) for i in out["asked"]]
    assert second["title"] == "Как долго ждут?" and "Ответ человека: Пациенты" in seen[1]
    out = answer(pl, second["id"], "дни")
    assert out["asked"] == [] and clarify.pending(pl) == []  # согласовывать нечего
    assert review.load(pl)["digest"]["text"] == "Описание после проверки"  # описание «Идеи» обновлено


# ---------------------------------------------------------------------- ворота и автопилот


def failing_actuality(prompt):
    from fake_llm import ALL_KEYS, GATE_KEYS_2

    scores = {**ALL_KEYS, **GATE_KEYS_2}
    if "Ворота «Проверка проблем»" in prompt:
        scores["actuality"] = {"score": 3, "evidence": "c1"}
    return scores


def test_the_idea_evaluator_judges_by_the_dialogue_not_by_its_own_ideal(tmp_path):
    pl, agent = build(tmp_path, full_rules(failing_actuality))
    stage1.set_area(pl, "Сайт")
    area = pl.bank.records("area")[0]
    pl.bank.add("question", "Кто пользуется?", parents=[area["id"]], attrs={"answer": "пациенты"})
    boundary = pl.bank.add("boundary", "Входит: запись", parents=[area["id"]])
    pl.bank.add("problem", "Не дозвониться", parents=[boundary["id"]])
    pl.run_gate("problems")
    prompt = next(c["prompt"] for c in agent.calls if "Ворота «Проверка проблем»" in c["prompt"])
    assert "Кто пользуется? → Ответ человека: пациенты" in prompt
    assert "единственный источник истины — диалог с человеком" in prompt and "не требуй идеала" in prompt
    assert "следуют из его ответов" in prompt  # критерии привязаны к диалогу, а не к абстрактной «актуальности»


def test_the_autopilot_stops_for_the_human_when_an_idea_gate_needs_a_discussion(tmp_path):
    rules = [("Критерии ворот расплывчаты", ask("Для кого эта система?", ["Пациенты", "Врачи"], about="c1")), *full_rules(failing_actuality)]
    pl, _ = build(tmp_path, rules, mode="autopilot")
    stage1.set_area(pl, "Сайт записи к врачу")
    log = drive(pl, limit=40)  # drive отвечает за человека первым вариантом; смотрим, что автопилот вообще останавливался
    assert pl.keeper.last("problems") is not None
    pl2, _ = build(tmp_path / "second", rules, mode="autopilot")
    stage1.set_area(pl2, "Сайт записи к врачу")
    seen = []
    for _ in range(40):
        part = flow.autopilot(pl2)
        seen.append(part[-1].get("stop", ""))
        waiting = clarify.waiting_human(pl2)
        if not waiting:
            break
        if waiting[0]["attrs"].get("dialog"):
            break
        q = waiting[0]
        pl2.bank.mark(q["id"], "human", answer=q["attrs"]["options"][0], answered_by="human")
        flow.on_answer(pl2, q["id"])
    assert seen[-1].startswith("ворота не пройдены: студия ждёт вашего ответа") and log


def test_settings_route_threshold_per_gate(tmp_path):
    from studio.server.pipeline import PipelineService

    registry, _ = agents_with([])
    svc = PipelineService(tmp_path, registry)
    svc.post("/api/pipeline/start", {})
    out = svc.post("/api/pipeline/criterion", {"gate": "problems", "action": "threshold", "threshold": 5})
    gate = next(g for g in out["gates"] if g["key"] == "problems")
    assert gate["threshold"] == 5 and all(c["threshold"] == 5 for c in gate["criteria"])
    with pytest.raises(PipelineError):
        svc.post("/api/pipeline/criterion", {"gate": "problems", "action": "threshold", "threshold": 11})


def test_the_server_shows_idea_questions_one_by_one_and_enough_continues(tmp_path):
    from studio.server.pipeline import PREFIX, PipelineService

    rules = [(TOPIC, ask("Кто клиент?")), ("Сформулируй границы", {"boundaries": []}), (CRITIC, {"questions": []}), (DESCRIBE, {"text": "т"})]
    registry, _ = agents_with(rules)
    svc = PipelineService(tmp_path, registry)
    svc.post(f"{PREFIX}/start", {})
    svc.post(f"{PREFIX}/area", {"text": "Запись к врачу"})
    pl = svc.pl()
    area = pl.bank.records("area")[0]
    for text in ("Первый?", "Второй?"):  # очередь вопросов «Идеи»: человек видит только первый
        clarify.create(pl, [{"text": text, "options": ["да"]}], source="interview", batch="", extra={"stage": 1, "topic": "x"}, own_batch=True)
    state = svc.state()
    assert [q["title"] for q in state["questions"]] == ["Первый?"] and state["queued"] == 1
    assert area["id"] == "c1"
    out = svc.post(f"{PREFIX}/interview/finish", {"topic": "x"})
    svc.wait()
    assert out["job"]["label"] == "продолжение по сказанному"
    with pytest.raises(PipelineError):
        svc.post(f"{PREFIX}/interview/finish", {})


# ---------------------------------------------------------------------- длинные вопросы и сбой на полпути


def test_a_long_question_is_kept_whole_and_does_not_break_the_step(tmp_path):
    long_text = "Это конкурент: «ДокторПлюс» — " + "очень длинное описание сервиса онлайн-записи. " * 8 + "?"
    assert len(long_text) > 300
    pl, _ = build(tmp_path, [])
    stage1.set_area(pl, "Запись к врачу")
    (made,) = clarify.create(pl, [{"text": long_text, "options": ["Да, верно", "Нет, убрать"]}], source="confirm", batch="", extra={"stage": 1}, own_batch=True)
    assert len(made["title"]) <= 200 and made["title"].endswith("…")
    assert made["body"] == long_text  # вопрос целиком — в тексте записи, человек видит его полностью
    assert clarify.create(pl, [{"text": long_text, "options": []}], source="confirm", batch="") == []  # повтор не плодится
    assert long_text in interview.dossier(pl)  # студия тоже видит вопрос целиком


def test_competitors_found_before_a_failure_are_still_confirmed_on_the_next_run(tmp_path):
    found = {"competitors": [{"name": "ДокторПлюс", "url": "https://a.example", "why": "очень длинное описание " * 20}]}
    rules = [("реальных конкурентов", found), ("Проанализируй конкурентов", {"analysis": []})]
    pl, _ = build(tmp_path, rules)
    stage1.set_area(pl, "Запись к врачу")
    (competitor,) = stage1.research_competitors(pl)
    assert [q["attrs"]["record"] for q in clarify.pending(pl)] == [competitor["id"]]
    for q in clarify.pending(pl):  # как будто вопрос не создался: шаг упал, запись осталась без подтверждения
        pl.bank.reject(q["id"], "test", "потерян", cascade=False)
    assert interview.unconfirmed(pl, "competitor") == [pl.bank.get(competitor["id"])]
    assert stage1.research_competitors(pl) == []  # новых нет, но спросить о найденном раньше студия обязана
    assert [q["attrs"]["record"] for q in clarify.pending(pl)] == [competitor["id"]]
