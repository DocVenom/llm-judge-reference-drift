# -*- coding: utf-8 -*-
"""s18 — Table 11（overall-level 两判据）的精确 McNemar 复算。

背景
----
s06 的 `_mcnemar_p()` 在 n_disc >= 25 时改用「卡方 + 连续性校正」近似
（函数名却叫 mcnemar exact），因此 compare_*.json 里存的 p 是近似值
（例：v1→v2 abs 存 3e-06，精确值 9.43e-07）。
论文 Table 11 统一用双侧精确二项检验，故本脚本独立重算一遍。

判据定义（与 00_config/config.py 的 DETECT_RULES 一致）
------------------------------------------------------
drop : score_original - score_perturbed >= 1.0   （overall 降分检出）
abs  : score_perturbed <= 3                       （overall 绝对阈值检出）

统计
----
双侧精确检验：p = min(1, 2 * sum_{i<=min(b,c)} C(n,i) / 2^n)，n = b + c
b = 仅第二条件检出，c = 仅第一条件检出（与 s06 的 mcnemar_b_only 同向）

输出
----
03_results/metrics/table11_exact_p.json

用法
----
python 02_scripts/s18_table11_exact.py
"""

import glob
import json
import os
import sys
from fractions import Fraction
from math import comb

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, '00_config'))
import config as C  # noqa: E402

JUDGE_DIR = C.JUDGE
OUT_PATH = os.path.join(C.METRICS, 'table11_exact_p.json')

JUDGE_TAG = 'Qwen_Qwen3-14B'
DROP = 1.0
FLAG = 3
VERSIONS = ('v1_baseline', 'v2_reword', 'v3_checklist')
PAIRS = (('v1_baseline', 'v2_reword'),
         ('v2_reword', 'v3_checklist'),
         ('v1_baseline', 'v3_checklist'))
RULES = ('drop', 'abs')


def load(ver):
    """按 judge tag 精确过滤，避免异族 judge 文件串入（s06 2026-10-13 修过的坑）。"""
    pat = os.path.join(JUDGE_DIR, f'judge_*_{JUDGE_TAG}_{ver}.jsonl')
    files = sorted(glob.glob(pat))
    recs = []
    for f in files:
        with open(f, encoding='utf-8') as fh:
            for ln in fh:
                try:
                    recs.append(json.loads(ln))
                except Exception:
                    pass
    return files, recs


def detect_set(recs, rule):
    """{case_id|ptype: 0/1}。原答分内联在 score_original，旧格式才回落单列记录。"""
    orig = {}
    for r in recs:
        if r.get('ptype') is None and r.get('score') is not None:
            orig[r['case_id']] = float(r['score'])

    det = {}
    for r in recs:
        if r.get('ptype') is None:
            continue
        sp = r.get('score')
        if sp is None:
            continue
        sp = float(sp)
        key = f"{r['case_id']}|{r['ptype']}"
        if rule == 'abs':
            det[key] = int(sp <= FLAG)
            continue
        so = r.get('score_original')
        if so is None:
            so = orig.get(r['case_id'])
        if so is None:
            continue
        det[key] = int((float(so) - sp) >= DROP)
    return det


def exact_p(b, c):
    n = b + c
    if n == 0:
        return 1.0
    m = min(b, c)
    p = Fraction(2 * sum(comb(n, i) for i in range(m + 1)), 2 ** n)
    return float(min(Fraction(1), p))


def main():
    cache, files_map = {}, {}
    for ver in VERSIONS:
        files, recs = load(ver)
        cache[ver], files_map[ver] = recs, [os.path.basename(f) for f in files]
        print(f'[load] {ver}: {len(recs)} 条记录 <- {files_map[ver]}')

    rows = []
    for a, b in PAIRS:
        for rule in RULES:
            A, B = detect_set(cache[a], rule), detect_set(cache[b], rule)
            keys = sorted(set(A) & set(B))
            b_only = sum(1 for k in keys if A[k] == 0 and B[k] == 1)
            c_only = sum(1 for k in keys if A[k] == 1 and B[k] == 0)
            both = sum(1 for k in keys if A[k] == 1 and B[k] == 1)
            sa = sum(A[k] for k in keys) / len(keys)
            sb = sum(B[k] for k in keys) / len(keys)
            p = exact_p(b_only, c_only)
            rows.append(dict(
                comparison=f'{a} -> {b}', criterion=rule, n_pairs=len(keys),
                det_a=sum(A[k] for k in keys), det_b=sum(B[k] for k in keys),
                sens_a=round(sa, 4), sens_b=round(sb, 4),
                gain_pp=round((sb - sa) * 100, 2),
                b_only_second=b_only, c_only_first=c_only, both=both,
                neither=sum(1 for k in keys if not A[k] and not B[k]),
                n_discordant=b_only + c_only,
                exact_p=p, significant=bool(p < 0.05),
            ))

    # Holm（族 = 6 个检验；结论方向不应因校正而改变）
    order = sorted(range(len(rows)), key=lambda i: rows[i]['exact_p'])
    m = len(rows)
    prev = 0.0
    for rank, i in enumerate(order):
        adj = min(1.0, (m - rank) * rows[i]['exact_p'])
        prev = max(prev, adj)
        rows[i]['holm_adjusted_p'] = prev
    bonf = 0.05 / m
    for r in rows:
        r['bonferroni_alpha'] = round(bonf, 6)
        r['significant_bonferroni'] = bool(r['exact_p'] < bonf)

    print('\n{:<40} {:<5} {:>7} {:>9} {:>4} {:>4} {:>12}'.format(
        'comparison', 'crit', 'n', 'gain_pp', 'b', 'c', 'exact_p'))
    for r in rows:
        print('{:<40} {:<5} {:>7} {:>+9.2f} {:>4} {:>4} {:>12.3e}'.format(
            r['comparison'], r['criterion'], r['n_pairs'], r['gain_pp'],
            r['b_only_second'], r['c_only_first'], r['exact_p']))

    payload = dict(
        note='Table 11 exact McNemar (two-sided doubling); independent of s06 chi-square branch',
        judge_model=JUDGE_TAG, drop_threshold=DROP, flag_score=FLAG,
        source_files=files_map, rows=rows,
    )
    with open(OUT_PATH, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f'\n-> {OUT_PATH}')


if __name__ == '__main__':
    main()
