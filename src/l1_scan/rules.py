# -*- coding: utf-8 -*-
"""L1 分类规则：八个功能大类（C1–C8）的判据表。

规则来源：`docs/09-文件功能大类与命名特征调研.md`（11 个系统的调研 + OHOS 真树实测）。
**本文件只放数据与纯函数，不做 I/O**——遍历与读文件在 `treemodel.py`。

三层判据，逐层叠加，前一层命中就不进下一层：

| 层 | 依据 | 代价 | 解决什么 |
| -- | ---- | ---- | -------- |
| 1 名字 | 完整文件名 → 后缀 | 0 | 后缀唯一的类型（`.c`、`.mk`、`.ld`…） |
| 2 路径 | 父目录 | 0 | 同一后缀在不同目录下语义不同（`debian/control`、`config.gni`） |
| 3 内容 | 正文开头的键名/指令 | 一次 2 KB 读 | 后缀缺失或歧义（`.hcs`、`.cfg`、`*.json`） |

名字层与路径层都是纯字符串比较，不碰磁盘。内容层只在「名字层认不出」或
「后缀本身有歧义」时才触发（见 `CONTENT_EXT`）。
"""

from __future__ import annotations

import re
from typing import NamedTuple

# --------------------------------------------------------------------------- #
# 遍历时固定剪掉的目录
# --------------------------------------------------------------------------- #

#: 不提供配置项：这四类目录在**所有**被测工程里都不是源码——
#: VCS 元数据、repo 工具的元数据、构建产物、预编译包。
#: 需要增减只能改这里，避免「换一棵树就多一份配置」。
EXCLUDED_DIRS: tuple[str, ...] = (".git/", ".repo/", "out/", "prebuilts/")

# --------------------------------------------------------------------------- #
# 大类 → 提取器
# --------------------------------------------------------------------------- #

#: classes 字段的合法取值与排序（S2 已从范围移除）
CLASS_ORDER: tuple[str, ...] = ("S1", "S3", "S4", "S5", "S6", "S7")

#: 每个大类要交给哪些提取器。依据 docs/09 §33.2.3 的 S×C 矩阵，
#: 只保留「主」与「确实可能承载的次」，避免每个文件都喂给全部提取器。
#: 两条约束：与 schema/filelists.schema.json 的 examples 一致——
#:   bundle.json（C3）→ S3,S6；BUILD.gn（C2）→ S1,S4,S5,S7。
CATEGORY_CLASSES: dict[str, tuple[str, ...]] = {
    "C1": ("S1", "S4", "S5"),        # 脚本：命令行、CC/CROSS_COMPILE、脚本里的 -l
    "C2": ("S1", "S4", "S5", "S7"),  # 构建描述：自定义命令、编译选项、链接参数、架构
    "C3": ("S3", "S6"),              # 模块清单：组件身份/依赖 + 对外头文件
    "C4": ("S3", "S4", "S7"),        # 板级配置：产品含哪些模块、工具链、开关取值
    "C5": ("S5",),                   # 链接脚本与符号表
    "C6": ("S6",),                   # C/C++ 源码
    "C7": ("S3", "S7"),              # 开关 schema：开关属于哪个模块 + 开关定义
    "C8": ("S4", "S7"),              # 设备树/硬件描述：CPU、compatible、板级硬件
}

#: 大类的中文名，只用于统计与日志
CATEGORY_NAMES: dict[str, str] = {
    "C1": "可执行脚本",
    "C2": "构建描述",
    "C3": "模块/包清单",
    "C4": "产品/板级/工具链配置",
    "C5": "链接与符号",
    "C6": "C/C++ 源码",
    "C7": "配置开关 schema",
    "C8": "设备树/硬件描述",
}

#: 「一个大类装不下」的文件：在基础 classes 之外追加提取器。
#: docs/09 §33.2.2 明确说了 `.bb` 既是构建描述又是包清单、`Android.bp` 同理，
#: 而单个 category 只能表达一个。这里只补「确实承载」的那一个，不滥加。
EXTRA_CLASSES_BY_SUFFIX: dict[str, tuple[str, ...]] = {
    ".bp": ("S3",), ".bb": ("S3",), ".bbappend": ("S3",), ".bbclass": ("S3",),
}
EXTRA_CLASSES_BY_NAME: dict[str, tuple[str, ...]] = {
    # NuttX 把板级取值写在 board.h 里：既是被编译的头文件（S6），又是板级配置（S4/S7）
    "board.h": ("S4", "S7"),
}
#: 路径条件化的追加：Yocto 的 `recipes-*/…/*.inc` 是 recipe 的一部分，装 DEPENDS/SRC_URI；
#: 而 `conf/distro/include/*.inc` 只是全局变量，不该给 S3。
EXTRA_CLASSES_BY_PATH: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"(?:^|/)recipes-[^/]+/.*\.inc$"), ("S3",)),
)

