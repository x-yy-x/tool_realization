# S3 部件提取：实现说明、全量验证与分析

---

# 第一部分：概念与实现

## 1. 这个实现里，「部件」是什么

### 1.1 一句话定义

**部件（component）= 在 `bundle.json` 里用 `component.name` 声明的一个交付单元。**

它有三件事必须说清楚：

1. **它叫什么、属于哪个子系统** —— `component.name` + `component.subsystem`
2. **它依赖哪些别的部件** —— `component.deps.components`
3. **它产出哪些构建目标** —— `component.build.sub_component` / `inner_kits`

### 1.2 和仓、模块、产品的关系

用真实例子画出来：

```text
产品 demo（productdefine/common/products/demo.json）
└── 子系统 communication
    └── 部件 ipc（bundle.json 里 component.name = "ipc"）
        ├── 依赖部件 hilog          ← bundle.json 的 deps.components
        └── 构建目标 //ipc:ipc       ← bundle.json 的 build.sub_component
```

| 概念           | 单位               | 在本实现里由哪个文件声明                                             | 例子                  |
| -------------- | ------------------ | -------------------------------------------------------------------- | --------------------- |
| 产品           | 一次构建的目标     | `productdefine/common/products/*.json`、`vendor/*/*/config.json` | `demo`              |
| 子系统         | 分组/命名空间      | 产品配置里的 `subsystem` 字段；GN 模板                             | `communication`     |
| **部件** | **交付单元** | `bundle.json` 的 `component`；老格式 `ohos.build` 的 `parts` | `ipc`               |
| 模块/目标      | 真正被编译的东西   | `bundle.json` 的 `build`；`BUILD.gn` 的 target                 | `//ipc:ipc`         |
| 仓             | 一个 git 仓库      | manifest                                                             | `communication_ipc` |

要点：

- 产品只选到**部件**，不直接选模块；
- 一个仓可以有多个 `bundle.json`（多个部件）；
- 部件是「声明」单位，不是「代码」单位——实现只读声明，不读实现代码。

### 1.3 身份怎么表示：短名 vs `子系统:名字`

这个实现用**复合键 `subsystem:name`** 当部件身份，例如 `communication:ipc`、`hiviewdfx:hilog`。

原因：不同子系统里可能有同名部件，复合键能区分它们。

代价（后面第六部分会用数据说明）：产品配置里的子系统标签并不总是和 `bundle.json` 里的一致，复合键会把「同一个部件」拆成两个身份。

### 1.4 处理方式：只做「读声明」，不做「跑构建」

实现明确不做四件事：

| 不做                   | 说明                                             |
| ---------------------- | ------------------------------------------------ |
| 不执行任何源码/脚本    | 只读文本                                         |
| 不展开产品继承         | `inherit` 只记录「引用了哪个文件」，不自动合并 |
| 不求值 GN              | 变量、条件原样保留，不计算                       |
| 不做依赖闭包、不做验证 | 只输出声明和关系，判断留给后续层                 |

所以它的定位是「**把部件相关的声明读成结构化记录**」，不是「判断产品能不能编」。

---

## 2. 涉及哪些文件

### 2.1 总表

| 文件                     | 例子                                                             | 声明了什么                        | 实现读成哪些 kind                              |
| ------------------------ | ---------------------------------------------------------------- | --------------------------------- | ---------------------------------------------- |
| `bundle.json`          | `examples/s3/source/ipc/bundle.json`                           | 部件定义、依赖、构建目标          | `component`、`requires`、`build_targets` |
| `ohos.build`（老格式） | `examples/s3/source/device/demo/ohos.build`                    | 部件定义、模块列表                | `component`、`build_targets`               |
| 板级产品配置             | `examples/s3/source/vendor/demo/board/config.json`             | 产品选了哪些部件、带什么 features | `selects`                                    |
| 产品清单                 | `examples/s3/source/productdefine/common/products/demo.json`   | 产品选了谁、继承谁                | `selects`、`inherits`                      |
| 继承清单                 | `examples/s3/source/productdefine/common/inherit/base.json`    | 产品清单的一部分                  | `selects`                                    |
| 公共基础清单             | `productdefine/common/base/*.json`（真实 OHOS）                | 同上，结构与产品配置一致          | 本应 `selects`，**当前实现解析失败**   |
| 子系统 GN 模板           | `examples/s3/source/build/lite/config/subsystem/demo/BUILD.gn` | 子系统包含哪些组件                | `subsystem`、`subsystem_components`        |
| 非 S3 文件               | `examples/s3/source/build/build.sh`                            | 无关                              | 不读（示例里专门放了它验证这一点）             |

### 2.2 `bundle.json`：最主要的文件

真实示例（`examples/s3/source/ipc/bundle.json`，全文）：

