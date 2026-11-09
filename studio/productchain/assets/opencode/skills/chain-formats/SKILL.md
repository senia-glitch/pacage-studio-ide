---
name: chain-formats
description: "Общие правила и JSON-форматы артефактов цепочки «Продукт → Фронтенд» в .studio/chain/. Загружай перед любой записью или чтением файлов цепочки."
---

# Форматы артефактов цепочки

Все артефакты — JSON в `.studio/chain/`. Корень каждого файла: `{"version": 1, ...}`.

## Правила для всех ролей

- **Пиши только свои файлы** (таблица ниже). Чужие файлы только читай.
- **Идентификаторы** `<префикс>.<номер>`: `role.3`, `src.12`, `fact.40`, `need.7`, `req.15`. Номер = максимальный существующий + 1. Идентификаторы не переиспользуются.
- **Ничего не удаляй.** Удалённый объект получает `"status": "removed"` и остаётся в файле.
- **Ссылки только по идентификаторам** (`need_ids`, `source_ids`). Ссылаться можно только на существующие объекты.
- **`run`** — номер прогона, который сообщил тебе дирижёр. Ставь его на каждый новый объект.
- Перезаписывая файл, сохраняй всё, что в нём было: читай файл, добавляй, пиши целиком.
- После записи дирижёр запускает `studio chain check`. Ошибки этой проверки — твои ошибки.

## Кто что пишет

| Файл | Пишет |
|---|---|
| `domain.json` | researcher (шаг 1) |
| `research/run-<N>-<stream>.json` | researcher (шаг 2, черновик своего направления) |
| `research.json` | только команда `studio chain merge-research` |
| `needs.json` | analyst (шаг 3) |
| `product.json` | product (шаг 4), поле `selected` — дирижёр |
| `requirements.json` | analyst: `areas`, `items`; reviewer: `glossary`, `entities`, `journeys` |
| `remarks.json` | critic (шаг 6), reviewer (шаг 7); поля `status`, `resolution` — analyst |
| `decisions.json` | decider или дирижёр (за человека) |
| `chain.json` | дирижёр |

## domain.json

```json
{"version": 1, "raw_idea": "...", "domain": "область одной фразой",
 "roles": [{"id": "role.1", "name": "Клиент", "description": "...", "run": 1}],
 "search_directions": ["что и где искать"]}
```

## research/run-N-<stream>.json (черновик) и research.json

`stream`: `needs` | `competitors` | `norms`. В черновике идентификаторы локальные, с 1: скрипт перенумерует их при слиянии и перепишет ссылки.

```json
{"sources": [{"id": "src.1", "url": "https://...", "title": "...", "kind": "review", "accessed": "2026-11-09"}],
 "facts": [{"id": "fact.1", "kind": "pain", "role_id": "role.1", "text": "...",
            "evidence": "чем подкреплено", "source_ids": ["src.1"]}],
 "competitors": [{"id": "comp.1", "name": "...", "url": "https://...", "segment": "...", "source_ids": ["src.1"],
                  "features": [{"id": "feat.1", "name": "...", "description": "...", "source_ids": ["src.1"]}]}]}
```

- `source.kind`: `review`, `forum`, `article`, `competitor_site`, `law`, `standard`.
- `fact.kind`: `pain`, `need`, `norm`, `observation`. `role_id` может быть `null` (для норм).
- Факт, конкурент и функция без `source_ids` не принимаются.

## needs.json

```json
{"version": 1, "needs": [{"id": "need.1", "role_id": "role.1", "statement": "...",
  "fact_ids": ["fact.1"], "frequency": "high", "severity": "high",
  "coverage": "partial", "covered_by": ["comp.1"], "required_by_norm": false,
  "rank": 1, "status": "active", "run": 3}]}
```

`frequency`, `severity`: `high` | `medium` | `low`. `coverage`: `none` | `partial` | `full`. `rank`: 1 — самая важная.

## product.json

```json
{"version": 1, "variants": [{"id": "var.1", "run": 4, "statement": "...",
  "role_ids": ["role.1"], "need_ids": ["need.1"], "differentiation": "...",
  "out_of_scope": ["..."], "differs_from_raw_idea": "..."}],
 "selected": null, "decision_id": null}
```

## requirements.json

```json
{"version": 1, "round": 1,
 "glossary": [{"id": "term.1", "term": "Запись", "definition": "...", "forbidden_synonyms": ["бронь"]}],
 "entities": [{"id": "ent.1", "name": "Запись", "term_id": "term.1", "states": ["new", "confirmed"]}],
 "areas": [{"id": "area.1", "name": "Запись на услуги", "domain": "booking"}],
 "items": [{"id": "req.1", "area_id": "area.1", "type": "mandatory", "title": "...", "description": "...",
   "role_ids": ["role.1"], "need_ids": ["need.1"], "entity_ids": [],
   "benefit": "польза для роли", "justification": [
     {"basis": "competitors", "text": "есть у 6 из 7", "competitor_ids": ["comp.1"], "source_ids": ["src.2"]}],
   "acceptance": ["проверяемый критерий"], "no_ui": false, "status": "active", "run": 5,
   "history": [{"round": 1, "by": "analyst", "change": "...", "remark_id": "rem.4"}]}],
 "journeys": [{"id": "journey.1", "role_id": "role.1", "name": "...",
   "steps": [{"n": 1, "action": "...", "req_id": "req.1"}]}]}
```

- `type`: `mandatory` | `proposed`. `basis`: `competitors` | `pain` | `norm` | `logic` (у `logic` источник не нужен).
- `areas[].domain` — латиница, snake_case.

## remarks.json

```json
{"version": 1, "remarks": [{"id": "rem.1", "step": 6, "round": 1, "by": "critic",
  "check": "coverage", "severity": "blocking", "target_ids": ["req.3"],
  "text": "...", "fix_hint": "...", "status": "open", "resolution": ""}]}
```

`check` шага 6: `coverage`, `source`, `duplicate`, `scope`, `benefit`. Шага 7: `glossary`, `roles`, `journey`, `entity`, `pattern`, `navigation`, `contradiction`. `status`: `open` | `fixed` | `rejected`.

## decisions.json

```json
{"version": 1, "decisions": [{"id": "dec.1", "step": 8, "target_id": "req.12", "decision": "reject",
  "by": "decider", "contested": true, "reason": "...",
  "pros": ["..."], "cons": ["..."], "extra_check": "что перепроверено",
  "overridden_by": null, "at": "2026-11-09T09:10:00+08:00"}]}
```

- `decision`: `accept` | `reject` | `select`. `by`: `human` | `decider`.
- Шаг 1: одно решение `select` с `target_id: "domain"`. Шаг 3: решение на каждую потребность. Шаг 4: `select` варианта. Шаг 8: решение на каждое активное требование.
- У `contested: true` поля `pros`, `cons`, `extra_check` обязательны.