# --------------------------------------------------------------------------- #
# 第 1 层：名字
# --------------------------------------------------------------------------- #

#: 完整文件名（小写）→ 大类。**先于后缀判断**，用来纠正「后缀像 A、实际是 B」。
#: 例如 BoardConfig.mk 是板级配置而不是构建描述，setup.py 是包清单而不是脚本。
EXACT_NAME: dict[str, str] = {
    # C2 构建系统入口（没有统一后缀，只能靠整名）
    "makefile": "C2", "gnumakefile": "C2", "cmakelists.txt": "C2",
    "meson.build": "C2", "build.gn": "C2", "sconscript": "C2", "sconstruct": "C2",
    "kbuild": "C2", "meson_options.txt": "C2", "meson.options": "C2",
    "make.defs": "C2", "makefile.targets": "C2", "makefile.in": "C2",
    # C3 模块/包清单
    "bundle.json": "C3", "ohos.build": "C3", "package.json": "C3",
    "pyproject.toml": "C3", "cargo.toml": "C3", "go.mod": "C3", "setup.py": "C3",
    # C4 板级/产品/工具链配置（含三处「后缀会判错」的纠正）
    "config.json": "C4", "boardconfig.mk": "C4", "config.gni": "C4",
    "rtconfig.py": "C4", "local.conf": "C4", "bblayers.conf": "C4",
    "prj.conf": "C4", "cmakepresets.json": "C4", "sdkconfig": "C4",
    "defconfig": "C4",
    # C5 链接脚本（无 .ld 后缀的那种）
    "ld.script": "C5",
}

#: 大小写敏感的整名。Buildroot 用大写 `Config.in`，Autotools 生成的是小写
#: `config.in`（内容完全不同：一个是 Kconfig，一个是 `#undef` 头文件模板）。
#: 所以这一条不能跟着 `.lower()` 走。
CASE_EXACT_NAME: dict[str, str] = {
    "Config.in": "C7",
}

#: 文件名前缀 → 大类。Kconfig 家族覆盖 `Kconfig`、`Kconfig.debug`、`Kconfig.defconfig`；
#: `Config.in` / `Config.in.legacy` 是 Buildroot 对同一套语法的叫法。
NAME_PREFIX: tuple[tuple[str, str], ...] = (
    ("kconfig", "C7"),
)

#: 后缀（小写）→ 大类。注意 `.config`/`.para` 是取值文件，归 C4 不归 C7。
SUFFIX: dict[str, str] = {
    ".sh": "C1", ".bash": "C1", ".zsh": "C1", ".py": "C1", ".pl": "C1",
    ".rb": "C1", ".bat": "C1", ".ps1": "C1",
    ".mk": "C2", ".mak": "C2", ".gn": "C2", ".gni": "C2", ".cmake": "C2", ".bp": "C2",
    ".bb": "C2", ".bbappend": "C2", ".bbclass": "C2", ".inc": "C2",
    # Autotools：configure.ac / Makefile.am / 宏包
    ".ac": "C2", ".am": "C2", ".m4": "C2",
    ".ld": "C5", ".lds": "C5", ".map": "C5", ".symbols": "C5", ".vers": "C5",
    ".c": "C6", ".cc": "C6", ".cpp": "C6", ".cxx": "C6", ".h": "C6",
    ".hh": "C6", ".hpp": "C6", ".hxx": "C6", ".inl": "C6", ".s": "C6",
    ".dts": "C8", ".dtsi": "C8", ".dtso": "C8", ".overlay": "C8", ".hcs": "C8",
    ".para": "C4", ".config": "C4",
    # Meson 的 subprojects/*.wrap 声明外部依赖（source_url / directory / revision）
    ".wrap": "C3",
}

# --------------------------------------------------------------------------- #
# 第 2 层：路径
# --------------------------------------------------------------------------- #

#: 路径规则先于后缀：同一后缀在不同目录下语义不同。
PATH_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Debian 的 control 只在 debian/ 下才是包清单；别处的 control 不是
    (re.compile(r"(?:^|/)debian/control$"), "C3"),
    # debian/rules 是 Makefile（通常以 #!/usr/bin/make -f 开头），按构建描述处理
    (re.compile(r"(?:^|/)debian/rules$"), "C2"),
    # OpenHarmony 产品定义目录下的 JSON 一律是产品/组件清单
    (re.compile(r"(?:^|/)productdefine/common/(?:inherit|products|base)/[^/]+\.json$"), "C3"),
    # Yocto 的 conf/ 目录放全局构建配置（bitbake.conf、machine-sdk/*.conf…）。
    # 这些文件的锚点（TARGET_ARCH）常在正文 2 KB 之后，路径比内容可靠。
    (re.compile(r"(?:^|/)conf/(?:[^/]+/)*[^/]+\.conf$"), "C4"),
)

