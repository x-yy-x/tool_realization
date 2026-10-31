# L1 分类器 · 跨项目测试报告

> **这份报告回答**：L1 的分类规则放到**非 OHOS** 的真实工程上，表现如何？哪些判断可靠、哪些会错、错的根因是什么？
> **测的时间**：2026-10-30。**被测代码**：`src/l1_scan/` 当前版本（测试期间未改动）。
> **复现**：见文末 §8。

---

## 1. 样本与方法

### 1.1 为什么测这些项目

L1 的规则不是按「哪个项目」写的，而是按「文件长什么样」写的。所以要验证它，样本必须**覆盖不同的工程组织方式**，而不只是不同的行业。这 14 个项目按性质分四组：

| 组 | 项目 | 性质 | 为什么选它 |
| -- | ---- | ---- | ---------- |
| **操作系统 / 启动链** | U-Boot | 引导加载程序（bootloader） | 板级配置用 `*_defconfig` + `include/configs/*.h` |
| | Zephyr | 实时操作系统（RTOS） | 板级目录里 Kconfig、dts、defconfig、CMake 混在一起 |
| | FreeRTOS-Kernel | RTOS 内核 | 大量编译器专用移植层（`.asm`、`.s79`…） |
| | BusyBox | 嵌入式 Linux 用户态工具集 | 一个仓里同时有 Kconfig、applet 源码、测试脚本、示例服务 |
| | OpenWrt | 面向路由器的 Linux 发行版 | 包级 Makefile + 项目自有脚本后缀 |
| **系统组件 / 工具链** | AOSP `system/core/init` | Android 的 1 号进程 | Soong（`Android.bp`）+ `.rc` 服务定义 |
| | apt | Debian/Ubuntu 的包管理器 | Autotools + CMake + `debian/` 三套并存 |
| | QEMU | 机器模拟器 | Meson + Kconfig + 自动生成的 `.hx` |
| | wayland | Linux 显示协议库 | 纯 Meson + 协议 XML |
| **通用库 / 开发工具** | curl | 网络传输库 + 命令行工具 | Autotools 与 CMake 双构建 |
| | googletest | C++ 单元测试框架 | CMake + Bazel 双构建 |
| **非 C/C++ 生态（边界测试）** | ripgrep | Rust 命令行搜索工具 | Cargo |
| | express | Node.js Web 框架 | npm |
| | gin | Go Web 框架 | Go modules |

> 说明：这 14 个里只有 Zephyr、OpenWrt、FreeRTOS、BusyBox 是「OS 本身或其用户态」，U-Boot 属于 OS 启动链，其余是库/工具/框架。**选样标准是「工程组织方式不同」，不是「是不是 OS 组件」。**

### 1.2 怎么取树

一律稀疏检出，只落盘需要看的目录，不动整仓：

```bash
git clone --depth 1 --filter=blob:none --sparse <repo> <dir>
git -C <dir> sparse-checkout set <子目录>
```

`systemd` 试过但**在 Windows 上无法检出**：仓库里有文件名带 `:`（`test/test-journals/...:...zst`），Windows 文件系统不接受。这是环境限制，不是分类器问题，已从样本中剔除。

### 1.3 怎么判「对错」

分三层，强度递减：

1. **契约校验（机器）**：每个产物都过 `schema/filelists.schema.json`，并检查路径有序、无重复。
2. **逐条人工核对（部分树）**：对 6 棵新树，把**所有非 C6 的路由**（共 213 条）逐条打开看内容，判断类型对不对。
3. **未路由清单核对（全部树）**：按后缀聚类未路由文件，抽样打开，判断「它该不该被认出来」。

**没有标注集**，所以本报告不给「整体精确率/召回率」，只给**核对过的子集上的错误数和错误类型**。

---

## 2. 总体结果

