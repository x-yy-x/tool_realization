# L1 · 扫描层

> **本文回答**：怎么把一棵源码树变成一份带分类的文件清单。
> **对应**：[`02` §29.1](../../docs/02-流水线与关.md)（四层里的 L1）+ 原设计文档 §16（目录结构）。

[← 实现设计（共用地基）](../README.md) · [L2 提取 →](../l2_extract/README.md)

> **实测报告**：14 个非 OHOS 工程（OS/RTOS、构建系统、库、Rust/JS/Go 生态）上的路由结果与误判分析，见 [`TEST-REPORT.md`](TEST-REPORT.md)。

---

**本层职责**：走一遍源码树，给每个文件判出「它承载哪几类信息」，产出 `filelists.json`。

|                    |                                                                                                                |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| **输入**     | 源码树根目录                                                                                                   |
| **输出**     | `filelists.json`（每条只有 `path` + `classes`，契约见 [`schema/`](../../schema/filelists.schema.json)） |
| **代码**     | [`treemodel.py`](treemodel.py)（遍历与判定）+ [`rules.py`](rules.py)（规则数据）                             |
| **入口**     | `py scan.py --stage tree --root <源码树>` 或 `py -m src.l1_scan`                                           |
| **执行点**   | 第 0 关（[`02` §7.2](../../docs/02-流水线与关.md)），跑在 **WSL、有完整源码树**                        |
| **有副作用** | 无——默认只 `readdir`；少数认不出的文件读文件头（≤8 KB）                                                   |

```powershell
#示例
cd D:\Compatibility_test\tool
py scan.py --stage tree --root D:\Compatibility_test\_ohos_probe
```

---

## 16.1 做什么：四步

| 步 | 动作                                                         | 代价                                  |
| -- | ------------------------------------------------------------ | ------------------------------------- |
| ① | **遍历**：`os.scandir` 递归走全树                    | 秒级（~184203 文件实测 40 ms/千文件） |
| ② | **排除**：剪掉 `.git/ .repo/ out/ prebuilts/` 等目录 | —                                    |
| ③ | **分类**：名字 → 路径 → 内容（内容只在必要时触发）   | 只读约 6–25% 的文件，每个最多 8 KB   |
| ④ | **固化**：写出 `filelists.json`                      | —                                    |

### 关键：遍历 ≠ 解析

|                | 做什么                     | 碰多少文件                        | 代价     |
| -------------- | -------------------------- | --------------------------------- | -------- |
| **遍历** | 只 `readdir` 拿路径      | 全部（~184203）                   | 秒级     |
| **读头** | 只读文件头（≤8 KB）判类型 | 名字认不出的那部分（实测 6–25%） | 百毫秒级 |
| **解析** | 打开文件读全文、抽需求     | L2 的任务                         | 分钟级   |

**遍历全场只做一次**（就是这里）。L2 的所有提取任务都从 `filelists.json` 出发，**不再走树**——「中央遍历，分散解析」（[`02` §29.2](../../docs/02-流水线与关.md)）。

---

## 16.2 排除哪些

| 目录           | 处理     | 为什么                                          |
| -------------- | -------- | ----------------------------------------------- |
| `.git/`      | 整棵剪掉 | VCS 元数据                                      |
| `.repo/`     | 整棵剪掉 | repo 工具的元数据；S2 已出范围，不再读 manifest |
| `out/`       | 整棵剪掉 | 构建产物                                        |
| `prebuilts/` | 整棵剪掉 | 预编译二进制                                    |

**这四条是固定规则，不提供配置项。** 实现在 [`rules.py`](rules.py) 的 `EXCLUDED_DIRS`，遍历时直接在 `os.scandir` 层剪枝。
理由：这四类目录在**所有**被测工程里都不是源码；做成配置只会变成「换一棵树就多一份配置」，而配置本身又会成为新的错误来源。

---

## 16.3 分类表

