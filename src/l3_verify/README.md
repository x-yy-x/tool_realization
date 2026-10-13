# L3 · 验证层

> **本文回答**：六个验证器具体怎么写——到命令、判据这一层。
> **对应**：原设计文档 §18（第 0 关树完整性）+ §20（验证器的实现）。
> **保留原 § 编号**。

[← L2 提取](../l2_extract/README.md) · [L4 判读与修改 →](../l4_triage_fix/README.md)

---

**本层职责**：拿 `requirements.json` 里的每条需求，**去问目标环境**，产出 `findings.json`。

|                |                                                                                                |
| -------------- | ---------------------------------------------------------------------------------------------- |
| **输入** | `requirements.json` + 目标环境                                                               |
| **输出** | `findings.json`（**带原始证据**）                                                      |
| **代码** | `src/l3_verify/`                                                                             |
| **闸门** | 第 0 关（`v_tree`）、第 3–6 关（`v_exists`/`v_identity`/`v_consistency`/`v_reach`） |

**五个验证原语**：存在性 / 身份 / 集合运算 / 可达性 / 不变量。**12 种** `kind` 的需求映射到六个验证器——**多对一**。

> **`v_coverage` 不在本层**——它是提取器自检，归 [`L2`](../l2_extract/README.md)（代码在 `src/l2_extract/`）。

---

## 18. 第 0 关：树完整性

**必须最先实现，且失败要阻断。**

> 第 1 关的七个提取器**读的就是这棵树**。
> 树缺了一半，提取出的清单也缺一半——**而你无法从清单本身看出来它缺了**。
> 后面所有关卡都建立在一个静默错误的结果上。

### 18.1 哨兵文件（对应 `SYNC-002`）

```yaml
# config.yaml
sentinel_files:
  - build/build.sh
  - build/ohos.gni
  - build/config/BUILDCONFIG.gn
  - .repo/manifest.xml
```

检查：逐个 `test -f <root>/<path>`。任一缺失即 `block`。

### 18.2 仓清单（对应 `SYNC-003` / `SYNC-005`）

```python
# 从 manifest 拿 path 列表，逐个 test -d
for proj in manifest.projects.values():
    d = root / proj.path
    if not d.is_dir():
        findings.append(missing(proj))
```

### 18.3 固定提交（对应 `BUILD-026`）

```python
import subprocess

def head_of(repo_dir):
    r = subprocess.run(['git', '-C', str(repo_dir), 'rev-parse', 'HEAD'],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip() if r.returncode == 0 else None

for proj in manifest.projects.values():
    want = proj.revision
    if not want or not is_sha(want):
        continue                      # 分支/标签先跳过，只钉死 SHA 的比对
    got = head_of(root / proj.path)
    if got is None:
        report(proj, 'not-a-git-repo')
    elif got != want:
        report(proj, 'revision-mismatch', want=want, got=got)
```

`is_sha()` = 40 位十六进制。**只比对 SHA 形式的 revision**——分支名会随时间漂移，比对没有意义，硬比会误报。

### 18.4 阻断行为

默认 `--fail-fast`：第 0 关不过就不往下跑，退出码 2。
留 `--force` 给「我知道树不全，只想扫现状」的场景，此时报告头部打免责声明。

---

---

## 20. 验证器的实现

**六个**，都不复杂，关键是**每条都留下原始证据**。

| 验证器            | 原语                                           | 判读要点                                       |
| ----------------- | ---------------------------------------------- | ---------------------------------------------- |
| `v_tree`        | 哨兵文件 + manifest 对账 + 固定提交            | 树不完整时后面全是静默错误（第 18 节）         |
| `v_coverage`    | 文件类型普查 − 提取器认领集                   | 第 2 关：防「扫描器自己漏了」（19.8）          |
| `v_exists`      | `command -v` / `test -f` / `test -d`     | **三态**：存在 / 不存在 / 存在但不可执行 |
| `v_identity`    | `-dumpmachine` / `readlink -f` / `-E -v` | 存在 ≠ 对                                     |
| `v_consistency` | 纯集合运算，不碰系统                           | 闭包差、缺口差、架构映射                       |
| `v_reach`       | `getent hosts` → `curl -I --max-time`     | 三档分层                                       |

> **版本白名单（OS / Python / Node）已不在这一关。** 它只需要 WSL + 配置，不需要源码树，
> 已挪到第 -1 层的 `precheck/p_base.py`（见第 7.0 节、第 26.7 节、第 27 节）。
> 本关的 `v_identity` 只留**需要第 1 关提取出的工具名**才能做的那三项。

### 20.1 三态而不是布尔

```python
def v_exists_cmd(name):
    r = run(['sh', '-c', f'command -v {shlex.quote(name)}'])
    if r.returncode != 0:
        return 'missing'
    path = r.stdout.strip()
    if not os.access(path, os.X_OK):
        return 'not-executable'          # ← 这一态最容易被漏掉
    return 'ok', path
```

`not-executable` 单独成一态：**文件在，但没 x 位**，修复动作和「没装」完全不同（一个 `chmod`，一个 `apt install`）。

### 20.2 v_identity：存在 ≠ 对

三元素的直接实现：

```sh
# ① 身份：是不是我要的目标架构
$CC -dumpmachine            # 期望 riscv32-unknown-elf

# ② 真身：是不是我期望的那份，还是 PATH 里另一个同名货
readlink -f "$(command -v $CC)"

# ③ 头文件搜索路径对不对
$CC -E -v - </dev/null 2>&1 | sed -n '/#include <...>/,/End of search/p'
```

典型场景：`arm-linux-gnueabi-gcc` 和 `arm-none-eabi-gcc` **都存在**，
但一个只能编用户态、一个只能编裸机。**只查存在性会漏，必须查身份。**

### 20.3 v_consistency：三条集合运算

都不碰系统，纯算：

```python
missing_parts = closure(seeds, idx) - set(idx)          # 部件闭包差
gaps          = used_types - declared_includes          # 使用 × 声明差
arch_ok       = arch_map.get(target_cpu) == kbuild_arch # 映射一致性
```

**这三条是全部检查里性价比最高的**：零 I/O、零外部依赖、结果确定。

### 20.4 v_reach：三档分层

```
① DNS     getent hosts <host>
            ↳ 解析不了 = 网络/DNS 问题，到此为止
② 连接    curl -I --silent --max-time 10 <url>
③ 判读    按 HTTP 码分档：
```

| 返回码                   | 判定                            | 对应问题              |
| ------------------------ | ------------------------------- | --------------------- |
| `200` / `302`        | 通                              | —                    |
| `401` / `403`        | **通，但凭证/权限有问题** | `SYNC-004` 就是这档 |
| `404`                  | 仓名或路径错                    | —                    |
| `000` / `curl: (28)` | 超时，网络不通                  | `NET-*`             |
| `curl: (6)`            | DNS 解析失败                    | `NET-*`             |

**分档的意义**：`403` 和「连不上」是**完全不同的修复动作**。报成一句「仓库不可达」等于没报。

每个请求都要 `--max-time`——**验证器自己不能挂住**，这是 `SYNC-001` 的教训。

---
