# 数据集

| 目录 | 内容 | 来源 / DOI | 许可 | 用途 |
|---|---|---|---|---|
| `raw/osf_zfmp5_synthetic_thermal/` | COMSOL 合成温度散斑：原始 601 张 + 增强 13.8 万张，126×126；含训练/预测 notebook、COMSOL 模型、MATLAB 脚本、预训练权重 | [OSF zfmp5](https://osf.io/zfmp5/) · 10.17605/OSF.IO/ZFMP5 | CC BY 4.0 | D0 / T1 |
| `raw/osf_8nxvk_experimental_thermal/` | 实验温度散斑（633 nm，62.5 µm MMF，25–200 °C，24,528 张）；含 MNet-reg notebook | [OSF 8nxvk](https://osf.io/8nxvk/) · 10.17605/OSF.IO/8NXVK | 以 OSF 页面为准 | D0 / T1 |
| `raw/mendeley_tbrnp6vrnj_leak_wds/` | PVC 管网泄漏：加速度计/动态压力 51.2 kHz、水听器 8 kHz，30 s × 280 组；孔口/纵向裂缝/环向裂缝/垫片/无泄漏，含背景噪声工况 | [Mendeley tbrnp6vrnj](https://data.mendeley.com/datasets/tbrnp6vrnj/1) · 10.17632/tbrnp6vrnj.1 | CC BY 4.0 | D1 扰动源 |
| `raw/orda_32229270_sheffield_leak/` | PE 管（63 mm）校准加速度计泄漏信号，2.8–4.2 bar，含代码 | [ORDA](https://doi.org/10.15131/shef.data.32229270.v1) | CC BY 4.0 | D1 扰动源 / 泄漏频谱参考 |

完整性：Mendeley 下载时逐文件校验 sha256（`_listing.txt`），ORDA 校验 md5（`_md5.txt`），OSF 校验文件大小（`_listing.tsv`）。

下载脚本：`osf_fetch.py <node> <dir>`、`raw/mendeley_tbrnp6vrnj_leak_wds/_fetch.sh`。
