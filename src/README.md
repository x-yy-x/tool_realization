# 03 · 实现设计（四层共用地基）

> **本文是索引**：四层分别怎么写，拆在四个源码目录各自的 `README.md` 里。**本文只留所有层共用的地基。**
> **对应**：原设计文档 §15–§20。
>
> **四份分册不在 `docs/` 下**——它们就在各自要写的代码目录里：
> [`l1_scan/README.md`](l1_scan/README.md) · [`l2_extract/README.md`](l2_extract/README.md) ·
> [`l3_verify/README.md`](l3_verify/README.md) · [`l4_triage_fix/README.md`](l4_triage_fix/README.md)。
> **文档和代码放在一起，改一个就得改另一个。**

**本文出现的代码是设计稿，不是实现。** 仓库里目前没有任何可运行的代码；
实现状态以根 `README.md` 的 STATUS 表为准。

[← 02 流水线与关](../docs/02-流水线与关.md) · [04 自测与验收 →](../docs/04-自测与验收.md)

---

> [`02`](../docs/02-流水线与关.md) 回答「**要做哪些检查、按什么顺序**」；
> 本文回答「**那些提取器和验证器具体怎么写**」。

## 14.5 四层分别去哪读

[`02` §29](../docs/02-流水线与关.md) 立了四层。**每一层一份设计文档、一个源码目录、一个负责人：**

```
src/
  README.md            ← 本文（索引：四层共用的地基）
  l1_scan/README.md       L1 扫描      → 源码树 → filelist.json
  l2_extract/README.md    L2 提取      → filelist → requirements.json
  l3_verify/README.md     L3 验证      → requirements → findings.json
  l4_triage_fix/README.md L4 判读与修改 → findings → 报告 + 处置
  common/                 多层共用
  precheck/               第 -2 / -1 层（不属四层）
```

**一个目录 = 一份设计文档 = 一层。** 文档跟着代码走，不堆在 `docs/`。

| 层                      | 设计文档（就在本层的 `README.md`）                        | 代码目录               | 输入 → 输出                          | 有副作用 |
| ----------------------- | -------------------------------------------------------- | ---------------------- | ------------------------------------- | -------- |
| **L1 扫描**       | [`L1`](l1_scan/README.md)                                 | `src/l1_scan/`       | 源码树 →`filelist.json`            | ❌       |
| **L2 提取**       | [`L2`](l2_extract/README.md)（= 原 §19）                   | `src/l2_extract/`    | `filelist` → `requirements.json` | ❌       |
| **L3 验证**       | [`L3`](l3_verify/README.md)（= 原 §18 + §20）            | `src/l3_verify/`     | `requirements` → `findings.json` | ❌       |
| **L4 判读与修改** | [`L4`](l4_triage_fix/README.md) + [`06`](../docs/06-修复段.md) | `src/l4_triage_fix/` | `findings` → 报告 + 处置           | ✅       |

> **不属四层的两块**：`common/`（`rules.py` / `probe.py`，被多层共用）、`precheck/`（第 -2/-1 层，是「没有 L2 的四层」）。见 §16。

**四层之间靠 [`schema/`](../schema/README.md) 的三份 JSON 交接**——那是接口冻结的凭据。
**下面 §15 / §16 / §17 是三块共用地基**，四层都依赖。

---

## 15. 三个先决决定

这三个决定了后面所有细节，先立住。

### 15.1 扫描器必须跑在**被检查的环境里**

`command -v mcopy` 在 Windows 上有意义吗？没有——构建发生在 WSL 里。
**宿主工具的「存在性」是环境属性，不是源码属性。**

| 阶段                   | 在哪跑                     | 为什么                                   |
| ---------------------- | -------------------------- | ---------------------------------------- |
| **提取 Extract** | 哪都行                     | 只读源码树做文本分析，**环境无关** |
| **验证 Verify**  | **必须在目标环境内** | `command -v` 的结果取决于当前系统      |

这个切分不是架构洁癖：**它让全部提取器可以在 Windows 上开发并自测**（怎么自测见 [`04` §21](../docs/04-自测与验收.md)）。

### 15.2 Python 3 标准库，零依赖

理由不是偏好，是被 [`01` §10](../docs/01-问题与论证.md) 和 61 条原始清单教训出来的：

| 教训        | 内容                             |
| ----------- | -------------------------------- |
| `ENV-003` | `python` 不一定在              |
| `ENV-004` | pip 会被 externally-managed 挡住 |
| `ENV-005` | Python 3.13 破坏旧代码           |
| `ENV-013` | 连`timeout` 都可能没有         |