# --------------------------------------------------------------------------- #
# 第 3 层：内容锚点
# --------------------------------------------------------------------------- #

#: 只有这些后缀（或名字层完全认不出）才读内容。
#: 后缀唯一的类型（.c/.hcs/.para…）直接由名字层判掉，读内容纯属浪费。
#: 实测：`.xml` / `.toml` / `.ini` / `.txt` 没有任何锚点会命中它们，
#: 放进这里只会白白多读一遍文件（OHOS 树里光 manifest XML 就 290 个）。
CONTENT_EXT: frozenset[str] = frozenset({
    ".json", ".cfg", ".conf",
})


class Anchor(NamedTuple):
    """一条内容锚点规则。"""

    name: str          # 规则名，出现在统计里
    category: str      # 命中后归入的大类
    pattern: re.Pattern[bytes]
    only_ext: frozenset[str] | None = None   # 限定后缀；None 表示不限
    weak: bool = False                        # 弱规则：要至少 2 处命中且正文无结构符号


def _rx(pattern: str) -> re.Pattern[bytes]:
    """编译一条作用于 bytes 的多行正则（`(?m)` 统一加在开头）。"""
    return re.compile(pattern.encode("ascii"), re.MULTILINE)


#: 顺序即优先级：特异性高的在前，宽泛的在后。
ANCHORS: tuple[Anchor, ...] = (
    Anchor("hdf",       "C8", _rx(r"match_attr\s*=|hostName\s*=")),
    Anchor("hdf-root",  "C8", _rx(r"^root\s*\{"), only_ext=frozenset({".hcs"})),
    Anchor("dts",       "C8", _rx(r"/dts-v1/;")),
    Anchor("symbol-map", "C5", _rx(r"\{\s*global\s*:")),
    Anchor("ldscript",  "C5", _rx(r"ENTRY\s*\(|OUTPUT_FORMAT\s*\(|SECTIONS\s*\{")),
    Anchor("kconfig",   "C7", _rx(r"^\s*(?:menu)?config\s+[A-Za-z0-9_]+|^\s*choice\b")),
    Anchor("product",   "C4", _rx(r'"product_name"\s*:|"target_cpu"\s*:')),
    Anchor("gn-board",  "C4", _rx(r"^board_[a-z_]+\s*=|^kernel_type\s*=")),
    Anchor("rtthread",  "C4", _rx(r"^ARCH\s*=|^CROSS_TOOL\s*=")),
    Anchor("android",   "C4", _rx(r"^TARGET_(?:ARCH|BOARD|CPU|CC|OS)\b")),
    Anchor("yocto-machine", "C4", _rx(r"^#@TYPE:\s*Machine")),
    Anchor("yocto-conf", "C4", _rx(r"^(?:MACHINE|DISTRO|KERNEL_IMAGETYPE|UBOOT_MACHINE|SERIAL_CONSOLES)\s*[?+]?=")),
    Anchor("losconfig", "C4", _rx(r"^(?:CONFIG|LOSCFG)_[A-Z0-9_]+\s*=")),
    Anchor("subsystems", "C3", _rx(r'"subsystems"\s*:')),
    Anchor("bundle",    "C3", _rx(r'"component"\s*:|"dependencies"\s*:')),
    Anchor("debian",    "C3", _rx(r"^Source:\s|^Package:\s")),
    Anchor("bitbake",   "C3", _rx(r"^SRC_URI\s*=|^DEPENDS\s*[+?]?=")),
    Anchor("cmake",     "C2", _rx(r"cmake_minimum_required|^\s*(?:add_executable|add_library|target_link_libraries)\s*\(")),
    Anchor("gn",        "C2", _rx(r"declare_args\s*\(|^\s*source_set\s*\(")),
    Anchor("android-bp", "C2", _rx(r"cc_library\s*\{|cc_binary\s*\{|soong_namespace")),
    Anchor("init-cfg",  "C1", _rx(r'"jobs"\s*:|"cmds"\s*:')),
    Anchor("c-include", "C6", _rx(r'#include\s*[<"]')),
)

#: 读内容之前就排除：文档、图片、二进制、仓库杂项。挡掉它们能省掉大量误报。
SKIP_CONTENT_EXT: frozenset[str] = frozenset({
    ".md", ".rst", ".txt~", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".svg", ".pdf", ".zip", ".gz", ".tar", ".so", ".a", ".o", ".img",
    ".bmp", ".webp", ".woff", ".ttf",
})
SKIP_CONTENT_NAME_PREFIX: tuple[str, ...] = (
    "license", "copying", "notice", "readme", "changelog", ".git",
)

