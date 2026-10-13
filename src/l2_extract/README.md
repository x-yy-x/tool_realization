# L2 · 提取层

> **本文回答**：七个提取任务具体怎么写——到函数、正则、命令这一层。
> **对应**：原设计文档 §19。**保留原 § 编号**（`19.3.5` 等被 [`05`](../../docs/05-实施顺序与风险.md)、[`06`](../../docs/06-修复段.md) 引用）。


[← 实现设计（共用地基）](../README.md) · [L3 验证 →](../l3_verify/README.md)

---

**本层职责**：读 `filelist.json`，**只解析自己认得的那几类文件**，产出 `requirements.json`。

| | |
| --- | --- |
| **输入** | `filelist.json`（[`L1`](../l1_scan/README.md) 产出，带 `kind` 标签）+ `rules/*.yaml` |
| **输出** | `requirements.json` |
| **代码** | `src/l2_extract/` |
| **闸门** | 第 1 关（七个 S 任务）+ 第 2 关（`v_coverage` 自检） |

**「中央遍历，分散解析」**——树只在 L1 走一次，解析按任务分头做（[`02` §29.2](../../docs/02-流水线与关.md)）。

> ⚠️ **`v_coverage.py` 放在本层目录**，不放在 `l3_verify/`——它是**提取器自己的哨兵**（「我漏了哪类文件」），归属提取层；L2 的人同时拥有提取器和它的自检。

---

## 19. 七个提取器的实现

排序按**确定性从高到低**：先写读 XML/JSON 的，后写读自由文本的。

### 19.1 S2 仓需求（确定性最高之一）

**解析对象**：`.repo/manifest.xml`（已合并的本地 manifest，`revision` 已解析），**不是** `.repo/manifests/*.xml`（有 `<include>` 要自己合）。

```python
import xml.etree.ElementTree as ET

def strip_ns(el):
    for e in el.iter():
        if '}' in e.tag:
            e.tag = e.tag.split('}', 1)[1]

def parse_manifest(path):
    """返回 (projects, warnings)。"""
    root = ET.parse(path).getroot()
    strip_ns(root)

    d = root.find('default')
    default_rev = d.get('revision') if d is not None else None

    projects, removed, warnings = {}, set(), []
    for el in root:                       # 按文档顺序处理
        if el.tag == 'remove-project':
            removed.add(el.get('name'))
        elif el.tag == 'extend-project':
            # 只加属性、不新增仓 → 仓清单按 no-op 处理。
            # 但它能改 dest-path，**会改变树模型**，所以不能静默跳过，记一条 warn。
            warnings.append({'kind': 'extend-project', 'name': el.get('name'),
                             'why': '可能重定向 dest-path，树模型未必等于 manifest 字面'})
            continue
        elif el.tag == 'project':
            projects[el.get('name')] = {
                'path': el.get('path') or el.get('name'),
                'revision': el.get('revision') or default_rev,
                'remote': el.get('remote') or (d.get('remote') if d is not None else None),
            }
    for n in removed:
        projects.pop(n, None)
    return projects, warnings
```

**三个坑 + 一个陷阱**：

| 项                              | 处理                                                      |
| ------------------------------- | --------------------------------------------------------- |
| `xmlns` 命名空间              | `strip_ns` 剥掉，否则 `find('project')` 永远返回 None |
| `<default revision>` 继承     | 没写`revision` 的仓继承 default                         |
| `<remove-project>` 的次序     | **必须按文档顺序**处理，否则移除不生效              |
| **`<extend-project>`**    | 仓清单按 no-op，**但记一条 `warn`**——见下             |

> **`extend-project` 为什么不能静默跳过**：它明面上只给已有仓加/改属性、不新增仓，所以**不影响仓清单**——这一点按 no-op 是对的。
> 但它能改 `dest-path`，而 `dest-path` 决定仓**落在树里的哪个位置**。
> 静默跳过的话，扫描器算出的树模型会和真实树悄悄错位，**后面所有基于路径的判断（S2/S3/S6）都跟着错**。
> 所以：**清单不认它，但要报出来让人看一眼。** 这是「局部 no-op，全局有影响」这类规则的通例。

