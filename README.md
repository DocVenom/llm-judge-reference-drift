# llm-judge-reference-drift

Reproduction code for the manuscript:

> **A perturbation-based, aggregation-aware evaluation framework for clinical-reasoning LLMs: controlling reference-point drift in LLM-as-a-judge**

This repository contains the judge prompts, the scoring and statistics pipeline, and the
aggregate metrics reported in the paper. It does **not** contain clinical records,
model answers or per-case judge outputs.

## Contents at a glance

| Included | Not included |
|---|---|
| five judge-prompt versions (baseline, wording-only control, checklist, aggregation rule, external baseline) | clinical case texts |
| the completeness (CE1–CE7) and cross-block contradiction (CC1–CC6) specifications, verbatim | generated answers |
| sampling, perturbation-construction, judging, metric, statistical-test and figure scripts | per-case judge scores and reasons |
| aggregate detection metrics per prompt version and perturbation type | human annotation sheets |

## Data

The 2,000-case clinical corpus that this study builds on is distributed separately,
through the project's benchmark repository
([`NJUCM-BJUCM-TCM-AI/TCMClinicalReason-Bench`](https://github.com/NJUCM-BJUCM-TCM-AI/TCMClinicalReason-Bench)),
together with the seven-block task definition and the inference entry points. This
repository does not redistribute that corpus.

The materials **generated** in this study are not released either: the perturbation
pairs, the per-case judge score files and the human annotation sheets all embed
verbatim clinical text. They are, however, fully **regenerable** from the published
corpus with the scripts and constants in this repository — sampling (`s01`), answer
generation (`s02b`), perturbation construction (`s03`, deterministic given the seed),
judging (`s05`) and metrics (`s06`, `s16`–`s18`). Every reported number can therefore be
traced without redistributing patient-level content.

## Repository layout

```
00_config/
  config.py            paths, seeds, model registry, run-time switches
  judge_prompts.py     v1-v5 judge prompts, incl. CE1-CE7 and CC1-CC6
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
| Detection, "score-drop" criterion | `score_original - score_perturbed >= 1` |
| Detection, "absolute" criterion | `score_perturbed <= 3` |
| Targeted-absolute criterion | injected dimension score `<= 3` |
| Paired comparisons | two-sided exact McNemar test |

The three perturbation types are `M1_omission` (removal of the formula-explanation
rationale), `P1_coldheat` (cold–heat inversion of the treatment principle) and
`P2_treatment` (treatment-direction reversal). Pairing is one-to-one: each perturbed
answer is scored against its own original answer, and all comparisons are made on
matched pairs.

Two criteria are reported side by side throughout, because the manuscript's argument
depends on the contrast between them: the score-drop criterion is relative to the
original answer, whereas the absolute criterion applies a fixed threshold to the
perturbed score. The latter is shown to track the judge's reference point rather than
the injected defect, which is the "reference-point drift" of the title.

## Running the pipeline

The pipeline expects the corpus and the generated answers to be present on disk in the
layout defined by `00_config/config.py`. Without them the scripts report missing input
rather than silently producing numbers.

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
from a local file excluded from version control; no credential is stored here.

## License

Released for non-commercial research use under CC BY-NC 4.0; see `LICENSE`.
Model outputs produced on this benchmark must not be used as a basis for clinical
diagnosis or treatment.
