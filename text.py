import os
import sys

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__",
    "build", "dist", "node_modules",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".studio",
    ".idea", ".vscode",
    "frontend", "удалить",
}
SKIP_EXT = {
    ".pyc", ".pyo", ".log", ".lock",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".pdf",
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".db", ".sqlite",
}
MAX_SIZE = 2 * 1024 * 1024


def is_binary(path):
    with open(path, "rb") as f:
        return b"\x00" in f.read(8192)


def scan_and_save(root_dir, output_file="files_content.txt"):
    script = os.path.abspath(__file__)
    out = os.path.abspath(output_file)
    count = 0

    with open(output_file, "w", encoding="utf-8") as f:
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # обрезаем обход до захода в каталог
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.endswith(".egg-info")
            ]
            for name in filenames:
                path = os.path.join(dirpath, name)
                if os.path.abspath(path) in (script, out):
                    continue
                if os.path.splitext(name)[1].lower() in SKIP_EXT:
                    continue
                try:
                    if os.path.getsize(path) > MAX_SIZE or is_binary(path):
                        continue
                except OSError:
                    continue

                f.write(f"Файл: {path}\nСодержимое:\n")
                try:
                    with open(path, "r", encoding="utf-8") as src:
                        f.write(src.read())
                except UnicodeDecodeError:
                    try:
                        with open(path, "r", encoding="cp1251") as src:
                            f.write(src.read())
                    except Exception:
                        f.write("[бинарный файл]")
                except Exception as e:
                    f.write(f"[ошибка: {e}]")
                f.write("\n\n")
                count += 1

        f.write(f"\nВсего: {count}")

    print(f"{output_file}: {count} файлов")


if __name__ == "__main__":
    scan_and_save(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())