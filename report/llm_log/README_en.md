# llm_log — LLM collaboration log

[中文](README.md) | English

The whole project was developed with an LLM coding assistant (Claude Code), split by task into parallel sessions: capture-chain debugging, bitstream builds, dataset handoff, training, drift analysis, comparison experiments and the report.
These are 17 representative excerpts, each organized as prompt → model answer → self-correction → result. They are condensed, not verbatim; the numbers come from the session outputs at the time, and the matching code and logs are in this repository.

| # | Area | Content | Kind of correction |
|---|---|---|---|
| [01](01_hw_sw_partition_mqtt_dp_en.md) | System architecture | HW/SW partition: MQTT on the PS, display through the DP live input; camera and CNN in one bitstream | the user pointed out a gap, the assistant revised the plan |
| [02](02_mipi_lane1_fault_location_en.md) | Camera capture | No data on MIPI lane 1, traced to pin 16 of the adapter-board connector | the user corrected a wrong register; the assistant asked back about readings before concluding |
| [03](03_framerate_sweep_false_positive_en.md) | Camera capture | A frame-rate sweep reported 546 fps, but the frames were blank | the assistant found the false positive itself and changed the pass criteria |
| [04](04_timing_constraints_not_applied_en.md) | RTL and build | Behind three "timing met" results, the constraints were not applied | the assistant found it by checking the clock table |
| [05](05_postsynth_zero_weights_en.md) | RTL and build | RTL simulation passes, weights are all zero after synthesis | the assistant's own `initial`-block code was the cause |
| [06](06_merged_bitstream_axi_width_reset_en.md) | Board bring-up | Combined bitstream: AXI width 128 vs. 32/64, reset polarity reversed | invisible in simulation, found register by register on the board |
| [07](07_dma_sigbus_en.md) | PS driver | SIGBUS when reading DMA results (unaligned access on `/dev/mem`) | caused by the assistant's own `.copy()` |
| [08](08_dp_live_sync_polarity_en.md) | Display | Black screen after the PL takes over Mini DP; the live input only accepts active-high sync | the assistant first misjudged the read path as broken, then withdrew it |
| [09](09_web_launcher_one_click_en.md) | Host tools | One-click camera web page with automatic reconnect | tested on a simulated camera first, found a NIC-parsing bug |
| [10](10_overexposure_root_cause_en.md) | Data collection | Overexposure during an overnight run, isolated by restarting parts one at a time | the assistant admitted taking web-page values for the sensor's real state |
| [11](11_collection_review_exposure_lock_en.md) | Data collection | Review of two collection runs; exposure locked per session | the plan changed after the user challenged it |
| [12](12_dataset_handoff_role_mixup_en.md) | Dataset | Handoff scripts: bad-pixel rule 1840 → 51; misread task | the assistant admitted misreading the task |
| [13](13_daf_negative_result_en.md) | Algorithm exploration | Digital aperture filtering does not help; same as an all-zero-image control | negative result; the assistant added the control group itself |
| [14](14_laser_drift_study_en.md) | Algorithm exploration | Slow laser drift: all de-drifting methods fail; switched to windowed decorrelation detection | the assistant corrected its description of what the detector measures |
| [15](15_eval_leakage_en.md) | Training and evaluation | Inflated metrics: control sessions leaked into training | found by the assistant while reviewing its own evaluation script |
| [16](16_compare_experiments_honest_claims_en.md) | Comparison experiments | Each claimed advantage marked as holding or not | the assistant put unfavorable results in the table |
| [17](17_report_v3_no_overclaim_en.md) | Report | Only conclusions the data supports | the assistant limited the claims; did not simply raise the threshold after false alarms on the board |

The skills distilled from this work are in `skill/` ([English](../../skill/README_en.md)). The four prompt templates in `skill/prompts/` come from 01 (HW/SW partition), 04 and 05 (locating problems from reports), 07 (reproduce before fixing) and 15 (data-leakage check).