| 项目 | 性质 | 构建体系 | 文件 | 路由 | 路由率 | 读内容 | 耗时 | 契约 |
| ---- | ---- | -------- | ---: | ---: | -----: | -----: | ---: | ---- |
| U-Boot | 引导加载程序 | Kconfig + Makefile | 7675 | 7615 | 99.2% | 29 | 60 ms | OK |
| BusyBox | 嵌入式用户态工具集 | Kconfig + Makefile | 2889 | 896 | 31.0% | 268 | 113 ms | OK |
| OpenWrt | 路由器发行版 | 包级 Makefile | 1741 | 436 | 25.0% | 455 | 64 ms | OK |
| QEMU | 机器模拟器 | Meson + Kconfig | 1142 | 1104 | 96.7% | 11 | 18 ms | OK |
| FreeRTOS-Kernel | RTOS 内核 | CMake | 886 | 772 | 87.1% | 2 | 34 ms | OK |
| curl | 网络库 + 工具 | Autotools + CMake | 543 | 513 | 94.5% | 5 | 5 ms | OK |
| Zephyr | RTOS | CMake + Kconfig | 428 | 300 | 70.1% | 9 | 7 ms | OK |
| googletest | C++ 测试框架 | CMake + Bazel | 222 | 193 | 86.9% | 3 | 3 ms | OK |
| apt | 包管理器 | Autotools + CMake + debian/ | 222 | 167 | 75.2% | 11 | 7 ms | OK |
| ripgrep | Rust 工具 | Cargo | 226 | 20 | 8.8% | 29 | 10 ms | OK |
| express | Node.js 框架 | npm | 216 | 1 | 0.5% | 5 | 5 ms | OK |
| AOSP init | Android 1 号进程 | Soong | 196 | 155 | 79.1% | 13 | 5 ms | OK |
| gin | Go 框架 | Go modules | 130 | 2 | 1.5% | 0 | 1 ms | OK |
| wayland | 显示协议库 | Meson | 101 | 73 | 72.3% | 3 | 1 ms | OK |
| **合计** | | | **16617** | **12247** | **73.7%** | 953 | — | 14/14 |

**路由率高低不代表好坏。** 分母里混着大量文档、测试数据、图片、补丁——它们本来就不该进路由表。判断质量要看 §5 的误判分析，不是看这个百分比。

---

## 3. 六个深度核对项目的分布

| 项目 | C1 脚本 | C2 构建 | C3 清单 | C4 板级 | C5 链接 | C6 源码 | C7 开关 | C8 硬件 |
| ---- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BusyBox | 120 | 8 | 0 | 9 | 0 | 755 | 4 | 0 |
| FreeRTOS | 5 | 14 | 0 | 1 | 0 | 752 | 0 | 0 |
| apt | 5 | 4 | 1 | 1 | 1 | 155 | 0 | 0 |
| AOSP init | 5 | 8 | 1 | 0 | 0 | 141 | 0 | 0 |
| QEMU | 1 | 7 | 1 | 1 | 0 | 1089 | 5 | 0 |
| wayland | 5 | 6 | 0 | 0 | 0 | 62 | 0 | 0 |

C6 占绝对多数是正常的：这几个仓都是 C/C++ 项目。

---

## 4. 逐项目观察

### 4.1 U-Boot（7675 → 7615，99.2%）

- **性质**：bootloader。板级配置两种写法并存：`configs/*_defconfig`（Kconfig 取值）和 `include/configs/<board>.h`（C 头文件里的 `CONFIG_*` 宏）。
- **结果**：C6 3334、C8 2308（`arch/arm/dts` 设备树）、C4 1591、C2 214、C7 147、C5 19。
- **观察**：这是「Kconfig 系」工程最标准的样子，规则几乎全覆盖。唯一的结构性问题是 `include/configs/*.h` 只被判成 C6（§5.2）。

### 4.2 BusyBox（2889 → 896，31.0%）