```json
{
  "name": "@ohos/ipc",
  "component": {
    "name": "ipc",
    "subsystem": "communication",
    "deps": {
      "components": ["hilog"]
    },
    "build": {
      "sub_component": ["//ipc:ipc"]
    }
  }
}
```

实现从这里读出：

| 读什么                                       | 得到什么                       |
| -------------------------------------------- | ------------------------------ |
| `component.name` + `component.subsystem` | 定义：`communication:ipc`    |
| `deps.components`                          | 依赖边：`ipc` 依赖 `hilog` |
| `build.sub_component`                      | 构建目标：`//ipc:ipc`        |

注意：顶层 `"name": "@ohos/ipc"` **不用**。实现明确要求 `component.name`，顶层名不作后备（代码里写死了这条）。

### 2.3 `ohos.build`：老格式

全文（`examples/s3/source/device/demo/ohos.build`）：

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

读成：定义 `chipset:demo_board`，构建目标 `//device/demo:board`。

### 2.4 产品配置：选择

（`examples/s3/source/productdefine/common/products/demo.json`，节选）

```json
{
  "product_name": "demo",
  "inherit": ["productdefine/common/inherit/base.json"],
  "subsystems": [
    { "subsystem": "communication",
      "components": [{ "component": "ipc" }] }
  ]
}
```

读成：

- `selects communication:ipc`（来自 `subsystems[].components[]`）
- `inherits productdefine/common/inherit/base.json`（来自 `inherit` 数组）

**注意**：`inherit` 只记录「引用了这个文件」，不会自动把 `base.json` 的内容并进来。这是刻意的——合并属于后续步骤。

### 2.5 继承清单：产品清单的一部分

（`examples/s3/source/productdefine/common/inherit/base.json`）

```json
{
  "subsystems": [
    { "subsystem": "hiviewdfx",
      "components": [{ "component": "hilog" }] }
  ]
}
```

结构完全一样，只是单独放在一个文件里。读成 `selects hiviewdfx:hilog`。

### 2.6 公共基础清单：当前实现的一个 bug

真实 OHOS 文件 `productdefine/common/base/mini_system.json` 的结构是：

```json
{
  "subsystems": [
    { "subsystem": "hiviewdfx", "components": [{ "component": "hilog_lite" }] }
  ]
}
```

和产品配置一模一样。但实现把 `productdefine/**/base/**` 当成另一种格式（`parts` 映射）来读，要求键是 `subsystem:component`，于是三个 base 文件全部报 `INVALID_STRUCTURE`，里面的选择全部丢失。

### 2.7 子系统 GN 模板

（`examples/s3/source/build/lite/config/subsystem/demo/BUILD.gn`）

```gn
lite_subsystem("demo") {
  subsystem_components = [ "//ipc:ipc" ]
  subsystem_components += [ "//hilog:hilog" ]
}
```

读成：

- `subsystem demo`（模板名）
- `subsystem_components`：`=` 赋值 `//ipc:ipc`；`+=` 追加 `//hilog:hilog`

实现保留**操作符和顺序**（`=`、`+=`、`-=`），因为它不会执行 GN，顺序本身就是信息。

全字段演示里还有更复杂的（`examples/s3/all-fields-demo/source/build/lite/config/subsystem/demo/BUILD.gn`）：

```gn
subsystem_components -= [ "//components/demo_legacy:legacy" ]
if (demo_core_enable_trace) {
  subsystem_components += [ "//components/demo_trace:trace" ]
}
subsystem_components += common_components + [ "//components/demo_extra:extra" ]
```

对应的输出会带 `op`、`when`（条件）、`expression`（表达式）、`symbolic`（含变量），而不是把它当成确定的列表。

---

## 3. 提取器的实现方法（对照例子走一遍）

### 3.1 主流程

```text
filelists.json（L1 产物）
   │  只取 classes 含 "S3" 的文件
   ▼
SourceProvider：路径安全、软链/越界检查、资源上限
   │
   ▼
按文件类型分派（ComponentReader / GNReader）
   │  每个 JSON 值都记下「字节区间 → 行号」
   ▼
声明（definition / selection / subsystem …）+ 引用（deps / inherit …）
   │
   ▼
compact：定义、选择、依赖、构建目标、GN 分组 → 扁平记录
   │
   ▼
requirements.json + diagnostics.json
```

### 3.2 JSON 文件怎么读

`component_reader.py` 是一个 `if/elif` 分派链，按文件内容判断类型：

| 判断顺序 | 文件长什么样                                | 处理成                                    |
| -------- | ------------------------------------------- | ----------------------------------------- |
| 1        | 被上下文标记为 `base`                     | 按 `parts` 读（**这一条是 bug**） |
| 2        | 文件名是 `ohos.build` 且有 `parts` 对象 | 每个 part = 一个定义                      |
| 3        | 有 `component` 对象                       | 定义 + 依赖 + 构建目标                    |
| 4        | 有 `components` 数组（旧版组件清单）      | 每个元素 = 一个定义                       |
| 5        | 有 `subsystems` 或 `product_name`       | 产品配置：选择 + 继承                     |
| 6        | 其他                                        | 标记为 metadata，报「无法识别」           |

