"""Что проверяется: env-окружение и Pass 1 (indexer).

Описание словами: env читает .env.fastbase (кавычки, комментарии, дефолты),
indexer находит сценарии по декоратору, модели по __tablename__ и endpoint'ы
по @router.<method>, не заходя в tests/ и чужие директории.
"""

from pathlib import Path

from studio.env import (
    DEFAULT_API_PREFIX,
    DEFAULT_CORE_DISCOVER,
    DEFAULT_ROUTES_PACKAGE,
    dotted_to_path,
    module_name,
    parse_env_text,
    read_env,
)
from studio.scanner.ast_utils import EXCLUDED_DIRS
from studio.scanner.indexer import build_index, index_endpoints, index_models, index_scenarios


class TestEnv:
    def test_дефолты_без_файла(self, tmp_path):
        env = read_env(tmp_path)
        assert env.routes_package == DEFAULT_ROUTES_PACKAGE == "app.api.v1.routes"
        assert env.core_discover == DEFAULT_CORE_DISCOVER == "app.scenarios"
        assert env.api_prefix == DEFAULT_API_PREFIX == "/api/v1"
        assert env.has_models is False
        assert env.models_path == tmp_path / "models.py"

    def test_парсит_файл_снимает_кавычки(self, tmp_path):
        (tmp_path / ".env.fastbase").write_text(
            "# комментарий\n"
            "\n"
            "FAT_ROUTES_PACKAGE='pkg.api'\n"
            'FAT_CORE_DISCOVER="pkg.core"\n'
            "FAT_API_PREFIX=/x\n"
            "строка_без_равно\n"
            "=value\n",
            encoding="utf-8",
        )
        env = read_env(tmp_path)
        assert env.routes_package == "pkg.api"
        assert env.core_discover == "pkg.core"
        assert env.api_prefix == "/x"

    def test_модели_только_в_корне(self, demo):
        env = read_env(demo)
        assert env.has_models is True
        assert env.models_path == demo / "models.py"

    def test_parse_env_text_пустые_строки(self):
        assert parse_env_text("") == {}
        assert parse_env_text("   \n#\n") == {}
        assert parse_env_text("K=") == {"K": ""}

    def test_dotted_to_path(self, tmp_path):
        assert dotted_to_path(tmp_path, "a.b.c") == tmp_path / "a" / "b" / "c"
        assert dotted_to_path(tmp_path, "") == tmp_path

    def test_module_name(self, tmp_path):
        pkg = tmp_path / "app" / "scenarios"
        pkg.mkdir(parents=True)
        f = pkg / "x.py"
        f.write_text("", encoding="utf-8")
        assert module_name(tmp_path, f) == "app.scenarios.x"
        init = pkg / "__init__.py"
        init.write_text("", encoding="utf-8")
        assert module_name(tmp_path, init) == "app.scenarios"


class TestIndexer:
    def test_сценарии_по_декоратору(self, demo):
        index = build_index(read_env(demo))
        assert set(index.scenarios) == {
            "create_order",
            "charge_payment",
            "no_endpoint_scenario",
            "payout",
        }
        entry = index.scenarios["create_order"]
        assert entry.class_name == "CreateOrderScenario"
        assert entry.file_path == "app/scenarios/orders.py"
        assert entry.module == "app.scenarios.orders"
        assert entry.dto == "UserDTO"
        assert entry.response == "OrderOut"
        assert entry.doc == "Сценарий создания заказа."

    def test_сценарии_из_вложенного_каталога(self, demo):
        index = build_index(read_env(demo))
        assert index.scenarios["payout"].file_path == "app/scenarios/billing/payout.py"

    def test_модели_по_tablename(self, demo):
        index = build_index(read_env(demo))
        assert set(index.models) == {"users", "orders"}
        assert index.models["users"].class_name == "User"
        assert index.models["users"].file_path == "models.py"
        assert index.models["users"].doc == "Модель пользователя."
        assert index.models["orders"].tablename == "orders"
        assert index.models["orders"].doc == "Модель заказа."

    def test_модель_без_tablename_пропущена(self, demo):
        index = build_index(read_env(demo))
        assert all(m.class_name != "NoTable" for m in index.models.values())

    def test_endpointы_декораторы(self, demo):
        index = build_index(read_env(demo))
        by_name = {e.function_name: e for e in index.endpoints}
        assert set(by_name) == {"create_order", "list_orders", "health", "root"}
        assert by_name["create_order"].http_method == "post"
        assert by_name["create_order"].path == "/create"
        assert by_name["create_order"].doc == "Создать заказ."
        assert by_name["create_order"].id == (
            "endpoint:app/api/v1/routes/orders.py:create_order"
        )
        assert by_name["root"].path == ""
        assert by_name["root"].doc is None

    def test_router_tags(self, demo):
        index = build_index(read_env(demo))
        by_name = {e.function_name: e for e in index.endpoints}
        assert by_name["create_order"].tags == ["orders"]
        assert by_name["health"].tags == ["health"]

    def test_tests_не_индексируются(self, demo):
        junk = demo / "tests"
        junk.mkdir(exist_ok=True)
        (junk / "junk.py").write_text(
            "from core import register_scenario\n"
            '@register_scenario("junk_scenario")\n'
            "class Junk:\n"
            "    pass\n",
            encoding="utf-8",
        )
        index = build_index(read_env(demo))
        assert all("tests/" not in e.file_path for e in index.endpoints)
        assert all("tests/" not in s.file_path for s in index.scenarios.values())

    def test_исключённые_директории(self):
        assert EXCLUDED_DIRS >= {"tests", "alembic", "docs", ".venv", "venv", ".studio"}

    def test_index_пустого_проекта(self, tmp_path):
        index = build_index(read_env(tmp_path))
        assert index.scenarios == {} and index.models == {} and index.endpoints == []