**路径引用补充**（这是 `BUILD-023/024/026` 的形态）：

```python
DEP_RE = re.compile(r'"//([A-Za-z0-9_./-]+?)(?::[A-Za-z0-9_]+)?"')

def referenced_repos(root):
    """扫 BUILD.gn 的 //path 引用，取前两段作为仓路径。"""
    refs = set()
    for gn in root.rglob('BUILD.gn'):
        for m in DEP_RE.finditer(read(gn)):
            parts = m.group(1).split('/')
            if len(parts) >= 2:
                refs.add('/'.join(parts[:2]))
    return refs
```

然后 `referenced − manifest_paths` = **「源码里引用了但 manifest 里没有」的仓**——这就是 `BUILD-023/024/026` 的根因形态。

### 19.2 S3 部件需求（**完全确定性，最适合做 golden test**）

```python
def bundle_index(root):
    """全树扫 bundle.json，建 name → path 索引。"""
    idx = {}
    for p in root.rglob('bundle.json'):
        try:
            b = json.loads(read(p))
        except json.JSONDecodeError:
            continue
        if 'name' in b:
            idx[b['name']] = p
    return idx

def closure(seeds, idx):
    """从种子出发按 deps.components 迭代到不动点。"""
    seen, stack = set(), list(seeds)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        b = json.loads(read(idx[name])) if name in idx else {}
        stack.extend(b.get('deps', {}).get('components', []))
    return seen
```

种子来自 `vendor/<vendor>/<product>/config.json`：

```python
cfg = json.loads(read(root / f'vendor/{vendor}/{product}/config.json'))
seeds = [c['component']
         for sub in cfg['subsystems']
         for c in sub['components']]
```

**缺失集 = 闭包 − 索引键**。无启发式，可以直接断言。

### 19.3 S1 工具需求（最难的一个）

**为什么最难**：其他提取器读 XML/JSON 的结构化字段，S1 读 **shell 自由文本**。没有 schema 可依。

#### 19.3.1 输入范围

| 来源                       | 怎么处理                                                            |
| -------------------------- | ------------------------------------------------------------------- |
| `*.sh`                   | 分词器逐行分析（主战场）                                            |
| `Makefile` / `*.mk`    | 只取**TAB 开头**的 recipe 行                                  |
| `BUILD.gn` 的 `action` | 取`script=` **递归进去**（见 19.3.5），`args=` 里取裸命令 |
| `*.py`                   | **用 `ast` 模块解析**，不用正则（见 19.3.6）                |

#### 19.3.2 分词算法

```
输入行
  ↓ ① 去注释：找不在引号内的 '#'，截断
  ↓ ② 续行合并：行尾反斜杠 → 与下一行拼接
  ↓ ③ 切段：按 ;  &&  ||  |  换行  以及 do / then / else / {  切开
  ↓ ④ 逐段分词：尊重单引号、双引号、$() 、`` 的嵌套
  ↓ ⑤ 取段首词
  ↓ ⑥ 过黑名单
  ↓ ⑦ 拆包装器
  ↓ 产出需求
```

```python
# lib/shelltoken.py

def strip_comment(line):
    """去掉不在引号内的 # 之后的内容。"""
    out, quote, esc = [], None, False
    for ch in line:
        if esc:
            out.append(ch); esc = False; continue
        if ch == '\\' and quote != "'":
            out.append(ch); esc = True; continue
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch; out.append(ch); continue
        if ch == '#':
            break
        out.append(ch)
    return ''.join(out)


def join_continuations(text):
    lines, buf = [], ''
    for ln in text.splitlines():
        if ln.rstrip().endswith('\\'):
            buf += ln.rstrip()[:-1] + ' '
            continue
        lines.append(buf + ln); buf = ''
    if buf:
        lines.append(buf)
    return lines


_SEG = re.compile(r'(?:;|&&|\|\||\||\n|\bthen\b|\bdo\b|\belse\b|\{|\})')

def segments(line):
    for seg in _SEG.split(line):
        seg = seg.strip()
        if seg:
            yield seg
```

