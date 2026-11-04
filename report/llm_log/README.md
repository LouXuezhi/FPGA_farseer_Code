# llm_log — 大模型协作记录

中文 | [English](README_en.md)

开发全程使用大模型编程助手（Claude Code），按任务拆成多个并行会话：采集链调试、比特流构建、数据集交接、训练、漂移分析、对比实验、报告。
这里摘录 17 段有代表性的交互，按"提示词 → 模型回答 → 纠错 → 结果"整理。用词有删节，不是逐字原文；数字取自当时的会话输出，对应的代码和日志在仓库里可以核对。

| # | 方面 | 内容 | 纠错类型 |
|---|---|---|---|
| [01](01_hw_sw_partition_mqtt_dp.md) | 系统架构 | 软硬件划分：MQTT 放 PS、DP 经 live 输入；相机与 CNN 合进同一比特流 | 用户指出遗漏，模型修正方案 |
| [02](02_mipi_lane1_fault_location.md) | 相机采集 | MIPI lane1 无数据，定位到转接板座子第 16 脚 | 模型用错寄存器，被用户纠正；追问读数后才下结论 |
| [03](03_framerate_sweep_false_positive.md) | 相机采集 | 帧率扫描报出 546 fps，实际是空白画面 | 模型自查发现假阳性，改合格标准 |
| [04](04_timing_constraints_not_applied.md) | RTL 与构建 | 三次"时序收敛"背后约束都没生效 | 模型自查时钟表发现 |
| [05](05_postsynth_zero_weights.md) | RTL 与构建 | RTL 仿真通过，综合后权重全为 0 | 模型自己写的 `initial` 写法有问题 |
| [06](06_merged_bitstream_axi_width_reset.md) | 上板 | 合并比特流：AXI 位宽 128 对 32/64、复位极性接反 | 仿真测不出，上板后逐个寄存器定位 |
| [07](07_dma_sigbus.md) | PS 驱动 | DMA 读结果 SIGBUS（`/dev/mem` 非对齐访问） | 模型自己写的 `.copy()` 引起 |
| [08](08_dp_live_sync_polarity.md) | 显示 | PL 接管 Mini DP 后黑屏，live 输入只认高有效同步 | 模型先误判读通道不通，后撤回 |
| [09](09_web_launcher_one_click.md) | 上位机 | 相机网页一键启动与自动重连 | 先用模拟相机测，发现网卡解析 bug |
| [10](10_overexposure_root_cause.md) | 数据采集 | 夜间采集过曝，逐级重启排查 | 模型承认把网页参数当成传感器实际状态 |
| [11](11_collection_review_exposure_lock.md) | 数据采集 | 两次采集回顾，曝光改为按 session 锁 | 用户质疑后模型修改方案 |
| [12](12_dataset_handoff_role_mixup.md) | 数据集 | 交接脚本：坏点规则 1840 → 51；理解错任务 | 模型承认任务理解错误 |
| [13](13_daf_negative_result.md) | 算法探索 | DAF 预处理无效，与图像全 0 对照持平 | 否定结论；模型主动加对照组 |
| [14](14_laser_drift_study.md) | 算法探索 | 激光慢漂移：去漂移全部无效，改为窗口去相关检测 | 模型修正对检测器含义的描述 |
| [15](15_eval_leakage.md) | 训练评估 | 评估指标虚高：训练集混入对照段 | 模型复查自己的评估脚本时发现 |
| [16](16_compare_experiments_honest_claims.md) | 对比实验 | 逐项标注优势成立与不成立 | 模型把不利结果写进表 |
| [17](17_report_v3_no_overclaim.md) | 报告 | 只写数据站得住的结论 | 模型限定结论写法；上板误报时不直接调门限 |

提炼出的技能见 [`skill/`](../../skill/README.md)，其中 `skill/prompts/README.md` 的四个提示词模板分别来自 01（软硬件划分）、04 和 05（读报告定位问题）、07（先复现再修改）、15（数据泄漏自查）。
