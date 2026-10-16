# studio — код → граф → код

Офлайн dev-инструмент для проектов на трёх пакетах (инфраструктура / бизнес-логика / представление).
Работает пока проект **не запущен**. Строит граф по коду, даёт править граф, по «Сохранить» пишет изменения обратно в код.

Цикл: `открыл studio → scan (код → граф) → edit (граф) → save (граф → код) → закрыл`.

## Установка (для разработки)

```bash
pip install -e .
```

## Команды

```bash
studio init [--project .] [--force]   # создать .studio/ с заготовкой config.toml
studio scan [--project .]             # собрать граф по коду → .studio/graph.json
studio dev [--project .]              # поднять UI (http://127.0.0.1:8777)
studio save [--project .] [--show-diff] [--diff-only] [-y]  # граф → код
studio validate [--project .]         # проверить чистую архитектуру
```

## Правила

- Узел = файл. Слой: сначала маркеры содержимого (`@register_scenario`, `SQLModel`, `router`, ...), потом пути из `config.toml`, иначе `unknown`. Домен: сегмент пути из конфига, иначе `app`.
- Связь = совпадение имён контрактов (абстрактный класс/метод ↔ реализация).
- Разрешённые направления из `config.toml` (`representation → logic/schema`, `logic → infrastructure/schema`, `infrastructure → schema`).
- Связи с `unknown` не судятся. Импорты игнорируются, рантайма нет.