**这是「文件分类 → 不同文件走不同提取器」的那张表。** 大类按「承载什么信息」定义，不按文件名定义；调研与跨系统对照见 [`docs/09`](../../docs/09-文件功能大类与命名特征调研.md)。

### 八个大类

| #            | 大类                 | 判据举例（名字层）                                                                                                            | 交给谁（`classes`） |
| ------------ | -------------------- | ----------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| **C1** | 可执行脚本           | `*.sh`、`*.py`、shebang、无后缀的可执行文件                                                                               | S1 S4 S5              |
| **C2** | 构建描述             | `Makefile`、`BUILD.gn`、`CMakeLists.txt`、`meson.build`、`Android.bp`、`*.bb`、`SConscript`                     | S1 S4 S5 S7           |
| **C3** | 模块/包清单          | `bundle.json`、`ohos.build`、`debian/control`、`package.json`                                                         | S3 S6                 |
| **C4** | 产品/板级/工具链配置 | `config.json`、`BoardConfig.mk`、`prj.conf`、`*_defconfig`、`*.para`、`*.config`                                  | S3 S4 S7              |
| **C5** | 链接与符号           | `*.ld`、`*.lds`、`*.map`、`*.symbols`、`ld.script`                                                                  | S5                    |
| **C6** | C/C++ 源码与头文件   | `*.c`、`*.h`、`*.cpp`、`*.S`                                                                                          | S6                    |
| **C7** | 配置开关 schema      | `Kconfig*`、`Config.in`、`Config.in.legacy`（**大写 C**；小写 `config.in` 是 Autotools 生成的头文件模板，不算） | S3 S7                 |
| **C8** | 设备树 / 硬件描述    | `*.dts`、`*.dtsi`、`*.hcs`                                                                                              | S4 S7                 |

**大类只是中间量。** 输出里没有 `C1…C8`，只有 `classes`——L2 不需要知道「这是第几类」，只需要知道「该由谁来读」。

### 八个大类各长什么样（真实例子）

下面每个例子都来自实际跑过的工程（OHOS、Yocto、Buildroot、NuttX、RT-Thread、Linux、Meson、AOSP、Debian、fmt）。看的时候别记文件名，记**「它为什么归这一类」**。

**C1 可执行脚本** —— 会被某个解释器从头执行完的一串命令。

- `vendor_ohemu/common/qemu-run`：首行 `#!/bin/bash`，后面是 `qemu-system-aarch64 -machine virt ...`。
- Linux `scripts/bloat-o-meter`：**没有后缀**，首行 `#!/usr/bin/env python3`，只能靠 shebang 认。
- OHOS `rootfs/init.qemu.arm.linux.cfg`：后缀像配置，内容却是 `{"jobs":[{"name":"boot","cmds":["mkdir /dev/...","start hilogd"]}]}`——**一条条要执行的命令**，所以是脚本，不是配置。
- 不是 C1：`rtconfig.py` 长得像 Python 脚本，但从来不被执行（见 C4）。

**C2 构建描述** —— 回答「有哪些目标、用哪些源文件、怎么编」。

- OHOS `BUILD.gn`：`static_library("flash") { sources = [ "ram_virt_flash.c" ] }`。
- Meson `meson.build`：`executable('prog', 'prog.c')`。
- Yocto `busybox_1.36.1.bb`：`require busybox.inc` 加一长串 `SRC_URI`。
- Debian `Makefile.am` / `configure.ac`：Autotools 的构建输入。
- 和 C3 的区别：**C2 说「怎么把它编出来」，C3 说「这个包是什么、依赖谁」**。`.bb` 两件事都干，所以 `classes` 里 S1/S4/S5/S7 之外再给一个 S3。

**C3 模块/包清单** —— 声明一个可交付单元的名字、版本、依赖。