**一个用来诊断环境缺什么的工具，自己不能对环境有要求。**
JSON、XML、AST 全是标准库自带，不需要任何第三方包。

工具还要**自查运行时**，作为第 -1 关（`hb` 本身需要 Python 3.8+，所以 WSL 侧通常没问题，但不该假设）。

### 15.3 四段用文件解耦

```
阶段 A 提取  →  requirements.json  →  阶段 B 验证  →  findings.json
                                                          │
                            阶段 D 修复  ←  ─ ─ ─ ─ ─ ─ ─ ─┘
                                 │   ↑ 重验
                                 ↓
                            阶段 C 报告  →  report.md
```

支持 `--only-extract` / `--only-verify` / `--no-fix` 分开跑。三个理由：

1. **提取在 Windows 跑、验证在服务器跑** → 必须有中间文件传递
2. 提取结果可存档 → 源码树变了能对比「需求清单的 diff」
3. 扫一次很慢 → 验证阶段要能反复跑而不重扫

**修复段单独成段还有个新理由（第 28 节）：它有破坏性。**

B 档会改源码树。所以它必须能单独关（`--no-fix`）、单独跑（`--fix-only`）、
默认不真改（`--dry-run`）、改完能回滚。**这四条要求合起来，就要求它不能散在验证里。**

---

## 16. 目录结构

**`src/` 按四层分。** 一个目录 = 一份设计文档 = 一层：

```
tool/
  scan.py                      # 唯一入口，--stage {env,fetch,tree,fix} 四段
  config.yaml                  # 目标配置：源码根 / 产品 / 板卡 / 端点 / 开关

  src/
    l1_scan/                   # L1 扫描  → 本层 README
      treemodel.py             # 源码树模型：全树遍历 / 排除 / 分类 / manifest 解析 / bundle 索引
      README.md                # 本层设计文档（职责 + 输入输出 + 算法 + 边界）

    l2_extract/                # L2 提取  → 本层 README
      shelltoken.py            # shell 分词器（S1 的地基，最需要测试）
      s1_tool.py               # 命令 → 宿主工具需求
      s2_repo.py               # manifest → 源码仓需求
      s3_part.py               # config.json + bundle.json → 部件需求（含闭包）
      s4_toolchain.py          # 编译器 + target_cpu → 工具链需求
      s5_lib.py                # libs / ldflags / .ld → 库与符号需求
      s6_include.py            # 使用 × 声明 → 头文件缺口
      s7_config.py             # defconfig / ARCH → 配置与架构需求
      v_coverage.py            # 第 2 关：提取自检（文件类型普查 → 差集，见 19.8）
      README.md

    l3_verify/                 # L3 验证  → 本层 README
      v_tree.py                # 第 0 关：树完整性
      v_exists.py              # command -v / test -f / test -d
      v_identity.py            # -dumpmachine / readlink -f / -E -v
      v_consistency.py         # 纯集合求差
      v_reach.py               # DNS → 连接 → 权限，三档带超时
      README.md

    l4_triage_fix/             # L4 判读与修改  → 本层 README + 06
      report.py                # 报告渲染（md + json）
      triage.py                # 每关一次：组装上下文 → 调 Agent → 收「处置决定」
      executor.py              # 按决定执行：跑 recipe / apply patch（A 档脚本、B 档人审后）
      loop.py                  # 小回环 / 大回环 + 四条收敛判据
      rollback.py              # B 档改动快照与回滚
      README.md

    common/                    # 不属任何单层（被多层共用）
      rules.py                 # 规则加载与匹配（L2/L3/L4 都读）
      probe.py                 # 探测原语：DNS / 连接 / 权限，统一带 --max-time
      README.md

    precheck/                  # 第 -2 / -1 层（不属四层）
      p_env.py                 # 第 -2 层：WSL 分发源 / CDN / 代理 / apt 源 / Windows 侧 python 身份
      p_fetch.py               # 第 -1 层：repo bootstrap + manifest 端点全量探测 + 并发风险预测
      p_base.py                # 第 -1 层：基线工具存在性 + 环境身份（OS / Python / Node vs 白名单）
      README.md

  schema/                      # 数据契约（四层的交接面，见 schema/README.md）
    filelist.schema.json
    requirements.schema.json
    findings.schema.json
  rules/
    tool_denylist.txt          # shell 关键字与内建命令黑名单
    knowledge.yaml             # 语义规则库（61 条 → 规则化）
    whitelist.yaml             # 版本白名单 + 基线工具清单（无声明来源的那两类）
    arch_map.yaml              # target_cpu ↔ Kbuild ARCH 映射
  tests/
    fixture/                   # 假源码树，离线自测用
    test_s1_tool.py ...
  coverage/
    expected.yaml              # 61 编号 → 期望的声明形态（覆盖率实验的靶子）
    coverage_report.md         # 实验产物
  docs/                        # 00–06（问题 / 流水线 / 风险），见 README 的仓库导航
  problems/                    # 61 条原始清单（基准，不改）
  patches/                     # Agent 产出的 patch（默认 dry-run，人审后才 apply）
  out/                         # 运行产物，不纳入版本管理
```

