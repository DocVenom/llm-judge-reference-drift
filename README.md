# TCMClinicalReason-Bench — Judge-Study Reproduction Code

This repository accompanies a manuscript under review that studies how the wording and
structure of an **LLM-as-a-Judge prompt** affect the detection of errors in
Traditional Chinese Medicine (TCM) clinical reasoning. It contains the judge prompts,
the scoring and statistics pipeline, and the aggregate metrics reported in the paper.

## Scope: what this repository does and does not contain

**Included** — everything needed to inspect how the reported numbers were produced:

- the five judge-prompt versions used in the study (baseline, wording-only control,
  completeness/contradiction checklist, aggregation rule, external baseline);
- the completeness (CE1–CE7) and cross-block contradiction (CC1–CC6) specifications
  exactly as they appear in the frozen prompt;
- the sampling, perturbation-construction, judging, metric, statistical-test and
  figure scripts;
- aggregate detection metrics (per prompt version and per perturbation type).

**Not included** — withheld deliberately:

- the clinical case texts and the model answers;
- per-case judge records (scores, reasons, contradiction reports);
- gold labels and human annotation sheets.

These materials contain verbatim clinical records and are therefore not redistributed
here. The aggregate numbers in `results/summary.json` are sufficient to verify every
value that appears in the manuscript's tables; the underlying per-case records can be
requested from the corresponding author under a data-use agreement.

## Repository layout

```
00_config/
  config.py            paths, seeds, model registry, run-time switches
  judge_prompts.py     v1–v5 judge prompts, incl. CE1–CE7 and CC1–CC6
02_scripts/
  s01_stratify_sample.py        stratified sampling of the evaluation set
  s02b_run_infer_vllm.py        answer generation with a local vLLM backend
  s03_build_perturbations.py    construction of the three perturbation types
  s05_judge_score.py            judge scoring (paired original/perturbed records)
  s06_compute_metrics.py        sensitivity, specificity, McNemar comparisons
  s06c_agg_diag.py              dimension-to-overall aggregation diagnostics
  s14_make_figures_dim_grid.py  dimension-level figures
  s16_mcnemar_targeted.py       exact McNemar tests, targeted-absolute criterion
  s17_recompute_table2.py       independent recomputation of the per-type table
  s18_table11_exact.py          exact McNemar tests for the two overall criteria
  figure_style.py               shared figure styling
results/
  summary.json         aggregate detection metrics per prompt version
LICENSE
```

## Design constants used throughout

| Constant | Value |
|---|---|
| Random seed (all sources) | `20261010` |
| Score scale | 0–5 per rubric dimension, plus an `overall` score |
| Detection, "score-drop" criterion | `score_original − score_perturbed ≥ 1` |
| Detection, "absolute" criterion | `score_perturbed ≤ 3` |
| Targeted-absolute criterion | injected dimension score `≤ 3` |
| Paired comparisons | two-sided exact McNemar test |

The three perturbation types are `M1_omission` (removal of the formula-explanation
rationale), `P1_coldheat` (cold–heat inversion of the treatment principle) and
`P2_treatment` (treatment-direction reversal). Pairing is one-to-one: each perturbed
answer is scored against its own original answer, and all comparisons are made on
matched pairs.

## Running the pipeline

The pipeline expects the case corpus and generated answers to be present on disk in the
layout defined by `00_config/config.py`. Those inputs are not part of this repository;
without them the scripts will report missing input rather than silently producing
numbers.

```
python 02_scripts/s01_stratify_sample.py
python 02_scripts/s02b_run_infer_vllm.py
python 02_scripts/s03_build_perturbations.py
python 02_scripts/s05_judge_score.py
python 02_scripts/s06_compute_metrics.py
python 02_scripts/s16_mcnemar_targeted.py
python 02_scripts/s18_table11_exact.py
```

API credentials, when a hosted backend is used, are read from environment variables or
from a local file that is excluded from version control; no credential is stored in
this repository.

## License

Released for non-commercial research use under CC BY-NC 4.0; see `LICENSE`.
Model outputs produced on this benchmark must not be used as a basis for clinical
diagnosis or treatment.