- OHOS `bundle.json`：`{"name":"@ohos/hilog","component":{...}}`。
- Debian `debian/control`：`Source: hello` / `Package: hello` / `Depends: ...`。
- OHOS `productdefine/common/inherit/2in1.json`：`{"subsystems":[{"subsystem":"updater","components":[...]}]}`。
- 和 C4 的区别：**C3 列「有哪些东西」，C4 定「这次选哪些值」**。

**C4 产品/板级/工具链配置** —— 给某块板、某个产品、某套工具链**定值**。

- OHOS `config.gni`：`board_cpu = "cortex-m4"`、`board_toolchain = "arm-none-eabi-gcc"`。
- Yocto `meta/conf/machine/qemuarm64.conf`：`#@TYPE: Machine`、`KERNEL_IMAGETYPE = "Image"`、`UBOOT_MACHINE ?= "qemu_arm64_defconfig"`。
- NuttX `configs/nsh/defconfig`：`CONFIG_ARCH="arm"`、`CONFIG_ARCH_BOARD="stm32f4discovery"`。
- OHOS `kernel_configs/debug.config`：`LOSCFG_COMPILER_GCC=y`。
- 和 C7 的区别：**C4 是「选了哪些值」，C7 是「有哪些开关可选」**。

**C5 链接与符号** —— 链接期怎么摆放代码段、导出哪些符号。

- OHOS `liteos.ld`：`OUTPUT_FORMAT("elf32-littlearm", ...)`、`ENTRY(Vector_Table)`、`SECTIONS`。
- OHOS `libhilog.map`：`{ global: HiLogPrint; HiLogIsLoggable; ... }`。
- NuttX `scripts/ld.script`（**连 `.ld` 后缀都没有**）和 `memory.ld`。
- 和 C2 的区别：`LDFLAGS = -lm`、`target_link_libraries(...)` 这类**链接参数写在构建描述里**，归 C2；只有专门的链接脚本和符号表才是 C5。

**C6 C/C++ 源码与头文件** —— 编译器直接吃的源文本。

- CoreMark `core_main.c`、fmt `include/fmt/format.h`。
- OHOS 树里的 `*.S`（大写 S：带预处理的汇编）。
- 不是 C6：Debian 的 `config.in` 里是 `#undef COPYRIGHT_YEAR`，看着像头文件，其实是 autoheader 生成的模板。
- 例外：NuttX 的 `board.h` 是头文件，但里面写的是板级取值，所以 `classes` 额外给 S4/S7。

**C7 配置开关 schema** —— 定义「有哪些开关、默认值、依赖关系」，本身不含某次构建的取值。

- Linux / NuttX / RT-Thread 的 `Kconfig`：`config SOC_STM32F429IG` + `bool` + `select ...`。
- Buildroot 的 `Config.in`（**大写 C**）：`config BR2_PACKAGE_BUSYBOX` + `bool "BusyBox"` + `default y`。
- Zephyr 的 `Kconfig.defconfig`。
- 不是 C7：小写 `config.in`（Autotools 生成的头文件模板，和 Kconfig 只差大小写）。

**C8 设备树 / 硬件描述** —— 板上有哪些设备、地址、中断、兼容性。

- Linux `arch/arm64/boot/dts/qcom/*.dts`，以及 `*.dtso`（overlay）。
- OHOS `device_info.hcs`：`root { device_info { match_attr = "hdf_manager"; ... } }`。
- 和 C4 的区别：**C4 说「用哪个 CPU、开哪些开关」，C8 说「板上接了哪些设备、挂在哪个地址」**。

### 最容易混的八组，一句话分清

