# 15 Inflated alarm evaluation metrics

[中文](15_eval_leakage.md) | English

Session: Held-out evaluation of the window-fusion alarm method　Date: 2026-11-03

## Prompt (condensed)

> The window-fusion alarm has a very high detection rate in 5-fold evaluation. The data is the 11 segments from the third capture run (4 pure control segments, 5 long plateau segments, 2 random walk/step segments).
> Check whether the train/test split, the threshold and the normalization parameters used test data, and if there is a problem, give a scheme that uses only the training folds.

## Model answer (summary)

- The training set included no-heating control segments. The control segments in the test fold came from times adjacent to the control segments in the training folds, with similar drift states, so the threshold was loose and "happened to fit".
- The mean, standard deviation and threshold used for normalization were also computed from all control segments, so test data took part in setting the parameters.
- Scheme: in each fold, hold out 1 long plateau segment + 1 control segment; determine the mean, standard deviation and threshold only from the control segments in the training folds; keep the fusion weights fixed (1 : 0.1) and do not tune them on the test fold.

## Self-correction

The assistant pointed out this problem on its own while reviewing the evaluation script it had written, and then recomputed all metrics with the new split.

## Result

Under nested hold-out (`src/model/win_alarm/nested.py`): detection of ≥1 °C/2 min is 92% (at 1% false alarm rate), 30/30 heating events raised an alarm, with a median latency of 25 s;
4 control segments totaling 1.5 h had 4 false alarms. See `src/model/win_alarm/README.md` and Part 3 of the design report for the numbers.