**三处不按层放的，理由：**

| 目录                                            | 为什么不属任何单层                                                                       |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `common/`                                     | `rules.py` 被 L2/L3/L4 读；`probe.py` 被 L3 和 `precheck/` 共用                    |
| `precheck/`                                   | 第 -2/-1 层没有源码，是「**没有 L2 的四层**」（L1 探测 → L3 对白名单 → L4 判读） |
| `schema/` `rules/` `tests/` `coverage/` | 是**数据与契约**，不是层                                                           |

---

## 17. 数据格式

流水线链上一共有 **六份** 产物。**前三份互相不依赖**，可以各自单独跑、单独存档：

| 产物                    | 谁产出             | 谁消费          | 单独跑             |
| ----------------------- | ------------------ | --------------- | ------------------ |
| `env-precheck.json`   | 第 -2 层           | 第 -2 层报告    | `--stage env`    |
| `fetch-precheck.json` | 第 -1 层           | 第 -1 层报告    | `--stage fetch`  |
| `filelist.json`       | L1 扫描（第 0 关） | L2 全部提取任务 | `--stage tree`   |
| `requirements.json`   | L2 提取（第 1 关） | L3 全部验证器   | `--only-extract` |
| `findings.json`       | L3 验证 + 修复段   | L4 判读、修复段 | `--only-verify`  |
| `report.md`           | L4 判读（第 8 关） | 人              | `--no-fix`       |

分成六份而不是一份，三个理由（§15.3）：**提取在 Windows 跑、验证在服务器跑**，必须有文件传递；提取结果可存档，源码树变了能对比「需求清单的 diff」；扫一次很慢，验证阶段要能反复跑而不重扫。

顺带的好处：**「上次 sync 时哪个端点慢了」这类历史对比成为可能**。

下面两份是核心结构。

### `requirements.json`

**它是整个工具的核心**——报告里「缺什么 + 出自哪一行」全靠它。

```json
{
  "meta": {
    "source_root": "/home/u/openharmony",
    "product": "qemu-arm-linux-min",
    "board": "arm_virt",
    "scanned_at": "2026-09-22T10:00:00+08:00",
    "tool_version": "0.1"
  },
  "requirements": [
    {
      "id": "REQ-00001",
      "kind": "tool",
      "name": "mcopy",
      "declared_at": "device/qemu/arm_virt/mkimage.sh:42",
      "declared_as": "mcopy -i $IMG rootfs/* ::",
      "extractor": "S1-tool",
      "confidence": "high",
      "check": "CHK-EXISTS-CMD",
      "severity": "block",
      "verify_cmd": "command -v mcopy",
      "maps_to": ["IMG-002"]
    }
  ],
  "extractor_stats": {"S1": 412, "S2": 187, "S3": 96, "S4": 14, "S5": 33, "S6": 7, "S7": 6},
  "self_check": {
    "uncovered_file_types": [".rs", ".te", ".dts"],
    "total_files_seen": 184203,
    "total_files_parsed": 12930
  }
}
```

### 各 kind 的记录样例

> **下面用的是「示意树」**（`device/qemu/arm_virt/...`），和 [`../tests/fixture/`](../tests/fixture) 那棵假树**不是同一棵**——示意树用来展示真实 OpenHarmony 里的典型形态，假树故意做小。
> 两棵树的路径、行号都不通用。**对照断言时看 fixture，理解形态时看这里。**