#### 19.3.3 黑名单（**准确率的关键**）

**内容已落盘**：[`../rules/tool_denylist.txt`](../../rules/tool_denylist.txt)。三段——shell 关键字、POSIX 内建、控制符号。

**这里有个值得注意的一致性**：黑名单排除了 `test`（它是 shell 内建），这正解释了为什么 `test -f` 能零依赖地当探测原语；
而 `timeout` **不是**内建，所以 `ENV-013` 才会炸。**同一套知识在检查设计和实现两处都成立。**

#### 19.3.4 包装器要拆两层，不能只取一层

`timeout 500 hb build` 里，`timeout` 和 `hb` **都是需求**：

```python
# 包装器分两类——这个区分不是抠细节，见下方「为什么 env 不算需求」
WRAPPERS      = {'sudo', 'nohup', 'nice', 'ionice', 'setsid', 'stdbuf'}  # 本身也是需求
SKIP_WRAPPER  = {'env', 'time'}          # 只是包装语法，不算需求；但该条降 low
TIMEOUT_LIKE  = {'timeout'}              # 不是包装器，是 ENV-013 的主角

def unwrap(seg):
    """返回这一段涉及的全部工具名（含包装器本身）+ 是否该降置信度。"""
    toks = tokenize(seg)
    found, low, i = [], False, 0
    while i < len(toks):
        t = toks[i]
        if t in SKIP_WRAPPER:
            low = True                                   # env FOO=1 make → 只要 make
            i += 1
            while i < len(toks) and (toks[i].startswith('-') or '=' in toks[i]):
                i += 1
            continue
        if t in WRAPPERS:
            found.append(t)                              # 包装器本身也是需求
            i += 1
            while i < len(toks) and (toks[i].startswith('-') or '=' in toks[i]):
                i += 1                                   # 跳 sudo -n
            continue
        if t in TIMEOUT_LIKE:
            found.append(t)
            i += 1
            if i < len(toks) and toks[i].isdigit():
                i += 1                                   # 跳时长
            continue
        found.append(t)
        break
    return found, low
```

| 写法                      | 提取结果                                                                      |
| ------------------------- | ----------------------------------------------------------------------------- |
| `sudo -n apt install x` | `sudo`, `apt`                                                             |
| `timeout 500 hb build`  | `timeout`, `hb`                                                           |
| `env FOO=1 make`        | `make`（**跳过 `env`**），且该条降 `low`                            |
| `nohup ./run.sh &`      | `nohup`，且 `./run.sh` 是树内相对路径 → **不提取，标记为树内引用** |

**只取 `hb` 就会漏掉 `ENV-013`。** 这正是「包装器失败 ≠ 构建失败」在实现层的落点。

**为什么 `env` / `time` 不算需求，`sudo` 算？** 判据是「**它自己会不会失败**」：

| 包装器       | 会自己失败吗                             | 处理                       |
| ------------ | ---------------------------------------- | -------------------------- |
| `sudo`     | 会（没权限就直接失败，`ENV-006`/`008`） | **算需求**，报出来   |
| `timeout`  | 会（工具不存在 → `ENV-013`）           | **算需求**，报出来   |
| `env`      | **不会**——它只是设个变量然后 exec      | 跳过，降 `low`           |
| `time`     | **不会**——shell 内建（bash 里）        | 跳过，降 `low`           |

把 `env` 当需求产出会污染整张工具清单：**它几乎每条构建命令里都有**，报出来就是几十条噪声，而它永远不会是失败原因。

