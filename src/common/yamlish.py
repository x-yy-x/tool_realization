"""极简 YAML 子集解析器（零第三方依赖）。

为什么需要它：设计原则是「Python 3 标准库，零依赖」（src/README.md §15.2），
但配置与规则库按设计是 `.yaml`。标准库没有 YAML。所以这里只实现本项目实际用到的子集。

支持
----
- 注释（行首或 ` #` 之后，引号内的 `#` 不算）
- 块状映射 `key: value`
- 块状序列 `- value` / `- key: value`
- 嵌套（按缩进）
- 标量：整数 / 浮点 / true / false / null / 单双引号字符串
- 行内列表 `[a, b, c]`
- 行内映射 `{a: 1, b: 2}`

不支持（遇到会明确报错，不静默猜）
----------------------------------
- 块标量 `|` 与 `>`
- 锚点 / 别名 / 标签（`&` `*` `!`）
- 多文档（`---`）
- 流式嵌套超过一层

这是有意的取舍：配置文件是给人手写的，用不到上面这些；真需要时再换 PyYAML，
而不是现在为了「格式完整」把零依赖原则破掉。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class YamlError(ValueError):
    """带行号的解析错误。"""


def load_file(path: str | Path) -> Any:
    return load(Path(path).read_text(encoding="utf-8"))


def load(text: str) -> Any:
    """把 YAML 文本解析成 Python 对象。"""
    lines = _prepare(text)
    if not lines:
        return None
    value, i = _parse_block(lines, 0, lines[0][0])
    if i != len(lines):
        raise YamlError(f"line {lines[i][2]}: 无法解析的剩余内容: {lines[i][1]!r}")
    return value


# --------------------------------------------------------------------------- #
# 预处理
# --------------------------------------------------------------------------- #

def _prepare(text: str) -> list[tuple[int, str, int]]:
    """去掉空行与注释，返回 (缩进, 内容, 行号)。"""
    out: list[tuple[int, str, int]] = []
    for no, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("---") or raw.startswith("..."):
            continue
        stripped = raw.lstrip(" ")
        indent = len(raw) - len(stripped)
        if "\t" in raw[:indent]:
            raise YamlError(f"line {no}: 缩进不能用制表符")
        content = _strip_comment(stripped).rstrip()
        if not content:
            continue
        out.append((indent, content, no))
    return out


def _strip_comment(s: str) -> str:
    quote = ""
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i]
        i += 1
    return s


# --------------------------------------------------------------------------- #
# 块解析
# --------------------------------------------------------------------------- #

def _parse_block(
    lines: list[tuple[int, str, int]], i: int, indent: int
) -> tuple[Any, int]:
    if lines[i][1].startswith("- ") or lines[i][1] == "-":
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_seq(
    lines: list[tuple[int, str, int]], i: int, indent: int
) -> tuple[list[Any], int]:
    items: list[Any] = []
    while i < len(lines) and lines[i][0] == indent:
        content, no = lines[i][1], lines[i][2]
        if not (content.startswith("- ") or content == "-"):
            break
        rest = content[1:].lstrip()
        if not rest:
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                value, i = _parse_block(lines, i + 1, lines[i + 1][0])
            else:
                value, i = None, i + 1
            items.append(value)
            continue

        if _looks_like_kv(rest):
            # `- key: value`：把这一行当成缩进 +2 的映射首行，后续更深行归它
            sub = [(indent + 2, rest, no)]
            j = i + 1
            while j < len(lines) and lines[j][0] > indent:
                sub.append(lines[j])
                j += 1
            value, consumed = _parse_map(sub, 0, indent + 2)
            if consumed != len(sub):
                raise YamlError(f"line {sub[consumed][2]}: 序列项内的映射缩进不一致")
            items.append(value)
            i = j
        else:
            items.append(_scalar(rest, no))
            i += 1
    return items, i


def _parse_map(
    lines: list[tuple[int, str, int]], i: int, indent: int
) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while i < len(lines) and lines[i][0] == indent:
        content, no = lines[i][1], lines[i][2]
        if content.startswith("- ") or content == "-":
            break
        key, rest = _split_kv(content, no)
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                value, i = _parse_block(lines, i + 1, lines[i + 1][0])
            else:
                value, i = None, i + 1
        else:
            value, i = _scalar(rest, no), i + 1
        out[key] = value
    return out, i


# --------------------------------------------------------------------------- #
# 标量
# --------------------------------------------------------------------------- #

def _looks_like_kv(s: str) -> bool:
    try:
        _split_kv(s, 0)
        return True
    except YamlError:
        return False


def _split_kv(s: str, no: int) -> tuple[str, str]:
    quote = ""
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == ":" and (i + 1 == len(s) or s[i + 1] == " "):
            return s[:i].strip(), s[i + 1:].strip()
    raise YamlError(f"line {no}: 期望 `key: value`，实际是 {s!r}")


def _scalar(s: str, no: int) -> Any:
    s = s.strip()
    if s in ("|", ">", "|-", ">-", "|+", ">+"):
        raise YamlError(f"line {no}: 不支持块标量 {s!r}")
    if s[:1] in ("&", "*", "!"):
        raise YamlError(f"line {no}: 不支持锚点 / 别名 / 标签")
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        body = s[1:-1]
        if s[0] == '"':
            body = body.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return body
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part, no) for part in _split_top(inner, ",")]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        out: dict[str, Any] = {}
        for part in _split_top(inner, ","):
            key, value = _split_kv(part.strip(), no)
            out[key] = _scalar(value, no)
        return out
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_top(s: str, sep: str) -> list[str]:
    """按 sep 切分，但忽略引号与方括号 / 花括号内部。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return parts
