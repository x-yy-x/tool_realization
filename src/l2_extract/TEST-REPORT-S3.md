# OpenHarmony 部件（S3）提取报告

> **测什么**：从源码树里把「部件」相关的声明读成结构化记录。
> **用什么**：`x-yy-x/tool_realization` 的 S3 提取器，冻结提交 `7c045da`（分支 `fix/s3-20261113`）。
> **数据来源**：OpenHarmony 5.1 完整源码树（806,106 个文件）+ OpenHarmony 6.1 按仓视图（642 个声明文件）。
> **报告只回答一件事**：S3 能读出什么、读出来以后能验什么、在真实树上暴露了哪些问题。

---

## 1. 组件（部件）是什么

### 1.1 一句话定义

**部件 = 在 `bundle.json` 里用 `component` 声明的一个交付单元。**

它必须说清三件事：

| 要点 | 字段 | 例子 |
| ---- | ---- | ---- |
| 叫什么、属于哪个子系统 | `component.name` + `component.subsystem` | `hilog`，属于 `hiviewdfx` |
| 依赖哪些别的部件 | `component.deps.components` | `c_utils`、`init`、`zlib` |
| 产出哪些构建目标 | `component.build.sub_component` | `//base/hiviewdfx/hilog:libhilog` |

### 1.2 和子系统、模块、仓的关系

```text
产品 demo（productdefine/common/products/demo.json）
└── 子系统 communication
    └── 部件 ipc（bundle.json: component.name = "ipc"）
        ├── 依赖部件 hilog        ← bundle.json 的 deps.components
        └── 构建目标 //ipc:ipc    ← bundle.json 的 build.sub_component
```

| 概念 | 单位 | 声明在哪 | 例子 |
| ---- | ---- | -------- | ---- |
| 产品 | 一次构建的目标 | 产品配置 | `demo` |
| 子系统 | 分组/命名空间 | 产品配置的 `subsystem`；GN 模板 | `communication` |
| **部件** | **交付单元** | `bundle.json`；旧格式 `ohos.build` | `ipc` |
| 模块/目标 | 真正被编译的东西 | `bundle.json` 的 `build`；`BUILD.gn` | `//ipc:ipc` |
| 仓 | 一个 git 仓库 | manifest | `communication_ipc` |

三条容易混的：

1. **产品只选到部件，不直接选模块。**
2. **一个仓可以有多个部件**（一个仓里多份 `bundle.json`）。
3. **子系统只是标签**，没有自己的清单文件，也不会报「子系统找不到」。

### 1.3 身份：短名为主，子系统是属性

S3 用**部件短名**做身份（`hilog`），把子系统当属性记录下来。

为什么不用「子系统:名字」当身份：

- 产品配置里的子系统标签**经常和 bundle 里的不一致**；
- 实测 5.1 全树里，**254 条选择记录**的标签与定义不同（例如同一部件在 `appexecfwk` 和 `bundlemanager` 两个标签下出现）；
- 用复合键会把同一个部件拆成两个，造成假的「缺失」。

代价：不同子系统真出现同名部件时，需要靠重复定义诊断和产品上下文消歧（见 §4.3）。

### 1.4 部件不是什么

| 不是 | 为什么 |
| ---- | ------ |
| 不是一个仓 | 仓可以包含多个部件；仓在 ≠ 产品要的部件在 |
| 不是一个模块 | 模块是构建目标，数量是部件的几十倍；产品配置不引用模块 |
| 不是一个安装包 | 包是发行单位（`debian/control`、`package.json`），粒度不同 |

---

## 2. 涉及哪些文件（含真实例子）

### 2.1 总表

| 文件 | 作用 | 例子 | 提取成什么 |
| ---- | ---- | ---- | ---------- |
| `bundle.json` | 部件定义、依赖、构建目标 | `base/hiviewdfx/hilog/bundle.json` | `component` / `requires` / `build_targets` |
| `ohos.build` | 旧格式部件定义 | `device/demo/ohos.build` | `component` / `build_targets` |
| 板级产品配置 | 产品选了哪些部件 | `vendor/demo/demo_board/config.json` | `selects` |
| 产品清单 | 产品选择 + 继承 | `productdefine/common/products/demo.json` | `selects` / `inherits` |
| 继承清单 | 产品清单的一部分 | `productdefine/common/inherit/base.json` | `selects` |
| 公共基础清单 | mini/small/standard 的公共选择 | `productdefine/common/base/mini_system.json` | `selects` |
| 子系统 GN 模板 | 子系统包含哪些组件 | `build/lite/config/subsystem/demo/BUILD.gn` | `subsystem` / `subsystem_components` |
| 非声明文件 | 脚本、示例、测试配置 | `build/build.sh` | 不读 |