#### 19.3.5 GN action 必须递归

OpenHarmony 的典型写法：

```gn
action("mk_image") {
  script = "//device/qemu/arm_virt/mkimage.sh"
  args = [ "--img", rebase_path(target_out_dir) ]
}
```

**`args` 里根本没有命令**——命令在 `mkimage.sh` 里面。所以：

1. 读 `action` / `action_foreach` 的 `script=`
2. 若是 `//` 开头的树内路径 → **递归解析那个文件**（按 `.sh` 或 `.py` 处理）
3. `args` 里只挑裸字符串字面量，且过黑名单

**不做递归的话，S1 在 GN 上产出几乎为零**——而 GN 是 OpenHarmony 构建的主体。这是实现里必须避开的坑。

#### 19.3.6 Python 用 AST，不用正则

```python
subprocess.run(["mcopy", "-i", img, ...])
os.system("mkfs.vfat -F 32 " + img)
shutil.which("ninja")
```

正则会被字符串拼接、跨行、变量搞得一团糟。用标准库 `ast`：

```python
import ast

CALL_TARGETS = {
    'subprocess.run', 'subprocess.call', 'subprocess.check_call',
    'subprocess.check_output', 'subprocess.Popen',
    'os.system', 'os.popen', 'os.execv', 'os.execvp',
    'shutil.which',
}

def tools_from_python(src):
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = dotted_name(node.func)
        if fn not in CALL_TARGETS or not node.args:
            continue
        a = node.args[0]
        if isinstance(a, ast.List) and a.elts and isinstance(a.elts[0], ast.Constant):
            out.append(a.elts[0].value)             # 高置信
        elif isinstance(a, ast.Constant) and isinstance(a.value, str):
            out.append(a.value.split()[0])          # 按空格切，取首词
        else:
            out.append(low_conf(node))              # 拼接/变量 → 不可解析，记为 low 并留源码片段
    return out
```

**这正是 `ENV-011` 的教训**：跨层传递时字符串会被拆坏。AST 能看穿拼接，正则不能。

#### 19.3.7 置信度分级

不做假阳性治理，S1 会淹没报告（大型 shell 脚本集能产出几千个 token）。

| 级别       | 判定条件                                                                                          | 默认上报                         |
| ---------- | ------------------------------------------------------------------------------------------------- | -------------------------------- |
| `high`   | 出现在**已知构建入口脚本**里（`build.sh` / `mk.sh` / `mkimage.sh` / Makefile 主目标） | ✅                               |
| `medium` | 出现在任意`.sh` / Makefile recipe                                                               | ✅                               |
| `low`    | 出现在 GN args / Python 拼接 / 间接引用                                                           | ⚠️ 折叠，`--show-low` 才展开 |

外加 `rules/tool_denylist.txt` 人工抑制表——**承认启发式不可能完美，留一个手动出口比追求完美算法划算**。

### 19.4 S4 工具链需求

- 从 board config 读 `target_cpu`
- 从 `//build/toolchain/...` 找 `cc` / `cxx` / `ld` 变量（常是 `$ohos_cc` 形式的间接引用，**需追变量定义**；追不到就记 low 并留源码）
- **实用捷径**：OpenHarmony 的 clang 固定在 `prebuilts/clang/ohos/<host>/llvm/bin/` → **先当仓做存在性检查**，便宜且确定
- 验证走三元素（见第 20.2 节）

### 19.5 S5 库与符号

七个来源里，S5 唯一管的是**链接期的需求**。对照着看：

| 时期             | 要什么             | 谁管         | 声明写在哪                                                |
| ---------------- | ------------------ | ------------ | --------------------------------------------------------- |
| **编译期** | 头文件             | S6           | 源码的`#include`                                        |
| **链接期** | **库和符号** | **S5** | `BUILD.gn` / `Makefile` 的链接参数 + `.ld` 链接脚本 |