关键细节：

- **证据精确到值**：解析时记录了每个 JSON 指针（如 `/subsystems/0/components/0`）对应的字节区间，再换算成行号。所以每条记录都能指出「第几行」。
- **严格 JSON**：重复键、NaN/Infinity、孤立代理字符都会报错，不会静默取最后一个。
- **类型不对只诊断、不猜**：例如 `features` 不是数组，会进 `diagnostics`，而不是强转。

### 3.3 GN 文件怎么读

`gn_components.py` 不是 GN 解释器，是一个**有界结构读取器**：

1. 词法分析：注释、字符串、标识符、操作符；
2. 找到 `lite_subsystem("名字")` 块；
3. 只跟踪对 `subsystem_components` 的赋值事件（`=`、`+=`、`-=`），保留顺序；
4. 字面量转成目标列表；含 `$` 的表达式（如 `${aafwk_lite_path}/...`）**不展开**，标记 `symbolic`；
5. `if (...)` 里的赋值带 `when` 条件，不当成确定生效。

示例里的 `aafwk` 真实模板就是第 4 种情况，会报 `GN_SYMBOLIC`——这是**正确行为**，不是失败。

### 3.4 例子跑出来的 13 条记录

对 `examples/s3` 运行，输出 13 条，逐条对应源文件：

| #  | kind                 | 名字                                   | 出处                                            | 来自哪个文件的哪一行             |
| -- | -------------------- | -------------------------------------- | ----------------------------------------------- | -------------------------------- |
| 1  | subsystem            | demo                                   | `build/lite/config/subsystem/demo/BUILD.gn:3` | `lite_subsystem("demo")`       |
| 2  | subsystem_components | demo                                   | `BUILD.gn:4`                                  | `=` `//ipc:ipc`              |
| 3  | subsystem_components | demo                                   | `BUILD.gn:5`                                  | `+=` `//hilog:hilog`         |
| 4  | component            | chipset:demo_board                     | `device/demo/ohos.build:4`                    | `parts.demo_board`             |
| 5  | build_targets        | chipset:demo_board                     | `ohos.build:4`                                | `module_list`                  |
| 6  | component            | hiviewdfx:hilog                        | `hilog/bundle.json:2`                         | `component.name`               |
| 7  | component            | communication:ipc                      | `ipc/bundle.json:3`                           | `component.name`               |
| 8  | requires             | hilog                                  | `ipc/bundle.json:8`                           | `deps.components[0]`           |
| 9  | build_targets        | communication:ipc                      | `ipc/bundle.json:12`                          | `build.sub_component`          |
| 10 | selects              | hiviewdfx:hilog                        | `productdefine/common/inherit/base.json:6`    | `subsystems[0].components[0]`  |
| 11 | inherits             | productdefine/common/inherit/base.json | `products/demo.json:4`                        | `inherit[0]`                   |
| 12 | selects              | communication:ipc                      | `products/demo.json:10`                       | `subsystems[0].components[0]`  |
| 13 | selects              | chipset:demo_board                     | `vendor/demo/board/config.json:7`             | `components[0]`（带 features） |

诊断：0，`complete=true`。

---

## 4. 为什么要九种 kind

先说结论：**因为后续要问九个不同的问题，而每个问题的证据位置和判断方法都不一样。** 把九种合成一种「部件表」，会丢掉「谁说的、在哪说的、说的哪种关系」。

### 4.1 九种 kind 各回答什么

| kind                     | 回答的问题                                        | 从哪种文件来                                | 例子                            | 如果不存在会怎样                     |
| ------------------------ | ------------------------------------------------- | ------------------------------------------- | ------------------------------- | ------------------------------------ |
| `component`            | 树里**有**这个部件吗？                      | `bundle.json`、`ohos.build`             | `communication:ipc`           | 无法判断任何东西是否存在             |
| `requires`             | 这个部件**依赖**谁？                        | `bundle.json` 的 `deps.components`      | `ipc → hilog`                | 只能查产品直接选的，漏掉二级依赖     |
| `conditional_requires` | 这个依赖**带条件**（内核/板卡）吗？         | `deps.kernel_special` / `board_special` | `linux → demo_linux_adapter` | 会把条件依赖当成确定依赖，误报       |
| `selects`              | **产品**选了谁？                            | 产品配置、inherit、vendor 配置              | `demo 选 ipc`                 | 不知道验哪些部件                     |
| `inherits`             | 产品清单**还引用了哪个文件**？              | 产品配置的 `inherit`                      | `demo → base.json`           | 会把「没合并」误当成「没有这些部件」 |
| `system_component`     | 产品指定的**系统组件包**是哪个？            | 产品配置的 `system_component`             | —                              | 系统级组件来源丢失                   |
| `build_targets`        | 这个部件**产出哪些构建目标**？              | `build.sub_component`、`module_list`    | `//ipc:ipc`                   | 无法把部件映射到模块（S1/S2 要用）   |
| `subsystem`            | GN 里**声明了哪个子系统**？                 | `build/lite/config/subsystem/*/BUILD.gn`  | `demo`                        | 拿不到子系统→组件的旧式映射         |
| `subsystem_components` | GN 里子系统**包含哪些组件**、怎么加怎么减？ | 同上                                        | `=`/`+=`/`-=` 列表        | 旧式子系统清单完全丢失               |

