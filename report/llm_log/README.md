# llm_log — 大模型协作记录（摘录）

开发全程使用大模型编程助手（Claude Code，Claude Opus 系列），按任务拆成多个并行会话：采集链调试、数据集交接、训练、漂移分析、对比实验、报告。
这里摘录三段有代表性的交互，按"提示词 → 模型回答 → 纠错 → 结果"整理，用词有删节，不是逐字原文。对应的代码和日志都在仓库里，可以核对。

提炼出的技能见 `skill/`，其中 `skill/prompts/README.md` 是从这些交互里固定下来的提示词模板。

| 文件 | 内容 | 对应技能 |
|---|---|---|
| `01_dma_sigbus.md` | 板上 DMA 读结果时进程 SIGBUS，定位到 `/dev/mem` 非对齐访问 | `skill/README.md` 第 3 项、`prompts` P3 |
| `02_postsynth_zero_weights.md` | RTL 仿真通过、综合后权重全为 0，定位到 `initial` 块被综合忽略 | 第 1、2 项 |
| `03_eval_leakage.md` | 报警评估指标虚高，发现训练集混入对照段，改为嵌套留出 | `prompts` P4 |