### 2.2 `bundle.json`：最核心的文件

真实例子（`base/hiviewdfx/hilog/bundle.json`，节选）：

```json
{
  "name": "@ohos/hilog",
  "component": {
    "name": "hilog",
    "subsystem": "hiviewdfx",
    "deps": {
      "components": ["bounds_checking_function", "c_utils", "init", "zlib"]
    },
    "build": {
      "sub_component": [
        "//base/hiviewdfx/hilog/services/hilogd:hilogd",
        "//base/hiviewdfx/hilog/interfaces/native/innerkits:libhilog"
      ]
    }
  }
}
```

读出来：

- 定义：`hilog`（子系统 `hiviewdfx`）
- 依赖：`hilog → bounds_checking_function / c_utils / init / zlib`
- 构建目标：`//…:hilogd`、`//…:libhilog`

**注意**：顶层 `"name": "@ohos/hilog"` 不是部件名，S3 只认 `component.name`。产品配置引用的也是 `hilog`，不是 `@ohos/hilog`。

### 2.3 `ohos.build`：旧格式

```json
{
  "subsystem": "chipset",
  "parts": {
    "demo_board": {
      "module_list": ["//device/demo:board"]
    }
  }
}
```

读出来：定义 `demo_board`（子系统 `chipset`），构建目标 `//device/demo:board`。

2.x / 3.0 的树里没有 `bundle.json`，只有这种格式；不认它就会漏掉整个时代的部件。

### 2.4 产品配置：选择

（`vendor/demo/demo_board/config.json`，节选）

```json
{
  "product_name": "demo_board",
  "subsystems": [
    {
      "subsystem": "chipset",
      "components": [
        { "component": "demo_board", "features": ["board_debug = false"] }
      ]
    }
  ]
}
```

读出来：`selects demo_board`，带 `features` 选项。

产品配置里的 `component` 是「要什么」，不是「有什么」——S3 把它和定义分开记录。

### 2.5 `inherit`：产品清单的一部分

```json
{
  "product_name": "demo",
  "inherit": ["productdefine/common/inherit/base.json"],
  "subsystems": [
    { "subsystem": "communication", "components": [{"component": "ipc"}] }
  ]
}
```

`inherit` 的意思是「除了我下面列的，再并上这份清单」。

S3 只记录「这个产品引用了哪个文件」（`inherits`），**不会自动展开合并**——展开属于后续解析步骤。

### 2.6 `base`：公共基础清单

真实文件 `productdefine/common/base/mini_system.json`：

```json
{
  "subsystems": [
    { "subsystem": "hiviewdfx", "components": [{"component": "hilog_lite"}] }
  ]
}
```

结构和产品配置一样。三个 base 文件共 95 条选择（mini 19、small 30、standard 46）。

> 早期实现把 base 当成另一种格式（`parts` 映射）读，导致三个文件全部报错、95 条选择丢失。本版已修复。

### 2.7 子系统 GN 模板

```gn
lite_subsystem("demo") {
  subsystem_components = [ "//ipc:ipc" ]
  subsystem_components += [ "//hilog:hilog" ]
}
```

读出来：`subsystem demo`；`subsystem_components` 先 `=` 后 `+=`。

S3 保留**操作符和顺序**（`=` / `+=` / `-=`），因为不执行 GN。真实树里还有带变量和条件的写法：

```gn
subsystem_components += "${aafwk_lite_path}/frameworks/ability_lite:aafwk_abilitykit_lite"
if (demo_core_enable_trace) {
  subsystem_components += [ "//components/demo_trace:trace" ]
}
```

对应输出会带 `symbolic`（含变量）或 `when`（条件），而不是当成确定列表。

