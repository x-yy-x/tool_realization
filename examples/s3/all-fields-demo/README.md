# S3 全字段演示样例

这些输入全部是人工构造的讲解样例，使用 `demo` 名称，不是 OpenHarmony 官方产品或真实构建结果。输出由当前 S3 程序读取这些输入后生成，不手工补写结果字段。

在项目根目录运行：

```sh
python3 -m src.l2_extract \
  --root examples/s3/all-fields-demo/source \
  --filelists examples/s3/all-fields-demo/filelists.json \
  --output out/s3-all-fields-demo
```

结果为 `out/s3-all-fields-demo/requirements.json`，诊断为同目录的 `diagnostics.json`。

本样例刻意包含未求值的 GN 条件、变量表达式和内核/板卡条件依赖，因此预期退出码为 1、`complete` 为 false。它展示如何保留不确定信息，不代表这些目标必定启用，也不代表依赖或编译已验证。

| 在结果中找 | 可以展示的字段 |
| --- | --- |
| `component` | `features`、`adapted_system_type`、`adapted_kernel` |
| `requires` | `from`、第三方依赖的 `category` |
| `conditional_requires` | `category` 为 `kernel_special` 或 `board_special`，以及 `value`、`symbolic` |
| `build_targets` | `field` 为 `sub_component` 或 `module_list`，以及 `targets` |
| `selects` | `from`、带特性赋值或排除标记的 `options` |
| `inherits`、`system_component` | 配置引用路径和 `from` |
| `subsystem` | `template` |
| `subsystem_components` | `op` 的 `=`、`+=`、`-=`，以及 `targets`、`when`、`expression`、`symbolic` |

每条记录仍按本身含义输出相关字段；不会把所有字段塞进同一条记录。配置引用文件也在清单中独立列出，其结果不是自动展开继承得到的。
