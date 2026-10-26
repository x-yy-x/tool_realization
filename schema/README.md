# `schema/` — 层间交接契约

本目录放层间交接的 JSON Schema。**2026-10-26 只冻结 L1 → L2 的 [`filelists.schema.json`](filelists.schema.json)。** L2 → L3、L3 → L4 的契约由组员自行决定；现有两份 schema 仅作参考，不是本次冻结项。

| 契约 | 状态 | 谁产出 | 谁消费 | 设计文档 |
| --- | --- | --- | --- | --- |
| [`filelists.schema.json`](filelists.schema.json) | ✅ **已冻结** | L1 扫描 | L2 全部提取任务 | [`src/l1_scan/`](../src/l1_scan/README.md) |
| [`requirements.schema.json`](requirements.schema.json) | 🟡 待组员决定 | L2 提取 | L3 全部验证器 | [`src/l2_extract/`](../src/l2_extract/README.md) |
| [`findings.schema.json`](findings.schema.json) | 🟡 待组员决定 | L3 验证 → L4 回写处置 | L4 判读 | [`src/l3_verify/`](../src/l3_verify/README.md) · [`src/l4_triage_fix/`](../src/l4_triage_fix/README.md) |

旧名 `filelist.schema.json` 已删除，避免两份契约并存；新代码只引用 `filelists.schema.json`。

---

## `filelists.json` 冻结要点

```json
[
  { "path": "base/hiviewdfx/hilog/bundle.json", "classes": ["S3", "S6"] },
  { "path": "base/hiviewdfx/hilog/BUILD.gn", "classes": ["S1", "S4", "S5", "S7"] }
]
```

- 顶层是数组；一个文件一条，`path` 唯一。
- 只有两个字段：`path` 和 `classes`。**没有** `meta`、`files`、`stats`、`kind`、`size`、`carrier`、`strength`、`sig`。
- `path` 是相对源码根、含文件名的 POSIX 路径；不绝对、不反斜杠、不带 `.`/`..` 段。
- `classes` 非空、去重、按 `S1,S3,S4,S5,S6,S7` 升序；S2 已移除。
- **没有类别的文件不出现**；`filelists.json` 是路由表，不是全树清单。
- 文件出现在里面只表示“该提取器应该打开它”，不表示对象已确认；提取器输出可以为空。

---

## 旧稿裁决参考（非当前冻结）

这些是两版旧稿冲突过的地方，留作组员讨论参考。**除 `filelists.json` 外，当前不冻结下表中的任何一条。**

| # | 争的是什么 | 旧稿选择 | 为什么 |
| - | --- | --- | --- |
| 1 | `fix_status` 取哪几个值 | **六个的并集**：`pending` / `fixed` / `patched` / `escalated` / `rolled_back` / `abandoned` | 旧稿各列了一半，**照着任意一份实现，另一份的产出都读不出来**。四动作（`apply_recipe`/`propose_patch`/`escalate`/`abandon`）加回滚，六个状态缺一不可 |
| 2 | 需求 id 用什么前缀 | **`REQ-`**（`^REQ-[0-9]{5}$`） | 旧的 `R-` 前缀和 [`docs/05`](../docs/05-实施顺序与风险.md) 的实现风险编号 `R1`–`R14` 撞名——同一个仓库里指两件事 |
| 3 | 验证规则 id 用什么前缀 | **`CHK-`**（`^CHK-[A-Z0-9-]+$`），同一检查只有一个 id（`CHK-LIB-EXISTS`） | 理由同第 2 条；且和字段名 `check` 对得上 |
| 4 | `kind` 有几类 | **12 类**，多对一映射到五个验证器 | 旧稿写「九种」却列了十二个。12 是提取侧的粒度，5 是验证侧的粒度，**两个数不是一回事**。注意：这是旧 `filelist.json` 的 `kind`，不是当前 `filelists.json` 的 `classes` |
| 5 | `severity` 谁定 | **提取得默认档（跟着 `check` 走），L4 按 `result` 上调下调**，理由写进报告 | 提取时还不知道验证会返回什么。同一个 `kind=tool`，报 `missing` 是 `block`，报「存在但不可执行」可能只该是 `warn` |
| 6 | 覆盖率靶子 `coverage/expected.yaml` 的键名 | **一律 `name`**，含仓条目 | 提取器产出的就是 `name`（`requirements.schema.json` 里 `name` 必填，**没有 `path` 这个字段**）。靶子用 `path`，覆盖率脚本对着 `v['name']` 取键会直接 `KeyError` |

---

## 三条规矩

1. **对已冻结的 `filelists.json`，契约先于代码。** 四个层可以各写各的，但字段名、枚举值以本目录为准；对不上就是没接上。
2. **改 `filelists` 字段 = 改 schema + 改所有引用方**：`src/` 里的读写代码、`tests/`、文档里的样例。**只改文档不算改。**
3. **`filelists` 样例必须能过 schema。** 文档里贴的 JSON 样例如果按本目录校验不通过，那是样例错，不是 schema 错。

```sh
# 校验一份产物（需要 jsonschema，仅开发期用；工具本体零依赖）
py -c "import json,jsonschema,sys; jsonschema.validate(json.load(open(sys.argv[1],encoding='utf-8')), json.load(open(sys.argv[2],encoding='utf-8')))" out/filelists.json schema/filelists.schema.json
```