| 看着像                              | 实际         | 一句话                                                  |
| ----------------------------------- | ------------ | ------------------------------------------------------- |
| `rtconfig.py`                     | C4           | 后缀是 `.py`，但从不执行——它是 SCons 读的工具链配置 |
| `BoardConfig.mk` / `config.gni` | C4           | 后缀像构建描述，内容全是板级取值                        |
| `setup.py`                        | C3           | 后缀像脚本，内容在声明「这个包是什么」                  |
| `.bb` / `Android.bp`            | C2（+S3）    | 构建描述和包清单两件事都做，两个都算                    |
| `board.h`                         | C6（+S4/S7） | 是头文件，但里面写的是板级取值                          |
| `Kconfig` vs `defconfig`        | C7 vs C4     | 前者定义「有哪些开关」，后者是「这次选了哪些」          |
| `LDFLAGS` vs `.ld`              | C2 vs C5     | 链接参数写在构建描述里；专门的链接脚本才归 C5           |
| `.hcs` vs `config.gni`          | C8 vs C4     | 前者描述硬件拓扑，后者选定编译取值                      |

**记不住时按动词想：** C1 会被执行、C2 用来构建、C3 说明包是什么、C4 定下取值、C5 决定链接、C6 被编译、C7 定义开关、C8 描述硬件。

### 三层判据，逐层叠加

| 层               | 依据                                   | 代价                  | 解决什么                   | 代码                          |
| ---------------- | -------------------------------------- | --------------------- | -------------------------- | ----------------------------- |
| **1 名字** | 完整文件名 → 前缀 → 后缀             | 0                     | 后缀唯一的类型             | `rules.EXACT_NAME / SUFFIX` |
| **2 路径** | 父目录                                 | 0                     | 同一后缀在不同目录语义不同 | `rules.PATH_RULES`          |
| **3 内容** | 跳注释头后的前 2 KB（不够再扩到 4 KB） | 一次读头（最多 8 KB） | 后缀缺失或歧义             | `rules.ANCHORS`             |

**三层不是并列的，是有顺序的：**

- 名字层的 `exact` / `prefix`（`BoardConfig.mk`、`Kconfig*`）是**强判据**，内容层不推翻；
- 名字层的 `suffix`（`.gni`、`.json`）是**弱判据**，路径和内容可以纠正；
- 内容层只在两种情况下跑：名字认不出，或后缀在 `CONTENT_EXT` 里（`.json .cfg .conf`）。

**已处理的几个「后缀会骗人」的例子：**

| 文件                 | 只看后缀会判成 | 实际        | 靠什么纠正                           |
| -------------------- | -------------- | ----------- | ------------------------------------ |
| `BoardConfig.mk`   | C2 构建描述    | C4 板级     | 整名                                 |
| `config.gni`       | C2 构建描述    | C4 板级     | 整名                                 |
| `rtconfig.py`      | C1 脚本        | C4 工具链   | 整名                                 |
| `setup.py`         | C1 脚本        | C3 包清单   | 整名                                 |
| `debian/control`   | —             | C3 包清单   | 路径（别处的 `control` 不认）      |
| `meta/conf/*.conf` | —             | C4 构建配置 | 路径（Yocto 的锚点常在第 2 KB 之后） |

### 代码里是怎么走的

三个函数串起来就是整条链路：

```text
iter_files()    遍历目录，产出 (相对路径, 绝对路径)
     ↓
classify()      判大类（三层判据），返回 (类别, 依据)
     ↓
classes_for()   大类 → S1…S7，装进 FileRoute
```

`classify()` 的判定顺序**就是上面三层的顺序**（[`treemodel.py`](treemodel.py)）：

1. `rules.match_name_detail(rel)`：大小写敏感整名 → 小写整名 → `kconfig` 前缀 → `_defconfig` 结尾 → 后缀；
2. 名字没命中，才调 `rules.match_path(rel)`；
3. `rules.needs_content(rel, category)` 说该读，才 `read_head()` 读最多 8 KB、跳过注释头：
   - 首行是 `#!` → C1（shebang 本身算注释，必须在跳注释**之前**判）；
   - 否则 `rules.sniff_content()` 按顺序试锚点，前 2 KB 没命中就扩到 4 KB 再试；
   - **名字层用 `exact` / `prefix` 判出来的结果，内容层不覆盖**。

