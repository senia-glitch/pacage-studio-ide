"""Что проверяется: валидация окружения `.env.fastbase`.

Описание словами: кривой FAT_ROUTES_PACKAGE/FAT_CORE_DISCOVER (абсолютный путь,
`..`, слэши, пробелы) заменяется дефолтом со шагом в лог, а не роняет scan
ValueError'ем из-за выхода за корень; dotted_to_path не выпускает чтение
за пределы проекта; scan на демо-проекте с битым env работает.
"""

from studio.env import (
    DEFAULT_CORE_DISCOVER,
    DEFAULT_ROUTES_PACKAGE,
    dotted_to_path,
    read_env,
)
from studio.scanner import scan
from studio.scanner.indexer import iter_module_files


class TestReadEnv:
    def test_дефолты_без_файла(self, tmp_path):
        env = read_env(tmp_path)
        assert env.routes_package == DEFAULT_ROUTES_PACKAGE
        assert env.core_discover == DEFAULT_CORE_DISCOVER

    def test_кривой_маршрут_заменяется_дефолтом(self, tmp_path):
        (tmp_path / ".env.fastbase").write_text(
            "FAT_ROUTES_PACKAGE=C:/secret/routes\n"
            "FAT_CORE_DISCOVER=../outside.core\n"
            "FAT_API_PREFIX=/api/v1\n",
            encoding="utf-8",
        )
        env = read_env(tmp_path)
        assert env.routes_package == DEFAULT_ROUTES_PACKAGE
        assert env.core_discover == DEFAULT_CORE_DISCOVER
        assert env.api_prefix == "/api/v1"

    def test_точечный_путь_принимается(self, tmp_path):
        (tmp_path / ".env.fastbase").write_text("FAT_ROUTES_PACKAGE=pkg.api.routes\n", encoding="utf-8")
        assert read_env(tmp_path).routes_package == "pkg.api.routes"


class TestDottedToPath:
    def test_обычный_путь(self, tmp_path):
        assert dotted_to_path(tmp_path, "a.b.c") == tmp_path / "a" / "b" / "c"

    def test_пустой_путь_корень(self, tmp_path):
        assert dotted_to_path(tmp_path, "") == tmp_path

    def test_относительный_выход_фильтруется(self, tmp_path):
        path = dotted_to_path(tmp_path, "..secret")
        assert tmp_path.resolve() in path.resolve().parents or path.resolve() == tmp_path.resolve()


class TestIterModuleFiles:
    def test_база_вне_корня_пусто(self, tmp_path):
        assert iter_module_files(tmp_path, "../../etc") == []

    def test_существующий_пакет(self, demo):
        files = iter_module_files(demo, "app.api.v1.routes")
        assert any(p.name == "orders.py" for p in files)


class TestScanWithBadEnv:
    def test_scan_не_падает_с_битым_env(self, demo):
        (demo / ".env.fastbase").write_text(
            "FAT_ROUTES_PACKAGE=<BAD>\nFAT_CORE_DISCOVER=/abs/path\n",
            encoding="utf-8",
        )
        graph = scan(demo)
        assert any(n.kind == "endpoint" for n in graph.nodes)
        assert any(n.kind == "scenario" for n in graph.nodes)