- **性质**：几百个 Linux 命令塞进一个二进制。Kconfig 配置 + Makefile 构建 + 大量测试脚本 + runit 服务示例。
- **结果**：C6 755、C1 120、C4 9、C2 8、C7 4。
- **观察**：未路由的 1993 个里，1540 个是 `testsuite/*.tests`（shell 测试用例）与 `*.right`（期望输出）——**测试数据，不该路由**。C1 的 120 个里，有 66 个在 `testsuite/`、`examples/` 下，是测试/示例脚本：**类型对，但不是构建载体**（§5.1.5）。

### 4.3 OpenWrt（1741 → 436，25.0%）

- **性质**：路由器发行版。每个软件包一个目录：`Makefile`（声明包、依赖、安装规则）+ `files/`（init 脚本、UCI 配置）。
- **结果**：C6 165、C2 133、C1 107、C4 17、C7 13、C5 1。
- **观察**：805 个 `.patch` 全部正确排除。包级 `Makefile` 全部命中 C2。**OpenWrt 的 init 脚本用 `.init` / `.hotplug` / `.script` 后缀，ucode 脚本用 `.uc`——全都不在通用后缀表里**，共 58 个漏判（§5.2）。

### 4.4 QEMU（1142 → 1104，96.7%）

- **性质**：机器模拟器。Meson 构建 + 每个目录一份 Kconfig + 大量 C。
- **结果**：C6 1089、C2 7、C7 5、C3 1、C4 1、C1 1。
- **观察**：`meson.build` 与 `Kconfig` 全部命中。发现两处误报：`gitdm.config`（开发统计工具配置）和 `include/hw/elf_ops.h.inc`（C 头文件片段），见 §5.1。

### 4.5 FreeRTOS-Kernel（886 → 772，87.1%）

- **性质**：RTOS 内核。主体是 C 源码 + 每个编译器/架构一套移植层（IAR、RVDS、GCC…）。
- **结果**：C6 752、C2 14、C1 5、C4 1。
- **观察**：移植层里有大量**汇编变体后缀**：`.asm`、`.s79`、`.s85`、`.s90`（对应不同汇编器版本），共 18 个漏判（§5.2）。另外 3 个 `portmacro.inc` / `ISR_Support.inc` 是汇编 include，被 `.inc → C2` 误判（§5.1）。

### 4.6 curl（543 → 513，94.5%）

- **性质**：网络传输库 + 命令行工具。同时维护 Autotools 与 CMake 两套构建。
- **结果**：C6 477、C2 32、C1 4。
- **观察**：Autotools 那一族（`configure.ac`、`Makefile.am`、`*.m4`）这次全部命中——这组规则是上一轮测 Debian 时补的。未路由 30 个是文档与 `*.in` 模板。

### 4.7 Zephyr（428 → 300，70.1%）

- **性质**：RTOS。板级目录 `boards/qemu/*` 里，`<board>_defconfig`、`<board>.dts`、`Kconfig.*`、`CMakeLists.txt`、`board.yml` 并列。
- **结果**：C7 116、C8 54、C4 50、C2 42、C6 36、C1 1、C5 1。
- **观察**：Kconfig 与设备树是主力。未路由 128 个里，**50 个是 `.yaml`**——Zephyr 4.x 的硬件模型 v2 用 YAML 描述板级硬件，这批可能是未来的 C4/C8 载体（§5.2）。其余是文档与 CI 配置。

### 4.8 googletest（222 → 193，86.9%）

- **性质**：C++ 单元测试框架。CMake 与 Bazel 双构建。
- **结果**：C6 157、C1 32（Python 测试脚本）、C2 4。
- **观察**：Bazel 文件（`BUILD.bazel`、`*.bzl`、`MODULE.bazel`）共 7 个未路由（§5.2）。

### 4.9 apt（222 → 167，75.2%）

- **性质**：Debian 的包管理器。C++ 代码 + `debian/` 打包 + 两套构建（CMake、Autotools）。
- **结果**：C6 155、C1 5、C2 4、C3 1、C4 1、C5 1。
- **观察**：`debian/control` → C3、`debian/rules` → C2、`debian/*.symbols` → S5，三条都能对上。一处误报：`debian/default-sequoia.config`（加密策略配置）被判成 C4。

