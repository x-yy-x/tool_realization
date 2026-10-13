# `schema/` — 四层之间的交接面

**这三份 JSON Schema 就是四层的接口。** 上游产出的东西下游读不读得懂，全看这里。

| 契约 | 谁产出 | 谁消费 | 设计文档 |
| --- | --- | --- | --- |
| [`filelist.schema.json`](filelist.schema.json) | L1 扫描 | L2 全部提取任务 | [`src/l1_scan/`](../src/l1_scan/README.md) |
| [`requirements.schema.json`](requirements.schema.json) | L2 提取 | L3 全部验证器 | [`src/l2_extract/`](../src/l2_extract/README.md) |
| [`findings.schema.json`](findings.schema.json) | L3 验证 → L4 回写处置 | L4 判读 | [`src/l3_verify/`](../src/l3_verify/README.md) · [`src/l4_triage_fix/`](../src/l4_triage_fix/README.md) |

三份都是 **draft 2020-12**，都带 `additionalProperties: false`——**写错字段名不报警，直接读不出来**。

---

## 已冻结的六条裁决

这些是两版旧稿冲突过、必须选一个的地方。**冻结的意思是：改它要先改 schema，不能只在文档里写。**

| # | 争的是什么 | 冻结成 | 为什么是这个 |
| - | --- | --- | --- |
| 1 | `fix_status` 取哪几个值 | **六个的并集**：`pending` / `fixed` / `patched` / `escalated` / `rolled_back` / `abandoned` | 旧稿各列了一半，**照着任意一份实现，另一份的产出都读不出来**。四动作（`apply_recipe`/`propose_patch`/`escalate`/`abandon`）加回滚，六个状态缺一不可 |
| 2 | 需求 id 用什么前缀 | **`REQ-`**（`^REQ-[0-9]{5}$`） | 旧的 `R-` 前缀和 [`docs/05`](../docs/05-实施顺序与风险.md) 的实现风险编号 `R1`–`R14` 撞名——同一个仓库里指两件事 |
| 3 | 验证规则 id 用什么前缀 | **`CHK-`**（`^CHK-[A-Z0-9-]+$`），同一检查只有一个 id（`CHK-LIB-EXISTS`） | 理由同第 2 条；且和字段名 `check` 对得上 |
| 4 | `kind` 有几类 | **12 类**，多对一映射到六个验证器 | 旧稿写「九种」却列了十二个。12 是提取侧的粒度，6 是验证侧的粒度，**两个数不是一回事** |
| 5 | `severity` 谁定 | **提取得默认档（跟着 `check` 走），L4 按 `result` 上调下调**，理由写进报告 | 提取时还不知道验证会返回什么。同一个 `kind=tool`，报 `missing` 是 `block`，报「存在但不可执行」可能只该是 `warn` |
| 6 | 覆盖率靶子 `coverage/expected.yaml` 的键名 | **一律 `name`**，含仓条目 | 提取器产出的就是 `name`（`requirements.schema.json` 里 `name` 必填，**没有 `path` 这个字段**）。靶子用 `path`，覆盖率脚本对着 `v['name']` 取键会直接 `KeyError` |

---

## 三条规矩

1. **契约先于代码。** 四个层可以各写各的，但字段名、枚举值、id 格式以本目录为准。对不上就是没接上。
2. **改字段 = 改 schema + 改所有引用方**：`src/` 里的读写代码、`coverage/expected.yaml`、文档里的样例。**只改文档不算改。**
3. **样例必须能过 schema。** 文档里贴的 JSON 样例如果按本目录校验不通过，那是样例错，不是 schema 错。

```sh
# 校验一份产物（需要 jsonschema，仅开发期用；工具本体零依赖）
py -c "import json,jsonschema,sys; jsonschema.validate(json.load(open(sys.argv[1],encoding='utf-8')), json.load(open(sys.argv[2],encoding='utf-8')))" out/findings.json schema/findings.schema.json
```
