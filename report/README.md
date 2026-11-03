# report — 设计报告与大模型协作记录

| 内容 | 文件 |
|---|---|
| 设计报告 | `design_report.pdf` |
| 系统架构图、数据流图与模块框图 | `architecture.md`、`figures/` |
| 大模型协作记录 | `llm_log/`（提示词、模型回答、自我纠错轨迹；待放入） |

报告中引用的数字以本仓库的原始输出为准：

| 数字 | 原始输出 |
|---|---|
| 推理核 565 µs/帧、144/144 逐位一致 | `sim/results/tb_qcnn_core.log` |
| 屏幕合成逐像素一致 | `sim/results/tb_video_overlay.log`、`sim/results/disp_ref_check.log` |
| 资源、时序、功耗 | `build/vivado/reports/` |
| 板上推理调用、预处理、步长 | `board/fz3b/results/node_log_excerpt.txt` |