**一次真实判定长这样**（直接调 `classify()` 的输出，文件都来自 OHOS 探测树）：

| 文件                                  | 名字层                 | 路径层 | 内容层                | 结论   | classes     |
| ------------------------------------- | ---------------------- | ------ | --------------------- | ------ | ----------- |
| `…/liteos_m/config.gni`            | `exact` → C4        | —     | 不读                  | C4     | S3 S4 S7    |
| `…/device_info.hcs`                | `.hcs` → C8         | —     | 不读                  | C8     | S4 S7       |
| `…/board/liteos.ld`                | `.ld` → C5          | —     | 不读                  | C5     | S5          |
| `…/drivers/Kconfig`                | `kconfig` 前缀 → C7 | —     | 不读                  | C7     | S3 S7       |
| `…/SmartL_E802/ohos.build`         | `exact` → C3        | —     | 不读                  | C3     | S3 S6       |
| `…/liteos_a/BUILD.gn`              | `exact` → C2        | —     | 不读                  | C2     | S1 S4 S5 S7 |
| `…/innerkits/libhilog.map`         | `.map` → C5         | —     | 不读                  | C5     | S5          |
| `…/rootfs/init.qemu.arm.linux.cfg` | 无                     | 无     | `"jobs"`/`"cmds"` | C1     | S1 S4 S5    |
| `…/common/qemu-run`                | 无（无后缀）           | 无     | shebang               | C1     | S1 S4 S5    |
| `…/LICENSE`                        | 无                     | 无     | **跳过不读**    | 不路由 | —          |

前 7 行只查了名字，一次磁盘都没碰；第 8、9 行的类别**完全来自内容**；最后一行连读都不读。这就是「三层」在真实数据上的分工。

### 内容锚点（只在前 2 KB 里找）

| 锚点                                                                | 命中 → | 例子                         |
| ------------------------------------------------------------------- | ------- | ---------------------------- |
| `#!`                                                              | C1      | 无后缀的 init 脚本           |
| `match_attr =` / `hostName =` / `root {`                      | C8      | OHOS HDF 的 `.hcs`         |
| `/dts-v1/;`                                                       | C8      | 设备树                       |
| `{ global:`                                                       | C5      | 符号版本脚本                 |
| `ENTRY(` / `OUTPUT_FORMAT(` / `SECTIONS {`                    | C5      | 链接脚本                     |
| `config X` / `menuconfig`                                       | C7      | Kconfig                      |
| `"product_name"` / `"target_cpu"`                               | C4      | 产品 JSON                    |
| `board_* =` / `kernel_type =`                                   | C4      | GN 板级配置                  |
| `ARCH =` / `CROSS_TOOL =`                                       | C4      | RT-Thread `rtconfig.py`    |
| `TARGET_ARCH` 等                                                  | C4      | AOSP `BoardConfig.mk`      |
| `#@TYPE: Machine` / `MACHINE =`                                 | C4      | Yocto machine conf           |
| `CONFIG_*=` / `LOSCFG_*=`                                       | C4      | 内核 defconfig / `.config` |
| `"subsystems"`                                                    | C3      | OHOS 产品/组件 JSON          |
| `"component"` / `"dependencies"`                                | C3      | `bundle.json`              |
| `Source:` / `Package:`                                          | C3      | Debian `control`           |
| `SRC_URI =` / `DEPENDS =`                                       | C3      | BitBake recipe               |
| `cmake_minimum_required` / `add_executable(` / `add_library(` | C2      | CMake                        |
| `declare_args(` / `source_set(`                                 | C2      | GN                           |
| `cc_library {`                                                    | C2      | Android.bp                   |
| `"jobs"` / `"cmds"`                                             | C1      | OHOS init 配置（`.cfg`）   |

**两条纪律：**

