# fiber_speckle_fpga

FPGA 流式多模光纤散斑解调，面向液冷管道振动/泄漏/堵塞监测。
目标赛事：2026 全国大学生嵌入式芯片与系统设计竞赛 · AMD FPGA 赛道 · 自主选题初级组。
平台：PYNQ-Z2（XC7Z020）。

方案与论证见 `docs/plan_speckle_vs_rl.md`（第 8–12 节为当前路线）。

## 路线

| 阶段 | 数据源 | 内容 |
|---|---|---|
| T1 | D0 公开温度散斑 | 训练 → Brevitas 量化 → FINN → PYNQ-Z2 推理，打通工具链 |
| S0/S1 | D0 抽行 / D1 合成动态散斑 | PC 或 DDR 回放 → PL 处理链（统一线阵 128 点/行格式） |
| T2 | 自采温度散斑 | 激光 + MMF + USB 相机 + TEC 控温台 |
| V1 | 自采振动 | TSL1401 线阵 + XADC，空管 + 激振器 |
| V2 | 自采泄漏/堵塞 | 水路台架（可选） |

## 目录

```
docs/            方案、赛题指南、器件手册 (datasheets/)
data/raw/        公开数据集原始包（见 data/README.md）
data/synth/      D1 合成动态散斑（pyMMF + 泄漏数据）
refs/            参考仓库：pyMMF, finn, finn-examples, brevitas, hls4ml, PYNQ
sim/             PC 端仿真与数据合成脚本
src/hls|rtl|host PL 源码与 PS/主机侧代码
board/           上板工程、运行脚本、实测输出
build/           Vivado/FINN 构建脚本与报告
skill/           技能包（赛事必交）
report/          设计报告与大模型协作记录（赛事必交）
notes/           过程笔记
_logs/           下载与运行日志
```

## 约定

- 目录和文件名只用小写英文、数字、下划线、连字符（赛事要求）
- 大模型协作过程需保留提示词、回答与纠错记录，统一放在 `report/llm_log/`
- 此机器同时运行 RL 训练：本项目的重计算用 `nice -n 19`