### 2.8 不读的文件

`build/build.sh`、示例应用配置、测试配置等即使被路由进来，S3 也只在 `diagnostics` 里报「无法识别」，不会把脚本里的词当成部件。

---

## 3. S3 提取什么，希望后面验证什么

### 3.1 提取九种事实

| kind | 回答的问题 | 来自哪种文件 | 例子 |
| ---- | ---------- | ------------ | ---- |
| `component` | 树里**有**这个部件吗？ | `bundle.json`、`ohos.build` | `hilog` |
| `requires` | 这个部件**依赖**谁？ | `deps.components` | `hilog → c_utils` |
| `conditional_requires` | 依赖是否**带内核/板卡条件**？ | `deps.kernel_special`、`board_special` | `linux → demo_linux_adapter` |
| `selects` | **产品**选了谁？ | 产品配置、inherit、vendor 配置 | `demo 选 ipc` |
| `inherits` | 产品清单**还引用了哪个文件**？ | `inherit` | `demo → base.json` |
| `system_component` | 产品指定的**系统组件包**是哪个？ | `system_component` 字段 | — |
| `build_targets` | 这个部件**产出哪些构建目标**？ | `build.sub_component`、`module_list` | `//ipc:ipc` |
| `subsystem` | GN 里**声明了哪个子系统**？ | 子系统 `BUILD.gn` | `demo` |
| `subsystem_components` | GN 里子系统**包含哪些组件**、怎么加怎么减？ | 同上 | `=` / `+=` / `-=` |

**为什么分开放**：后续要问的问题不一样，证据位置也不一样。

- 「有没有」看 `component`；
- 「产品要什么」看 `selects`；
- 「依赖闭不闭合」要 `requires` + `component` + `selects`；
- 「条件依赖」要 `conditional_requires` + 配置；
- 「部件对应哪些模块」看 `build_targets`。

合成一张表就分不清「谁说的、在哪说的、说的哪种关系」。

### 3.2 希望后面验证什么

| 验证问题 | 需要的记录 |
| -------- | ---------- |
| 产品选的部件，树里都有定义吗？ | `selects` + `component` |
| 这些部件的**依赖闭包**闭合吗？ | `selects` + `requires` + `component` |
| 带条件的依赖，在当前内核/板卡下满足吗？ | `conditional_requires` + 环境/配置 |
| 产品继承链完整吗？ | `inherits` + `selects` |
| 子系统 GN 清单和 bundle 清单一致吗？ | `subsystem_components` + `component` |
| 同名多定义该选哪个？ | 重复定义 + 产品上下文 |

**S3 本身不做这些判断**：它只输出事实。判断交给后面的验证层。

---

## 4. 测试结果与发现的问题

### 4.1 两次实验

| 实验 | 输入 | 方法 |
| ---- | ---- | ---- |
| **5.1 完整树** | 806,106 个文件 | A 组用原始 L1 路由；B 组用人工载体清单；对照看路由差异 |
| **6.1 按仓视图** | 492 个仓、642 个声明文件 | 每仓 `git ls-tree` 取完整文件清单，只落盘声明文件 |

### 4.2 规模与命中率

| 指标 | 5.1 B（载体清单） | 6.1（按仓视图） |
| ---- | ----------------: | --------------: |
| 输入文件 | 531 | 642 |
| 记录总数 | 8,621 | 9,918 |
| 部件定义 | 481（476 个唯一名） | 587（582 个唯一名） |
| 产品选择 | 2,771 | 2,927 |
| 依赖记录 | 4,868（显式第三方 252） | 5,794 |
| 选择记录同名命中 | 2,676 / 2,771（96.6%） | — |
| **唯一选择名命中** | **391 / 442（88.5%）** | **447 / 499（89.6%）** |
| 依赖记录同名命中 | 4,578 / 4,616（99.2%） | — |
| **唯一依赖名命中** | **297 / 324（91.7%）** | **343 / 365（94.0%）** |
| 子系统标签不一致记录 | 254 | — |
| 重复定义冲突 | 5 | 5 |

**两个命中率要一起看**：

- 按「条」算 96.6% / 99.2%——绝大多数记录都对得上；
- 按「名」算 88.5% / 91.7%——对不上的名字不多，但每个只出现 1–5 次。