而链接期的需求写在**两个完全不同的地方**，所以要用两种手法去捞：

| 手法                   | 在哪捞                                                | 捞到什么                                 |
| ---------------------- | ----------------------------------------------------- | ---------------------------------------- |
| **扫链接参数**   | `BUILD.gn` / `Makefile` / 直接调 `$CC` 的命令行 | **库名**（`-lfoo`）              |
| **解析链接脚本** | `.ld` 文件                                          | **符号名**（`ENTRY()` / 段定义） |

#### 19.5.1 库那一半：扫链接参数

`-lfoo` 这个形式会在**五个地方**冒出来，得全扫（前四个是声明，第五个是前提）：

| 位置                           | 长什么样                                   | 说明                                                        |
| ------------------------------ | ------------------------------------------ | ----------------------------------------------------------- |
| `BUILD.gn` 的 `libs`       | `libs = [ "m", "dl", "nosys" ]`          | **GN 会自动加 `-l` 前缀**，所以写的是裸名           |
| `BUILD.gn` 的 `ldflags`    | `ldflags = [ "-lc", "-lnosys" ]`         | 这里写的是完整形式                                          |
| `Makefile` 变量              | `LDFLAGS += -lnosys` / `LDLIBS = -lc`  |                                                             |
| `.mk` / `.sh` 里的直接调用 | `$CC -o out $OBJS -lc -lnosys`           | 这种要过 S1 的分词器（19.3.1）                              |
| `-L` 搜索路径                | `ldflags = [ "-L/path/to/sysroot/lib" ]` | **这也是需求**——路径不存在的话 `-lfoo` 一样找不到 |

提取成：

```json
{"kind": "lib", "name": "nosys",
 "declared_at": "device/qemu/arm_virt/BUILD.gn:88",
 "declared_as": "libs = [ \"nosys\" ]",
 "extractor": "S5-lib", "confidence": "high",
 "check": "CHK-LIB-EXISTS", "verify_cmd": "$CC -print-file-name=libnosys.a",
 "maps_to": ["BUILD-017"]}
```

**验证有个漂亮的零依赖技巧**：

```sh
out=$($CC -print-file-name=libfoo.a)
if [ "$out" = "libfoo.a" ]; then echo MISSING; else echo "OK $out"; fi
```

找到了输出完整路径，**找不到就原样回显输入**。所以判据是「**输出 == 输入**」。

三个好处：

1. **一次调用**，不需要真的链接
2. **零依赖**——问的是编译器驱动自己，不需要 `find` / `ls` / 任何额外工具
3. **问的是正确的那个编译器**——这正好抓住 `BUILD-017`

> **`BUILD-017` 的要点**：链接报 `cannot find -lnosys` / `cannot find -lc`，
> 根因是「ARM 工具链缺少**与目标 CPU/multilib 匹配的** Newlib 库」。
>
> 注意——**库"存在"和"对这个 multilib 存在"是两件事**。系统里可能有 `libnosys.a`，
> 但那是给另一个 ABI 编的。`-print-file-name` 问的正是**这个 `$CC` 会不会找到它**，所以能区分出来。

#### 19.5.2 符号那一半：解析链接脚本

一个链接脚本长这样，六处都要认：

```ld
ENTRY(_start)                    /* ① 入口符号 */

EXTERN(__exidx_start)            /* ② 强制引用的外部符号 */

SECTIONS
{
  . = 0x40000000;                /* ③ 地址计数器 */

  .text : { *(.text) }

  .ARM.exidx : {                 /* ④ 输出段 —— BUILD-022 缺的就是它 */
    __exidx_start = .;           /* ⑤ 符号赋值：脚本自己定义了它 */
    *(.ARM.exidx)
    __exidx_end = .;
  }
}
```

