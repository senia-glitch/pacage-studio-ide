> Исходное ТЗ 3.x; изменения 4.0 — в конце документа.

ТЗ для AI-агента: рефакторинг и доработка pacage-studio (3.3.0 → 4.0.0)
Аудит выполнен по полному дампу пакета (Python-код, тесты, документация, UI). Документ самодостаточен: агенту не нужно ничего угадывать, у каждой задачи есть файлы, шаги и критерии приёмки.
Как читать пометки в аудите: ✔ — подтверждено чтением кода; ? — вероятно, но агент обязан сначала проверить тестом. Ограничение аудита: тесты в среде аудита запустить не удалось (нет pydantic/pytest, нет сети), а геометрию drag/overlap в index.html (строки ~291–670, ~776–920) я не разбирал построчно — агент читает её сам перед WP7.
0. Правила работы агента (обязательны)
1. Сначала baseline. pip install -e ".[dev]", pytest -q. Запиши: сколько тестов, какие падают. Не чини то, чего нет в этом ТЗ.
2. Работай строго по рабочим пакетам (WP1…WP11) в указанном порядке, в ветке refactor/4.0. После каждого WP: pytest и ruff check зелёные, отдельный коммит с сообщением WPn: ….
3. Баг → сначала красный тест, потом исправление. Для каждого пункта аудита с пометкой ✔/? пиши тест, который падает на старом коде.
4. Нерушимое ограничение (ТЗ §10 пакета): пакет не генерирует код и пишет в файлы проекта только блоки # studio: … start/end (и файлы-планы конструктора). Любая запись, меняющая другие строки пользователя, — баг.
5. Формат маркеров # studio: не менять. Чтение старых файлов должно работать (обратная совместимость). Расширения формата — только добавляющие (см. §3).
6. Зависимости: в runtime только pydantic>=2. Всё остальное — stdlib. UI — без CDN, без сборщиков, без npm в runtime.
7. Не выдумывай API стека (fastbase, core-package, event-infra). Если не уверен, как устроен run(), register_scenario, роутер — найди в site-packages (pip show -f fastbase) или оставь текущее поведение и добавь диагностику «не поддержано».
8. Не переписывай всё сразу. Не удаляй и не ослабляй существующие тесты ради зелёного прогона; если тест проверяет изменённое поведение — обнови его осознанно и опиши в отчёте.
9. Отчёт после каждого WP (в тексте ответа): что сделано, что не сделано и почему, вывод pytest, список изменённых публичных имён.
10. Если этот документ противоречит файлу «ТЗ этого пакета — истина…», главнее этот документ, но в конце каждого WP обнови docs/SPEC.md (перенесённая копия исходного ТЗ) и CHANGELOG.md.
Сверка с текущим состоянием: не делать двойную работу (правило 11, действует перед каждым WP). Часть пунктов этого ТЗ могла быть уже реализована — по вчерашней версии ТЗ или вручную. Поэтому перед каждым WP выполни «Шаг 0»:
1. Перед началом: git status, git log --oneline -30, запусти pytest. Чужие и незакоммиченные правки не откатывать и не затирать.
2. Пройди по пунктам WP и для каждого определи статус по признакам из таблицы ниже: СДЕЛАНО, ЧАСТИЧНО или НЕ СДЕЛАНО. Признак — наблюдаемое поведение (тест или команда), а не функция с похожим именем. Сомневаешься — напиши тест из ТЗ: зелёный на текущем коде = СДЕЛАНО, красный = ЧАСТИЧНО/НЕ СДЕЛАНО.
3. СДЕЛАНО: не переписывай, не переименовывай и не «улучшай». Проверь только, что есть тест из ТЗ (нет — добавь тест, код не трогай) и что критерий приёмки проходит.
4. ЧАСТИЧНО: доделай недостающее поверх существующего кода, сохранив его имена и публичные API; заново не начинай.
5. НЕ СДЕЛАНО: делай по ТЗ.
6. Реализация отличается от ТЗ, но проходит критерий приёмки и тесты — оставь как есть, различие запиши в отчёт (не переделывай ради единообразия). Нарушает критерий — исправь минимально.
7. В отчёте WP — таблица «пункт → статус → что сделано / почему пропущено». Коммит WPn: … содержит только реальные изменения; пустой WP (всё СДЕЛАНО) — без коммита, только отчёт.
WP
Признаки, что уже сделано
WP1
в git нет .pytest_cache/, .ruff_cache/, .studio/, requirements.txt; версия dynamic из studio.__version__; в pyproject есть [tool.ruff]; есть studio/_constants.py и docs/SPEC.md
WP2
есть studio/source.py и studio/diagnostics.py; scan() не падает на cp1251-файле и на мусорных .py (fuzz); в graph.json есть diagnostics; в scanner/ и metadata/ нет read_text(/ast.parse( вне source.py/writer.py
WP3
id io оканчивается на #n; есть graph.index(), Node.complete, ScenarioPath.endpoints; тест «домен не протекает» зелёный; нет any(n.id == в цикле
WP4
есть tests/golden/full и он зелёный; тесты в class Test* находятся; include_router(prefix=…) попадает в url_path; JOIN даёт два ребра table; неподдерживаемое даёт W_*
WP5
есть metadata/writer.py с SaveResult; round-trip на CRLF/BOM/без финального \n зелёный; base_hash и 409 file_changed; экранирование строк studio:
WP6
есть ProjectState, токен и Host-проверка (POST без токена → 403), /api/wait, watcher
WP7
есть ui/static/js/*.js, index.html < 150 строк, заголовок CSP
WP8
есть editor.js, storage.js, search.js, savestate.js; панель «Диагностика»; кнопка «Открыть в редакторе»
WP9
работают studio doctor, --version, scan --json; ожидаемые ошибки без трейсбека
WP11
работают studio brief и studio verify; есть studio/agent/rules.md
WP10
есть структура docs/, CHANGELOG.md с 4.0.0, .github/workflows/ci.yml
1. Контекст и цели
pacage-studio — офлайн-инструмент для проектов на стеке fastbase / core-package / event-infra. По AST (без запуска кода) строит карту пути HTTP-запроса endpoint → scenario → io → model, показывает её в браузерном UI (вкладки «Карта», «Сценарий», «Создание») и хранит описание/критерии/домен/связи узлов в комментариях # studio: … прямо в коде. Цель пакета — упростить жизнь разработчику: быстро понять систему и вести её документацию там, где живёт код.
Цели версии 4.0 и измеримые критерии «10 из 10»:
A. Удобство (UX 10/10)
• От pip install до карты на экране — не больше 3 команд и ~60 секунд; studio сам открывает браузер.
• От карточки узла до строки в редакторе — 1 клик («Открыть в редакторе»).
• Правка кода в IDE видна на карте за ≤3 с без действий пользователя; выбор, раскладка и зум не сбрасываются.
• Раскладка, зум, состояние вкладок и черновик «Создания» переживают F5.
• Ни одна проблема не молчит: пустая карта всегда объясняет почему и что делать.
B. Главная идея (карта верна, 10/10)
• Каждый узел и каждая связь соответствуют коду; для поддерживаемых конструкций нет ложных и нет пропущенных связей; для неподдерживаемых — явная диагностика, а не тишина.
• Домены и признак «неполный» считаются корректно (направленно), логика — в одном месте (Python), UI её не дублирует.
• Идентичность узлов стабильна при правках кода и при записи метаданных самим пакетом.
C. Стабильность и простота
• Скан никогда не падает на пользовательском коде: любой набор .py (битая кодировка, SyntaxError, пустые файлы, симлинки) даёт граф + диагностики, но не исключение.
• Запись в файлы пользователя минимальна, сохраняет кодировку/BOM/перевод строки/финальный \n, атомарна, защищена от гонок с IDE и обратима.
• Сервер безопасен (CSRF/DNS-rebinding), потокобезопасен, быстр (скан 300 файлов ≤ 1–2 с).
• Нет дублирования логики и «костылей»: один источник правды для версии, правил маркеров, списка файлов-источников, расчёта «неполных».
D. Мост «разработчик → AI-агент» (WP11)
• Разработчик расширяет систему только описаниями планируемых узлов в студии; пакет сам собирает полное ТЗ для AI-агента: правила работы, карта проекта с путями к узлам, подробные задачи на реализацию узлов без кода.
• Результат проверяется командой studio verify и виден в UI («Выполнено N/M»); писать ТЗ вручную не нужно.
2. Результаты аудита
Приоритеты: P0 — потеря данных/падения/безопасность или искажение смысла карты; P1 — заметно мешает главной идее или UX; P2 — гигиена. В скобках — рабочий пакет, где пункт закрывается.
2.1. Стабильность и сохранность данных
• F1 (P0 ✔) Скан падает на файле не в UTF-8. scan_metadata, scan_planned, save_node_metadata читают read_text(encoding="utf-8") и ловят только OSError; UnicodeDecodeError — это ValueError, исключение уходит наверх и роняет весь скан (а значит и studio dev). (WP2)
• F2 (P0 ✔) Запись метаданных портит файл пользователя. update_text_metadata возвращает "\n".join(result) — финальный перевод строки теряется при каждом сохранении. Чтение через read_text + запись через atomic_write без newline="" не сохраняют стиль переводов строк: на Windows LF-файл целиком превратится в CRLF (гигантский git-diff). BOM (\ufeff) не обрабатывается (? ast.parse может упасть → файл молча пропадёт). (WP5)
• F3 (P0 ✔) Нет защиты от одновременной правки. Форма сохраняет поверх файла, не сверяя, что файл не менялся с момента открытия формы. Правка в IDE между открытием и blur теряется молча. (WP5)
• F4 (P0 ✔) Небезопасный локальный сервер. Нет проверки Host/Origin, нет токена, Content-Type не проверяется. Любая веб-страница в браузере разработчика может сделать POST на http://127.0.0.1:8777/api/metadata/node или /api/create и писать комментарии в файлы проекта (CSRF / DNS-rebinding). (WP6)
• F5 (P1 ✔) graph.json пишется не атомарно (write_text) при параллельных чтениях → «рваное» чтение → read_graph возвращает None → тихий пересчёт; /api/rescan не берёт блокировку записи. (WP6)
• F6 (P1 ✔) Производительность. Каждое сохранение формы = два полных скана под глобальным локом (_save_metadata вызывает scan, затем _scan_and_save). Каждый файл читается 3–4 раза и парсится 3 раза (parse_file, read_file_metadata, scan_planned). В резолвере повсюду any(n.id == … for n in graph.nodes) — O(n²). (WP2, WP3, WP6)
• F7 (P1 ✔) Тихие отказы. Файл с SyntaxError просто исчезает из карты; неразрешённый edge (continue), неоднозначный якорь (setdefault берёт первый), незакрытый/вложенный блок, кривой FAT_* (только строка в логе), отсутствие папок роутов/сценариев — пользователь видит пустую карту без причины. В приложенном studio.log весь вывод — «сценариев: 0». (WP2, WP8)
• F8 (P1 ✔) Нестабильные id io-узлов. В id входит номер строки (…_db.create:5). Любая правка выше по файлу — и сама запись метаданных пакетом (она вставляет строки!) — меняет id: теряются раскладка и выделение. В UI это уже обложено костылём (saveMetadata: if(!byId[selectedNode]) selectedNode=null) — после сохранения io-узла выделение пропадает. (WP3)
• F9 (P1 ✔) Инъекция в разметку. Описание, содержащее строку studio: description end, преждевременно закрывает блок и ломает метаданные. (WP5)
• F10 (P2 ✔) load_graph отдаёт graph.json с диска без проверки актуальности; cmd_list вызывает slog.start_run после скана (шаги скана теряются); лог пишет каждый HTTP GET. (WP6, WP9)
2.2. Корректность главной идеи (карта пути запроса)
• F11 (P0 ✔) Домены «протекают». compute_domains делает BFS по неориентированному графу и кладёт в очередь другие эндпоинты (проверка kind == "endpoint" стоит после queue.append). Если эндпоинты A и B используют одну модель или один вызываемый сценарий, домен A растекается на всё, что достижимо от B. (WP3)
• F12 (P1 ✔) «Неполные» узлы считаются неориентированно (exporter._undirected_complete) и ещё раз отдельно в JS (incompleteNodes() в index.html): сценарий без эндпоинта, вызывающий достижимый сценарий, ошибочно считается «полным»; две копии логики расходятся. (WP3)
• F13 (P1 ✔) ScenarioPath.endpoint — один эндпоинт (next(...)), хотя сценарий могут вызывать несколько эндпоинтов (ТЗ §5.7 прямо говорит о множестве). (WP3)
• F14 (P1 ✔) Тесты находятся плохо. Только top-level def test_*; методы class TestX не видны (собственные тесты пакета написаны классами!). Матч эндпоинта — подстрока ep.function_name in node.name (функция list «находит» test_listing_*). Нет матча по URL в client.post("/api/v1/…"). (WP4)
• F15 (P1 ✔) io и вызовы ищутся слишком узко. Только тело execute и только self._db.*; helper-методы сценария (self._load() → self._db.read) невидимы; run() распознаётся только с литералом; динамические вызовы теряются молча. (WP4)
• F16 (P1 ✔) Эндпоинты. Не учитывается include_router(..., prefix=…) → неверный url_path; роутер распознаётся только как переменная с именем router (_router_prefix/_router_tags); api_route(methods=[…]) и несколько декораторов на одной функции не поддержаны. (WP4)
• F17 (P1 ✔) Модели. Только прямой базовый класс SQLModel и явный __tablename__: class User(Base, table=True) и таблица по умолчанию (имя класса в нижнем регистре) игнорируются. SQL: берётся только первая таблица — JOIN теряется. (WP4)
• F18 (P1 ✔) Периферийные узлы — только импортируемые модули. ТЗ §4.3 говорит ещё о методах и вызовах внутри сценария — разрыв между ТЗ и кодом. (WP4)
• F19 (P1 ✔) Edge-блоки ссылаются на голый якорь. Одинаковые имена в разных файлах → коллизия, которая решается молча. (WP3)
2.3. UX
• F20 (P0 ✔) Нет «открыть в редакторе». На карточке есть file:line, но перейти к коду нельзя. Для инструмента «упростить жизнь разработчику» это главный недостающий жест. (WP8)
• F21 (P1 ✔) Нет live-обновления. После правки в IDE нужно вручную жать «обновить». (WP6, WP8)
• F22 (P1 ✔) Раскладка и черновик «Создания» живут в JS-памяти (mapPos, scenPos, createState…) и пропадают при F5. (WP8)
• F23 (P1 ✔) Нет поиска/фильтра; на проекте в 200 эндпоинтов карта нечитаема. (WP8)
• F24 (P1 ?) Автосейв по blur. При смене выделения/вкладки/Esc возможна потеря несохранённой правки (inspector перерисовывается, blur может не успеть); нет индикатора «сохранено/ошибка», нет «отменить». (WP8)
• F25 (P2 ✔) index.html — 1300 строк/67 КБ с глобальным состоянием; UI-тесты проверяют наличие подстрок в HTML (а не поведение). (WP7)
2.4. Архитектура и гигиена
• F26 (P1 ✔) Дублирование: _source_files в metadata и scanner/planned; правила маркеров размножены в README / ИНСТРУКЦИЯ / exporter._rules_section; версия в pyproject.toml и studio/__init__.py; git-URL fastbase в pyproject, cli, документации. (WP1, WP2, WP10)
• F27 (P2 ✔) Мусор в репозитории: .pytest_cache (в lastfailed — несуществующие тесты), .ruff_cache, .studio/graph.json; requirements.txt дублирует pyproject; нет конфига ruff, CI, CHANGELOG. (WP1, WP10)
• F28 (P2 ✔) CLI: часть сообщений на английском при русском UI; ошибка «порт занят» — трейсбек; нет --version, doctor, автооткрытия браузера, --json. (WP9)
3. Изменения спецификации (что отличается от исходного ТЗ)
Эти решения принимает владелец пакета; агент их реализует и вносит в docs/SPEC.md в раздел «Изменения 4.0».
• S1. Стабильный id io-узла. Было: io:{file}:{Scenario}._db.{method}:{строка}. Стало: io:{file}:{Scenario}._db.{method}#{n}, где n — порядковый номер (с 1) вызовов этого метода порта внутри сценария в порядке исходника. Номер строки остаётся в node.line и показывается в UI. Старый формат :{строка} принимается при чтении (edge-блоки, старые id) и разрешается на ближайший вызов того же метода.
• S2. Якорь в edge-блоке: <якорь> или <путь/к/файлу.py>::<якорь>. Голый якорь, подходящий нескольким узлам, — диагностика W_EDGE_AMBIGUOUS, связь не применяется (раньше молча брался первый).
• S3. Домены наследуются направленно: от эндпоинта вниз по endpoint → scenario → io → model (и по uses/depends). Через другой эндпоинт обход не идёт. Тест наследует домены цели своего tests-ребра.
• S4. «Неполный узел» = недостижим из какого-либо эндпоинта по направленным рёбрам потока. Считает только Python (поле Node.complete); exporter, CLI и UI используют это поле.
• S5. ScenarioPath.endpoints: list[str] — все эндпоинты-корни (прямой dispatch или через цепочку scenario_call); endpoint = первый элемент (совместимость).
• S6. Диагностики — часть графа (graph.diagnostics), схема графа v6.
• S7. Конфликт при сохранении формы: запрос несёт base_hash файла; при расхождении — 409 file_changed; перезапись возможна только явным force: true (это и есть «волевой акт» из §9 исходного ТЗ).
• S8. Сироты удаляются при явном сохранении, как и было (§5.5), но ответ содержит removed_orphans, а UI показывает «Удалено устаревших блоков: N · Отменить».
• S9. Экранирование: строка внутри текста метаданных, похожая на маркер (studio: <раздел> start|end), записывается с ведущим \ и при чтении \ снимается.
• S10. Раскладка/зум/черновик «Создания» хранятся в localStorage браузера. Это не данные проекта: в проект ничего не пишется.
• S11. Порт БД (_db) — одна константа PORT_ATTR в scanner/ast_utils.py.
• S12. ТЗ для AI-агента (WP11). Новые: studio brief, studio verify, окно «ТЗ для AI-агента» в UI, Node.end_line, details.signature/fields/doc, Edge.origin, файлы .studio/agent-brief.md и .studio/briefs/, необязательный studio.rules.md в корне проекта. studio export — устаревший псевдоним.
4. Целевая структура пакета
Публичные имена, которые импортируют тесты (studio.scanner.scan, studio.metadata.save_node_metadata, studio.creator.create_metadata_files, studio.server.make_handler и т.д.), сохраняются (реэкспорт из __init__.py). Внутреннее разбиение:
studio/
  __init__.py            # только __version__ (без импортов подпакетов)
  _constants.py          # FASTBASE_FULL_SPEC, PORT_ATTR, лимиты
  diagnostics.py         # Diagnostic, Sink, коды
  source.py              # SourceFile, SourceSet, load_sources()  <- ЕДИНСТВЕННОЕ чтение файлов
  metamodel/             # схема v6, GraphIndex
  env/                   # .env.fastbase, пути, диагностики окружения
  scanner/               # indexer, resolver, tests_index, peripherals, planned, domains, reach
  metadata/              # parser.py (блоки), anchors.py, reader.py, writer.py, render.py
  creator/  exporter/  store/
  server/                # app.py (роуты), state.py (ProjectState+watcher), security.py, static.py
  cli/                   # main.py + commands/*.py
  ui/static/             # index.html, app.css, js/*.js (ES-модули)
Правила: scanner/metadata не читают файлы сами — получают SourceSet; scan() — чистая функция «файлы → Graph + диагностики»; пишет на диск только writer.py, creator и store.
5. Рабочие пакеты
WP1. Гигиена репозитория и фундамент (закрывает F26, F27)
1. Удали из git: .pytest_cache/, .ruff_cache/, .studio/, requirements.txt. В .gitignore добавь .pytest_cache/, .ruff_cache/, .studio/, *.egg-info/. Выполни git rm -r --cached для уже закоммиченного.
2. Версия — один источник. В pyproject.toml: [project] dynamic = ["version"] (убрать version = ...) и [tool.setuptools.dynamic] version = {attr = "studio.__version__"}. studio/__init__.py остаётся без побочных импортов. Поставь __version__ = "4.0.0.dev0" (финальное 4.0.0 — в WP10). Обнови tests/test_packaging.py (версия читается из studio.__version__, формат допускает .devN).
3. Ruff: в dev добавь ruff>=0.6; в pyproject.toml — [tool.ruff] line-length = 100, target-version = "py311", [tool.ruff.lint] select = ["E","F","I","B","UP","SIM"]. Запусти ruff check --fix, остаток поправь руками. ruff format не запускать (иначе огромный diff, потеряется ревью).
4. [tool.pytest.ini_options]: addopts = "-q", markers = ["slow", "e2e"].
5. Создай studio/_constants.py: FASTBASE_FULL_SPEC = "fastbase[full] @ git+https://github.com/senia-glitch/fastapi-transport.git". Используй в cli install-full; тест сверяет, что optional-dependencies.full в pyproject совпадает с константой.
6. git mv "ТЗ этого пакета - истина для его рефакторинга.txt" docs/SPEC.md; сверху добавь строку «Исходное ТЗ 3.x; изменения 4.0 — в конце документа».
Приёмка WP1: pip install -e ".[dev]" && ruff check && pytest зелёные; git status после запуска тестов чист; версия в одном месте.
WP2. Безопасное чтение и диагностики (закрывает F1, F6 частично, F7, F26 частично)
2.1. studio/diagnostics.py:
class Diagnostic(BaseModel):
    level: Literal["error", "warning", "info"]
    code: str          # см. таблицу
    message: str       # по-русски, одно предложение, без жаргона
    file: str = ""     # относительный posix-путь
    line: int = 0
    hint: str = ""     # что сделать пользователю
Коды (используй ровно их): E_ENCODING (файл не UTF-8), E_SYNTAX (SyntaxError, с номером строки), E_INTERNAL (сбой фазы скана), W_FILE_TOO_BIG (>2 МБ, пропущен), W_ENV_INVALID (кривое FAT_*), W_NO_ROUTES / W_NO_SCENARIOS / W_NO_MODELS (источник не найден; в hint — эффективное значение и какую переменную поправить), W_UNCLOSED_BLOCK, W_NESTED_BLOCK, W_ORPHAN_BLOCK (блок без якоря), W_EDGE_UNRESOLVED, W_EDGE_AMBIGUOUS, W_EDGE_SELF, W_DUP_ANCHOR (два узла с одним якорем), W_DISPATCH_DYNAMIC (run() с нелитералом), W_TABLE_UNKNOWN (таблица из io нет среди моделей), I_EMPTY_PROJECT (ничего не найдено — подсказка, с чего начать).
2.2. studio/source.py:
@dataclass(frozen=True)
class SourceFile:
    rel: str; path: Path
    text: str              # без BOM, переводы строк нормализованы к "\n"
    lines: list[str]
    tree: ast.Module | None
    sha1: str              # sha1 ИСХОДНЫХ байтов файла
    bom: bool
    newline: str           # "\n" или "\r\n" (преобладающий)
    final_newline: bool
    error: Diagnostic | None

def load_source(root: Path, path: Path) -> SourceFile: ...   # НИКОГДА не бросает
def load_sources(env: ProjectEnv, sink: Sink) -> SourceSet: ... # единственное место, где определён список файлов-источников
Алгоритм load_source: read_bytes() (любая OSError → E_INTERNAL с файлом) → снять BOM \xef\xbb\xbf (запомнить) → decode("utf-8") строго; UnicodeDecodeError → E_ENCODING, tree=None → определить newline (больше \r\n чем одиночных \n → CRLF) → нормализовать → ast.parse с перехватом SyntaxError, ValueError, RecursionError, MemoryError → E_SYNTAX. Файл не UTF-8 или не парсится — только для чтения: записывать в такой файл запрещено (WP5 вернёт понятную ошибку).
load_sources объединяет источники fastbase/core/models(models.py, models_*.py)/tests (сейчас этот список дублируется в metadata._source_files, planned._source_files и в индексаторах — удалить дубли). Обход — через os.walk(followlinks=False) с отсечением EXCLUDED_DIRS до захода в каталог (сейчас rglob обходит .venv целиком и фильтрует потом).
2.3. Подключение:
1. scan(root): read_env → load_sources → фазы получают SourceSet. Из scanner/* и metadata (путь чтения) убрать все read_text(/parse_file(/ast.parse( — grep по ним должен находить только source.py и writer.py. ast_utils.parse_file и кэш удалить; тесты, которые их импортировали, поправить.
2. ProjectEnv получает поле diagnostics: tuple[Diagnostic, ...] = (); read_env заполняет W_ENV_INVALID и W_NO_* (вместо slog.step).
3. Graph: version=6, diagnostics: list[Diagnostic], generated_at: str (ISO), fingerprint: str (sha1 от отсортированных пар rel:sha1 всех источников). store.GRAPH_VERSION = 6.
4. Каждая фаза скана (index, resolve, tests, peripherals, metadata, planned, domains, paths) обёрнута в try/except Exception → E_INTERNAL с именем фазы; при STUDIO_DEBUG=1 исключение пробрасывается. Это страховочная сетка, а не замена корректного кода.
5. I_EMPTY_PROJECT: если нет ни одного эндпоинта и сценария — одна диагностика с перечнем проверенных путей и подсказкой studio doctor.
6. Диагностики блоков (W_UNCLOSED_BLOCK, W_NESTED_BLOCK, W_ORPHAN_BLOCK) выдаёт metadata.parser (сейчас эти случаи молча continue).
Тесты WP2 (все — красными до правки):
• файл в cp1251 в routes/ → scan завершается, есть E_ENCODING с file, остальные узлы на месте;
• файл с BOM парсится, узлы найдены; файл с SyntaxError → E_SYNTAX и line; файл с нулевым байтом; пустой файл; файл >2 МБ;
• FAT_ROUTES_PACKAGE=.. → W_ENV_INVALID; нет папки роутов → W_NO_ROUTES;
• незакрытый и вложенный блок → соответствующие W_*;
• fuzz: 30 детерминированных seed'ов — в каждом источнике лежит мусорный .py (случайные байты/обрезанный валидный код); scan() не бросает;
• счётчик чтений: monkeypatch Path.read_bytes — каждый файл-источник прочитан ровно один раз за scan.
Приёмка WP2: ни один сценарий с битым/странным файлом не роняет scan/studio dev; в graph.json есть diagnostics; grep-проверка из п.1 пройдена.
WP3. Модель графа: стабильные id, направленные домены и «неполные», индексы (закрывает F6, F8, F11, F12, F13, F19)
3.1. Индексы вместо O(n²). Добавь в metamodel GraphIndex (by_id, out_edges[id], in_edges[id]), строится один раз: graph.index(). Внутри сканера — GraphBuilder с dict узлов/рёбер (add_node возвращает существующий, add_edge не дублирует). Удали все any(n.id == … for n in graph.nodes) и повторные graph.by_id() в циклах (resolver.py, scanner/__init__.py, exporter, cli).
3.2. Стабильный id io (S1). Единый хелпер в scanner/ast_utils.py:
@dataclass
class IOCall: method: str; ordinal: int; lineno: int; col: int; call: ast.Call
def iter_io_calls(execute_or_body, port_attr=PORT_ATTR) -> list[IOCall]  # порядок (lineno, col); ordinal считается отдельно для каждого method
Используют и resolver.resolve_io_and_tables, и metadata.collect_anchors (сейчас там отдельный дублирующий цикл). node_anchor(io) = Scenario._db.method#n; details.ordinal = n; node.line — реальная строка. Функция resolve_anchor_text(text, graph) принимает #n (точно) и legacy :LINE (ближайший по строке среди того же метода) — её используют apply_metadata_edges, server._resolve_node, creator.
3.3. Домены (S3) — алгоритм, реализовать как есть:
FLOW = {dispatch, scenario_call, port_call, table, uses, depends}
out = прямой список смежности по рёбрам FLOW (from -> to)
для каждого endpoint в порядке обнаружения (file_path, line):
    domains = normalize(endpoint.domain); если пусто — пропустить
    BFS от endpoint по out; ДРУГИЕ endpoint в очередь не ставить
    каждому достигнутому не-endpoint узлу дописать домены (без дублей, порядок сохранить)
для каждого ребра tests (test -> X): test.domain = X.domain   # один шаг, без дальнейшего распространения
Удали неориентированный adjacency и код, который ставит в очередь эндпоинты.
3.4. Достижимость (S4) — новый scanner/reach.py: прямой BFS от всех эндпоинтов по FLOW → множество reachable; test-узел достижим, если его tests-ребро ведёт в достижимый узел. Node.complete = id in reachable or kind == "endpoint". Удали exporter._undirected_complete, _incomplete_ids и JS-функцию incompleteNodes() (WP7/8 переключит UI на node.complete).
3.5. ScenarioPath.endpoints (S5): для сценария — все эндпоинты, от которых он достижим по цепочке dispatch + scenario_call; прямые — первыми, дальше по id. endpoint = endpoints[0] if endpoints else None. Группировку «метод → домен» в UI и экспорте делать по endpoints (сценарий, вызываемый двумя эндпоинтами, показывается в каждой группе).
3.6. Резолвер якорей (S2): класс AnchorResolver(graph) строит якорь → [id] один раз; resolve(text) -> tuple[str | None, str | None] (id, код диагностики). Формы: anchor, path.py::anchor, Scenario._db.method#n, legacy …:LINE. Неоднозначно → W_EDGE_AMBIGUOUS; не найдено → W_EDGE_UNRESOLVED; from == to → W_EDGE_SELF; в каждой диагностике file/line edge-блока. Правило «edge из метаданных заменяет code-ребро между той же парой узлов» сохраняется.
Тесты WP3:
• id io не меняется после вставки 10 строк выше вызова и после записи метаданных над ним;
• домен не протекает: A→S1→M, B→S2→M (общая модель M); домен A у S2 нет; у M — оба домена в порядке обнаружения;
• сценарий без эндпоинта, вызывающий достижимый сценарий, — complete=False;
• сценарий, вызываемый двумя эндпоинтами: endpoints из двух элементов; в studio list и экспорте виден в обеих группах;
• два узла с одним якорем в разных файлах: голый edge → W_EDGE_AMBIGUOUS, форма path::anchor работает;
• legacy …_db.create:5 в edge-блоке разрешается;
• перф-тест tests/test_perf.py: синтетический проект (150 эндпоинтов, 150 сценариев, 400 io, 60 моделей, 300 тестов), scan < 5 с (порог щедрый, чтобы тест не флакал).
Приёмка WP3: graph.json v6 с diagnostics, complete, endpoints; в коде нет неориентированного обхода и дублей логики «неполных»; exporter и CLI используют graph.index() и node.complete.
WP4. Точность карты: покрытие конструкций стека (закрывает F14–F18; это «главная идея 10/10»)
Принцип: поддерживаемая конструкция → точный узел/ребро; неподдерживаемая → диагностика, а не тишина. Добавь в таблицу кодов WP2: W_ROUTE_DYNAMIC (путь маршрута не литерал), W_IO_DYNAMIC (SQL/таблица не литерал), W_IO_SAME_LINE (несколько вызовов порта в одной строке — метаданные привязываются к первому).
4.1. Эндпоинты (scanner/indexer.py):
• Собери все модульные NAME = APIRouter(...) (вызов как APIRouter или x.APIRouter) → словарь имя → (prefix, tags). Получатель декоратора (@users_router.get) ищется в нём; неизвестный получатель (app.get) → prefix/tags пустые. Сейчас жёстко ищется переменная router.
• include_router: найди вызовы <x>.include_router(<obj>, prefix="…") в файлах роут-пакета, в __init__.py всех родительских пакетов до корня проекта и в main.py/app/main.py (эти доп. файлы читаются только для этого, в SourceSet.readonly). <obj> разрешается через импорты (from .users import router as users_router, from . import users → users.router). Итоговый URL = FAT_API_PREFIX + цепочка include-префиксов (до 5 уровней, защита от циклов) + prefix роутера + путь маршрута. Не нашёл include — цепочка пуста (как сейчас).
• Несколько декораторов на одной функции и api_route("/p", methods=[...]): один узел на функцию (id не меняется), первый маршрут — основной, остальные — в details.extra_routes: [{http_method, path, url_path}]; в UI на карточке чип «+N», в экспорте перечислены все.
• Путь не литерал → W_ROUTE_DYNAMIC (раньше декоратор молча игнорировался).
4.2. Диспетчеризация run() (resolver.py):
• Алиасы: from core import run as r → r("x") тоже вызов (собери run_aliases(tree)).
• Имя сценария — литерал или Name, ссылающийся на модульную строковую константу того же файла. Иначе → W_DISPATCH_DYNAMIC с file:line.
• Один уровень помощников: эндпоинт вызывает функцию того же модуля (await _do(...)), внутри которой run("x") — ребро dispatch создаётся, label = 'run("x") через _do' (глубина ≤ 2, цикл-защита).
4.3. Внутренности сценария (io, вызовы, периферия):
• Раньше анализировался только execute. Теперь — замыкание по self.<метод>(): execute + методы того же класса, до которых можно дойти вызовами self.<name>(...) (глубина ≤ 3, циклы режем). В них ищутся PORT_ATTR-вызовы и run().
• iter_io_calls (WP3) работает по этому замыканию; в details io — via_method: str | None (None, если вызов прямо в execute).
• Периферийные узлы (§4.3 исходного ТЗ — методы и функции): помимо импортируемых модулей, добавляй записи {role: "method", name, symbols: [], file_path, line} для вызванных self-методов и {role: "function", …} для вызванных функций того же файла. Добавь роли в ROLE_NAMES (UI и exporter): method → «метод», function → «функция».
4.4. SQL-таблицы: extract_tables_from_sql(sql) -> list[str] вместо extract_table_from_sql: распознаёт FROM, JOIN, INTO, UPDATE, DELETE FROM, schema.table, кавычки "t"; предварительно вырезаются -- … и /* … */. На каждую таблицу — своё ребро table от io-узла; details.tables — список, details.table — первая (совместимость); метка io — _db.query(users +1). SQL не литерал → W_IO_DYNAMIC. Литерал-таблица отсутствует среди моделей → W_TABLE_UNKNOWN.
4.5. Модели (index_models): класс — модель, если у него есть именованный аргумент table=True или __tablename__, и среди его баз (прямых или транзитивных внутри файлов моделей) есть SQLModel. Имя таблицы: __tablename__, иначе ClassName.lower() при table=True (дефолт SQLModel). Необязательно: ключ .env.fastbase STUDIO_MODELS=models.py,app/models (список файлов/пакетов через запятую) — по умолчанию как сейчас (models.py + models_*.py). Одинаковое имя таблицы в двух классах → W_DUP_ANCHOR (раньше второй молча отбрасывался; так же для двух register_scenario с одним именем — второй молча затирал первый).
4.6. Тесты → узлы и рёбра (tests_index.py, переписать):
• Собирай def test_* и async def test_* на верхнем уровне и в методах классов Test*; фикстуры (@pytest.fixture, @fixture) пропускай. Для метода details.test_name = "TestX.test_y", он же якорь (обнови node_anchor, metadata.collect_anchors, planned, exporter, anchorText в UI; блок метаданных ставится над методом с его отступом).
• Матч — по приоритету, останавливаясь на первом уровне, давшем совпадения; причина — в Edge.label:
    1. run("<имя>") в теле → сценарий (по run());
    2. строковый литерал URL в вызове <любой>.get|post|put|patch|delete("…") → эндпоинт с тем же методом и url_path ({param} → [^/]+, без query и хвостового /) (по URL);
    3. имя теста без префикса test_ равно имени сценария/функции эндпоинта или начинается с него по границе токена (имя или имя_…), побеждает самое длинное имя (по имени). Подстрока ep.function_name in node.name удаляется.
Тесты WP4: по одному на каждую подпункт-конструкцию (стековые фикстуры дописывай в tests/conftest.py отдельными проектами, не ломая demo): include_router с префиксом; users_router; stacked-декораторы; api_route; динамический путь → диагностика; алиас run; константа-имя сценария; хелпер эндпоинта; self._load() с io внутри; JOIN-запрос → 2 ребра table; модель через Base, без __tablename__; дубли имён; тест в классе; тест по URL; ложное срабатывание test_listing не матчится на функцию list.
Golden-тест: проект tests/golden/full/ (все конструкции выше) + tests/golden/full.expected.json (отсортированные id узлов, рёбра from/kind/to, домены, диагностики-коды). tests/test_golden.py сравнивает; перегенерация — UPDATE_GOLDEN=1 pytest tests/test_golden.py (после ревью diff человеком).
Приёмка WP4: golden зелёный; в UI/экспорте нет узлов-призраков; для всех неподдерживаемых конструкций есть диагностика с file:line.
WP5. Безопасная запись метаданных (закрывает F2, F3, F9; плюс жёсткость конструктора)
5.1. Модуль metadata/writer.py. Перенеси туда update_text_metadata, update_planned_text_metadata, save_node_metadata; разбор блоков — в metadata/parser.py, якоря — anchors.py, чтение — reader.py, генерация — render.py. Публичные имена реэкспортируй из studio.metadata.
@dataclass
class SaveResult: changed: bool; removed_orphans: list[dict]; new_hash: str; undo_id: str | None
class MetadataError(Exception): code: str; message: str   # коды ниже
Коды MetadataError: file_changed, not_writable, anchor_not_found, invalid_input, file_missing. Сообщения — по-русски, с причиной и действием.
5.2. Минимальная запись, сохраняющая файл. Работай по SourceFile.lines (нормализованные \n). После правки:
1. text = "\n".join(lines); если final_newline — добавь \n (сейчас финальный перевод строки теряется при каждом сохранении);
2. замени \n на source.newline (CRLF/LF исходника); добавь BOM, если был;
3. закодируй в UTF-8 и запиши через atomic_write_bytes (tempfile в той же папке → flush + os.fsync → shutil.copymode → os.replace); PermissionError при replace на Windows — 3 повтора по 50 мс (антивирус/IDE держат файл), затем not_writable;
4. если новые байты == старые — не писать (changed=False).
Нельзя писать: SourceFile.error не None (E_ENCODING, E_SYNTAX) → not_writable с текстом «Файл не разбирается (SyntaxError, строка N) — исправьте код и повторите».
5.3. Оптимистичная блокировка (S7). Каждый узел в графе несёт file_hash (= SourceFile.sha1). save_node_metadata(..., base_hash, force=False): под блокировкой проекта перечитай байты, посчитай sha1; не совпал с base_hash и нет force → MetadataError("file_changed"). Сервер отвечает 409 {error: {code: "file_changed", message, details: {current: {description, acceptance, domain}}}} — UI покажет выбор (WP8).
5.4. Экранирование (S9). При записи строки содержимого, подходящей под ^\s*\\*studio:\s*[a-z]+\s+(start|end)\s*$, получает дополнительный ведущий \; при чтении ровно один ведущий \ у такой строки снимается. Round-trip тест: описание из трёх строк, где вторая — studio: description end, возвращается неизменным и не ломает блок; обычная строка, начинающаяся с \, не затрагивается.
5.5. Сироты (S8). Логику удаления сирот сохрани (в пределах этого файла, при явном сохранении), но собери removed_orphans=[{section, line, preview}] (preview ≤ 60 символов) и верни в SaveResult. Пустые строки вокруг удалённых блоков не трогать.
5.6. Привязка к io в одной строке. Если две io-якоря на одной строке (a = await self._db.x() or await self._db.y()), порядок по (lineno, col), блок привязывается к первому, на второй — W_IO_SAME_LINE.
5.7. Отмена (P2). ProjectState (WP6) хранит кольцевой буфер последних 20 записей (rel, old_bytes, new_hash, ts); undo(id) восстанавливает old_bytes, только если текущий хеш файла равен new_hash, иначе file_changed.
5.8. Конструктор (creator/__init__.py):
• Всё или ничего: сначала проверка всех путей и имён, потом запись; ошибка записи любого файла → удалить уже созданные в этом вызове, вернуть CreateError.
• Имя узла не должно совпадать с якорем существующего узла графа (CreateError: «имя X занято узлом … (файл)») — иначе рёбра становятся неоднозначными (WP3 S2); не должно быть ключевым словом Python (keyword.iskeyword).
• _snake для аббревиатур: HTTPServer → http_server, UserIDMap → user_id_map (разбиение по (?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])), сейчас даёт h_t_t_p_server.
• Рёбра между новыми узлами пишутся с квалифицированным якорем path::anchor, если голый якорь неоднозначен внутри создаваемого набора.
Тесты WP5 (все с матрицей файлов: LF, CRLF, смешанные, с BOM, без финального \n, с табами, с вложенным отступом в execute):
• инвариант «diff касается только строк внутри блоков # studio:»: сравни difflib до/после сохранения для каждого узла каждого фикстурного файла; BOM, стиль переводов строк и наличие финального \n не изменились;
• идемпотентность: повторное сохранение тех же данных → changed=False, байты равны;
• id io-узлов не изменились после сохранения (связь с WP3);
• конфликт: файл изменён между графом и сохранением → file_changed; с force=True — записано;
• битый/не-UTF-8 файл → not_writable, файл не тронут (побайтовое сравнение);
• откат конструктора при сбое записи второго файла (monkeypatch atomic_write);
• HTTPServer → http_server.py; занятое имя → CreateError.
Приёмка WP5: git diff после любого сохранения из UI на реальном проекте с CRLF показывает только строки # studio:.
WP6. Сервер: безопасность, единое состояние, live-обновление (закрывает F4, F5, F6, F10, F21)
6.1. server/state.py — ProjectState (заменяет Ctx, _write_lock, _locked, load_graph):
class ProjectState:
    lock: threading.RLock          # ОДИН замок на скан, запись метаданных, создание, undo
    graph: Graph | None; revision: int; scanning: bool; token: str
    def rescan(self, reason: str) -> Graph        # scan -> store.write_graph (атомарно) -> revision += 1, только если fingerprint изменился; cond.notify_all()
    def save_metadata(self, data: dict) -> dict   # валидация -> writer.save_node_metadata -> ОДИН rescan
    def create(self, data: dict) -> dict
    def undo(self, undo_id: str) -> dict
    def wait_for_change(self, rev: int, timeout: float = 25) -> int
Сохранение работает по текущему графу в памяти (узел ищется по id через resolve_anchor_text, file_hash берётся из графа/запроса) — без предварительного полного скана; после записи — один rescan. /api/graph отдаёт граф из памяти, graph.json на диске сервер при запросах не читает (он остаётся артефактом для studio scan/внешних инструментов; пишется атомарно через atomic_write).
6.2. Наблюдатель за файлами. Поток-демон, без внешних зависимостей. Каждые poll_interval (по умолчанию 1,0 с; --poll, env STUDIO_POLL) считает «отпечаток по stat»: (rel, mtime_ns, size) всех файлов-источников + .env.fastbase (появление/исчезновение файла тоже меняет отпечаток). Отпечаток изменился → debounce 300 мс (ждать, пока он перестанет меняться) → rescan("watch"). Если скан идёт дольше интервала — следующий тик пропускается. Ошибки скана → E_INTERNAL в диагностике, поток не умирает.
6.3. API (все ответы JSON; ошибки — единый формат {"error": {"code": "...", "message": "...", "details": {...}}}):
Метод и путь
Назначение
GET /
index.html с подставленным <meta name="studio-token" content="…">
GET /static/<path>
статика из studio/ui/static/ (белый список расширений .html .css .js .svg .ico; .. и абсолютные пути → 404; MIME text/javascript для .js; Cache-Control: no-cache)
GET /api/graph
граф из памяти; заголовок X-Studio-Revision; ETag = ревизия, поддержка If-None-Match → 304
GET /api/state
{version, revision, fingerprint, generated_at, scanning, project_root, env: {routes_package, core_discover, api_prefix, models, counts}}
GET /api/wait?rev=N
long-poll до 25 с, ответ {revision} как только revision > N
POST /api/rescan
принудительный скан
POST /api/metadata/node
тело: node_id, description, acceptance, domain, base_hash, force? → {graph?, changed, removed_orphans, undo_id}; ошибки: 400 invalid_input, 404 node_not_found, 409 file_changed, 422 not_writable
POST /api/create
как сейчас; ошибки конструктора — 409 create_rejected
POST /api/undo
{undo_id}
6.4. Безопасность (F4).
• При старте token = secrets.token_urlsafe(24); он встраивается в index.html; все /api/* требуют заголовок X-Studio-Token (сравнение hmac.compare_digest), иначе 403 bad_token.
• Host-проверка для всех запросов: Host ∈ {127.0.0.1:PORT, localhost:PORT, [::1]:PORT} ∪ значение из --host; иначе 403 bad_host (защита от DNS-rebinding).
• Если есть Origin — он обязан совпасть с http://<Host>; иначе 403 bad_origin.
• POST: Content-Type: application/json (иначе 415), Content-Length обязателен (иначе 411), >16 МБ → 413, битый JSON → 400 bad_json. Сейчас _read_body в этих случаях молча возвращает {}, и пользователь получает невнятное «нужен node_id».
• --host не loopback → предупреждение при старте: «сервер доступен по сети; токен защищает запись, но трафик не шифруется».
• Заголовки: X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer; CSP включается в WP7 (после выноса inline-скриптов).
6.5. Запуск (serve).
• Порт не задан явно → перебор 8777…8796, занято всё → понятная ошибка; задан явно и занят → ошибка без перебора и без трейсбека: «Порт 8777 занят. Укажите другой: studio dev --port 8800».
• Открыть браузер (webbrowser.open), если нет --no-open, и переменная CI не задана.
• В консоль: URL, корень проекта, сводка эндпоинтов N · сценариев N · io N · моделей N · предупреждений N (если есть предупреждения — «подробности: studio doctor»), «Ctrl+C — остановить».
• daemon_threads = True; на KeyboardInterrupt — корректно остановить сервер и watcher, напечатать «Остановлено».
• Лог .studio/studio.log: сканы, записи (POST), ошибки. GET-запросы не логируются (флаг --verbose включает всё).
6.6. Миграция существующих тестов: make_handler(Ctx(root)) сохраняется как фабрика (Ctx — тонкий алиас на создание ProjectState); вспомогательный _post в tests/test_server.py добавляет заголовок X-Studio-Token. Безопасность по умолчанию включена; отключать её в тестах нельзя.
Тесты WP6:
• ProjectState: ревизия не растёт без изменений файлов; растёт после правки файла; wait_for_change просыпается; watcher с poll=0.05 ловит создание/правку/удаление файла;
• безопасность: чужой Host → 403; POST без токена → 403; чужой Origin → 403; text/plain → 415; битый JSON → 400; тело > лимита → 413; /static/../pyproject.toml → 404;
• порт: занять 8777 сокетом → сервер поднимается на 8778; явный занятый порт → ошибка без трейсбека;
• конкурентность: 20 параллельных POST (разные узлы одного файла) → итоговый файл содержит все 20 блоков без дублей, граф согласован;
• GET /api/graph с If-None-Match → 304.
Приёмка WP6: сохранение формы = ровно один скан; curl -X POST без токена не пишет в файлы; после правки файла в редакторе revision растёт за ≤ 2 с без действий пользователя.
WP7. UI: механическое разбиение (закрывает F25; поведение НЕ меняется)
Цель — подготовить почву для WP8. В этом WP запрещено менять логику и внешний вид. Сначала прочитай index.html целиком, включая геометрию drag/overlap (я её не аудировал).
7.1. Шаги (каждый — отдельный коммит, после каждого ручная проверка по чек-листу 7.4):
1. Создай studio/ui/static/. <style>…</style> → app.css дословно. Содержимое <script>…</script> → js/legacy.js дословно, подключение <script src="/static/js/legacy.js">. index.html — только разметка. Сервер отдаёт статику (WP6.3).
2. Разрежь legacy.js на ES-модули по существующим разделам (в файле есть комментарии /* ---------- … ---------- */):
   Модуль
   Содержимое
   state.js
   единый объект state вместо глобалей G, byId, tab, selectedScenario, selectedNode, hideMeta, *View, *Pos, createState …
   util.js
   $, esc, toast
   api.js
   api() (+ заголовок X-Studio-Token из <meta>), разбор единого формата ошибок
   geometry.js
   чистые функции без DOM: rectsOverlap, areaSize, proposedOverlap, zoneOverlaps, proposedZoneRect, methodOrder, inferEdgeKind
   viewport.js
   bindViewport, applyTransform, fitView, revealElement
   drag.js
   bindDrag, enableZoneDrag, enableNodeDrag
   edges.js
   drawEdges, measure, sizeStage, clearStage, offsetInStage
   map.js, scenario.js, inspector.js, create.js
   вкладки и форма метаданных
   main.js
   boot, переключение вкладок, горячие клавиши
