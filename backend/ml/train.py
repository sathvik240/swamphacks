"""Train the hazard index model and write data/hazard_model.joblib.

  python -m ml.train                          # synthetic, threshold-labelled data
  python -m ml.train --data data/training.csv # historical data from ml.build_dataset
  python -m ml.train --data data/training.csv --blend 0.3  # mix in 30% synthetic rows
"""
import argparse
from pathlib import Path

import pandas as pd

from planit import config
from planit.hazard import model, synthetic
from planit.hazard.features import FEATURES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="CSV produced by ml.build_dataset")
    ap.add_argument("--blend", type=float, default=0.0, help="fraction of synthetic rows to add to real data")
    ap.add_argument("--out", default=str(config.MODEL_PATH))
    args = ap.parse_args()

    if args.data:
        df = pd.read_csv(args.data)[FEATURES + ["hazard"]]
        source = f"historical ({args.data})"
        if args.blend > 0:
            df = pd.concat([df, synthetic.generate(int(len(df) * args.blend))], ignore_index=True)
            source += f" + {args.blend:.0%} synthetic"
    else:
        df = synthetic.generate()
        source = "synthetic"
    bundle = model.train(df, source=source)
    model.save(bundle, Path(args.out))
    print(f"trained on {bundle['n_train']} rows from {source}; holdout MAE {bundle['mae']} -> {args.out}")


if __name__ == "__main__":
    main()
