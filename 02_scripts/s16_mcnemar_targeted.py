#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""s16_mcnemar_targeted.py — 定向维度口径的配对 McNemar 精确检验

背景
----
s06_compute_metrics.py 的 stage_compare 只对**两条 overall 口径**做 McNemar：
  · drop → score_original − score_perturbed ≥ 1
  · abs  → score_perturbed(overall) ≤ 3
两条都在 **n = 287**（全部 ptype 合并）上计算，落盘为
`03_results/metrics/compare_*_vs_*.json` 与 `05_paper/tables/table2_mcnemar.md`。

论文 §5.1 的**主指标**是 targeted-absolute（扰动注入维度的分数 ≤ 3），
由 `s06c_agg_diag.py::section_targeted_abs()` 计算并落盘到
`03_results/metrics/agg_diag.json` 的 `targeted_abs` 字段 —— 但该脚本
**只给点估计与 Wilson 区间，从未做过配对检验**。

本脚本补上这一格：按 case_id 配对，对同一 ptype 的同一批样本做
McNemar 精确检验（双侧二项），并给 Bonferroni 校正。

口径（与 s06c 完全一致，勿改）
------------------------------
  targeted-abs : dims[PTYPE_DIM[ptype]] <= PERT_FLOOR (=3)
  targeted-rel : dims_original[dim] − dims[dim] >= 1

配对单位
--------
  key = case_id（同一 ptype 下 1 case = 1 pair）
  仅取两个条件共有的 case，且两个条件都必须有该维度的分数。

不一致格定义（与 s06 的 b_only/c_only 命名不同，此处按论文 §3.7 的 b/c 定义）
  b = 前一条件检出、后一条件未检出   (A=1, B=0)
  c = 前一条件未检出、后一条件检出   (A=0, B=1)
  Δ   = rate_B − rate_A （百分点）

McNemar 精确检验
----------------
  H0: b ~ Binomial(b+c, 0.5)
  双侧 p = min(1, 2 * Σ_{i=0}^{min(b,c)} C(n,i) / 2^n)

用法
----
  python 02_scripts/s16_mcnemar_targeted.py
  python 02_scripts/s16_mcnemar_targeted.py --ptype M1_omission --metric abs
  python 02_scripts/s16_mcnemar_targeted.py --results-dir 03_results/e3_HuatuoGPT-3-8B --tag e3
"""
import argparse
import json
import os
import sys
from math import comb, sqrt

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PTYPE_DIM = {
    'M1_omission': '处方解析_Block',
    'P1_coldheat': '治则治法_Block',
    'P2_treatment': '治则治法_Block',
}
PERT_FLOOR = 3.0

RUNGS = ['v1_baseline', 'v2_reword', 'v3_checklist']
CONTRASTS = [('v1_baseline', 'v3_checklist'),
             ('v2_reword', 'v3_checklist'),
             ('v1_baseline', 'v2_reword')]


# ---------------------------------------------------------------- 载入
def load_rows(judge_dir, ver, model_tag):
    p = os.path.join(judge_dir, f'judge_vllm_{model_tag}_{ver}.jsonl')
    if not os.path.exists(p):
        return None, p
    rows = []
    with open(p, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows, p


def det_set(rows, ptype, dim, metric):
    """{case_id: 0/1}。缺该维度分数的样本不计入分母（与 s06c 一致）。"""
    out = {}
    for r in rows:
        if r.get('ptype') != ptype:
            continue
        dp = (r.get('dims') or {}).get(dim)
        do = (r.get('dims_original') or {}).get(dim)
        if dp is None or do is None:
            continue
        if metric == 'abs':
            out[r['case_id']] = int(float(dp) <= PERT_FLOOR)
        elif metric == 'rel':
            out[r['case_id']] = int((float(do) - float(dp)) >= 1)
        else:
            raise ValueError(metric)
    return out


# ---------------------------------------------------------------- 统计
def mcnemar_exact_two_sided(b, c):
    """精确双侧 McNemar（二项检验）。b/c 为两格不一致数。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = 2.0 * sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, p)