3. Правила зависимостей: map/scenario/inspector/create не импортируют друг друга; межмодульные вызовы (клик по эндпоинту → вкладка «Сценарий», сохранение → перерисовка) идут через bus.js (bus.emit("openScenario", id) / bus.on). Чистые функции никогда не трогают document.
4. Перед выключением inline-скриптов проверь grep -n "onclick=\|onchange=\|javascript:" index.html js/*.js — таких быть не должно (кроме назначений через el.onclick = … в JS).
5. CSP: на / и /static/* добавь заголовок Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none' ('unsafe-inline' для стилей нужен из-за style="" в шаблонах).
6. Аудит XSS: пройди по всем innerHTML = … (их ~25: cardHtml, renderMap, fillScenarioSelect, renderInspector, список связей конструктора и т.д.). Каждая подстановка, пришедшая из графа (id, label, пути, описания, домены, домены, имена), обязана идти через esc(), включая значения атрибутов. Рекомендуется ввести html tagged template (экранирует все ${…}, raw() — явное исключение) и перевести шаблоны на него.
7.2. Тесты UI — заменить подстроки на поведение. tests/test_ui.py проверял, что в HTML есть слова вроде enableNodeDrag/scenPos — это не тест поведения. Оставь только осмысленное: / и все /static/js/*.js из index.html отдаются с 200 и правильным MIME; в index.html есть <meta name="studio-token">; нет http:///https:// ссылок (офлайн); нет inline-обработчиков. Добавь JS-тесты на node:test для чистых модулей (tests/js/*.test.mjs: геометрия, inferEdgeKind, группировка метод→домен, esc), запускаемые из tests/test_js.py через node --test, с pytest.skip, если node не найден. Опционально — e2e-тест (Playwright, маркер e2e, importorskip): открыть demo-проект → на карте 3 эндпоинта → клик → вкладка «Сценарий» → выбрать узел → изменить описание → blur → файл содержит блок # studio: description.
7.3. Упаковка: [tool.setuptools.package-data] "studio.ui" = ["static/*.html", "static/*.css", "static/js/*.js"]; обнови INDEX в ui/__init__.py и test_packaging (проверка, что все файлы из index.html покрыты glob'ами package-data).
7.4. Чек-лист ручной проверки (идентичность поведения; результат — в описании коммита): 1) карта показывает эндпоинты в группах fastbase → метод; 2) клик по эндпоинту открывает «Сценарий»; 3) перетаскивание областей/узлов, остановка при пересечении; 4) зум колесом, панорама, двойной клик = fit, кнопки +/−/сброс/fit; 5) выбор узла открывает форму, Esc и пустой клик снимают выделение; 6) правка описания и blur → toast «метаданные сохранены», блок в файле; 7) «скрыть метаданные»; 8) раздел «Неполные»; 9) «Создание»: + в области, имя, связь протягиванием, «Создать» → файлы созданы; 10) «Обновить карту»; 11) консоль браузера без ошибок; 12) вкладка Network — нет запросов наружу.
Приёмка WP7: внешнее поведение неотличимо; index.html < 150 строк; нет inline-скриптов; CSP включён и консоль чиста; pytest зелёный (включая JS-тесты, если установлен node).
WP8. UI: удобство разработчика (закрывает F7 в UI, F20–F24) — «UX 10/10»
Все модули — в studio/ui/static/js/ (после WP7). Новая логика, не зависящая от DOM, выносится в чистые модули и покрывается node:test.
8.1. «Открыть в редакторе» (главная функция).
• editor.js (чистый): buildEditorUrl(editorId, absPath, relPath, line, template?). Пресеты: vscode://file/{path}:{line} (по умолчанию), vscode-insiders://file/{path}:{line}, cursor://file/{path}:{line}, windsurf://file/{path}:{line}, zed://file/{path}:{line}, pycharm://open?file={path}&line={line} (агент проверяет по документации JetBrains, что схема актуальна, и при сомнении оставляет пользовательский шаблон), «Своё» — шаблон с {path}, {rel}, {line}.
• Путь надо кодировать: Windows C:\Users\Arseniy\Desktop\PACAGE 4 IDE\app\x.py → C:/Users/Arseniy/Desktop/PACAGE%204%20IDE/app/x.py (слэши прямые, пробелы и кириллица через encodeURI). Пробел в пути — реальный случай этого проекта.
• Абсолютный путь = graph.project_root + / + node.file_path. Для planned-узла (кода нет) — открыть файл-план, строка 1.
• UI: кнопка ↗ на каждой карточке (видна при наведении/фокусе, клик не выбирает узел); в инспекторе — «Открыть в редакторе» и «Копировать путь:строка» (navigator.clipboard, запасной вариант через временный textarea); O при выбранном узле. Выбор редактора — выпадающий список в шапке, хранится в localStorage. Подсказка у кнопки: «Если ничего не произошло — выберите другой редактор или проверьте, что протокол vscode:// зарегистрирован». Открытие — через программный клик по скрытому <a href>.
• В списке диагностик (8.6) каждая строка file:line открывается так же.
8.2. Live-обновление. main.js держит цикл: GET /api/wait?rev=N (long-poll) → при росте ревизии GET /api/graph → applyGraph → перерисовка активной вкладки с сохранением выбора, зума, панорамы, позиций, раскрытых списков, прокрутки. Во время drag перерисовка откладывается до pointerup. Если в инспекторе есть несохранённый ввод — инспектор не перерисовывается, а показывает баннер конфликта (8.5). В шапке индикатор: зелёная точка «live · обновлено 12:03:44» / серая «нет связи со студией — перезапустите studio» (при ошибках long-poll — повтор с паузой 1→5 с). Кнопка «Обновить карту» остаётся (принудительный POST /api/rescan).
8.3. Раскладка переживает F5 (S10). storage.js (чистый, принимает объект-хранилище → тестируется с моком): ключ studio:v1:<хеш project_root>, значение {tab, selectedScenario, selectedNode, hideMeta, editor, views, positions, listOpen, createDraft}; сохранение с debounce 300 мс; все обращения в try/catch (приватный режим, квота); неизвестные/устаревшие поля игнорируются; позиции для id, которых нет в графе, отбрасываются при загрузке. Черновик «Создания» восстанавливается с уведомлением «Восстановлен черновик (N узлов)». Пункт меню «Сбросить раскладку».
8.4. Поиск и навигация.
• Поле поиска в шапке (фокус по /): на «Карте» подсвечивает/скрывает (переключатель) эндпоинты по подстроке в методе, URL, имени функции, домене, описании; показывает «найдено N из M»; Enter — открыть сценарий первого совпадения. На «Сценарии» — фильтрует левый список.
• Палитра Ctrl+K: нечёткий поиск (подпоследовательность, search.js) по всем узлам: label, якорь, файл, домен; топ-20, стрелки + Enter → перейти (эндпоинт/сценарий — открыть сценарий и выбрать узел; модель/тест/io — открыть сценарий, где они есть, иначе выбрать в разделе «Неполные»).
• Чипы доменов на «Карте» (мультивыбор) — быстрый фильтр.
8.5. Надёжное сохранение и конфликты (S7, S8).
• savestate.js — чистый редьюсер состояний формы: idle → dirty → saving → saved | error | conflict. Индикатор в футере инспектора: «Сохранено ✓ / Сохранение… / Не сохранено / Ошибка — повторить».
• flushPending() (возвращает промис) вызывается перед сменой выбора, вкладки, Esc, «Обновить карту», закрытием страницы (beforeunload + fetch(..., {keepalive: true}); если не успело — стандартное предупреждение браузера). Автосейв по blur (§5.10 исходного ТЗ) остаётся основным механизмом.
• В запросе — base_hash = node.file_hash на момент открытия формы. Ответ 409 file_changed или баннер при live-обновлении (хеш файла узла изменился, а форма «грязная») → модальное окно «Файл изменён вне студии»: две колонки «Ваши правки» / «Сейчас в коде» (из details.current) и кнопки [Сохранить мои правки поверх] (force: true) · [Взять из кода] · [Отмена].
• Ответ с removed_orphans → уведомление «Удалено устаревших блоков: N» с кнопкой [Отменить] (POST /api/undo).
• Узел исчез после обновления (удалили код) → «Узел пропал из кода», выбор снимается. Костыль if(!byId[selectedNode]) selectedNode = null в saveMetadata заменить на эту логику (id io теперь стабилен, WP3).
8.6. Диагностика и понятные пустые состояния (F7).
• Бейдж в шапке «⚠ N» (красный при наличии error, жёлтый при warning) → правая панель «Диагностика»: группировка по уровню, строка = сообщение, file:line (открывается в редакторе), hint, чип с кодом; фильтр по коду; «Копировать всё» (markdown — для баг-репорта).
• Пустая «Карта»/«Сценарий» вместо «Эндпоинтов не найдено» показывают чек-лист источников из /api/state.env: ✓/✗ fastbase · app.api.v1.routes · 12 файлов, для ✗ — причина, текущее значение переменной и что поправить (FAT_ROUTES_PACKAGE в .env.fastbase или studio doctor).
• Клик по названию проекта в шапке — popover «О проекте»: корень, версия, ревизия, время скана, счётчики, эффективные значения окружения.
• Все подсказки по неоднозначным полям (§7.1 исходного ТЗ) сохраняются.
8.7. Мелочи. Горячие клавиши (оверлей справки по ?): Esc — снять выделение, / — поиск, Ctrl+K — палитра, O — открыть в редакторе, F — вписать, +/- — зум, H — скрыть метаданные. Карточки фокусируются (tabindex="0", Enter = клик), у кнопок aria-label. document.title = "studio · <имя проекта>", favicon — data-URI SVG.
Тесты WP8 (node:test): buildEditorUrl (Windows-путь с пробелом и кириллицей, POSIX-путь, свой шаблон); storage (запись/чтение, битый JSON, версия, переполнение квоты, чистка несуществующих id); search (ранжирование); savestate (все переходы, конфликт, повтор). Опциональный e2e: правка файла на диске → карточка обновилась без действий пользователя; правка файла между открытием формы и blur → модальное окно конфликта.
Приёмка WP8: на проекте из 200 эндпоинтов нужный узел находится за ≤ 3 нажатия клавиш; от карточки до строки в редакторе — 1 клик; F5 не сбрасывает ничего из списка в 8.3; сломанный файл в проекте виден в «Диагностике», а не молчит.
WP9. CLI (закрывает F28, F10)
Команды: studio (без аргументов = dev), dev [--project P] [--host H] [--port N] [--no-open] [--poll S] [--verbose], scan [--json], list [--json], export [--out PATH], doctor [--json] [--strict], install-full, help, --version.
studio doctor — диагностика одной командой (и для CI). Пример вывода:
pacage-studio 4.0.0 · проект: C:\Users\me\myapp
Источники:
  ✓ fastbase      app.api.v1.routes   12 файлов · 34 эндпоинта
  ✓ core-package  app.scenarios        9 файлов · 21 сценарий
  ✗ event-infra   models.py            не найден → создайте models.py или задайте STUDIO_MODELS
  ✓ tests         tests/              18 файлов · 96 тестов
Окружение: .env.fastbase найден (FAT_API_PREFIX=/api/v1)
Граф: узлов 210 · связей 305 · неполных 4
Диагностика: 1 ошибка · 3 предупреждения
  E_SYNTAX  app/scenarios/x.py:41 — синтаксическая ошибка: …
Итог: есть ошибки
Коды возврата: 0 — порядок (предупреждения допустимы, если нет --strict); 1 — есть error-диагностики (или warning при --strict); 2 — неверные аргументы / проект не найден.
Остальное:
• scan --json печатает только JSON (counts, diagnostics, fingerprint) — удобно для скриптов; без --json — человекочитаемо. list --json — аналогично.
• Исправить порядок в cmd_list: slog.start_run вызывается до скана.
• Все сообщения CLI — по-русски (сейчас no scenarios: run studio scan first и др. на английском).
• Ожидаемые ошибки (нет проекта, занят порт, нет прав записи) — одна строка по-русски + код 1, без трейсбека; STUDIO_DEBUG=1 печатает трейсбек.
• _safe_stdio: если поток не TTY (перенаправление в файл/пайп) — reconfigure(encoding="utf-8", errors="replace"); для TTY ничего не менять.
• Корень проекта: если --project не задан и в cwd нет признаков проекта (.env.fastbase, app/, models.py), поискать вверх до 6 уровней каталог с этими признаками; выбранный корень всегда печатать первой строкой.
• install-full: использовать FASTBASE_FULL_SPEC (WP1), печатать команду перед запуском, обрабатывать OSError/ненулевой код понятным сообщением.
• export: писать атомарно (atomic_write), --out задаёт путь.
Тесты WP9: main(["--version"]); doctor на demo-проекте (код 0), на проекте с битым файлом (код 1), с --strict и предупреждением (код 1); scan --json парсится json.loads; --project на несуществующий путь → код 2 и русское сообщение; перенаправленный вывод без UnicodeEncodeError; поиск корня вверх по дереву.
Приёмка WP9: сценарий «установил → studio doctor → studio» проходит без чтения документации; ни одна ожидаемая ошибка не печатает трейсбек.
WP10. Документация, CI, релиз 4.0.0 (закрывает F26)
10.1. Документация — один источник правды на тему. Сейчас README, ИНСТРУКЦИЯ, УСТАНОВКА и исходное ТЗ повторяют друг друга (правила маркеров — в трёх местах плюс exporter._rules_section). Новая структура:
Файл
Содержание
README.md
не больше одного экрана: что это, 3 команды (установка → studio doctor → studio), ссылки на docs/
docs/usage.md
бывшая ИНСТРУКЦИЯ: вкладки, повседневный цикл, горячие клавиши, «Открыть в редакторе»
docs/install.md
бывшая УСТАНОВКА (venv, обновление, Windows/PowerShell)
docs/metadata-format.md
единственное описание маркеров # studio:, якорей (включая #n, path::anchor), правил сирот, экранирования
docs/troubleshooting.md
таблица «код диагностики → причина → что делать» для каждого кода из diagnostics.CODES
docs/SPEC.md
исходное ТЗ + раздел «Изменения 4.0» (S1–S11)
CHANGELOG.md
формат Keep a Changelog; запись 4.0.0
Текст правил маркеров лежит в studio/metadata/rules.md (ресурс пакета); exporter._rules_section читает его оттуда, а тест test_rules_in_sync проверяет, что тот же текст дословно присутствует в docs/metadata-format.md. Тест test_docs_cover_codes проверяет, что каждый код диагностики упомянут в troubleshooting.md.
10.2. CI — .github/workflows/ci.yml: матрица os: [ubuntu-latest, windows-latest] × python: [3.11, 3.12, 3.13]; шаги pip install -e ".[dev]" → ruff check → pytest; отдельное задание с Node 20 для JS-тестов. Windows в матрице обязателен (CRLF, пути с пробелами, блокировки файлов — реальные риски этого пакета).
10.3. Упаковка. Если нет файла LICENSE (в pyproject заявлена MIT) — добавь. В pyproject — keywords, classifiers, [project.urls]. Проверь локально python -m build и что в wheel лежат studio/ui/static/** и studio/metadata/rules.md.
10.4. Релиз. __version__ = "4.0.0"; запись в CHANGELOG.md с разделом «Несовместимые изменения»: схема графа v6 (старый graph.json пересоздаётся автоматически); формат id io-узла (#n; старый :LINE в edge-блоках читается); API требует токен; новые коды возврата CLI. Раздел «Обновление»: перезапустить studio, обновить страницу (Ctrl+F5).
Приёмка WP10: чистая установка по README работает; CI зелёный на обеих ОС; нет файла документации, дублирующего правила маркеров.
WP11. Экспорт ТЗ для AI-агента — «мост разработчик → агент» (новая функция, S12)
Как я понимаю идею. Разработчик описывает систему как карту «запрос → код» прямо в проекте. Узлы, которых ещё нет в коде, он заводит как планируемые: через вкладку «Создание» или руками блоками # studio: (описание, критерии приёмки, домен, связи). WP11 даёт одну кнопку и одну команду, которые собирают самодостаточное ТЗ для AI-агента: правила работы + вся карта проекта с путями к узлам + подробные задачи на реализацию планируемых узлов. Разработчику не нужно писать ТЗ вручную: он расширяет систему только описаниями в студии. Цикл замкнут: описать → экспортировать ТЗ → агент пишет код → studio verify → карта обновляется сама (WP6).
Заменяет старый studio export (STUDIO_METADATA.md: правила ручной правки + отчёт). Тот остаётся устаревшим псевдонимом studio brief --map full с предупреждением; тесты exporter обновить под новый документ. Расположение: studio/agent/ (brief.py, rules.md, verify.py); exporter/ удалить после переноса.
11.1. Границы: что попадает в ТЗ
• Задача = узел с planned=true и непустым description. Планируемый узел без описания — не задача: он идёт в раздел «Не готово к реализации» с причиной. Флаг --include-empty (в UI — чекбокс) превращает его в задачу «реализуй по имени и связям».
• Узлы с кодом задачами не бывают (правка существующего кода — вне WP11, см. допущения).
• Область: по умолчанию все задачи; --only <якорь|id> … (повторяемый); --scenario <имя> — все планируемые узлы, лежащие в одном связном (по FLOW-рёбрам, без учёта направления) потоке с этим сценарием/эндпоинтом.
• Режим карты --map: full (все узлы с описаниями), related (полностью — окрестность задач ≤ 2 шага по рёбрам плюс цепочки «эндпоинт → сценарий» этих узлов; остальное — одной строкой), compact (одна строка на узел), none (только задачи). По умолчанию related, если узлов > 150, иначе full.
• Размер: считать chars и approx_tokens = chars / 3; при chars > 60 000 — предупреждение «ТЗ большое: сузьте область (--only/--scenario) или режим карты».
• Задач нет → документ без раздела «Задачи» (режим «только карта»: полезен как контекст любому агенту); CLI печатает «Нет узлов к реализации», код 0.
11.2. Обогащение узлов (в сканере) — чтобы агент видел контракты соседей, а не только имена. Всё берётся из SourceFile (WP2), без повторного чтения; сбой ast.unparse → пустое поле, не исключение.
• Node.end_line — последняя строка узла (end_lineno), для образцов кода.
• details.signature: endpoint — декораторы + def name(args) -> ret (без тела); scenario — class X(Base) + сигнатура execute; test — сигнатура. details.doc — первая строка docstring (≤ 200 символов).
• details.fields у моделей: [{name, annotation, default}] из AnnAssign тела класса, плюс table.
• Edge.origin: "code" | "metadata" — найдено анализом кода или только из блока edge (если пара совпала — code, а вид ребра берётся из метаданных, как сейчас). Нужен для verify.
11.3. Структура документа (build_brief(graph, sources, options) -> Brief; детерминирован: один граф → одинаковые байты, кроме строки «Сформировано»). Все пути — относительные, с /, корень проекта указан один раз в шапке.
# ТЗ для AI-агента: <проект>
Сформировано: <дата> · studio <версия> · brief <id12> · отпечаток кода <fp12>
Область: … · Карта: related · Задач: N · Не готово: M

1. Что нужно сделать   — 3–5 строк постановки + таблица задач: № | вид | имя | целевой файл | зависит от
2. Правила работы (обязательны)        — текст из studio/agent/rules.md (11.4)
3. Правила этого проекта                — содержимое studio.rules.md из корня (если файл есть)
4. Задачи                               — карточки T1…Tn в порядке реализации (11.5)
5. Карта проекта                        — 5.1 условные обозначения ([ПЛАН] = узла нет в коде)
                                          5.2 дерево: домен → метод → эндпоинт → сценарий → io → модель,
                                              в строке: вид, имя, `path:line`, домен, описание ≤ 120 симв.
                                          5.3 модели и таблицы (поля)   5.4 тесты (что покрывают)
                                          5.5 узлы вне путей запроса (неполные)
6. Не готово к реализации               — планируемые узлы без описания + что заполнить
7. Предупреждения по коду               — диагностики error/warning (файлы, которым карта не доверяет)
8. Формат блоков `# studio:`            — дословно studio/metadata/rules.md (единый источник, WP10)
9. Отчёт и проверка                     — формат отчёта и команды (11.4, п. 13–14)
<!-- studio-brief:v1 {json: brief_id, fingerprint, tasks[]} -->   # для studio verify
Порядок задач — топологический по FLOW-рёбрам (модели → io → сценарии → эндпоинты → тесты); при цикле — по виду, затем по id; цикл — предупреждение в разделе 7.
11.4. Правила для агента (studio/agent/rules.md) — текст фиксирован, версионируется (rules_version в шапке). Реализуй дословно:
1. Реализуй только задачи из раздела «Задачи». Не добавляй функциональность, не рефакторь и не «улучшай» остальной код.
2. Идти строго по порядку списка: он учитывает зависимости (модели → io → сценарии → эндпоинты → тесты).
3. Подражай образцам: у каждой задачи указан образец — реализованный узел того же вида. Повтори его структуру, импорты, именование, обработку ошибок и стиль. Не выдумывай API фреймворка — используй то, что видно в образцах и на карте.
4. Целевой файл указан в задаче. Файл-план содержит только комментарии # studio: — замени его реальным кодом, сохранив все блоки # studio: без изменений над своим якорем (description, acceptance, domain, edge). Формат блоков не менять, новые виды блоков не придумывать.
5. Имя класса/функции — дословно якорь из задачи (регистр и написание).
6. Связи из карточки («Связи») должны появиться в коде так, как это сделано в образце (вызов сценария через run("…"), вызов метода порта, обращение к модели). Блоки edge не удалять.
7. Каждый критерий приёмки — пункт чек-листа. Для каждого напиши тест в tests/ по образцу существующих; имя теста содержит имя узла (иначе студия не свяжет тест с узлом). Запусти тесты проекта.
8. Вне целевых файлов меняй только минимально необходимое для подключения (импорт, регистрация роутера/модели, миграция — если проект так устроен). Каждое такое изменение указать в отчёте.
9. Не добавлять зависимости; не трогать .env*, .studio/, конфигурацию; не делать git commit/push; не удалять файлы, кроме замены файла-плана.
10. Если данных не хватает или описание противоречит коду — не угадывай: сделай безопасный минимум и запиши вопрос в отчёт.
11. Не доверяй карте в файлах из раздела «Предупреждения по коду».
12. Не выводи в отчёт и не копируй в код значения секретов и содержимое .env*.
13. После работы выполни studio verify <этот файл> и studio doctor --strict, исправь расхождения.
14. Итоговый отчёт: (а) по каждой задаче — статус и path:line; (б) изменения вне целевых файлов; (в) какие тесты запущены и результат; (г) допущения и вопросы; (д) вывод studio verify.
Расширение проекта: необязательный файл studio.rules.md в корне проекта (коммитится в git; пример — «пиши докстринги по-русски», «используем ruff») подставляется в раздел 3 дословно. Агент обязан соблюдать его так же, как п. 1–14; при конфликте побеждают п. 1–14.
11.5. Карточка задачи (формат фиксирован, поля не опускать; пустое — «не указано»):
### T2. Сценарий `CancelOrder`  [ПЛАН]
- Целевой файл: `app/scenarios/cancel_order.py` (сейчас файл-план)
- Якорь (имя в коде): `CancelOrder` · Домен: Заказы · Детали: <details узла: метод/путь/таблица…>
- Место в запросе: `POST /api/v1/orders/cancel` (T3, план) → **CancelOrder** → `_db.update(orders)`
- Что сделать: <description дословно, без сокращений>
- Критерии приёмки:
  - [ ] <acceptance[0]>
- Связи (должны появиться в коде):
  - вызывает сценарий `ValidateOrder` — `app/scenarios/validate_order.py:12` (реализован), сигнатура: `…`
  - обращается к таблице `orders` — модель `Order`, `models.py:34`, поля: `id: int`, `status: str`, …
  - вызывается из эндпоинта T3 (план)
- Образец: `app/scenarios/create_order.py:18-52` (сценарий, домен «Заказы»)
  ```python
  …до 40 строк кода образца…
  ```
- Можно переиспользовать (периферия образца): `app/schemas/order.py`, `app/errors.py`
- Проверка (`studio verify`): узел `CancelOrder` найден в коде; подтверждены рёбра: …; есть тест с `cancel_order` в имени
Правила наполнения: образец — реализованный (planned=false), complete=true узел того же вида; приоритет: тот же домен → тот же каталог → меньший id; для планируемого test — ближайший тест. Код образца ≤ 40 строк (при обрезке — «… обрезано»); строки вида (?i)(secret|token|password|api[_-]?key)\s*[:=] в образце заменяются на … (redact_sample). У соседей: путь, строка, сигнатура (11.2); у соседей-планов — ссылка Tn и их целевой файл. Образца нет — «образца нет: ориентируйся на ближайшие узлы карты» и предупреждение в разделе 7.
11.6. studio verify (замкнутый цикл). Читает brief (<!-- studio-brief:v1 … --> из файла или .studio/briefs/<id>.json; без аргумента — последний), делает свежий скан и по каждой задаче печатает статус:
• выполнено — узел найден по якорю и виду, planned=false (если файл поменялся — с пометкой «перенесён в path»);
• частично — узел есть, но не подтверждено кодом хотя бы одно ожидаемое ребро (Edge.origin не code), или нет теста при непустых критериях, или блоки description/acceptance отличаются от выданных в ТЗ (потеряны метаданные — сверка по хешу), или у узла появились error-диагностики;
• не сделано — узел всё ещё planned или не найден.
Код возврата: 0 — все выполнено; 1 — иначе; 2 — brief не найден. --strict не нужен (частично всегда ≠ 0), --json — машиночитаемо. Сборка brief сохраняет .studio/briefs/<id>.json и .studio/last-brief.json (внутри игнорируемого .studio/).
11.7. Интерфейсы
• CLI: studio brief [--out PATH | --stdout] [--only A …] [--scenario NAME] [--map full|related|compact|none] [--include-empty] [--json]; по умолчанию пишет .studio/agent-brief.md атомарно и печатает путь, число задач, размер и готовую фразу для агента: «Прочитай и выполни ТЗ из файла .studio/agent-brief.md, следуй его правилам». Если задачи не готовы (нет описаний) — перечислить их и что заполнить. studio verify [BRIEF] [--json]. studio doctor дополнительно печатает «К реализации: N · не готово: M».
• API: POST /api/brief ({scope, only[], scenario, map, include_empty}) → {markdown, brief_id, stats: {tasks, unready, chars, approx_tokens}, warnings[]}; POST /api/brief/save → {path}; GET /api/verify?brief=<id|last> → отчёт. Все — с токеном (WP6).
• UI: чип в шапке «К реализации: N» (N = готовые задачи; красная точка, если есть «не готово»); клик → окно «ТЗ для AI-агента»: слева список планируемых узлов с чекбоксами (готовые отмечены, «нет описания» — серые с подсказкой), переключатели «Карта: связанная / вся / кратко / нет» и «включать без описания»; справа предпросмотр (<pre>, только чтение) и счётчики (задач, ~токенов, предупреждение о размере); кнопки [Скопировать ТЗ], [Скопировать короткий промпт], [Скачать .md], [Сохранить в проект]. После «Создать» во вкладке «Создание» — действие в уведомлении «Сформировать ТЗ». После генерации чип показывает прогресс «Выполнено 2/5» (обновляется при каждом live-обновлении через /api/verify); клик по нему — список статусов с path:line (открывается в редакторе, WP8.1).
11.8. Ограничения и безопасность. Генерация — локальная и офлайн, без сетевых запросов; в документ не попадают значения из .env* (только имена используемых настроек из сводки окружения); ТЗ содержит фрагменты вашего кода и описания — перед отправкой во внешний AI-сервис это решает пользователь (в окне и в studio brief — одна строка-напоминание). Пакет по-прежнему не пишет код проекта (§10 исходного ТЗ): он выдаёт ТЗ и проверяет результат.
11.9. Тесты WP11
• golden на tests/golden/full с тремя планируемыми узлами (модель, сценарий, эндпоинт) и одним без описания: tests/golden/full.brief.md (дата, версия, отпечаток нормализуются); перегенерация через UPDATE_GOLDEN=1;
• детерминизм: два вызова build_brief дают одинаковые байты; пути только с / и без абсолютных (Windows-фикстура);
• порядок задач топологический, цикл → предупреждение; узел без описания — не в «Задачах», с --include-empty — в них;
• образец: приоритет домен → каталог → id; обрезка до 40 строк; redact_sample скрывает password = "…";
• режим related включает окрестность ≤ 2 шага полностью, остальное — одной строкой; порог 150 узлов выбирает режим по умолчанию;
• details.signature/fields/end_line и Edge.origin: фикстуры с декораторами, Annotated, моделью с Base;
• verify: до (все не сделано) → подменить файл-план реальным кодом (в тесте) → выполнено; без вызова run() — частично; потерянный блок description — частично; код 0/1/2;
• CLI brief --stdout, --only, --scenario, export (устаревший псевдоним, предупреждение); API /api/brief без токена → 403; предупреждение о размере > 60 000.
Приёмка WP11: на demo-проекте с тремя планами из «Создания» studio brief даёт файл, где у каждой задачи есть описание, критерии, целевой файл, связи с путями, образец и проверка; агент без дополнительных вопросов может начать работу; после реализации studio verify показывает 3/3 выполнено, а UI — «Выполнено 3/3».
Допущения WP11 (подтвердите): (1) правка уже реализованных узлов агентом не входит — только реализация планируемых; (2) файл по умолчанию лежит в .studio/ (вне git) — если агенту-IDE нужен файл в корне, используйте --out; кнопка «Скопировать ТЗ» работает всегда; (3) язык документа — русский (шаблон правил можно перевести отдельным rules.en.md); (4) список «правил проекта» берётся из studio.rules.md в корне.
6. Стратегия тестирования (сводка)
• Виды: unit; fuzz «мусорные файлы не роняют scan» (WP2); golden на полном проекте (WP4); round-trip записи на матрице форматов файлов (WP5); серверные интеграционные тесты с токеном/Host/Origin (WP6); JS-тесты чистых модулей на node:test (WP7–8); опциональный e2e на Playwright (маркер e2e, importorskip).
• Детерминизм: никаких sleep без таймаута — используй хелпер wait_until(cond, timeout=5); watcher-тесты — с poll=0.05.
• Все фикстуры проектов — через tmp_path; тесты не пишут в репозиторий (git status после прогона чист — проверяется в CI).
• Покрытие: studio/scanner, studio/metadata, studio/server ≥ 90 % строк (необязательный pytest-cov в dev); новые ветки без тестов не принимаются.
• Красный-зелёный: для каждого F-пункта аудита с меткой ✔/? в коммите должен быть тест, падающий на старом коде.
7. Порядок и зависимости
WP1 → WP2 → WP3 → (WP4 ‖ WP5) → WP6 → WP7 → WP8 → WP9 → WP11 → WP10. WP4 и WP5 независимы друг от друга после WP3, но выполнять последовательно. WP7 нельзя начинать до WP6 (статика и токен), WP8 — до WP7; WP11 — после WP4, WP5, WP6, WP8 и WP9 и строго перед WP10 (WP10 всегда последний: документация и релиз; документацию WP11 — docs/agent-brief.md, раздел в usage.md, третья команда в README — пишет WP10). Оценка объёма: WP1 S, WP2 M, WP3 M, WP4 L, WP5 M, WP6 M, WP7 M, WP8 L, WP9 S, WP11 L, WP10 S. После каждого WP — отчёт по правилу 9 из раздела 0.
8. Definition of Done (весь проект)
1. pip install -e ".[dev]" && ruff check && pytest зелёные на Linux и Windows (CI).
2. Стабильность: fuzz-тест «любой набор .py не роняет scan» проходит; ни одного except Exception: pass без записи диагностики/лога (grep); запись метаданных проходит инвариант «diff только внутри # studio:» на матрице LF/CRLF/BOM/без финального \n.
3. Идея: golden-проект даёт ожидаемые узлы, рёбра, домены и диагностики; домены не протекают; «неполные» считает только Python; каждая неподдерживаемая конструкция даёт диагностику с file:line.
4. UX: от установки до карты ≤ 3 команды; 1 клик от карточки до строки в редакторе; правка в IDE видна на карте ≤ 2–3 с без действий; F5 не сбрасывает раскладку/выбор/черновик; пустая карта всегда объясняет причину.
5. Безопасность: POST без токена, с чужим Host/Origin или не JSON отклоняется; CSP включён; консоль браузера чиста.
6. Чистота: нет неориентированных обходов, дублей _source_files/правил маркеров/логики «неполных»; версия в одном месте; index.html < 150 строк.
7. Документация и docs/SPEC.md соответствуют коду; CHANGELOG.md содержит «Несовместимые изменения». Дополнительно (WP11): на demo-проекте с тремя планируемыми узлами studio brief выдаёт ТЗ, где у каждой задачи есть описание, критерии, целевой файл, связи с путями, образец и проверка; после реализации studio verify показывает 3/3 «выполнено», UI — «Выполнено 3/3».
9. Чего НЕ делать
• Не добавлять runtime-зависимости (кроме pydantic), не подключать CDN, npm-сборку, фреймворки (React/Vue и т. п.) — UI остаётся «открыл index.html и работает офлайн».
• Не генерировать код проекта и не править ничего, кроме # studio: блоков и файлов-планов конструктора (§10 исходного ТЗ).
• Не менять синтаксис маркеров # studio:; не вводить новые разделы (description/acceptance/domain/edge — полный список).
• Не «чинить» неоднозначность молча (первый попавшийся узел, setdefault): всегда диагностика.
• Не выполнять код пользовательского проекта и не импортировать его модули — только AST.
• Не переписывать UI с нуля и не менять визуальный стиль в WP7; не делать больше одного крупного изменения на коммит.
• Не удалять и не ослаблять тесты ради зелёного прогона; не запускать ruff format по всему репозиторию.
• Не добавлять телеметрию и сетевые запросы наружу.
10. Допущения, которые стоит подтвердить владельцу пакета
1. Редактор по умолчанию — VS Code (vscode://), остальные — в списке; подойдёт ли это основной среде разработки?
2. localStorage в браузере для раскладки/черновика (S10) допустим — в проект ничего не пишется. Если нужно переносимое между машинами хранилище, понадобится файл в .studio/ (вне git) — это отдельное решение.
3. Направленное наследование доменов (S3) считается корректной трактовкой §5.7 («когда иной узел связывается с эндпоинтом»); если нужна иная семантика (например, наследование и «вверх» по uses), её надо зафиксировать до WP3.
4. install-full оставлен (с константой вместо дублей). Если команда не нужна — удалить в WP9 вместе с extra full.
5. Поведение стека fastbase/core-package (run, register_scenario, include_router, SQLModel) в аудите выведено только из кода сканера и тестов, исходников самих пакетов у меня не было — поэтому правило 7 раздела 0 («не выдумывать API») особенно важно в WP4.

---

## Изменения 4.0

- **S1.** Стабильный id io-узла: `io:{file}:{Scenario}._db.{method}#{n}`,
  где `n` — порядковый номер вызова метода порта в сценарии. Номер строки
  остаётся в `Node.line`. Старый формат `:LINE` читается в edge-блоках и
  разрешается на ближайший вызов того же метода.
- **S2.** Якорь в edge-блоке: `<якорь>` или `<путь/к/файлу.py>::<якорь>`.
  Голый неоднозначный якорь даёт `W_EDGE_AMBIGUOUS`; связь не применяется.
- **S3.** Домены наследуются направленно: endpoint → scenario → io → model
  и по `uses`/`depends`; через другой эндпоинт обход не идёт. Тест наследует
  домен цели своего ребра `tests`.
- **S4.** «Неполный узел» = недостижим из эндпоинта по направленным рёбрам
  потока. Считает только Python (`Node.complete`); exporter/CLI/UI используют
  это поле.
- **S5.** `ScenarioPath.endpoints: list[str]` — все эндпоинты-корни (прямой
  dispatch или цепочка `scenario_call`); `endpoint` — первый элемент.
- **S6.** Диагностики — часть графа (`graph.diagnostics`), схема графа v6.
- **S7.** Конфликт при сохранении формы: запрос несёт `base_hash` файла; при
  расхождении — 409 `file_changed`; перезапись только с `force: true`.
- **S8.** Сироты удаляются при явном сохранении; ответ содержит
  `removed_orphans`, UI показывает «Удалено устаревших блоков: N · Отменить».
- **S9.** Строка внутри метаданных, похожая на маркер
  (`studio: <раздел> start|end`), записывается с ведущим `\`, при чтении
  один ведущий `\` снимается.
- **S10.** Раскладка, зум и черновик «Создания» хранятся в localStorage
  браузера; в проект ничего не пишется.
- **S11.** Порт БД (`_db`) — одна константа `PORT_ATTR` в
  `studio/_constants.py`.
- **S12.** ТЗ для AI-агента: `studio brief`, `studio verify`, окно
  «ТЗ для AI-агента» в UI, `Node.end_line`, `details.signature/fields/doc`,
  `Edge.origin`, файлы `.studio/agent-brief.md` и `.studio/briefs/`,
  необязательный `studio.rules.md` в корне проекта. `studio export` —
  устаревший псевдоним `studio brief --map full`.