1. **宽泛的锚点宁可不留。** 试过用 `key=value` 认参数表，结果把 `.pylintrc` 判成了板级配置——这类「几乎任何文本都能命中」的规则已经删掉；能用后缀表达的（`.para`）就不要用内容。
2. **规则要「任一命中」而不是「全部命中」。** 实测某个链接脚本里 `ENTRY` 在 1793 字节、`SECTIONS` 在 2103 字节——要求两个都出现就会漏。

### 一个大类装不下的文件

有些文件同时属于两类。`class` 只能给一个，所以在 `classes` 上补：

| 文件                                              | 基础 | 追加    | 理由                                            |
| ------------------------------------------------- | ---- | ------- | ----------------------------------------------- |
| `*.bp`、`*.bb`、`*.bbappend`、`*.bbclass` | C2   | +S3     | 既是构建描述又是包清单                          |
| `recipes-*/…/*.inc`                            | C2   | +S3     | Yocto recipe 的片段，装 `DEPENDS`/`SRC_URI` |
| `board.h`                                       | C6   | +S4 +S7 | NuttX 把板级取值写在头文件里                    |

`other`（认不出的文件）**不进输出**——没有 `classes` 的记录不写。

> ⚠️ **分类会有误判。** 当前实现建立在 11 个系统、14 棵真实树的实测上（[`docs/09` §33.8](../../docs/09-文件功能大类与命名特征调研.md)）：OHOS 上名字层覆盖 75.1%，内容层把它推到 83.1%，60 文件抽样精度 96.7%。**非 OHOS 的树只做了「已知载体必须认出 + 未认出清单人工核对」，没有标注集，不能报精度。** 更大的树、更怪的命名会带来新的漏判。

---

## 16.4 输出：`filelists.json`

**契约已冻结为 `filelists.json`：顶层就是一个数组，每条只有 `path` + `classes`。**
见 [`schema/filelists.schema.json`](../../schema/filelists.schema.json)。

```json
[
  { "path": "base/hiviewdfx/hilog/bundle.json", "classes": ["S3", "S6"] },
  { "path": "base/hiviewdfx/hilog/BUILD.gn",    "classes": ["S1", "S4", "S5", "S7"] }
]
```

三条硬约束（测试会校验，`FileRoute` 构造时也会校验）：

1. `classes` 非空、去重、按 `S1,S3,S4,S5,S6,S7` 升序；
2. `path` 是相对源码根、正斜杠分隔、无 `.`/`..`/盘符/反斜杠；
3. **不写 `meta` / `stats` / `size` / `kind`**——契约是 `additionalProperties: false`。

**统计不写进文件。** 扫描过程的数量（扫了多少、读了多少、各类多少）打到 stderr：

```text
[tree] 1766 个文件，路由 1228 个（读内容 113 个，63 ms）
```

需要机器可读的统计时，用 `py -m src.l1_scan --stats-json stats.json`。这样 `filelists.json` 永远是纯路由表，L2 不需要跳过一堆元数据。

### 在 OHOS 探测树上的真实输出

```bash
py scan.py --stage tree --root D:\Compatibility_test\_ohos_probe --out out/filelists.json
```

```text
[tree] 1766 个文件，路由 1228 个（读内容 113 个，63 ms）
[tree] written: out\filelists.json
```

产物 1228 条、191 KB、按 `path` 排序（`out/` 在 `.gitignore` 里，不会进仓库）。开头是：

```json
[
  { "path": "_scripts/analyze.py", "classes": ["S1", "S4", "S5"] },
  { "path": "device_qemu-OpenHarmony-4.0-Release/SmartL_E802/BUILD.gn",
    "classes": ["S1", "S4", "S5", "S7"] }
]
```

1228 条只有 **8 种组合**，正好对应 8 个大类：