结论：**坏名字是长尾，不是普遍现象**。

### 4.3 问题一：同名与身份（最主要的开放问题）

#### ① 同物不同标签

真实例子（5.1）：

- `productdefine/common/inherit/headless.json:81` 用 `appexecfwk` 标签选 `bundle_framework`；
- 同一文件 `:92` 又用 `bundlemanager` 标签选同一个部件；
- 定义在 `foundation/bundlemanager/bundle_framework/bundle.json:9`，标签是 `bundlemanager`。

全树里这种「标签和定义不一致」的选择有 **254 条**。

**影响**：如果按「子系统:名字」匹配，这 254 条会变成假缺失；按短名匹配能对上，但标签信息丢失。

#### ② 改名与疑似拼写

| 清单里写的 | 定义里的 | 判断 |
| ---------- | -------- | ---- |
| `runttime_core` | `runtime_core` | 只差一个字母，**疑似拼写错误** |
| `call` | `cellular_call` | 不同子系统，不能仅凭语义宣布等价 |
| `mms` | `sms_mms` | 同上 |
| `profiler` | `hiprofiler` | 同上 |

报告的处理是克制的：`runttime_core` 只标「值得核对」，不自动改；`call`/`mms` 不自动当别名。

**影响**：不改名就会一直产生 3–4 个假缺失；自动改名又有改错的风险。

#### ③ 同名不同物（重复定义）

5 个键各出现两次，分三种情况：

| 键 | 情况 |
| -- | ---- |
| `wifi_iot_sample_app` | 一个是 app，一个是整个 sample 仓 → **同名不同物** |
| `hisilicon_products` | 两块海思板各声明一次 → **同名不同板** |
| `hardware` / `middleware` / `hi3861_sdk` | 子目录 `bundle.json` 与父目录 `ohos.build` 聚合声明并存 → **聚合 + 细粒度** |

**影响**：

1. 下游若用 `{名字: 记录}` 建索引，后写的一条会静默覆盖先写的；
2. 被覆盖那条的依赖和构建目标会消失；
3. 报告里只看到一条，用户不知道还有另一条。

本版的处理：全部保留出处，输出 `DUPLICATE_COMPONENT` 冲突诊断，不自动选一条。

### 4.4 问题二：L1 路由太宽

5.1 的 A 组（原始 L1 路由）：

| 指标 | 数值 |
| ---- | ---: |
| 路由文件 | 7,048 |
| 有输出的文件 | 529 |
| **无输出文件** | **6,519** |
| `S3_UNRECOGNIZED` 诊断 | 1,059 |
| `UNSUPPORTED_SEMANTICS` 诊断 | 5,456 |

A 组和 B 组的**记录内容只差 2 条**（A 独有 0，B 独有 2）。

**结论**：L1 把 13 倍的文件送进 S3，只多花了时间和诊断噪声，没有多读到事实。**路由精度要收窄**，把「名字像配置、内容不是声明」的文件挡在外面。

### 4.5 问题三：S3 分支里缺 L1 路由规则

这是 5.1 实验里 `subsystem` 记录为 0 的直接原因，也已经核对过代码：

| 分支 | 提交 | 有没有「子系统 GN → S3」规则 |
| ---- | ---- | ---------------------------- |
| `sync/filelists-contract` | `e1d6eb7` | **有** |
| `fix/s3-20261113`（本次冻结） | `7c045da` | **没有** |

原因：`fix/s3-20261113` 是从 `codex/s3-server-test` 切出来的，那个分支创建时还没有这条 L1 规则；L1 的改动提交在另一个分支上。

**这不是 5.1 源码的问题，也不是 S3 提取的问题，是分支同步问题。** 把 `e1d6eb7` 挑到 S3 分支后，A 组的 `subsystem` / `subsystem_components` 就会从 0 变成 1 + 1。

### 4.6 问题四：严格 JSON 会整份丢弃

6.1 视图里发现一个真实文件：

`drivers/interface/display/bundle.json` 的 `features` 出现了两次（第 22 行和第 28 行）。