```json
{"kind":"repo","name":"third_party/asmjit","declared_at":".repo/manifest.xml:214",
 "declared_as":"<project name=\"third_party/asmjit\" path=\"third_party/asmjit\" revision=\"9f3c...\"/>",
 "check":"CHK-REPO-REVISION","verify_cmd":"git -C third_party/asmjit rev-parse HEAD"}

{"kind":"component","name":"bar","declared_at":"third_party/foo/bundle.json:8",
 "declared_as":"\"deps\": {\"components\": [\"bar\"]}",
 "check":"CHK-COMPONENT-RESOLVABLE","verify_cmd":"find -name bundle.json"}

{"kind":"lib","name":"libfoo.a","declared_at":"src/BUILD.gn:31",
 "declared_as":"libs = [ \"foo\" ]",
 "check":"CHK-LIB-EXISTS","verify_cmd":"$CC -print-file-name=libfoo.a"}

{"kind":"gap","name":"uint32_t-cstdint","declared_at":"src/a.cpp:1",
 "declared_as":"uint32_t g_counter = 0;  (无 #include <cstdint>)",
 "check":"CHK-GAP-INCLUDE","verify_cmd":"static"}

{"kind":"arch","name":"riscv32→riscv","declared_at":"vendor/demo/board/config.json:12",
 "declared_as":"\"target_cpu\": \"riscv32\"",
 "check":"CHK-ARCH-MAP","verify_cmd":"static"}
```

### 三个字段值得单独说

| 字段            | 为什么必须有                                                                                   |
| --------------- | ---------------------------------------------------------------------------------------------- |
| `declared_at` | 「第 42 行的 mcopy 没带 -o」——**没有它，报告就退化成一句「缺 mcopy」**，用户还得自己找 |
| `confidence`  | S1 是启发式的，必然有假阳性。分级让报告可用（第 19.7 节）                                      |
| `maps_to`     | 反向指回 61 编号。**覆盖率实验靠这个字段自动算**                                         |

### `findings.json`

requirements 加上验证结果：

```json
{
  "id": "F-0007",
  "req": "REQ-0042",
  "gate": "g3",
  "severity": "block",
  "kind": "tool",
  "name": "mcopy",
  "declared_at": "device/qemu/arm_virt/mkimage.sh:42",
  "declared_as": "mcopy -i $IMG rootfs/* ::",
  "verify_cmd": "command -v mcopy",
  "result": "missing",
  "evidence": "command -v mcopy → exit 1, stdout 空, stderr 空",
  "maps_to": ["IMG-002"],

  "fix_class": "A",
  "allow": ["apply_recipe", "escalate"],
  "decision": "apply_recipe",
  "recipe": "K1-IMG-002",
  "fix": "apt-get install -y mtools",
  "fix_status": "fixed",
  "attempts": 1
}
```

**`evidence` 存原始输出，不是布尔值。** 因为 `ENV-013` 教的：`timeout` 退出码 124 和 1 是两回事，必须留证据给人判读。

### 修复相关的字段（第 28 节）

| 字段                 | 作用                                                                                                                                |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `gate`             | 哪一关产出的。**决定分诊时配哪套上下文、允许哪些动作**                                                                        |
| `fix_class`        | A / B / C / D。**由规则定，不由 Agent 定**（28.1、28.2）                                                                      |
| `allow`            | 本条允许 Agent 选的动作集。C / D 档不含`propose_patch`（28.4）                                                                    |
| `decision`         | Agent 分诊的结果：`apply_recipe` / `propose_patch` / `escalate` / `abandon`                                                 |
| `recipe`           | 命中`knowledge.yaml` 里的哪条 `fix_recipe`（28.6）                                                                              |
| `fix` / `advice` | **可执行动作** / **给人看的重建方案**。C / D 档只有 `advice`                                                          |
| `fix_status`       | **六个值**（四动作 × 回滚，取并集）：`pending` / `fixed` / `patched` / `escalated` / `rolled_back` / `abandoned` |
| `attempts`         | 修了几次。**上限 3 次**（28.5 收敛判据 ③）                                                                                   |

> `source_requirement` 不必单独存——`declared_at` + `declared_as` 已经是从 `requirements.json` 里捞出来的那份。

`severity` 三档：

| 档        | 含义                 | 例子                                |
| --------- | -------------------- | ----------------------------------- |
| `block` | 必然导致构建失败     | 工具缺失、仓缺失、revision 不符     |
| `warn`  | 大概率有问题，需人判 | mcopy 缺`-o`、uint32_t 缺 cstdint |
| `info`  | 仅供参考             | low confidence 的 S1 命中           |

---