### 4.10 AOSP `system/core/init`（196 → 155，79.1%）

- **性质**：Android 的 1 号进程。C++ + Soong（`Android.bp`）+ `.rc` 服务定义。
- **结果**：C6 141、C2 8（其中 5 个 `Android.bp`，含额外 S3）、C1 5、C3 1。
- **观察**：`Android.bp` 的「既是构建描述又是包清单」被正确表达为 `S1 S3 S4 S5 S7`。**3 个 `.rc` 未路由**——那是 init 的服务/动作定义，本质是一串命令（§5.2）。

### 4.11 wayland（101 → 73，72.3%）

- **性质**：Linux 显示服务器协议库。纯 Meson + C + 协议 XML。
- **结果**：C6 62、C2 6、C1 5。
- **观察**：未路由 28 个里 12 个是 `protocol/*.xml`（协议定义，不是构建载体），8 个是无后缀的构建辅助文件。没有发现误判。

### 4.12 ripgrep / express / gin（8.8% / 0.5% / 1.5%）

- **性质**：Rust / Node.js / Go 的普通应用项目。
- **结果**：
  - ripgrep：11 个 `Cargo.toml` → C3，9 个无后缀脚本 → C1；102 个 `.rs` 源码不路由。
  - express：1 个 `package.json` → C3；142 个 `.js` 不路由。
  - gin：1 个 `go.mod` → C3，1 个 `Makefile` → C2；99 个 `.go` 不路由。
- **观察**：**这是设计边界，不是故障。** C6 的定义是「C/C++ 源码」，`.rs/.js/.go` 不在范围内；这几个生态里能喂给 S1–S7 的只有包清单和构建入口，而它们都被认出来了。

---

## 5. 误判解析

### 5.1 误报：路由了，但类型错了

在 6 棵深度核对树里，**213 条非 C6 路由中确认 9 条类型错误（4.2%）**；占这 6 棵全部 3167 条路由的 0.28%。按根因分四类：

#### 5.1.1 `.inc` 一刀切成 C2

| 文件 | 实际是什么 | 应归 |
| ---- | ---------- | ---- |
| QEMU `include/hw/elf_ops.h.inc` | C 头文件片段（`#define` / `#include`） | C6 |
| FreeRTOS `portable/RVDS/ARM_CA9/portmacro.inc` | RVDS 汇编 include（注释用 `;`） | C6 |
| FreeRTOS `portable/Renesas/SH2A_FPU/ISR_Support.inc` | 同上 | C6 |

**根因**：`.inc → C2` 是为 Yocto 的 recipe 片段（`recipes-*/busybox.inc`）加的，但 `.inc` 在 C/汇编工程里是 include 文件。正例确实存在：curl 的 `Makefile.inc`、`.libs/libcurl.inc` 就是 Makefile 片段。
**可行修法**（未实施）：`.inc` 只在路径含 `recipes-` 时判 C2；否则交给内容层或按 `.h.inc` 判 C6。

#### 5.1.2 `.config` 一刀切成 C4

| 文件 | 实际是什么 |
| ---- | ---------- |
| QEMU `gitdm.config` | LWN gitdm 开发统计工具配置 |
| apt `debian/default-sequoia.config` | APT 的加密策略配置 |
| FreeRTOS `examples/coverity/coverity_misra.config` | Coverity/MISRA 静态分析配置 |

**根因**：`.config` 既可能是 Kconfig 输出（U-Boot 的 `configs/am335x_evm.config`、OHOS 的 `debug.config`，内容都是 `CONFIG_*=`），也可能是任意工具的配置。同类里真阳性占多数：U-Boot 48 个、OpenWrt 16 个都是真的取值文件。
**可行修法**：`.config` 不直接判 C4，先读内容，命中 `^(CONFIG|LOSCFG)_\w+=` 才归 C4。

