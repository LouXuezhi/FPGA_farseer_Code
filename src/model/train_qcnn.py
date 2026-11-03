"""Small int8 CNN for FPGA: float pre-train -> calibrate -> QAT -> integer export.

Same split and leakage rules as train_mnet_reg.py (dataset 14 only at the end).
Scales are powers of 2, so the RTL requantizes with a rounding shift only.
Label y = (T - 25) / 175; last layer outputs y with 12 frac bits (int16).
"""
import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from qcnn_int import export_rtl, predict_int, save_qmodel
from train_mnet_reg import TEST_DATASET_ID, regression_metrics

T_MIN, T_SPAN, OUT_FRAC = 25.0, 175.0, 12


def fake_quant(x, frac, lo, hi):
    # floor(x * 2^frac + 0.5), clamped; straight-through gradient inside the range
    scale = 2.0 ** frac
    x_c = x.clamp(lo / scale, hi / scale)
    return x_c + ((torch.floor(x * scale + 0.5).clamp(lo, hi) / scale) - x_c).detach()


class QConv(nn.Conv2d):
    def __init__(self, c_in, c_out, kernel, stride, relu=True, act_bits=8):
        super().__init__(c_in, c_out, kernel, stride, padding=kernel // 2)
        self.relu = relu
        self.act_hi = 2 ** act_bits - 1
        self.f_out = None if relu else OUT_FRAC  # hidden: set by calibration
        self.act_samples = []

    def frac_w(self):
        return int(np.floor(np.log2(127 / self.weight.detach().abs().max().item())))

    def forward(self, x, f_in, quant):
        if not quant:
            y = F.conv2d(x, self.weight, self.bias, self.stride, self.padding)
            if self.relu:
                y = F.relu(y)
                if not self.training:  # random activation samples, reset by calibrate()
                    flat = y.detach().flatten()
                    self.act_samples.append(flat[torch.randint(len(flat), (4096,), device=flat.device)])
            return y
        f_w = self.frac_w()
        w = fake_quant(self.weight, f_w, -128, 127)
        b = fake_quant(self.bias, f_in + f_w, -2 ** 31, 2 ** 31 - 1)
        y = F.conv2d(x, w, b, self.stride, self.padding)
        return fake_quant(y, self.f_out, *((0, self.act_hi) if self.relu else (-32768, 32767)))


class QCNN(nn.Module):
    """[conv3x3 s2]*N -> GAP 4x4 -> fc -> fc(1)."""

    def __init__(self, widths, hidden=16, act_bits=8):
        super().__init__()
        widths = [1, *widths]
        self.act_hi = 2 ** act_bits - 1
        self.convs = nn.ModuleList(QConv(widths[i], widths[i + 1], 3, 2, act_bits=act_bits)
                                   for i in range(len(widths) - 1))
        self.fc1 = QConv(widths[-1], hidden, 1, 1, act_bits=act_bits)
        self.fc2 = QConv(hidden, 1, 1, 1, relu=False)

    def forward(self, x_u8, quant=False):
        x, f = x_u8.float().unsqueeze(1) / 256, 8  # input frac bits = 8
        for conv in self.convs:
            x, f = conv(x, f, quant), conv.f_out
        x = x.mean((2, 3), keepdim=True)
        if quant:
            x = fake_quant(x, f, 0, self.act_hi)
        x = self.fc1(x, f, quant)
        return self.fc2(x, self.fc1.f_out, quant).flatten()

    def calibrate(self, x_u8, batch_size=512, percentile=0.9999):
        for layer in [*self.convs, self.fc1]:
            layer.act_samples = []  # drop samples seen on val during float training
        self.eval()
        with torch.no_grad():
            for i in range(0, len(x_u8), batch_size):
                self(x_u8[i:i + batch_size])
        for layer in [*self.convs, self.fc1]:  # clip rare outliers instead of wasting range
            act_top = torch.quantile(torch.cat(layer.act_samples).float(), percentile).item()
            layer.f_out = int(np.floor(np.log2(layer.act_hi / act_top)))

    def to_integer(self):
        qmodel, f_in = [], 8
        for layer in [*self.convs, "gap", self.fc1, self.fc2]:
            if layer == "gap":
                qmodel.append({"type": "gap", "shift": 4})
                continue
            f_w = layer.frac_w()
            shift = f_in + f_w - layer.f_out
            assert shift >= 1, f"shift {shift} < 1"
            qmodel.append({
                "type": "conv", "stride": layer.stride[0], "pad": layer.padding[0], "relu": layer.relu, "shift": shift,
                "act_hi": layer.act_hi,
                "w": np.clip(np.floor(layer.weight.detach().cpu().numpy() * 2.0 ** f_w + 0.5), -128, 127).astype(np.int64),
                "b": np.floor(layer.bias.detach().cpu().numpy() * 2.0 ** (f_in + f_w) + 0.5).astype(np.int64),
            })
            f_in = layer.f_out
        return qmodel


def run_phase(model, data, epochs, lr, batch_size, quant, ckpt):
    x_train, y_train, x_val, y_val = data
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, epochs)
    best = float("inf")
    for epoch in range(epochs):
        model.train()
        for batch in torch.randperm(len(x_train), device=x_train.device).split(batch_size):
            loss = F.mse_loss(model(x_train[batch], quant), y_train[batch])
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
        scheduler.step()
        model.eval()
        with torch.no_grad():
            val_rmse = T_SPAN * torch.cat([model(x_val[i:i + 1024], quant)
                                           for i in range(0, len(x_val), 1024)]).sub(y_val).pow(2).mean().sqrt().item()
        if val_rmse < best:  # select on val only
            best = val_rmse
            torch.save(model.state_dict(), ckpt)
        print(f"{'qat' if quant else 'float'} epoch {epoch + 1:3d} val_rmse {val_rmse:.3f} best {best:.3f}", flush=True)
    model.load_state_dict(torch.load(ckpt, weights_only=True))
    return best


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/processed/fss_exp_thermal")
    parser.add_argument("--input-size", type=int, choices=[56, 112], default=56)
    parser.add_argument("--widths", default="8,16,32,32", help="conv channels; 4 convs for 56, 5 for 112")
    parser.add_argument("--act-bits", type=int, default=8)
    parser.add_argument("--float-epochs", type=int, default=60)
    parser.add_argument("--qat-epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="runs")
    parser.add_argument("--export-dir", default=None, help="e.g. src/rtl/gen")
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    device = torch.device("cuda")
    widths = [int(w) for w in args.widths.split(",")]
    run_dir = Path(args.out) / f"qcnn_in{args.input_size}_w{'-'.join(map(str, widths))}_a{args.act_bits}_seed{args.seed}"
    run_dir.mkdir(parents=True, exist_ok=True)

    labels = np.load(Path(args.data) / "labels.npz")
    images_all = np.load(Path(args.data) / "images.npy", mmap_mode="r")
    pool_idx = np.random.default_rng(args.seed).permutation(
        np.flatnonzero(labels["dataset_id"] < TEST_DATASET_ID))
    n_test = round(0.1 * len(pool_idx))
    n_val = round(0.2 * (len(pool_idx) - n_test))
    split = {"internal_test": pool_idx[:n_test], "val": pool_idx[n_test:n_test + n_val],
             "train": pool_idx[n_test + n_val:]}

    def load(idx):
        # 224 -> input_size by integer average pooling: (sum + k^2/2) >> log2(k^2)
        idx, k = np.sort(idx), 224 // args.input_size
        x = torch.cat([torch.floor(F.avg_pool2d(torch.from_numpy(images_all[chunk]).to(device).float().unsqueeze(1), k) + 0.5)
                       for chunk in np.array_split(idx, -(-len(idx) // 1024))]).squeeze(1).to(torch.uint8)
        y = (torch.from_numpy(labels["temperature"][idx]).to(device) - T_MIN) / T_SPAN
        return x, y

    x_train, y_train = load(split["train"])
    x_val, y_val = load(split["val"])
    data = (x_train, y_train, x_val, y_val)

    model = QCNN(widths, act_bits=args.act_bits).to(device)
    float_rmse = run_phase(model, data, args.float_epochs, 1e-3, args.batch_size, False, run_dir / "float.pt")
    model.calibrate(x_train)  # activation ranges from train split only
    qat_rmse = run_phase(model, data, args.qat_epochs, 1e-4, args.batch_size, True, run_dir / "qat.pt")

    # Integer model is what the FPGA runs; report its metrics
    qmodel = model.to_integer()
    save_qmodel(qmodel, run_dir / "qmodel.npz")
    x_int, y_int = load(split["internal_test"])
    x_ext, y_ext = load(np.flatnonzero(labels["dataset_id"] == TEST_DATASET_ID))

    def int_metrics(x, y):
        pred = T_MIN + T_SPAN * predict_int(qmodel, x.cpu().numpy()) / 2 ** OUT_FRAC
        return regression_metrics(torch.tensor(pred), T_MIN + T_SPAN * y.cpu().double())

    results = {"args": vars(args), "val_rmse_float": float_rmse, "val_rmse_qat": qat_rmse,
               "n_params": sum(p.numel() for p in model.parameters()),
               "shifts": [layer["shift"] for layer in qmodel],
               "internal_test_int": int_metrics(x_int, y_int),
               "external_test_ds14_int": int_metrics(x_ext, y_ext)}
    (run_dir / "metrics.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))

    if args.export_dir:  # test vectors: first 16 frames of dataset 14
        export_rtl(qmodel, x_ext[:16].cpu().numpy(), args.export_dir)


if __name__ == "__main__":
    main()
