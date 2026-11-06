# 固定上游样例

此目录用于 S3 GN 解析回归，文件正文来自 OpenHarmony，保留原有 Apache-2.0 许可头。下列 SHA 标识上游文件，本仓库检出时使用 LF 行尾。

- [aafwk/BUILD.gn](https://github.com/openharmony/build/blob/OpenHarmony-6.1-Release/lite/config/subsystem/aafwk/BUILD.gn)，Git blob SHA：`a852f1d18f598267aed93e3470165ab910a81cb0`。
- [lite_subsystem.gni](https://github.com/openharmony/build/blob/OpenHarmony-6.1-Release/lite/config/subsystem/lite_subsystem.gni)，Git blob SHA：`c02f276fa947f58c954c0cef051f1eb0eb06231c`。模板仅用来核对 `subsystem_components` 是 GN 目标列表，不参与提取器执行。

抓取于 2026-10-26。