#### 5.1.3 Kconfig 锚点太松

- BusyBox `TODO` 被判成 C7。原因是正文第 45 行有一句英文 `config option for "date" are low hanging fruit here?`，匹配了 `^\s*(?:menu)?config\s+\w+`。
- **根因**：锚点只要求「行首是 config + 一个词」，散文也能命中。
- **可行修法**：要求同一文件里至少 2 处 `config` 行，或出现 `bool`/`tristate`/`depends on`/`default` 之一。

#### 5.1.4 `kconfig` 前缀命中文档

- BusyBox `docs/Kconfig-language.txt` 被判成 C7——它是**讲 Kconfig 语法的文档**，因为文件名以 `Kconfig` 开头。
- **可行修法**：前缀规则叠加「不在 `docs/` 下」或「后缀不是 `.txt/.md/.rst`」。

#### 5.1.5 类型对、载体错：测试与示例脚本

- BusyBox 的 `testsuite/*.sh`、`examples/var_service/*/run` 等 66 个脚本被判成 C1。
- 这**不是类型错误**（它们确实是 shell 脚本），但下游 S1 会把测试命令当构建工具读一遍。
- 与 OHOS 实测的「噪声目录占 9%」同源。当前版本没有目录降权，这是已知取舍。

### 5.2 漏报：该路由，但没路由

| 现象 | 例子 | 数量 | 现状与原因 | 应归 |
| ---- | ---- | ---: | ---------- | ---- |
| Android init 的 `.rc` | `init/*.rc` | 3 | 后缀不在表里；内容层不读未知后缀 | C1（命令序列） |
| 汇编变体后缀 | FreeRTOS `.asm` / `.s79` / `.s85` / `.s90` | 18 | 只认 `.s` 和 `.S` | C6 |
| 板级头文件 | U-Boot `include/configs/*.h` | 764 | 只对**名字恰好是 `board.h`** 的文件追加 S4/S7（NuttX 特例） | C6 + S4/S7 |
| `.defconfig` 当后缀 | U-Boot `configs/tqma6_mba6_common.defconfig` | 3 | 规则只认 `_defconfig` 结尾 | C4 |
| 项目自有脚本后缀 | OpenWrt `.init` / `.hotplug` / `.script` / `.uc` | 58 | 后缀不在表里 | C1 |
| `BSDmakefile` | OpenWrt 根目录 | 1 | 整名表只有 `Makefile` / `GNUmakefile` | C2 |
| Bazel 文件 | googletest `BUILD.bazel` / `*.bzl` / `MODULE.bazel` | 7 | 未覆盖 Bazel 生态 | C2 |
| 板级 YAML | Zephyr `boards/**/*.yaml` | 50 | 未覆盖硬件模型 v2 | 待定（C4/C8） |
| `*.cmake` 模块名不规范 | — | 0 | 已覆盖 | — |

### 5.3 边界：不算误判，但要知道

| 现象 | 例子 | 说明 |
| ---- | ---- | ---- |
| Rust / JS / Go 源码不路由 | `.rs` 102、`.js` 142、`.go` 99 | C6 只定义为 C/C++ |
| 文档 / 图片 / 补丁不路由 | `.md`、`.png`、`.patch` 805 | 本来就不该进路由表 |
| 自动生成物不路由 | QEMU `.hx`（命令定义生成源）、U-Boot `.cfg`（镜像布局） | 不是 S1–S7 的载体 |
| 测试数据不路由 | BusyBox `.tests` / `.right` 1540 | 测试夹具 |

---

## 6. 精确率与召回率：能说到什么程度

| 指标 | 值 | 范围与口径 |
| ---- | -- | ---------- |
| 契约合规 | **14/14** | 产物全部通过 schema，路径有序无重复 |
| 类型错误率（深度核对子集） | **9 / 213 = 4.2%** | 只统计 6 棵新树的非 C6 路由；C6 未逐条核，按定义不会错 |
| 类型错误率（这 6 棵全量） | **9 / 3167 = 0.28%** | 同上 |
| 小样本准确率（OHOS） | **58 / 60 = 96.7%** | 60 个分层抽样，类别一致率 |
| 整体精确率 | **未测** | 没有跨树标注集 |
| 召回率 | **未测** | 同上；漏报按类型列在 §5.2，但没有基数 |