### 4.2 为什么不能合并

举三个具体冲突：

1. **`ipc` 既出现在 `component`（定义），又出现在 `selects`（被产品选中）**。如果只有一种记录，就分不清「树里有」和「产品要」——而验证恰恰是拿「要」减「有」。
2. **`ipc → hilog` 是依赖，`demo → ipc` 是选择**。两者都有方向，但方向含义不同：一个是部件作者声明的，一个是产品作者选的。修法也不同（改 bundle 还是改产品配置）。
3. **`aafwk` 的 GN 目标带变量 `${aafwk_lite_path}`**。如果强行拼成名字，会得到假目标；分开成 `symbolic` 记录，下游可以决定「解析变量」还是「跳过」。

### 4.3 kind 与后续验证的关系

| 后续要做的判断         | 需要哪些 kind                              |
| ---------------------- | ------------------------------------------ |
| 产品选的部件在不在     | `selects` + `component`                |
| 依赖闭包是否闭合       | `requires` + `component` + `selects` |
| 条件依赖是否满足       | `conditional_requires` + 配置/环境       |
| 产品继承是否完整       | `inherits` + `selects`                 |
| 部件是否真的产出目标   | `build_targets`                          |
| 旧式子系统清单是否一致 | `subsystem` + `subsystem_components`   |

**本实现只产出这些记录，不做上面的判断**——判断属于 L3。这也是它为什么叫「提取器」而不是「验证器」。

---

# 第二部分：全量验证

## 5. 怎么取得全量数据

### 5.1 服务器路线失败

目标服务器上 `<SOURCE_ROOT>` 存在，但 `ls`/`stat`/`mount` 全部挂起——该路径在 `<SHARE_MOUNT>` 网络挂载上，挂载已失效。没有 remount（需要 root，且影响共享服务器）。已记入 `服务器操作日志.md`。

### 5.2 替代：从 Gitee 拼一个「S3 视图」

1. 解析 `manifest` 6.1（492 个项目）；
2. 逐仓 `--filter=blob:none --no-checkout` 拉取，只取出 S3 相关文件：
   `bundle.json`、`ohos.build`、`build/lite/config/subsystem/**/*.gn`、`vendor/**/config.json`、`productdefine/**/*.json`；
3. 落到本地形成 416 个文件的树。

结果：

- 247 个仓有 S3 文件，26 个仓一个都没有；
- 34 个仓克隆失败：**Gitee 404**（manifest 默认 remote 是 gitcode，部分仓只在 gitcode 有）；
- `third_party`（178 个仓）按设计排除。

## 6. 全量结果

### 6.1 规模与速度

- 412 个 S3 文件 → **8538 条记录，0.44 秒**
- 诊断：3 条（全部是 base 文件的解析失败）

| kind                             |                            条数 |
| -------------------------------- | ------------------------------: |
| requires                         |                            5055 |
| selects                          |                            2735 |
| build_targets                    |                             367 |
| component                        |                             364 |
| inherits                         |                              17 |
| subsystem / subsystem_components | 0 / 0（L1 没把 GN 模板交给 S3） |

把 GN 模板也交给 S3 后（手工补 `S3` 类），多出 `subsystem 1 / subsystem_components 1`，并多两条 GN 诊断。

### 6.2 定义

- 364 条，359 个唯一键
- **5 个重复定义**：
  - `applications:wifi_iot_sample_app`（同仓两个 bundle）
  - `hisilicon_products:hisilicon_products`（两块板各一份 ohos.build）
  - `vendor:hardware` / `vendor:middleware` / `vendor:hi3861_sdk`（`device/soc/hisilicon/ohos.build` 与子目录 bundle 重复）

### 6.3 选择

- 2735 条，来自 47 个文件：products 3、inherit 10、vendor 34
- 451 个唯一组件名，**297 个能找到定义（66%）**

154 个找不到的：

