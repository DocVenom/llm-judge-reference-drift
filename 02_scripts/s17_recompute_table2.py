#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""s17_recompute_table2.py — 从原始判分 JSONL 重建 Table 2 全部 28 行

不读任何中间产物（compare_*.json / table2_mcnemar.md），直接按 s06 的判定
规则重建检出集合，再算精确 McNemar。目的：核验已发表的 p 值。

判定规则（与 s06_compute_metrics.py 完全一致）
----------------------------------------------
  drop : score_original - score_perturbed >= DROP_THRESHOLD (=1.0)
  abs  : score_perturbed(overall) <= FLAG_SCORE (=3)

配对单位： `case_id|ptype`，两版取交集。

b / c 定义（本脚本统一按论文 §3.7 的写法）
-----------------------------------------
  b = first-condition-only   (A=1, B=0)
  c = second-condition-only  (A=0, B=1)
  Δ = rate_B - rate_A（百分点）

⚠️ s06 / Figure 5 用的是相反命名（b_only 指 second-only）。本脚本在输出里
   同时给出 `b_first_only` 与 `c_second_only`，避免歧义。

精确检验： p = min(1, 2 * Σ_{i<=min(b,c)} C(n,i) / 2^n)，n = b + c。
"""
import json
import os
import sys
from math import comb

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DROP_THRESHOLD = 1.0
FLAG_SCORE = 3.0

# (group, results_root, judge_tag, a, b)
COMPARISONS = [
    ('main', '03_results', 'Qwen_Qwen3-14B', 'v1_baseline', 'v3_checklist'),
    ('main', '03_results', 'Qwen_Qwen3-14B', 'v2_reword', 'v3_checklist'),
    ('main', '03_results', 'Qwen_Qwen3-14B', 'v1_baseline', 'v2_reword'),
    ('main', '03_results', 'Qwen_Qwen3-14B', 'v3_no_cc', 'v3_checklist'),
    ('main', '03_results', 'Qwen_Qwen3-14B', 'v3_no_ce', 'v3_checklist'),
    ('main', '03_results', 'Qwen_Qwen3-14B', 'v1_baseline', 'v5_checkeval'),
    ('main', '03_results', 'Qwen_Qwen3-14B', 'v2_reword', 'v5_checkeval'),
    ('main', '03_results', 'Qwen_Qwen3-14B', 'v3_checklist', 'v5_checkeval'),
    ('e2', '03_results', 'internlm_internlm2_5-7b-chat', 'v1_baseline', 'v3_checklist'),
    ('e2', '03_results', 'internlm_internlm2_5-7b-chat', 'v2_reword', 'v3_checklist'),
    ('e2', '03_results', 'internlm_internlm2_5-7b-chat', 'v1_baseline', 'v2_reword'),
    ('e3', '03_results/e3_HuatuoGPT-3-8B', 'Qwen_Qwen3-14B', 'v1_baseline', 'v3_checklist'),
    ('e3', '03_results/e3_HuatuoGPT-3-8B', 'Qwen_Qwen3-14B', 'v2_reword', 'v3_checklist'),
    ('e3', '03_results/e3_HuatuoGPT-3-8B', 'Qwen_Qwen3-14B', 'v1_baseline', 'v2_reword'),
]


def load(group_root, tag, ver):
    p = os.path.join(ROOT, group_root, 'judge_scores', f'judge_vllm_{tag}_{ver}.jsonl')
    if not os.path.exists(p):
        raise SystemExit(f'缺文件: {p}')
    out = []
    with open(p, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out, p


def det_set(rows, rule):
    """{case_id|ptype: 0/1}，跳过 ptype 为空的原答记录。"""
    d = {}
    for r in rows:
        if r.get('ptype') is None:
            continue
        sp = r.get('score')
        so = r.get('score_original')
        if sp is None:
            continue
        key = f"{r['case_id']}|{r['ptype']}"
        if rule == 'abs':
            d[key] = int(float(sp) <= FLAG_SCORE)
        else:
            if so is None:
                continue
            d[key] = int((float(so) - float(sp)) >= DROP_THRESHOLD)
    return d


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(comb(n, i) for i in range(k + 1)) / (2 ** n))


def chi2p(b, c):
    """s06 的旧实现：n>=25 时卡方 + 连续性校正。仅用于对照。"""
    from math import erfc, sqrt
    n = b + c
    if n == 0:
        return 1.0
    chi2 = (abs(b - c) - 1) ** 2 / n
    return min(1.0, erfc(sqrt(chi2 / 2)))


def main():
    cache = {}
    rows_out = []
    for group, root, tag, va, vb in COMPARISONS:
        for ver in (va, vb):
            if (root, tag, ver) not in cache:
                cache[(root, tag, ver)] = load(root, tag, ver)[0]
        for rule in ('drop', 'abs'):
            A = det_set(cache[(root, tag, va)], rule)
            B = det_set(cache[(root, tag, vb)], rule)
            keys = sorted(set(A) & set(B))
            n = len(keys)
            b = sum(1 for k in keys if A[k] == 1 and B[k] == 0)   # first-only
            c = sum(1 for k in keys if A[k] == 0 and B[k] == 1)   # second-only
            ka = sum(A[k] for k in keys)
            kb = sum(B[k] for k in keys)
            pe = mcnemar_exact(b, c)
            rows_out.append(dict(
                group=group, comparison=f'{va} -> {vb}', criterion=rule,
                n=n, det_a=ka, det_b=kb,
                rate_a_pct=round(ka / n * 100, 2) if n else None,
                rate_b_pct=round(kb / n * 100, 2) if n else None,
                delta_pp=round((kb - ka) / n * 100, 2) if n else None,
                b_first_only=b, c_second_only=c, discordant=b + c,
                p_exact=pe, p_chi2_approx=chi2p(b, c),
                sig_exact_05=bool(pe < 0.05),
                improved=bool(pe < 0.05 and kb > ka),
            ))
            print(f'{group:<5} {va:<12}->{vb:<14} {rule:<5} n={n:<4} '
                  f'{ka:>3}/{n}={ka/n*100:5.1f}% -> {kb:>3}/{n}={kb/n*100:5.1f}%  '
                  f'b={b:<3} c={c:<3} Δ={(kb-ka)/n*100:+7.2f}pp  '
                  f'p_exact={pe:.3e}  p_chi2={chi2p(b,c):.3e}')

    out = os.path.join(ROOT, '03_results', 'metrics', 'table2_recomputed.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(dict(script='02_scripts/s17_recompute_table2.py',
                       drop_threshold=DROP_THRESHOLD, flag_score=FLAG_SCORE,
                       b_definition='first-condition-only',
                       c_definition='second-condition-only',
                       n_rows=len(rows_out), rows=rows_out),
                  fh, ensure_ascii=False, indent=2)
    print(f'\n共 {len(rows_out)} 行 → {out}')


if __name__ == '__main__':
    main()
