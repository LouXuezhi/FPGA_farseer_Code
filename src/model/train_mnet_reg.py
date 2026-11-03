"""MNet-reg temperature regression on experimental specklegrams.

Reproduces Photonics 2026, 13, 84 (Velez Hoyos et al.), Table 2:
  train pool = datasets 1 | 1-6 | 1-13, external test = dataset 14 (never seen).
Leakage control:
  - dataset 14 is loaded only after training, for the final evaluation;
  - split 72/18/10 (train/val/internal test) is done inside the pool only;
  - no statistics are fitted on data: pixels /255, labels in raw degC;
  - checkpoint selection uses val loss only.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import timm
import torch
from torch import nn

TEST_DATASET_ID = 14


class MNetReg(nn.Module):
    """MobileNetV1 (ImageNet) + GAP + 3x Dense(1024) + Dropout(0.1) + Dense(1)."""

    def __init__(self, init_bias):
        super().__init__()
        self.backbone = timm.create_model("mobilenetv1_100.ra4_e3600_r224_in1k",
                                          pretrained=True, num_classes=0)  # -> GAP 1024
        self.head = nn.Sequential(
            nn.Linear(1024, 1024), nn.ReLU(),
            nn.Linear(1024, 1024), nn.ReLU(),
            nn.Linear(1024, 1024), nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(1024, 1),
        )
        nn.init.constant_(self.head[-1].bias, init_bias)  # train-split mean, speeds up start

    def forward(self, x):
        return self.head(self.backbone(x)).squeeze(1)


def to_input(images_u8):
    # (B,H,W) uint8 red channel -> (B,3,H,W) float in [0,1], as in the paper
    return (images_u8.float() / 255).unsqueeze(1).expand(-1, 3, -1, -1)


@torch.no_grad()
def predict(model, images, batch_size=256):
    model.eval()
    preds = []
    for i in range(0, len(images), batch_size):
        with torch.autocast("cuda", dtype=torch.float16):
            preds.append(model(to_input(images[i:i + batch_size])).float())
    return torch.cat(preds)


def regression_metrics(pred, target):
    err = pred - target
    mse = (err ** 2).mean().item()
    return {
        "mse": mse,
        "rmse": mse ** 0.5,
        "mae": err.abs().mean().item(),
        "max_error": err.abs().max().item(),
        "r2": 1 - (err ** 2).sum().item() / ((target - target.mean()) ** 2).sum().item(),
        "n": len(target),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/processed/fss_exp_thermal")
    parser.add_argument("--train-datasets", type=int, default=13, help="use datasets 1..N")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--lr-schedule", choices=["constant", "cosine"], default="cosine",
                        help="paper: constant; cosine lowers end-of-training noise")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="runs")
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    device = torch.device("cuda")
    run_dir = Path(args.out) / f"mnet_reg_ds1-{args.train_datasets}_{args.lr_schedule}_seed{args.seed}"
    run_dir.mkdir(parents=True, exist_ok=True)

    labels = np.load(Path(args.data) / "labels.npz")
    images_all = np.load(Path(args.data) / "images.npy", mmap_mode="r")
    assert args.train_datasets < TEST_DATASET_ID
    pool_idx = np.flatnonzero((labels["dataset_id"] >= 1) & (labels["dataset_id"] <= args.train_datasets))

    # Paper split: 10% internal test, then 80/20 train/val of the rest
    rng = np.random.default_rng(args.seed)
    pool_idx = rng.permutation(pool_idx)
    n_test = round(0.1 * len(pool_idx))
    n_val = round(0.2 * (len(pool_idx) - n_test))
    split = {"internal_test": pool_idx[:n_test],
             "val": pool_idx[n_test:n_test + n_val],
             "train": pool_idx[n_test + n_val:]}
    np.savez(run_dir / "split_idx.npz", **split)

    # Whole subsets fit on GPU as uint8 (<1.2 GB)
    def load(idx):
        idx = np.sort(idx)
        return (torch.from_numpy(images_all[idx]).to(device),
                torch.from_numpy(labels["temperature"][idx]).to(device), idx)

    x_train, y_train, _ = load(split["train"])
    x_val, y_val, _ = load(split["val"])

    model = MNetReg(init_bias=y_train.mean().item()).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.LambdaLR(  # per-epoch factor
        optimizer, lambda e: 1.0 if args.lr_schedule == "constant" else 0.5 * (1 + np.cos(np.pi * e / args.epochs)))
    scaler = torch.amp.GradScaler()
    best_val, history = float("inf"), []

    for epoch in range(args.epochs):
        model.train()
        t0, train_loss = time.time(), 0.0
        for batch in torch.randperm(len(x_train), device=device).split(args.batch_size):
            with torch.autocast("cuda", dtype=torch.float16):
                pred = model(to_input(x_train[batch]))
            loss = nn.functional.mse_loss(pred.float(), y_train[batch])
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            train_loss += loss.item() * len(batch)
        scheduler.step()

        val_loss = nn.functional.mse_loss(predict(model, x_val), y_val).item()
        history.append({"epoch": epoch + 1, "train_mse": train_loss / len(x_train), "val_mse": val_loss})
        if val_loss < best_val:  # checkpoint on val only
            best_val = val_loss
            torch.save(model.state_dict(), run_dir / "best.pt")
        print(f"epoch {epoch + 1:3d} train {history[-1]['train_mse']:9.3f} "
              f"val {val_loss:9.3f} best {best_val:9.3f} ({time.time() - t0:.0f}s)", flush=True)

    # Final evaluation with the selected checkpoint; dataset 14 touched only here
    model.load_state_dict(torch.load(run_dir / "best.pt", weights_only=True))
    del x_train, y_train
    x_int, y_int, _ = load(split["internal_test"])
    x_ext, y_ext, ext_idx = load(np.flatnonzero(labels["dataset_id"] == TEST_DATASET_ID))
    pred_ext = predict(model, x_ext)

    results = {"args": vars(args), "best_val_mse": best_val,
               "internal_test": regression_metrics(predict(model, x_int), y_int),
               "external_test_ds14": regression_metrics(pred_ext, y_ext),
               "history": history}
    (run_dir / "metrics.json").write_text(json.dumps(results, indent=2))
    np.savez(run_dir / "pred_ds14.npz", idx=ext_idx, target=y_ext.cpu().numpy(), pred=pred_ext.cpu().numpy())
    print(json.dumps({k: results[k] for k in ("internal_test", "external_test_ds14")}, indent=2))


if __name__ == "__main__":
    main()