| 原因                                        | 数量 | 例子                                                                |
| ------------------------------------------- | ---: | ------------------------------------------------------------------- |
| 指向 `third_party_*` / 测试仓（视图不含） |  109 | `abseil-cpp`、`FreeBSD`、`bounds_checking_function`           |
| 改名（产品用旧名）                          |    3 | `call→cellular_call`、`mms→sms_mms`、`profiler→hiprofiler` |
| 找不到同名仓（疑似 6.1 改名/合并）          |   42 | `ability_tools`、`appexecfwk_core`                              |

### 6.4 依赖

- 5055 条边，`third_party` 类 169 条
- 组件类依赖 314 个唯一目标，**215 个有定义（68%）**
- 未定义的 99 个大多是第三方名：`FreeBSD`、`abseil-cpp`、`bzip2`、`cJSON`、`alsa-lib`

### 6.5 子系统标签冲突

**122 个组件在产品配置里出现多个子系统标签**：

| 组件                                                 | 标签                                  |
| ---------------------------------------------------- | ------------------------------------- |
| `updater` / `update_service` / `sys_installer` | `update` 与 `updater`             |
| `netmanager_base` / `netstack`                   | `communication` 与 `netmanager`   |
| `bluetooth` / `dhcp`                             | `communication` 与 `connectivity` |

另有 3 个组件「选择时的子系统」与「bundle 声明的子系统」真不一致。

---

## 7. 对照之前的例子：和我原来那份 S3 设计的差别

| 维度     | 我原来的设计（`S3-部件.md`）    | 这个实现                           |
| -------- | --------------------------------- | ---------------------------------- |
| 身份     | 短名（`hilog`）                 | 复合键（`hiviewdfx:hilog`）      |
| 记录种类 | 4 类事实（定义/选择/依赖/继承）   | 9 种 kind                          |
| 闭包     | 在提取阶段算 `M = closure − D` | **不算**，只输出边           |
| 验证     | `v_consistency` 做集合差        | 不做，留给后续层                   |
| GN       | 不读                              | 读子系统模板，保留操作符/条件/符号 |
| 构建目标 | 不提取                            | `build_targets`                  |
| 证据     | `文件:行`                       | `文件:行`，且 JSON 值级字节区间  |
| 严格性   | 普通 `json.loads`               | 重复键/非有限数/代理字符都报错     |
| 诊断     | 少量                              | 有完整诊断通道，未知结构不静默     |

**这个实现比我的设计更细，但少了「闭包/验证」这一步**。它把「声明」读得很扎实，把「判断」留给 L3——这和分层设计一致。

**复合键是双刃剑**：解决了同名不同子系统，但在真实数据上造成 122 个多标签、3 个真不一致的匹配问题（第六部分的数据）。

---

## 8. 问题与根因（逐条举例）

### 8.1 base 文件被当成另一种格式

**现象**：3 条 `INVALID_STRUCTURE`，提取标记为 `partial`。

**文件实际长什么样**（`productdefine/common/base/mini_system.json`）：

```json
{
  "subsystems": [
    { "subsystem": "hiviewdfx",
      "components": [{ "component": "hilog_lite" }, { "component": "hievent_lite" }] }
  ]
}
```

**代码怎么读**：路径里有 `/base/` → 走 `selections(..., parts=True, base=True)`。这个分支把**整个文档**当成 `parts` 映射，要求每个顶层键都是 `subsystem:component`。第一个键是 `subsystems` → 不符合 → 报错，整个文件跳过。

**影响**：三个 base 文件共 **95 条选择**全部丢失（mini 19、small 30、standard 46）。用这些公共清单的产品会看起来「少选了 95 个部件」；后续验证会把它们当成没要求，漏检。

### 8.2 复合键把同一个部件拆成两个

**现象**：122 个组件在产品配置里有多个子系统标签。

**具体例子**（同一次运行）：

| 组件                | 定义在                            | 产品里也写成                   |
| ------------------- | --------------------------------- | ------------------------------ |
| `netmanager_base` | `communication:netmanager_base` | `netmanager:netmanager_base` |
| `updater`         | `updater:updater`               | `update:updater`             |
| `access_token`    | `security:access_token`         | `accesscontrol:access_token` |

**根因**：产品配置的 `subsystem` 只是分类标签，不是身份权威；OHOS 历史上改过子系统名（accesscontrol→security、update→updater、netmanager→communication）。

**影响**：

- 用完整键匹配：2735 条选择里 **2036 条**能匹配；
- 用短名匹配：**2153 条**能匹配；
- 差出的 **117 条（4.3%）**会因为标签不同被误判为「缺失」；
- 同一个部件被重复计数（122 个），选择总数虚高。

### 8.3 改名

**例子**（产品名 → bundle 里的真名）：

| 产品里写的                | bundle 里定义的   |
| ------------------------- | ----------------- |
| `telephony:call`        | `cellular_call` |
| `telephony:mms`         | `sms_mms`       |
| `developtools:profiler` | `hiprofiler`    |