# --------------------------------------------------------------------------- #
# 纯函数：名字层 + 路径层
# --------------------------------------------------------------------------- #

def match_name(relpath: str) -> str | None:
    """按「完整文件名 → 前缀 → 后缀」给大类；认不出返回 None。

    `relpath` 用正斜杠分隔的相对路径。不读文件，不碰磁盘。
    """
    hit = match_name_detail(relpath)
    return hit[0] if hit else None


def match_name_detail(relpath: str) -> tuple[str, str] | None:
    """同 `match_name`，但返回 (大类, 依据)。依据 ∈ {exact, prefix, suffix}。

    依据决定强度：exact / prefix 是「这个文件名就是这么规定的」，内容层不该推翻；
    suffix 只是「这个后缀通常是这类」，内容层可以纠正。
    """
    raw_name = relpath.rsplit("/", 1)[-1]

    # 大小写敏感的少数整名先判（Config.in vs config.in）
    hit = CASE_EXACT_NAME.get(raw_name)
    if hit is not None:
        return hit, "exact"
    if raw_name.startswith("Config.in."):
        return "C7", "prefix"

    name = raw_name.lower()

    hit = EXACT_NAME.get(name)
    if hit is not None:
        return hit, "exact"

    for prefix, category in NAME_PREFIX:
        if name.startswith(prefix):
            return category, "prefix"

    # Linux / Zephyr / OHOS 的 `*_defconfig`（下划线，不是后缀）
    if name.endswith("_defconfig"):
        return "C4", "suffix"

    dot = name.rfind(".")
    if dot > 0:                      # dot == 0 是隐藏文件（.gitignore），没有后缀
        ext = name[dot:]
        hit = SUFFIX.get(ext)
        if hit is not None:
            return hit, "suffix"
    return None


def match_path(relpath: str) -> str | None:
    """按路径规则给大类；没有命中返回 None。"""
    for pattern, category in PATH_RULES:
        if pattern.search(relpath):
            return category
    return None


def needs_content(relpath: str, name_category: str | None) -> bool:
    """判断这个文件是否值得读内容。

    两种情况读：名字层认不出；后缀本身有歧义（`.json` 可能是包清单/产品配置/普通配置）。
    文档、图片、仓库杂项一律不读。
    """
    name = relpath.rsplit("/", 1)[-1].lower()
    dot = name.rfind(".")
    ext = name[dot:] if dot > 0 else ""

    if ext in SKIP_CONTENT_EXT:
        return False
    if name.startswith(SKIP_CONTENT_NAME_PREFIX):
        return False
    if name_category is None:
        # 名字认不出时，只读「可能是配置」的：已知的歧义后缀，或没有后缀（rcS、qemu-run）。
        # 其余（.xml/.rs/.bin…）没有任何锚点会命中，读了也是白读。
        return ext in CONTENT_EXT or ext == ""
    return ext in CONTENT_EXT


def sniff_content(body: bytes, ext: str) -> tuple[str, str] | None:
    """在正文开头里找锚点，返回 (大类, 规则名)；都不命中返回 None。

    `body` 应当是「跳过注释头之后」的字节，`ext` 是小写后缀（用于限定规则范围）。
    """
    for anchor in ANCHORS:
        if anchor.only_ext is not None and ext not in anchor.only_ext:
            continue
        hits = anchor.pattern.findall(body)
        if not hits:
            continue
        if anchor.weak:
            # 弱规则：至少 2 处，且正文里没有结构符号（避免把普通文本里的 a=b 当参数表）
            if len(hits) < 2 or any(ch in body for ch in b"{}()<>"):
                continue
        return anchor.category, anchor.name
    return None


def classes_for(category: str | None, relpath: str = "") -> tuple[str, ...]:
    """大类 → 提取器列表（已去重、已按 CLASS_ORDER 排序）。

    `relpath` 用于叠加 `EXTRA_CLASSES_*`：有些文件一个大类装不下（`.bb` 既是
    构建描述又是包清单），只能在这里补。
    """
    if category is None:
        return ()
    classes = set(CATEGORY_CLASSES[category])

    name = relpath.rsplit("/", 1)[-1].lower()
    dot = name.rfind(".")
    ext = name[dot:] if dot > 0 else ""
    classes.update(EXTRA_CLASSES_BY_SUFFIX.get(ext, ()))
    classes.update(EXTRA_CLASSES_BY_NAME.get(name, ()))
    for pattern, extra in EXTRA_CLASSES_BY_PATH:
        if pattern.search(relpath):
            classes.update(extra)

    return tuple(sorted(classes, key=CLASS_ORDER.index))