def wilson(k, n, z=1.959963985):
    if not n:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def compare(a_name, b_name, A, B):
    """b = A 检出/B 未检出；c = A 未检出/B 检出。"""
    keys = sorted(set(A) & set(B))
    if not keys:
        return None
    b = sum(1 for k in keys if A[k] == 1 and B[k] == 0)
    c = sum(1 for k in keys if A[k] == 0 and B[k] == 1)
    n = len(keys)
    ka, kb = sum(A[k] for k in keys), sum(B[k] for k in keys)
    p = mcnemar_exact_two_sided(b, c)
    return dict(
        a=a_name, b=b_name, n=n,
        detected_a=ka, detected_b=kb,
        # ★ 存 6 位小数并单独给两位小数的百分数 —— 4 位小数会把
        #   84/99=84.848% 舍成 0.8485，再显示成 84.9%，与真值不符。
        rate_a=round(ka / n, 6), rate_b=round(kb / n, 6),
        rate_a_pct=round(ka / n * 100, 2), rate_b_pct=round(kb / n * 100, 2),
        delta_pp=round((kb - ka) / n * 100, 2),
        ci_a=wilson(ka, n), ci_b=wilson(kb, n),
        mcnemar_b=b, mcnemar_c=c, discordant=b + c,
        p_exact=p,
        direction=('improved' if kb > ka else ('worse' if kb < ka else 'flat')),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ptype', default='M1_omission')
    ap.add_argument('--judge', default='Qwen_Qwen3-14B')
    ap.add_argument('--results-dir', default='03_results')
    ap.add_argument('--tag', default='main')
    ap.add_argument('--alpha', type=float, default=0.05)
    args = ap.parse_args()

    results_dir = args.results_dir
    if not os.path.isabs(results_dir):
        results_dir = os.path.join(ROOT, results_dir)
    judge_dir = os.path.join(results_dir, 'judge_scores')
    metrics_dir = os.path.join(results_dir, 'metrics')
    os.makedirs(metrics_dir, exist_ok=True)

    dim = PTYPE_DIM[args.ptype]
    print(f'ptype = {args.ptype}   注入维度 = {dim}   判据阈值 <= {PERT_FLOOR:g}')
    print(f'judge = {args.judge}   results = {results_dir}\n')

    rows_by = {}
    for ver in RUNGS:
        rows, path = load_rows(judge_dir, ver, args.judge)
        if rows is None:
            print(f'  ✗ 缺文件 {path}')
            return
        rows_by[ver] = rows

    payload = dict(
        script='02_scripts/s16_mcnemar_targeted.py',
        judge_model=args.judge, group=args.tag,
        ptype=args.ptype, target_dim=dim, threshold=PERT_FLOOR,
        note=('targeted-absolute = injected dimension score <= 3 '
              '(NOT overall-score absolute); pairs keyed by case_id, '
              'one M1 pair per case at each rung'),
        metrics={},
    )

    for metric in ('abs', 'rel'):
        dets = {v: det_set(rows_by[v], args.ptype, dim, metric) for v in RUNGS}
        print(f'=== 口径 {metric} ===')
        for v in RUNGS:
            n = len(dets[v])
            k = sum(dets[v].values())
            print(f'  {v:<14} n={n:<4} detected={k:<4} ({k / n:.1%})')
        block = {}
        for a, b in CONTRASTS:
            r = compare(a, b, dets[a], dets[b])
            if r is None:
                continue
            r['p_bonferroni'] = min(1.0, r['p_exact'] * len(CONTRASTS))
            r['significant_raw'] = bool(r['p_exact'] < args.alpha)
            r['significant_bonferroni'] = bool(r['p_bonferroni'] < args.alpha)
            block[f'{a}->{b}'] = r
            print(f'  {a} -> {b}: {r["rate_a_pct"]:.1f}% -> {r["rate_b_pct"]:.1f}%  '
                  f'Δ={r["delta_pp"]:+.1f} pp  b={r["mcnemar_b"]} c={r["mcnemar_c"]}  '
                  f'p={r["p_exact"]:.3e}  p_bonf={r["p_bonferroni"]:.3e}')
        payload['metrics'][metric] = block
        print()

    payload['n_contrasts'] = len(CONTRASTS)
    payload['bonferroni_family'] = [f'{a}->{b}' for a, b in CONTRASTS]

    out = os.path.join(
        metrics_dir,
        f'mcnemar_targeted_{args.ptype}_{args.tag}.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f'→ {out}')


if __name__ == '__main__':
    main()
