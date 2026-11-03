"""Pack the OSF 8nxvk experimental specklegrams into one uint8 array + label table.

Source: data/raw/osf_8nxvk_experimental_thermal/Datasets/<id>/*.tiff
Output: data/processed/fss_exp_thermal/{images.npy, labels.npz}
"""
import argparse
import re
from pathlib import Path

import numpy as np
from PIL import Image

# e.g. FSS_exp_0633.0nm_20.0mm_025.45C_14dt_dw_24527.tiff
NAME_PATTERN = re.compile(r"_(\d+\.\d+)C_(\d+)dt_(up|dw)_(\d+)\.tiff$")


def parse_name(path):
    temp, dataset_id, phase, frame_idx = NAME_PATTERN.search(path.name).groups()
    return float(temp), int(dataset_id), phase == "up", int(frame_idx)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default="data/raw/osf_8nxvk_experimental_thermal/Datasets")
    parser.add_argument("--dst", default="data/processed/fss_exp_thermal")
    args = parser.parse_args()

    paths = sorted(Path(args.src).glob("*/*.tiff"))
    meta = np.array([parse_name(p) for p in paths])  # (N, 4)
    order = np.argsort(meta[:, 3], kind="stable")     # sort by acquisition index
    paths, meta = [paths[i] for i in order], meta[order]

    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    # Images are already 224x224 ROI crops; only the red channel carries signal.
    images = np.lib.format.open_memmap(dst / "images.npy", mode="w+",
                                       dtype=np.uint8, shape=(len(paths), 224, 224))
    for i, path in enumerate(paths):
        images[i] = np.asarray(Image.open(path))[..., 0]
    images.flush()

    np.savez(dst / "labels.npz",
             temperature=meta[:, 0].astype(np.float32),
             dataset_id=meta[:, 1].astype(np.int16),
             is_heating=meta[:, 2].astype(bool),
             frame_idx=meta[:, 3].astype(np.int32))
    print(f"{len(paths)} images, datasets {np.unique(meta[:, 1]).astype(int).tolist()}")


if __name__ == "__main__":
    main()