**影响**：精确匹配必然报「缺失」。这是 3 个稳定的假阳性，必须靠别名表或构建脚本里的映射解决。

### 8.4 第三方依赖混在 components 里

**例子**：某些 `bundle.json` 的 `deps.components` 写着 `FreeBSD`、`abseil-cpp`、`cJSON`、`bzip2`、`alsa-lib`。

**根因**：代码只给 `deps.third_party` 打 `category: third_party`；写进 `deps.components` 的第三方名没有标记。

**影响**：314 个组件依赖目标里 **99 个「没有定义」**，其中大部分是第三方，不是真的缺部件。噪声会淹没真正缺失的组件。

### 8.5 重复定义（重点）

**现象**：5 个键在输出里各出现两次。逐个看，是三种不同情况：

| 键                                                                | 两处定义                                                                                                                                                                                      | 情况                            |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| `applications:wifi_iot_sample_app`                              | `applications/sample/wifi-iot/app/bundle.json:12`（依赖 `c_utils`，产出 `//…/app:app`）`applications/sample/wifi-iot/bundle.json:15`（依赖 `utils_lite`、`liteos_m` 等，无产出） | **同名不同物**            |
| `hisilicon_products:hisilicon_products`                         | `device/board/hisilicon/hispark_phoenix/linux/ohos.build:4device/board/hisilicon/hispark_taurus/linux/ohos.build:4`                                                                         | **同名不同板**            |
| `vendor:hardware`、`vendor:middleware`、`vendor:hi3861_sdk` | 子目录 `bundle.json`（带 `sub_component`）父目录 `device/soc/hisilicon/ohos.build`（老格式聚合）                                                                                        | **聚合声明 + 细粒度声明** |

**为什么会影响下游**：

1. **索引覆盖**：下游若用 `{名字: 记录}` 建索引，后写的一条会静默覆盖先写的。`wifi_iot_sample_app` 会只剩一条，另一条的依赖和产出消失。
2. **闭包漏边**：`applications/sample/wifi-iot/bundle.json` 的 5 条依赖（`utils_lite`、`liteos_m`、`peripheral`、`acts`、`samgr_lite`）在覆盖后就查不到了。
3. **目标映射错**：`vendor:hardware` 的 `//device/soc/hisilicon/common/hal/media:hardware_media_sdk` 是「一个组件的目标」，而 `device/soc/hisilicon/ohos.build` 的 `module_list` 是「整个 SoC 的目标集合」。合并后无法区分两者。
4. **无法指认**：没有冲突诊断时，用户看到两条同名记录，不知道哪条算数、该改哪个文件。
5. **按板卡无法区分**：`hisilicon_products` 在两块板上同名，产品配置只写名字，无法判断选的是哪块板的 part。

**建议**：

- 定义记录保留**全部出处**，不要只留一条；
- 同键多条时输出 `DUPLICATE_COMPONENT` 诊断，并按「同名不同物 / 同名不同板 / 聚合与细粒度」三类给不同提示；
- 下游索引改用 `(子系统, 名字, 来源路径)`，或先做冲突消解再建索引。

### 8.6 L1 不把子系统 GN 交给 S3

**例子**：`build/lite/config/subsystem/aafwk/BUILD.gn` 在我们的 L1 输出里是 `["S1","S4","S5","S7"]`，没有 `S3`。

**影响**：S3 完全看不到这个文件。全量跑 A 时 `subsystem`/`subsystem_components` 记录是 **0**；手工补上 S3 后才出现 1+1 条。依赖 GN 子系统清单的 lite 产品会「一个部件都没选」。

### 8.7 全树不可得

**例子**：目标服务器的 `<SOURCE_ROOT>` 挂载失效；34 个仓在 Gitee 404。

**影响**：154 个「未定义选择」里 **109 个只是因为视图里没有对应仓**，不能据此判定 OHOS 缺部件。全量结论只能限定在「247 仓视图」范围内。

---

## 9. 优化建议

**P0（正确性）**

1. 修 base：`productdefine/common/base/*.json` 按 `subsystems` 读。
2. 身份改成「**短名为主、子系统为属性**」：先按 name 匹配，多个同名再按 subsystem 区分并报歧义。
3. 给 `deps.components` 里的第三方名打标。
4. 重复定义出诊断，保留全部出处。

**P1**

5. 别名表（`call→cellular_call` 等）。
6. 我们 L1 把 `build/lite/config/subsystem/**/*.gn` 也路由给 S3。
7. GN 增强：解析 `import` 的 `.gni` 取变量默认值，或至少保留变量名。

**P2**

8. 用 gitcode 补 34 个 404 仓。
9. 全树按仓缓存 blob。

## 10. 难点