| 条数 | classes     | 大类           | 例子                                          |
| ---: | ----------- | -------------- | --------------------------------------------- |
|  739 | S6          | C6 源码        | `…/adapter/hals/utils/file/src/hal_file.c` |
|  214 | S1 S4 S5 S7 | C2 构建描述    | `…/SmartL_E802/BUILD.gn`                   |
|  116 | S3 S4 S7    | C4 板级配置    | `…/liteos_m/config.gni`                    |
|   66 | S3 S6       | C3 模块清单    | `…/SmartL_E802/ohos.build`                 |
|   49 | S1 S4 S5    | C1 脚本        | `_scripts/analyze.py`                       |
|   26 | S4 S7       | C8 硬件描述    | `…/hdf_config/device_info/device_info.hcs` |
|   12 | S5          | C5 链接符号    | `…/liteos_m/board/liteos.ld`               |
|    6 | S3 S7       | C7 开关 schema | `…/liteos_a/drivers/Kconfig`               |

剩下 538 个文件不进输出：manifest XML 231、文档 96、`LICENSE` 一类 61、图片/二进制 58、JS 测试应用 16……按设计，**没有 `classes` 的记录不写**。

---

## 16.5 `treemodel.py` 的接口

```python
def scan(root, *, use_content=True) -> tuple[list[FileRoute], ScanStats]:
    """全树遍历 + 分类。唯一一次走树。目录剪枝用 rules.EXCLUDED_DIRS，不读配置。"""

def classify(rel, abs_path, *, use_content=True, stats=None) -> tuple[str | None, str]:
    """单个文件 → (大类, 依据)。依据 ∈ exact/prefix/suffix/path/content/none。"""

def iter_files(root, *, stats=None) -> Iterator[tuple[str, str]]:
    """只遍历，产出 (相对路径, 绝对路径)。按 EXCLUDED_DIRS 原地剪枝。"""

def read_head(abs_path) -> tuple[bytes | None, int]:
    """读前 8 KB，返回 (head, 第一个有效行的偏移)。二进制返回 (None, 0)。"""
```

规则全部在 [`rules.py`](rules.py) 里，是**数据表 + 纯函数**，不碰磁盘：

| 表                                            | 内容                          |
| --------------------------------------------- | ----------------------------- |
| `EXCLUDED_DIRS`                             | 固定剪枝的四个目录            |
| `CATEGORY_CLASSES`                          | 大类 → `classes`（S1…S7） |
| `EXACT_NAME` / `NAME_PREFIX` / `SUFFIX` | 名字层                        |
| `PATH_RULES`                                | 路径层                        |
| `ANCHORS`                                   | 内容层（22 条）               |
| `EXTRA_CLASSES_*`                           | 「一个大类装不下」的追加      |

改规则只动 `rules.py`，不用碰遍历逻辑；测试在 [`tests/test_l1_scan.py`](../../tests/test_l1_scan.py)。

**`parse_manifest` / `bundle_index` 不在本版。** 原设计里它们服务 S2（仓清单），S2 已从研究范围移除；`bundle.json` 的索引改由 L2 从 `filelists.json` 里筛 `C3` 记录时顺手建。

---

## 16.6 已知边界

| 边界                                         | 现状                                                                               |
| -------------------------------------------- | ---------------------------------------------------------------------------------- |
| 符号链接环                                   | `os.scandir` 不跟随符号链接，天然避开                                            |
| 超大仓（`prebuilts/` 几十 GB）             | 只建索引不解压，代价是 `readdir` 次数                                            |
| Windows 侧跑（提取开发时）                   | 全程 `pathlib` / `os.path.join`，输出统一正斜杠                                |
| 第 0 关的**树完整性判定**（哨兵/对账） | **不在这里**——是 L3 的 `v_tree`，见 [`L3` §18](../l3_verify/README.md) |
| 规则覆盖率                                   | 名字层 75.1%、加内容层 83.1%（OHOS 实测）；换树会变                                |