| 指令                       | 含义                                                                       | 提取成什么                             |
| -------------------------- | -------------------------------------------------------------------------- | -------------------------------------- |
| ①`ENTRY(_start)`        | 入口符号，链接器必须找到它                                                 | 需要符号`_start`                     |
| ②`EXTERN(x)`            | 「我引用了 x」——**强制链接器去库里找它**，即使没有任何目标文件引用 | 需要符号`x`                          |
| ③`. = 0x40000000;`      | 地址计数器赋值                                                             | 目标地址约束                           |
| ④`.ARM.exidx : { ... }` | 输出段定义，把输入段`*(.ARM.exidx)` 收集进来                             | 需要输入段`.ARM.exidx`               |
| ⑤`__exidx_start = .;`   | **符号赋值**，`.` 是当前地址                                       | **定义了**符号 `__exidx_start` |

#### 19.5.3 关键：符号那一半是**第二个「缺口提取」**

看 `BUILD-022` 的两边：

|                  | 谁                                                                  |
| ---------------- | ------------------------------------------------------------------- |
| **使用方** | libgcc 的 C++ 异常展开代码，引用`__exidx_start` / `__exidx_end` |
| **声明方** | **链接脚本**（用 `__exidx_start = .;` 定义它）              |

**缺口 = 有人用了，但没人定义。** 这和 S6 是**完全同一个模式**：

|              | 使用                        | 声明                         | 缺口 → 问题  |
| ------------ | --------------------------- | ---------------------------- | ------------- |
| **S6** | 源码用了`uint32_t`        | 源码 include 了`<cstdint>` | `BUILD-008` |
| **S5** | 代码引用了`__exidx_start` | **链接脚本定义了它**   | `BUILD-022` |

**所以 S5 符号那一半的验证是纯文本的**——读 `.ld` 文件，问「脚本里有没有 `__exidx_start = .;` 这一行、
有没有 `.ARM.exidx` 这个输出段」。**不用跑链接器。**

这就是它便宜的原因，也是它能在"第 -2 ~ 第 6 关零编译"这条线里的原因（7.2）。

#### 19.5.4 为什么是「库**和**符号」两样东西

因为它们是**两种不同的失败模式**，症状、根因、修复动作全都不一样：

| 缺什么         | 报错长什么样                             | 根因在哪                     | 修复动作         | 61 条               |
| -------------- | ---------------------------------------- | ---------------------------- | ---------------- | ------------------- |
| **库**   | `cannot find -lnosys`                  | **工具链**里没有这个库 | 装库（改环境）   | `BUILD-017`（A1） |
| **符号** | `undefined reference to __exidx_start` | **链接脚本**没定义它   | 改脚本（改源码） | `BUILD-022`（D）  |

**两者不能互相替代**：

- 库在，符号仍可能缺（版本不对、multilib 不对）
- 符号在，库仍可能缺（`-lc` 指向的那份 `libc` 没装）

所以必须**分开报**——报成一句「链接失败」等于没报。

#### 19.5.5 主动缩小范围：三类符号，只做第一类

⚠️ **本节原来只写了 12 行，符号那一半的验证一个字没写。** 补上，并且要说清哪部分**故意不做**。

三种符号形式要分开看：

| 符号形态               | 例子                   | 怎么验                                          | 做不做                                      |
| ---------------------- | ---------------------- | ----------------------------------------------- | ------------------------------------------- |
| **脚本自己定义** | `__exidx_start = .;` | 纯文本：脚本里有没有这行                        | ✅**做**——这是 `BUILD-022` 的正解 |
| **入口符号**     | `ENTRY(_start)`      | 要知道目标文件里有没有                          | ⚠️**只报不判**——标 `info`       |
| **真外部符号**   | `EXTERN(x)`          | **要知道候选库里有没有 x → 得上 `nm`** | ❌**不做**                            |

**第三类为什么不做**——按 19.6 给 S6 定的同一条规矩：