S3 的严格解析拒绝整份文件 → 部件 `drivers_interface_display` 没有定义 → 它在「产品选择」和「他人依赖」两侧同时变成未解析。

**影响**：一个格式错误会让整个部件从清单里消失；普通 `json.loads` 会静默取最后一个，问题被藏起来。

5.1 的 531 个载体文件里独立复核没有发现重复键；6.1 的视图里有 1 个。

### 4.7 问题五：子模块没覆盖

6.1 的 642 个文件来自 `git ls-tree`，但 `ls-tree` 不会展开子模块（gitlink）。

实测有 18 个 gitlink，其中 `third_party/rust/rust` 有 11 个，而且它自己没有 `bundle.json`——Rust 的 crate 声明在子模块里。

**影响**：这类部件在清单里完全不可见，不是「未解析」，而是「根本没看到」。

### 4.8 问题六：未解析名字的分类

5.1 的 51 个未解析选择名、27 个未解析依赖名，按原因分：

| 类型 | 例子 | 处理方式 |
| ---- | ---- | -------- |
| 改名 / 历史命名 | `call`、`mms`、`profiler` | 别名表，人工确认 |
| 疑似拼写 | `runttime_core` | 只提示，不自动改 |
| 同物不同标签 | `appexecfwk` vs `bundlemanager` | 短名匹配 + 标签属性 |
| 第三方 / 外部名 | `eudev`、`giflib`、`rust` | 第三方索引，不混进「缺失」 |
| 条件配置 / 未覆盖输入 | `location_*`、`*_device_driver` | 需要产品上下文或补输入 |

**关键**：仅凭「没有同名定义」不能判定缺源码。必须分类，否则报告会被噪声淹没。

### 4.9 这些问题对构建意味着什么

| 问题 | 可能的后果 |
| ---- | ---------- |
| 改名 / 拼写 | 配置阶段报 `component not found`，构建直接停止 |
| 同物不同标签 | 按名字解析通常不炸，但子系统归组、feature 门控可能错 |
| 重复定义 | 不必然失败，可能静默取一份 → 依赖少了、目标错了 → 链接失败或功能丢失 |
| 严格 JSON 丢文件 | 部件定义整份消失，构建把它当不存在 |
| 子模块未覆盖 | 静态清单看不到，构建却会用到 |

**但提取结果不能直接断言「一定编不过」**——它只证明「声明之间不一致」。要回答「能不能编」，需要验证层做闭包差（产品要的传递闭包 − 树里有的定义）。

---

## 5. 结论与建议

### 5.1 结论

1. S3 能把分散的部件声明转成**带文件行号的事实清单**，适合作为验证前的可审计输入。
2. 两次独立实验（5.1 完整树、6.1 按仓视图）的命中率接近（88.5% / 89.6%），**重复定义完全一致（5 个）**——说明这些是跨版本稳定的结构性问题。
3. 当前主要问题不是「读不出来」，而是**读出来的东西对不上**：标签漂移、改名、重复定义、格式化错误、子模块缺口。
4. 提取与验证必须分开：S3 只出事实，判断留给验证层。

### 5.2 建议（按优先级）

| 优先级 | 动作 | 解决什么 |
| ------ | ---- | -------- |
| P0 | 把 L1 路由修复合进 S3 分支 | `subsystem` 记录缺失（§4.5） |
| P0 | 收窄 L1 路由 | 6,519 个无输出文件、1,059 条噪声诊断（§4.4） |
| P1 | 重复定义保留多值索引 + 产品上下文消歧 | 5 个重复定义（§4.3） |
| P1 | 别名表（`call`/`mms`/`profiler`），拼写只提示不改 | 未解析长尾（§4.8） |
| P1 | 严格 JSON 失败时保留「降级记录」并标 `PARSE_REJECTED` | 整份文件丢失（§4.6） |
| P2 | 展开或显式标注子模块 | Rust 等 crates 不可见（§4.7） |
| P2 | 第三方索引（manifest 生成），与缺失分开统计 | 未解析依赖噪声（§4.8） |

### 5.3 边界

本报告的数字只衡量「声明可关联程度」，**不是产品正确率，也不是构建成功率**。S3 不做继承展开、不做 GN 求值、不做依赖闭包；这些是后续验证层的工作。
