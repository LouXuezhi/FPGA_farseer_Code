# src/model/win_alarm — 光纤沿线温升报警的训练与评估

报警方法：每 5 s 取最近 60 s 的平均散斑图样（窗口 B）和 120 s 前的平均图样（窗口 A）；
QCNN（2×56² → 9 路温升，与 PL 推理核同一结构）给出温升估计，同时算两个窗口 10×16 块的 1 − 相关系数；
两路按不加热对照数据的均值、标准差标准化后加权（1 : 0.1）相加，连续 3 次超过门限报警，报警后冷却 300 s。
门限取对照数据得分的 99.5% 分位。板端实现是 `src/host/win_fusion.py`，节点参数 `--win-fusion data/fusion/fusion.json`。

| 脚本 | 作用 |
|---|---|
| `extract56.py <session 目录> ...` | 每帧走板端同一份预处理（`src/host/speckle_preproc.py`），存 56×56 平面和 10×16 块到 `$FSF_WIN_WORK/cache/` |
| `win_qcnn.py cv` | 5 折留出（每折留 1 段长平台 + 1 段对照）：训练 QCNN（浮点 → 校准 → 量化感知），输出留出段的两路得分 `out/cv.npz` |
| `nested.py <cv.npz> <out> 0.1 pack` | 固定融合权重 0.1，门限只用训练折的对照定，统计检出率、事件检出、延迟、误报 |
| `cmp_alarm.py <50×80 特征缓存> <pack.npz> <out.json>` | 同窗口、同折、同报警规则下与逐帧相关、窗口均方差、ResNet-18 / ViT-B/16 特征、微调 ResNet-18 / MobileNetV3-S 对比 |
| `win_qcnn.py final` | 用全部数据训练，写 `out/final/qmodel.npz`、`fusion.json`、16 组测试输入 |
| `replay_board.py <qmodel> <fusion.json> <session 目录>` | 按板上 2 s 步长、整数模型回放一段录制，检查报警时刻 |

`FSF_WIN_WORK` 默认 `build/win_alarm/`。数据是第三次采集的 11 段（4 段纯对照、5 段长平台、2 段游走/台阶），按 `FZ3B_数据集交接规范` v1.3 目录格式。

导出到硬件：

```bash
python src/model/qcnn_int.py data/fusion/qmodel_win.npz src/rtl/gen data/fusion/tv_pairs_win_16x2x56x56.npy
./sim/rtl/run_verilator.sh tb_qcnn_core        # 144/144 逐位一致
```

留出结果（`report/` 设计报告第三部分）：2 min 内单路升温 ≥1 °C 时 AUC 0.993、1% 误报下检出 92%，30/30 次升温事件报警，延迟中位 25 s；
4 段对照共 1.5 h 误报 4 次，全部在同一段。

失效条件：门限依赖当前光路的漂移水平。换光纤、动过耦合或激光器状态变化后，先录 20–30 min 不加热的数据，按上表重新定门限（`fusion.json` 的 `mu`、`sd`、`threshold`）。