> 「扫所有 `#include` 查可达性」不该做，两个理由：
> **和编译器重复**：include 找不到，编译器第一时间就报，不需要预检
> **太贵**：几十万文件的 include 图，收益远低于成本

**S5 的 `EXTERN(x)` 是一模一样的处境**：

|                        | 理由                                                       |
| ---------------------- | ---------------------------------------------------------- |
| **和链接器重复** | 符号找不到，链接器第一时间就报`undefined reference`      |
| **有工具依赖**   | 要扫候选库的符号表，就得上`nm`——违反零依赖原则（15.2） |

**做与不做的分界**：

| 做                                                                                    | 不做                         |
| ------------------------------------------------------------------------------------- | ---------------------------- |
| ✅ 扫`-lfoo` / `libs` → 库存在性（`BUILD-017`）                                | ❌`EXTERN(x)` 去库里找符号 |
| ✅ 解析`.ld` 自洽性：用了的段 / 边界符号**脚本自己定义了吗**（`BUILD-022`） | ❌ 全量符号解析图            |
| ✅`-L` 路径存在性                                                                   |                              |

**留下来的两条都是零成本、纯文本，而且各自正好命中一条已知问题。**

> **说清一处**：`EXTERN(x)` 本身**不是**第 11 节那 13 条里的条目——那 13 条是从 61 条已知问题里划出来的。
> 这里说的是**性质相同**：它和「结果判读」那 6 条一样，属于**硬编检查会造假**的一类。
> 少做一条没有对应已知问题的检查，不亏。

### 19.6 S6 头文件——要**主动缩小范围**

**原始设计里说「扫所有 `#include` 查可达性」——这个不该做，两个理由：**

| 理由                   | 说明                                           |
| ---------------------- | ---------------------------------------------- |
| **和编译器重复** | include 找不到，编译器第一时间就报，不需要预检 |
| **太贵**         | 几十万文件的 include 图，收益远低于成本        |

**该做的是「缺口提取」**，即第 3 节三类里的第二类——**使用 × 声明求差**：

```python
TYPE_RE    = re.compile(r'\b(u?int(?:8|16|32|64)_t|size_t|ptrdiff_t)\b')
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"](?:cstdint|stdint\.h)[>"]', re.M)

def find_gap(path, src):
    src = strip_comments_and_strings(src)      # 必须先剥，否则注释里的 uint32_t 也命中
    if not TYPE_RE.search(src):
        return None
    if INCLUDE_RE.search(src):
        return None
    return gap_record(path, TYPE_RE.search(src))
```

三条缺口规则：

| 检查                                                    | 做法                                       | 对应                          |
| ------------------------------------------------------- | ------------------------------------------ | ----------------------------- |
| 用了`uint32_t` 族但没 include `<cstdint>`           | 上面那段，配`strip_comments_and_strings` | `BUILD-008`                 |
| 脚本用`pushd` / `shopt` 但 shebang 是 `#!/bin/sh` | 读 shebang，扫内建名                       | `BUILD-004` / `BUILD-005` |
| `cp` 到不存在目录前没 `mkdir -p`                    | 扫`cp` 目标路径参数                      | `BUILD-009`                 |

**三条都是差集运算，代价极低，且编译器不会替你报**（前两条要跑到才炸，第三条要跑一半才炸）。

`BUILD-008` 的命中**只能算 `warn` 不能算 `block`**——间接包含无法从静态判定，这是设计上就承认的局限。

### 19.7 S7 配置与架构

- board → `kernel/linux/config/<ver>/arch/<ARCH>/configs/<name>_defconfig` 存在性
- `target_cpu`(GN) ↔ `ARCH`(Kbuild) 的映射**不在源码里**，是外部知识 → `rules/arch_map.yaml`

```yaml
arm64:   arm64
arm:     arm
riscv32: riscv
riscv64: riscv
x86_64:  x86
```

**这张表本身就是 `BUILD-028/029/030` 那类问题的规则化产物**——`BUILD-028` 就是映射写错。

