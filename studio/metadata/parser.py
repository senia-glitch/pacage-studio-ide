"""Разбор блоков `# studio: <раздел> start/end` и экранирование (S9)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from studio.diagnostics import Sink
from studio.metamodel import EDGE_KINDS

PACKAGE_MARK = "studio"
SECTIONS = ("description", "acceptance", "domain", "edge")

START_RE = re.compile(r"^\s*#\s*studio:\s*([a-z]+)\s+start\s*$")
END_RE = re.compile(r"^\s*#\s*studio:\s*([a-z]+)\s+end\s*$")
MARKER_TEXT_RE = re.compile(r"^\s*\\*(studio:\s*[a-z]+\s+(?:start|end))\s*$")
NOTE_RE = re.compile(r"^\s*#\s*Желаемое имя/якорь:")


@dataclass
class Block:
    section: str
    start: int
    end: int
    content: list[str] = field(default_factory=list)
    indent: str = ""


@dataclass
class EdgeSpec:
    from_anchor: str
    to_anchor: str
    kind: str
    file_path: str
    line: int


def _content(line: str) -> str:
    text = line.lstrip()
    if text.startswith("#"):
        text = text[1:]
        if text.startswith(" "):
            text = text[1:]
    return text


def _unescape(text: str) -> str:
    if re.match(r"^\s*\\+studio:\s*[a-z]+\s+(?:start|end)\s*$", text):
        match = re.match(r"^(\s*)\\", text)
        if match is not None:
            return text[: match.end() - 1] + text[match.end() :]
    return text


def escape_content(text: str) -> str:
    """Строка содержимого, похожая на маркер, получает ведущий `\\`."""
    if MARKER_TEXT_RE.match(text):
        indent = text[: len(text) - len(text.lstrip())]
        return f"{indent}\\{text[len(indent):]}"
    return text


def parse_blocks(
    lines: list[str], sink: Sink | None = None, rel_path: str = ""
) -> list[Block]:
    """Найти пары start/end. Незакрытые и вложенные блоки дают диагностики."""
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        match = START_RE.match(lines[i])
        if match is None or match.group(1) not in SECTIONS:
            i += 1
            continue
        section = match.group(1)
        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        content: list[str] = []
        valid = True
        end: int | None = None
        j = i + 1
        while j < len(lines):
            end_match = END_RE.match(lines[j])
            if end_match is not None and end_match.group(1) == section:
                end = j
                break
            nested = START_RE.match(lines[j])
            if nested is not None and nested.group(1) in SECTIONS:
                valid = False
                if sink is not None:
                    sink.add(
                        "W_NESTED_BLOCK",
                        f"Вложенный блок studio: {nested.group(1)} внутри {section}",
                        file=rel_path,
                        line=j + 1,
                        hint="Разделы не вкладываются: закройте внешний блок перед новым.",
                    )
            content.append(lines[j])
            j += 1
        if end is None:
            if sink is not None:
                sink.add(
                    "W_UNCLOSED_BLOCK",
                    f"Блок studio: {section} не закрыт",
                    file=rel_path,
                    line=i + 1,
                    hint=f"Добавьте строку `# studio: {section} end`.",
                )
            i += 1
            continue
        if valid:
            blocks.append(Block(section=section, start=i, end=end, content=content, indent=indent))
        i = end + 1
    return blocks


def block_text(block: Block) -> str:
    return "\n".join(_unescape(_content(line)) for line in block.content)


def block_lines(block: Block) -> list[str]:
    return [_unescape(_content(line)) for line in block.content]


def edge_from_block(block: Block, file_path: str) -> EdgeSpec | None:
    values: dict[str, str] = {}
    for line in block_lines(block):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in ("from", "to", "kind"):
            values[key] = value.strip()
    if not values.get("from") or not values.get("to"):
        return None
    kind = values.get("kind", "")
    if kind not in EDGE_KINDS:
        return None
    return EdgeSpec(
        from_anchor=values["from"],
        to_anchor=values["to"],
        kind=kind,
        file_path=file_path,
        line=block.start + 1,
    )


def _block_text(block: Block) -> str:
    """Совместимость с 3.x."""
    return block_text(block)
