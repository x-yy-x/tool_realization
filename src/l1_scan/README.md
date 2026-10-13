# L1 · 扫描层

> **本文回答**：怎么把一棵源码树变成一份带分类的文件清单。
> **对应**：[`02` §29.1](../../docs/02-流水线与关.md)（四层里的 L1）+ 原设计文档 §16（目录结构）。


[← 实现设计（共用地基）](../README.md) · [L2 提取 →](../l2_extract/README.md)

---

**本层职责**：走一遍源码树，**只记路径、不读内容**，产出 `filelist.json`。

|            |                                                                        |
| ---------- | ---------------------------------------------------------------------- |
| **输入**   | 源码树根目录 + `config.yaml`（排除规则、哨兵路径）                 |
| **输出**   | `filelist.json`（每条带 `kind` 标签）                            |
| **代码**   | `src/l1_scan/treemodel.py`                                           |
| **执行点** | 第 0 关（[`02` §7.2](../../docs/02-流水线与关.md)），跑在 **WSL、有完整源码树** |
| **有副作用** | 无——只 `readdir`                                                    |

---

## 16.1 做什么：四步

| 步 | 动作                                                      | 代价                            |
| -- | --------------------------------------------------------- | ------------------------------- |
| ① | **遍历**：`os.scandir` 递归走全树，**只记路径不打开文件** | **秒级**（~184203 文件）  |
| ② | **排除**：剪掉不该看的目录                                | —                              |
| ③ | **分类**：按后缀 + 按路径给每个文件打 `kind`        | —                              |
| ④ | **固化**：写出 `filelist.json`                        | —                              |

### 关键：遍历 ≠ 解析

这是 L1 和 L2 的**唯一分界**，也是整套设计能「秒级扫描」的原因：

|          | 做什么                     | 碰多少文件          | 代价   |
| -------- | -------------------------- | ------------------- | ------ |
| **遍历** | 只 `readdir` 拿路径    | ~184203（全部）   | 秒级   |
| **解析** | 打开文件读内容             | ~12930（认得的那部分） | 分钟级 |

**遍历全场只做一次**（就是这里）。L2 的所有提取任务都从 `filelist.json` 出发，**不再走树**——「中央遍历，分散解析」（[`02` §29.2](../../docs/02-流水线与关.md)）。

---

## 16.2 排除哪些

| 目录          | 处理                            | 为什么                                        |
| ------------- | ------------------------------- | --------------------------------------------- |
| `.git/`     | 完全跳过                        | 不是源码                                      |
| `.repo/`    | 跳过内容，**保留 manifest** | manifest 要读（S2）                          |
| `out/`      | 跳过内容，**保留路径**    | `BUILD-010` 要判「`out/` 里有没有同名目录」 |
| `prebuilts/` | **只建索引，不解析**      | `SYNC-005` 要判存在性                       |

**注意后三行不是「全跳」，是「跳内容、留索引」。** 一律 `os.walk(..., topdown=True)` 里改 `dirnames` 原地剪枝。

排除规则写在 `config.yaml`，不硬编——换一棵树可能要调。

---

## 16.3 分类表

**这就是「文件分类 → 不同文件走不同识别」的那张表**（[`02` §29.2](../../docs/02-流水线与关.md) 的输入侧）。

| `kind`       | 匹配                              | 归 L2 的哪个任务用           |
| ------------ | --------------------------------- | ---------------------------- |
| `shell`    | `*.sh` / `*.bash` / 带 shebang | S1（工具）、K1（语法）       |
| `make`     | `Makefile` / `*.mk`            | S1、K1                       |
| `gn`       | `BUILD.gn` / `*.gni`           | S1（action）、S2、S5      |
| `product`  | `vendor/*/config.json`           | S3、S7                     |
| `bundle`   | `**/bundle.json`                 | S3                           |
| `manifest` | `.repo/manifests/*.xml` + `.repo/manifest.xml` | S2            |
| `header`   | `*.h` / `*.hpp`                | S6（**声明侧**）       |
| `csrc`     | `*.c` / `*.cc` / `*.cpp`       | S6（**使用侧**）       |
| `ldscript` | `*.ld` / `*.lds`               | S5                           |
| `kconfig`  | `*_defconfig` / `Kconfig*`      | S7                           |
| `ninja`    | `out/**/*.ninja`                 | 第 7 关（半静态档）          |
| `other`    | 其余                              | **第 2 关的哨兵**      |

**最后一行是第 2 关的全部意义**：`other` 里若出现**一个提取器都不认识的新文件类型**，说明规则缺了一类——补规则，不是补特例（[`01` §9](../../docs/01-问题与论证.md)）。

> ⚠️ **分类只按后缀与路径，不读内容。** 所以 `kind` 会误判（比如一个没有 `.sh` 后缀但带 `#!/bin/sh` 的脚本）——兜底靠 shebang 探测（只读前 2 字节）。

---

## 16.4 输出：`filelist.json`

```json
{
  "meta": { "source_root": "/srv/oh", "scanned_at": "2026-10-13T10:00:00+08:00" },
  "files": [
    { "path": "build/scripts/build.sh", "kind": "shell", "size": 4213 }
  ],
  "stats": {
    "total_files_seen": 184203,
    "by_kind": { "shell": 1204, "gn": 9021, "other": 41207 }
  }
}
```

契约见 [`schema/filelist.schema.json`](../../schema/filelist.schema.json)。

`stats.total_files_seen` 和 `by_kind` **不是装饰**——第 2 关的 `v_coverage`（[`L2` §19.8](../l2_extract/README.md)）拿它们和提取器的认领集求差集。

---

## 16.5 `treemodel.py` 的接口

```python
def walk(root, excludes, shebang_probe=True) -> list[FileEntry]:
    """全树遍历 + 排除 + 分类。唯一一次走树。"""

def classify(path, name) -> str:
    """路径 + 文件名 → kind。纯函数，无 I/O（shebang 探测除外）。"""

def parse_manifest(root) -> tuple[dict[str, Project], list[dict]]:
    """读 .repo/manifest.xml，返回 (name → {path, revision, remote}, warnings)。

    注意是「已合并的本地 manifest」，不是 .repo/manifests/*.xml（那份有 <include>）。
    三个坑 + `<extend-project>` 那条 warn 见 L2 §19.1。
    """

def bundle_index(root, filelist) -> dict[str, Path]:
    """从 filelist 里挑出 bundle 类，建 name → path 索引。不重复走树。"""
```

**`parse_manifest` / `bundle_index` 放本层，不放 L2**——它们都是「把树读成一个模型」，是 L2 所有提取任务的地基。L2 只消费模型，不做遍历。

---

## 16.6 已知边界

| 边界                                     | 现状                                        |
| ---------------------------------------- | ------------------------------------------- |
| 符号链接环                               | `os.scandir` 不跟随符号链接，天然避开       |
| 超大仓（`prebuilts/` 几十 GB）         | 只建索引不解压，代价是 `readdir` 次数      |
| Windows 侧跑（提取开发时）               | 路径分隔符要 `pathlib`，不能拼 `/`       |
| 第 0 关的**树完整性判定**（哨兵/对账） | **不在这里**——是 L3 的 `v_tree`，见 [`L3` §18](../l3_verify/README.md) |
