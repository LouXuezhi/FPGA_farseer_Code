# data — 测试数据与参考结果

| 文件 | 内容 | 用途 |
|---|---|---|
| `fusion/qmodel_win.npz` | 报警用 QCNN 的训练结果（2×56² → 9 路温升），`src/model/win_alarm/win_qcnn.py final` | 生成 `src/rtl/gen/` 与预编译比特流 |
| `fusion/fusion.json` | 窗口融合报警参数：窗口 60 s、间隔 120 s、融合权重、对照数据标定的均值/标准差/门限 | 节点 `--win-fusion` |
| `fusion/tv_pairs_win_16x2x56x56.npy` | 16 组实测窗口平均图样对（uint8） | 导出 RTL 测试向量 |
| `qmodel_dt_synthetic.npz` | ΔT 形状（2×56² → 9）的整数测试模型：卷积层取自 `qmodel_t1.npz`，第一层扩成两平面，末层随机 9 输出；不是训练结果 | 生成 `src/rtl/gen/`，RTL 与板端软件测试 |
| `tv_pairs_16x2x56x56.npy` | 16 组（参考帧, 当前帧）uint8 输入 | 与 `qmodel_dt_synthetic.npz` 配套的测试输入，`qcnn_int.py` 导出测试向量时用 |
| `qmodel_t1.npz` | 前期验证模型：公开数据集单帧 → 1 路温度，`train_qcnn.py --widths 16,32,64,64 --act-bits 12` | 测试模型的卷积层来源 |
| `cam_frame_640x800.npy` | 一帧板上相机 RAW8（800 行 × 640 列） | PL 屏幕仿真的相机输入（`build_ui.py --cam`） |
| `speckle_pair_2x800x640.npy` | 板上相机连续两帧 RAW8 | PS 界面预览（参考帧 + 当前帧） |
| `osf_fetch.py` | 公开数据集下载：`python data/osf_fetch.py 8nxvk data/raw/osf_8nxvk_experimental_thermal` | 前期验证 |

公开数据集（不入库）：OSF 8nxvk 实验温度散斑，633 nm 激光、62.5 µm 多模光纤、25–200 °C，14 组 × 1752 张，DOI 10.17605/OSF.IO/8NXVK。

板上参考结果：`board/fz3b/results/`。仿真参考结果：`sim/results/`。