1. **部件身份不统一**：产品配置、bundle、GN 三处对「叫什么、属于哪个子系统」不一致。
2. **没有权威注册表**：改名只能靠别名表或构建脚本推导。
3. **GN 是程序不是数据**：变量、条件、import 让静态解析只能拿到一部分。
4. **产品清单是继承 + 条件组合**：`inherit` 链与 features 让「实际要什么」不能只看单文件。
5. **镜像不完整 + 服务器挂载失效**：全树验证目前只能做到 247 仓视图。
6. **测试环境**：Windows 无软链权限，一条安全测试无法本地复现。

---

# 第三部分：修复记录与复测（2026-11-12）

## 11. 已修的四项

| # | 修复          | 改了什么                                                                                                                           | 验证                                                                                                              |
| - | ------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 1 | base 文件解析 | `component_reader.read()`：只有「base + 有 `parts` 且无 `subsystems`」才按 parts 读；否则按普通 `subsystems` 读            | base 的 3 个文件不再报 `INVALID_STRUCTURE`，恢复 95 条选择                                                      |
| 2 | 身份改为短名  | 定义与选择的 `key` 都改成短名；`subsystem` 作为独立字段输出（schema 新增 `subsystem`）                                       | 2735→2830 条选择中，按名字可解析的从 2036 升到 2211                                                              |
| 3 | 重复定义      | `diagnostics` 新增 `conflicts`：同名同子系统 → `DUPLICATE_COMPONENT`；同名跨子系统 → `AMBIGUOUS_COMPONENT`；保留全部出处 | 本树 5 条 `DUPLICATE_COMPONENT`，0 条 `AMBIGUOUS_COMPONENT`                                                   |
| 4 | L1 路由       | `rules.py` 的 `EXTRA_CLASSES_BY_PATH` 增加 `(build/)?lite/config/subsystem/*.gn → +S3`                                      | 该类文件的 classes 从 `S1,S4,S5,S7` 变为 `S1,S3,S4,S5,S7`；S3 输出新增 `subsystem`/`subsystem_components` |

测试：`tool` 48 项全过；`tool_s3` 76 项，1 项因 Windows 无软链权限失败（环境限制）。

## 12. 修复前后对比

| 指标                                     |               修复前 |                        修复后 |
| ---------------------------------------- | -------------------: | ----------------------------: |
| 记录总数                                 |                 8538 |                          8635 |
| `selects`                              |                 2735 |                          2830 |
| `subsystem` / `subsystem_components` |                0 / 0 |                         1 / 1 |
| base 解析诊断                            |                    3 |                             0 |
| 冲突诊断                                 |                   无 | 5（全部 DUPLICATE_COMPONENT） |
| 选择记录可解析（按名字）                 | 2036 / 2735（74.4%） |          2211 / 2830（78.1%） |
| 唯一选择名可解析                         |            297 / 451 |                     297 / 455 |
| 依赖目标可解析                           |            215 / 331 |                     215 / 331 |

## 13. 第三方名怎么区分（方案，未实施）

**先说结论：不能靠名字猜，必须靠外部索引。** 名字本身看不出是不是第三方（`permission`、`cjson`、`abseil-cpp` 看起来没区别）。

分五层，逐层收紧：

| 层 | 依据                            | 做法                                                                                                                                                      | 覆盖                                          |
| -- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| 1  | 显式字段                        | `deps.third_party` 直接标 `category=third_party`                                                                                                      | 精确，但只覆盖写了这个字段的                  |
| 2  | **manifest 生成的名字表** | 从 manifest 里所有 `path` 以 `third_party/` 开头的项目生成名字集合：`third_party/<name>` → `<name>`；仓库名 `third_party_<name>` → `<name>` | 实测覆盖 116 个未解析依赖中的 **89 个** |
| 3  | 定义优先                        | 如果这个名字在树里有 component 定义，就算组件，不标第三方（防误杀）                                                                                       | 解决同名冲突                                  |
| 4  | 规范化/别名                     | 大小写（`cjson`↔`cJSON`）、连字符/下划线、已知映射（`libjpeg`↔`libjpeg-turbo`）                                                                 | 少量                                          |
| 5  | 残余                            | 不在以上任何一类 → 标 `UNRESOLVED_COMPONENT`，**不冒充第三方**                                                                                   | 让真正的缺失暴露出来                          |

生成物：

- `rules/third_party_names.txt`：**由 manifest 自动生成**，记录来源版本（如 `OpenHarmony-6.1-Release`），不手写；
- 少量「外部系统名」（如 `FreeBSD`、`NuttX`、`FatFs`）实测已被 manifest 的 `third_party_*` 覆盖，不需要单独维护；
- 测试类仓（`xts_acts`、`testfwk_arkxtest`）单独标 `category=test`，不混进第三方。

**不建议**：按大小写、连字符、是否含 `lib` 前缀来猜——这类启发式在真实数据上会把 `libsec_static`（可能是组件）和 `libjpeg`（第三方）判反。

