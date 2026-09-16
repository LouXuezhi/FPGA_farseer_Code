"""hls4ml reference build of the same QCNN, for comparison with the hand-written RTL.

Same weights (from the QAT checkpoint), same device and clock as the RTL build.
Reports HLS resource/latency estimates; the RTL numbers come from Vivado.

  conda activate hls4ml
  PATH=$HOME/.local/bin:$PATH python build/hls4ml/run_hls4ml.py --reuse 64
"""
import argparse
import json
from pathlib import Path

import hls4ml
import torch
from torch import nn

PART, CLOCK_NS = "xc7z020clg400-1", 10


class QCNNFloat(nn.Module):
    """Float twin of src/model/train_qcnn.py's QCNN (fc layers as Linear)."""

    def __init__(self, widths=(16, 32, 64, 64), hidden=16):
        super().__init__()
        channels = [1, *widths]
        self.conv = nn.Sequential(*[layer
                                    for i in range(len(widths))
                                    for layer in (nn.Conv2d(channels[i], channels[i + 1], 3, 2, 1), nn.ReLU())])
        self.pool = nn.AvgPool2d(4)
        self.fc1, self.relu, self.fc2 = nn.Linear(widths[-1], hidden), nn.ReLU(), nn.Linear(hidden, 1)

    def forward(self, x):
        x = self.pool(self.conv(x)).flatten(1)
        return self.fc2(self.relu(self.fc1(x)))


def load_qat_weights(model, ckpt_path):
    """QAT checkpoint -> float twin (1x1 conv heads become Linear)."""
    state = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    with torch.no_grad():
        for i in range(len(model.conv) // 2):
            model.conv[2 * i].weight.copy_(state[f"convs.{i}.weight"])
            model.conv[2 * i].bias.copy_(state[f"convs.{i}.bias"])
        model.fc1.weight.copy_(state["fc1.weight"].flatten(1))
        model.fc1.bias.copy_(state["fc1.bias"])
        model.fc2.weight.copy_(state["fc2.weight"].flatten(1))
        model.fc2.bias.copy_(state["fc2.bias"])
    return model


def patch_build_script(tcl_path):
    """Drop Tcl options that Vitis 2025.1 no longer accepts (hls4ml targets older HLS)."""
    dropped = ("config_array_partition",)
    lines = tcl_path.read_text().splitlines(keepends=True)
    tcl_path.write_text("".join(l for l in lines if not l.strip().startswith(dropped)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", default="runs/qcnn_in56_w16-32-64-64_a12_seed0/qat.pt")
    parser.add_argument("--reuse", type=int, default=64, help="hls4ml ReuseFactor (higher = fewer DSPs)")
    parser.add_argument("--precision", default="ap_fixed<16,6>")
    parser.add_argument("--out", default="build/hls4ml/project")
    args = parser.parse_args()

    model = load_qat_weights(QCNNFloat(), args.ckpt).eval()
    config = hls4ml.utils.config_from_pytorch_model(
        model, input_shape=(1, 56, 56), granularity="name",
        default_precision=args.precision, channels_last_conversion="internal", transpose_outputs=False)
    for layer in config["LayerName"].values():
        layer["ReuseFactor"] = args.reuse
    config["Model"]["ReuseFactor"] = args.reuse
    config["Model"]["Strategy"] = "Resource"      # latency strategy does not fit on a 7020

    hls_model = hls4ml.converters.convert_from_pytorch_model(
        model, hls_config=config, output_dir=args.out, backend="Vitis",
        io_type="io_stream", part=PART, clock_period=CLOCK_NS)
    hls_model.write()
    patch_build_script(Path(args.out, "build_prj.tcl"))
    report = hls_model.build(csim=False, synth=True, export=False)
    Path(args.out, "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str)[:2000])


if __name__ == "__main__":
    main()
