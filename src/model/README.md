# src/model — 训练、量化、定点参考模型

QCNN 是给推理核设计的定点 CNN：4 层 3×3 stride-2 卷积 + GAP + 2 层 FC，权重 int8，激活 12 bit 无符号，缩放全部是 2 的幂，硬件里只做舍入移位。

| 文件 | 作用 |
|---|---|
| `qcnn_int.py` | 整数参考模型（RTL 必须与它逐位一致）、`qmodel.npz` 读写、RTL 参数导出 `export_rtl` |
| `train_qcnn.py` | 浮点预训练 → 校准 → 量化感知训练 → 整数导出；`--export-dir src/rtl/gen` 直接写 RTL 参数 |
| `train_mnet_reg.py` | 浮点基线（MobileNetV1 回归），提供数据划分与指标函数，`train_qcnn.py` 复用 |
| `prepare_dataset.py` | 公开数据集（OSF 8nxvk）整理成 `data/processed/` |

## 定点规则（`qcnn_int.py` 文件头）

```
输入   uint8；ΔT 模型 2 个平面：平面 0 参考帧，平面 1 当前帧
卷积   acc = Σ w_int8 · a_in + b_int32
       a_out = (acc + 2^(sh-1)) >>> sh
       隐藏层截断到 [0, act_hi]（ReLU + 无符号）；最后一层截断到 int16
GAP    4×4 求和后舍入移位（sh = 4）
```

## 导出到硬件

```bash
python src/model/qcnn_int.py <qmodel.npz> src/rtl/gen data/fusion/tv_pairs_win_16x2x56x56.npy
```

写出权重 ROM、`qcnn_params.vh`（形状、并行度、移位量）和测试向量；之后按 `build/README.md` 仿真并重建比特流。
用训练好的报警模型复现 `src/rtl/gen/` 当前内容：

```bash
python src/model/qcnn_int.py data/fusion/qmodel_win.npz /tmp/gen data/fusion/tv_pairs_win_16x2x56x56.npy
diff -r /tmp/gen src/rtl/gen          # 无输出
```

报警方法（窗口平均 + QCNN + 去相关融合）的训练与评估见 `win_alarm/README.md`。

## 板端输入与训练输入一致

板上每步把相机帧 `(800, 640)` RAW8 经 `src/host/speckle_preproc.py`（行平均、坏点替换、σ=40 平场、标准化）得到 `(400, 640)`，
再由 `speckle_node.to_plane` 取中心正方形、块平均到 56×56、按 `q_scale / q_zero` 量化成 uint8。训练数据必须走同一份预处理，
`--q-scale / --q-zero / --dt-lsb` 三个数要与训练时一致。

## 前期验证（公开数据集）

工具链先在公开温度散斑数据集上打通（单帧 → 1 路温度，形状 1×56² → 1）：

```bash
python data/osf_fetch.py 8nxvk data/raw/osf_8nxvk_experimental_thermal
python src/model/prepare_dataset.py
python src/model/train_qcnn.py --widths 16,32,64,64 --act-bits 12
```

`data/qmodel_t1.npz` 是这一步的结果（dataset 14 全程不参与训练和选型）。