剩余 27 个未覆盖的名字（如 `permission`、`netmanager`、`web_webview`）需要逐个核：它们可能是「组件仍在、只是本视图没拉到仓」，也可能是改名。这一步不能自动化。

---

# 第四部分：6.1 全量复测（含 third_party，2026-11-13）

## 14. 怎么拉的

- 源：gitcode.com（Gitee 缺 34 个仓）
- 版本：`OpenHarmony-6.1-Release`（与产品清单同一版本）
- 范围：manifest 的 **492 个项目**，含 `third_party` 178 个
- 方法：每仓 `--filter=blob:none --no-checkout` 拉完整目录树，`git ls-tree -r` 拿全部文件，再只 materialize S3 相关文件

## 15. 对账结果

| 项                         |                 数量 |
| -------------------------- | -------------------: |
| 项目（含 chipset 8 个）    |                  500 |
| 有 S3 文件                 |                  467 |
| 无 S3 文件                 |                   33 |
| 完整清单中匹配到的声明文件 |        **642** |
| 成功落盘                   |        **642** |
| blob 读取失败              |          **0** |
| 克隆失败                   |          **0** |
| 子模块（gitlink）          | 18（分布在 10 个仓） |

**matched = selected = 642**：在「仓清单 + 文件清单」这两层上没有漏。

## 16. 与上一轮（273 仓、master 分支）对比

| 指标             |             上一轮 |                     6.1 全量 |
| ---------------- | -----------------: | ---------------------------: |
| 视图文件         |                417 |                          642 |
| 记录总数         |               8635 |                         9918 |
| `component`    |                364 |                          587 |
| `selects`      |               2830 |                         2927 |
| `requires`     |               5055 |                         5794 |
| 唯一选择名可解析 | 297 / 455（65.3%） | **447 / 499（89.6%）** |
| 唯一依赖名可解析 | 215 / 331（65.0%） | **343 / 365（94.0%）** |
| 未解析选择名     |                158 |                           52 |
| 未解析依赖名     |                116 |                           22 |
| 重复定义冲突     |                  5 |                            5 |

## 17. 新发现

### 17.1 一个真实畸形文件

`drivers/interface/display/bundle.json` 里 **`features` 出现了两次**（第 22 行和第 28 行）。严格 JSON 解析拒绝整个文件，于是：

- 部件定义 `drivers_interface_display` 丢失；
- 它在「产品选择」和「他人依赖」两侧都变成未解析。

这不是版本问题，是仓库里真实存在的写错。普通 `json.loads` 会静默取最后一个，问题被藏起来。

### 17.2 子模块没被覆盖

18 个 gitlink，其中 `third_party_rust_rust` 有 11 个，且它自己没有 S3 文件——Rust 的各个 crate 是子模块，`bundle.json` 在子模块里。当前清单覆盖不到这部分。

### 17.3 剩余 52 个未解析选择名

| 类型                                | 数量 | 例子                                                                                                                                                               |
| ----------------------------------- | ---: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 仓库名 ≠ 部件名（已核对 6.1 分支） |    9 | `call`→`cellular_call`、`mms`→`sms_mms`、`profiler`→`hiprofiler`、`rust`、`launcher`、`systemui`、`settings_data`、`update_app`、`file` |
| 第三方别名                          |    1 | `thirdparty_bounds_checking_function`                                                                                                                            |
| 畸形 JSON                           |    1 | `drivers_interface_display`                                                                                                                                      |
| 无同名仓（疑似改名/合并）           |   41 | `appexecfwk_standard`、`permission_standard`、`i18n_standard`、`storage_standard`、`hdf`                                                                 |

### 17.4 剩余 22 个未解析依赖名

| 类型              | 数量 | 例子                                                                  |
| ----------------- | ---: | --------------------------------------------------------------------- |
| 第三方别名        |    2 | `cjson`（`cJSON`）、`optimized-routines`                        |
| 仓库名不一致      |    2 | `rust`、`web_webview`                                             |
| 畸形 JSON         |    1 | `drivers_interface_display`                                         |
| 无同名仓 / 外部名 |   17 | `opencv`、`llvm-project`、`mingw-w64`、`libffi`、`jemalloc` |

## 18. 结论修正

1. 之前「154 个未解析里 109 个是视图缺口」成立。补齐 third_party、对齐 6.1 后，**选择解析率 65% → 90%，依赖解析率 65% → 94%**。
2. `call`/`mms`/`profiler` 三个改名**是 6.1 的真实不一致**，不是版本漂移——已核对 6.1 分支的 bundle，确实叫 `cellular_call`/`sms_mms`/`hiprofiler`。
3. 新发现两类真实问题：
   - `drivers_interface_display` 的 `bundle.json` 重复 `features` 键；
   - 18 个子模块未覆盖（Rust crates）。
4. 5 条重复定义在 6.1 里仍然存在（同一批）。