> ⚠️ 这几行的值要**对着树核一遍**再落地，别照抄。上表是 mainline Linux 的映射，OpenHarmony 可能有出入。

### 19.8 第 2 关：提取自检（`v_coverage`）

**它防的不是 61 条里的任何一条，是「扫描器自己漏了」。**

为什么必要：**「扫描器漏了一整类文件」和「这棵树本来就不需要那类东西」，症状一模一样**——都是「报告里没有」。没有这一关，你分不出来。

#### 机制：五步

```
① 枚举树里全部文件，按扩展名聚合
   {".c": 82000, ".h": 61000, ".gn": 9000, ".py": 12000,
    ".json": 15000, ".dts": 412, ".te": 90, ".rs": 12, ...}

② 七个提取器各自上报「我认领了哪些扩展名 + 扫了哪些路径」
   S1 → {".sh", ".mk", "Makefile", ".py"}   范围: 全树（.py 限 device/vendor/build）
   S2 → {"manifest.xml"} + ".gn" 的 // 引用  范围: 全树
   S3 → {"config.json", "bundle.json"}      范围: vendor/, third_party/
   S5 → ".gn" 的 libs/ldflags + ".ld"        范围: 全树
   S6 → {".c", ".cc", ".cpp", ".h", ".hpp"}  范围: 全树

③ 求差集，分三类：
      有提取器认领            → ✅ 通过
      在「不承载声明」白名单里 → ✅ 跳过（文档 / 图片 / 二进制 / 测试数据）
      两者都不是              → ⚠️ 报出来查

④ 对每个「未认领」的扩展名，随机抽 20 个文件，
   用**同一套七条规则**去试：
      有命中 → warn「这个类型里有声明形态，但没人管」
      零命中 → info「看起来确实没有，建议加进白名单」

⑤ 反向哨兵：某个提取器在整棵树上命中数是 0
      → 多半是**它坏了**，不是树里真没有
```

**第 ④ 步的巧妙处**：它用的是**同一套七条规则**，不是新写的—**自检本身不引入新逻辑**。

**第 ⑤ 步**就是 `requirements.json` 里 `extractor_stats` 字段的用途（第 17 节）。

#### 输出形态

```json
"self_check": {
  "total_files_seen": 184203,
  "uncovered_file_types": [
    {".dts": {"count": 412, "sample_hit": 0.35, "verdict": "warn",
              "msg": "抽样 20 个，7 个含 // 路径引用和 target_cpu 字段 —— S7 应该管"}},
    {".te":  {"count": 90, "sample_hit": 0.0, "verdict": "info",
              "msg": "抽样无声明形态，建议加白名单"}},
    {".rs":  {"count": 12, "sample_hit": 0.0, "verdict": "info"}}
  ],
  "zero_hit_extractors": ["S5"]
}
```

#### 定级：`warn` / `info`，**不是 `block`**

因为「未覆盖」有两种可能：**真漏了**（该补提取器），或**本来就正常**（`.te` SELinux 策略确实不承载构建声明）。**这个判定必须留给人。**

#### 它是个回环，不是直行

第 ④ 步报出 `warn` → 补一条提取器 → **重跑第 1 关**。

这就是 §7.2 的图里那条回指箭头。**越早发现，重跑代价越小**——这是它排在第 1 关之后、第 3 关之前的理由。

#### 诚实边界：它查不出什么

| 能查出                               | 查不出                   |
| ------------------------------------ | ------------------------ |
| 整类文件**没被任何提取器看过** | **看了，但看漏了** |

反例：S1 会扫 `.sh`，但它对 **heredoc 里的命令**支持不好，漏了几条。第 2 关**发现不了**——因为在文件级别，那个 `.sh` 是「已认领」的。

**这类漏只有靠覆盖率实验（第 9 节）和真实失败（增量模式）才能发现。**

---