**为什么不能把 4.2% 当成整体误报率**：分母只含非 C6 路由，而这 6 棵树里 C6 占 93%。反过来，也不能因为 0.28% 就宣布「精确率 99.7%」——那只说明「C/C++ 源码容易认」。

**要得到真实指标**，需要一次标注：跨树分层抽 200–300 个文件，人工标「承载哪些 S」，再算 precision / recall。这是唯一还没做的量化。

---

## 7. 结论

1. **Kconfig / Makefile / defconfig / dts 这一类「结构化构建体系」覆盖得最好**：U-Boot 99%、QEMU 97%、curl 95%。
2. **项目自有约定是主要缺口**：每个工程都有 1–3 种自己的写法（OpenWrt 的 `.init`、FreeRTOS 的 `.s79`、U-Boot 的板级头文件），这些不靠通用规则解决，只能按系统补「专属规则」。
3. **两类后缀被过度泛化**：`.inc` 和 `.config` 在不同工程里语义完全不同，是目前最明确的误报来源（7/9 条错误来自它们）。
4. **类型正确 ≠ 载体正确**：测试、示例、文档类脚本会被按类型路由出去。对下游是噪声，但不会造成错误结论（S1 多读几个脚本而已）。
5. **非 C/C++ 生态几乎不覆盖**，这是范围决定，不是缺陷。

**改进优先级**（均未实施，代码保持原样）：

| 优先级 | 改动 | 预期收益 |
| ------ | ---- | -------- |
| P0 | `.inc` / `.config` 增加路径或内容约束 | 消掉 7/9 条已确认误报 |
| P1 | Kconfig 锚点加严（≥2 处或出现 `bool`/`depends on`） | 消掉 `TODO` 这类误报 |
| P1 | `kconfig` 前缀排除 `docs/` | 消掉文档误报 |
| P2 | 补 `.rc`、`.asm`、`.s79/.s85/.s90`、`BSDmakefile`、`*_defconfig` 变体 | 补回 25+ 个漏报 |
| P2 | U-Boot 式板级头文件（`include/configs/*.h`）追加 S4/S7 | 764 个文件的 classes 更准 |
| P3 | Bazel / OpenWrt 脚本后缀 / Zephyr YAML | 按需，取决于是否要接这些生态 |

---

## 8. 复现

```bash
# 单棵树
py scan.py --stage tree --root <源码树> --out out/filelists.json --stats

# 多棵树（自动过 schema）
py tests/tools/scan_matrix.py \
  --tree u-boot=C:\path\to\u-boot \
  --tree qemu=C:\path\to\qemu \
  --tree busybox=C:\path\to\busybox
```

树怎么取：

```bash
git clone --depth 1 --filter=blob:none --sparse <repo> <dir>
git -C <dir> sparse-checkout set <子目录>      # 例：hw/arm hw/core include/hw
```

| 项目 | 检出的子目录 |
| ---- | ------------ |
| U-Boot | `configs` `include/configs` `arch/arm` `board/qemu` |
| BusyBox | 完整克隆 |
| OpenWrt | `package/network` `target/linux/generic` `include` |
| QEMU | `hw/arm` `hw/core` `include/hw` |
| FreeRTOS-Kernel | 完整克隆 |
| curl | `lib` `include` `m4` `src` |
| Zephyr | `boards/qemu` `samples/hello_world` `soc/arm` |
| googletest | `googletest` `googlemock` |
| apt | `debian` `apt-pkg` `methods` |
| AOSP init | `init` |
| wayland | `src` `protocol` `tests` |
| ripgrep / express / gin | 完整克隆 |
