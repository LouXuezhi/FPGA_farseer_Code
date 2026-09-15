# src/model — 温度散斑回归（T1）

复现 Photonics 2026, 13, 84（MNet-reg）Table 2。数据：OSF 8nxvk 实验散斑，14 组 × 1752 张，224×224，仅红通道有信号。

## 运行

```bash
conda env create -f environment.yml && conda activate speckle
unzip "data/raw/osf_8nxvk_experimental_thermal/Experimental FSS Datasets.zip" -d data/raw/osf_8nxvk_experimental_thermal
python src/model/prepare_dataset.py                      # -> data/processed/fss_exp_thermal
python src/model/train_mnet_reg.py --train-datasets 13   # 1 | 6 | 13，对应论文 Model 1/2/3
```

结果写到 `runs/<name>/`：`metrics.json`（内部测试 + dataset 14）、`pred_ds14.npz`（后续 FPGA 金标准比对用）、`split_idx.npz`。

## 防泄露

| 风险 | 处理 |
|---|---|
| 相邻帧几乎相同，随机划分会虚高 | 主指标只看 **dataset 14**（独立实验，训练全程不加载） |
| 测试集参与调参（原 notebook 用 test loss 做 Optuna 目标） | 不调参；checkpoint 只按 val 选 |
| 归一化统计量泄露 | 不拟合任何统计量：像素 /255，标签原始 °C |
| 跨组重复图像 | 已用 md5 核查：跨 dataset 完全重复 0 张 |

内部测试（随机 10%）与训练帧高度相关，仅作参考。

## 与论文的差异

- 框架 PyTorch + timm MobileNetV1（ImageNet 权重来源不同于 Keras）
- 论文对指标做了 95% 置信区间离群过滤；这里 `metrics.json` 报原始指标，不过滤
- 默认 cosine 学习率衰减 + 100 epoch（论文：constant 1e-4 + 50 epoch，用 `--lr-schedule constant --epochs 50` 复现）
