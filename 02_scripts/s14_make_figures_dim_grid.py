#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""s14_make_figures_dim_grid.py —— 补图（第四批）：法官行为证据三张

前几批图覆盖了「检出率 / 权衡 / 参考点漂移 / 聚合丢信号」四个层面。
本脚本补的是**法官内部行为**的三张证据：

  fig10  Paired 2×2 confusion grid —— v1 vs v3 的**配对四格**，两个口径各一格：
         降分口径（左上重）与绝对口径（左下重）方向相反 —— 支撑 §5.2 的图解。
         （数据：judge_scores/*.jsonl 配对；并与 metrics/compare_*.json 的 b/c 交叉核对）
  fig11  Dimension-level heatmap —— 10 个维度（7 Block + 3 Cross）× v1–v4 的**均分矩阵**，
         左面板原答、右面板扰动答。回答审稿人最常追问的一句：
         「尺度上移是全维度的，还是某几个维度被抬高？」—— 支撑 §5.3。
         （数据：judge_scores/*.jsonl 的 dims_original / dims）
  fig_supp_weak_judge —— 弱法官（internlm2.5-7B）退化证据（论文 Figure S3）：
         (a) 287 条扰动答的整体分分布（v3 塌成 272 条 5.0）
         (b) 输出契约合规率（是否恰好给出 10 个规定维度键）
         （数据：judge_scores/judge_vllm_internlm_*_v{1,2,3}_*.jsonl）

★ 图号约定（2026-10-22 更正，此前两轮注释均已失效）：
  **文件名 ≠ 论文图号。** `fig10_*` / `fig11_*` 是生成批次序号，论文重排后实际对应：
    fig10_paired_confusion    → 论文 **Figure 5**（§5.2 配对四格）
    fig11_dimension_heatmap   → 论文 **Figure 8**（§5.3 维度级热力图）
    fig_supp_weak_judge       → 论文 **Figure S3**（§5.6 弱法官退化）
  单一事实来源：`02_scripts/s12_audit_figures.py` 的 `FIGURE_MAP` / `SUPP_MAP`，
  并由 `s12` 机械核验「题注 File: 字段 ↔ 附录 D ↔ FIGURE_MAP」三处一致 +
  「图比脚本旧」「脚本不存在」两类失败项。**改图号时只改 s12 与论文，不要在此处另立一套。**

约定：
  · 只读 `03_results/`，不重跑实验、不需 GPU、不联网；
  · 图内文字**全英文**（s12 体检项 E 禁 CJK），维度键的中文名一律映射成英文标签；
  · 与 s09/s10/s13 同一套配色：灰 #999999 / 蓝 #0072B2 / 橙 #D55E00 / 冷蓝 #4C6EA8；
  · 所有叠在 imshow 上的分隔线都打 `gid='leader'`，否则会被 s12 的「文字压曲线」误判。

用法：
    python 02_scripts/s14_make_figures_dim_grid.py
    python 02_scripts/s14_make_figures_dim_grid.py --out 05_paper/figures

产物：
    <out>/fig10_paired_confusion.{png,pdf}  + <out>/fig10_data.csv
    <out>/fig11_dimension_heatmap.{png,pdf} + <out>/fig11_data.csv
    <out>/fig_supp_weak_judge.{png,pdf}     + <out>/fig_supp_weak_judge_data.csv
"""
import os
import csv
import sys
import json
import argparse

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RES = os.path.join(ROOT, '03_results')
JS = os.path.join(RES, 'judge_scores')
sys.path.insert(0, HERE)          # 同目录的排印规范模块
import figure_style as FST       # noqa: E402

MAIN_JUDGE = 'Qwen_Qwen3-14B'
WEAK_JUDGE = 'internlm_internlm2_5-7b-chat'

LADDER = ['v1_baseline', 'v2_reword', 'v3_checklist', 'v4_aggregate']
VSHORT = {'v1_baseline': 'v1', 'v2_reword': 'v2',
          'v3_checklist': 'v3', 'v4_aggregate': 'v4'}
VNAME = {'v1_baseline': 'v1 baseline', 'v2_reword': 'v2 reword',
         'v3_checklist': 'v3 checklist', 'v4_aggregate': 'v4 aggregate'}

# 10 个维度：judge_scores 里的中文键 → 论文用英文标签（7 Block + 3 Cross，顺序固定）
DIMS = ['病因病机_Block', '证候诊断_Block', '治则治法_Block', '处方生成_Block',
        '处方解析_Block', '症状变化推理_Block', '处方调整_Block',
        '病机_证候_Cross', '证候_治法_Cross', '治法_处方_Cross']
DIM_LABEL = ['Etiology & pathogenesis', 'Syndrome diagnosis', 'Treatment principle',
             'Prescription generation', 'Prescription analysis',
             'Symptom-change reasoning', 'Prescription adjustment',
             'Pathogenesis $\\rightarrow$ syndrome',
             'Syndrome $\\rightarrow$ treatment',
             'Treatment $\\rightarrow$ prescription']
N_BLOCK = 7                     # 前 7 行是 Block 维，后 3 行是 Cross 维

# 配色（与全篇一致）
C_V1, C_V2, C_V3 = '#999999', '#0072B2', '#D55E00'
C_MAIN, C_WEAK = '#4C6EA8', '#D55E00'
CMAP_DIM = LinearSegmentedColormap.from_list(
    'paperblue', ['#F5F9FC', '#C9DCEF', '#8FB4D9', '#5B8FC7', '#2E5F9E'])

# ★ 2026-10-16 第四轮（排印统一）：字号与柱体框线一律取自 `figure_style.py`。
FS_VAL = FST.FS_VALUE           # 格内次要数字 / 柱顶数值
FS_TICK = FST.FS_TICK
FS_TITLE = FST.FS_TITLE
FS_CELL = FST.FS_CELL           # 格内主数字（原先 9.5 pt 明显偏大，已回调到 8.0）
BAR_KW = FST.BAR_EDGE_KW        # edgecolor='black', linewidth=0.4

FST.apply_rcparams({'mathtext.fontset': 'dejavusans'})


# ------------------------------------------------------------------ 数据
def load_dims(judge, ver):
    """返回 {dim: {'orig': {'n','mean'}, 'pert': {'n','mean'}}}（只读，缺失键自动跳过）。"""
    p = os.path.join(JS, 'judge_vllm_%s_%s.jsonl' % (judge, ver))
    acc = {d: {'orig': [], 'pert': []} for d in DIMS}
    for line in open(p, encoding='utf-8'):
        r = json.loads(line)
        for key, side in (('dims_original', 'orig'), ('dims', 'pert')):
            blk = r.get(key) or {}
            for d in DIMS:
                x = blk.get(d)
                if isinstance(x, (int, float)):
                    acc[d][side].append(float(x))
    out = {}
    for d in DIMS:
        out[d] = {}
        for side in ('orig', 'pert'):
            xs = acc[d][side]
            out[d][side] = {'n': len(xs),
                            'mean': (sum(xs) / len(xs)) if xs else float('nan')}
    return out


def keyed(judge, ver):
    """{(case_id, ptype): record} —— 用于 v1 vs v3 的配对对齐。"""
    p = os.path.join(JS, 'judge_vllm_%s_%s.jsonl' % (judge, ver))
    d = {}
    for line in open(p, encoding='utf-8'):
        r = json.loads(line)
        d[(r['case_id'], r['ptype'])] = r
    return d


def _detected(rec, criterion):
    """criterion: 'drop' = 原答 − 扰动答 ≥ 1（整体降分口径）；'abs' = 扰动答 overall ≤ 3。"""
    if criterion == 'drop':
        o, p = rec.get('score_original'), rec.get('score')
        return int(isinstance(o, (int, float)) and isinstance(p, (int, float))
                   and (o - p) >= 1.0)
    p = rec.get('score')
    return int(isinstance(p, (int, float)) and p <= 3)


def load_overall_scores(judge, ver):
    """扰动答 overall 分布（Counters）。"""
    p = os.path.join(JS, 'judge_vllm_%s_%s.jsonl' % (judge, ver))
    c = {}
    for line in open(p, encoding='utf-8'):
        r = json.loads(line)
        s = r.get('score')
        if isinstance(s, (int, float)):
            c[float(s)] = c.get(float(s), 0) + 1
    return c


def load_contract(judge, ver):
    """输出契约合规率：`dims`（扰动答）恰好给出规定的 10 个维度键的记录占比。"""
    p = os.path.join(JS, 'judge_vllm_%s_%s.jsonl' % (judge, ver))
    n = ok = 0
    for line in open(p, encoding='utf-8'):
        r = json.loads(line)
        n += 1
        if set((r.get('dims') or {}).keys()) == set(DIMS):
            ok += 1
    return ok, n


def _cell_text_color(val, vmin, vmax, cmap):
    """按单元底色亮度自动选黑/白字 —— 比写死阈值靠谱（深底白字、浅底黑字）。"""
    norm = (val - vmin) / float(vmax - vmin)
    r, g, b = cmap(min(max(norm, 0.0), 1.0))[:3]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return 'white' if lum < 0.55 else '#1A1A1A'


# ------------------------------------------------------------------ fig11
def fig11(outdir):
    """论文 Figure 8 —— 维度级均分热力图（10 维 × v1–v4，左原答 / 右扰动答）。"""
    data = {v: load_dims(MAIN_JUDGE, v) for v in LADDER}
    vmin, vmax = 3.5, 5.0

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.45), sharey=True,
                             gridspec_kw={'width_ratios': [1, 1]})
    im = None
    for k, (side, subt) in enumerate((('orig', 'Original answers'),
                                      ('pert', 'Perturbed answers'))):
        ax = axes[k]
        M = np.full((len(DIMS), len(LADDER)), np.nan)
        for j, v in enumerate(LADDER):
            for i, d in enumerate(DIMS):
                M[i, j] = data[v][d][side]['mean']
        im = ax.imshow(M, cmap=CMAP_DIM, vmin=vmin, vmax=vmax, aspect='auto')
        for i in range(len(DIMS)):
            for j in range(len(LADDER)):
                val = M[i, j]
                if np.isnan(val):
                    continue
                ax.text(j, i, '%.2f' % val, ha='center', va='center',
                        fontsize=FS_VAL,
                        color=_cell_text_color(val, vmin, vmax, CMAP_DIM))
        # Block / Cross 分隔线（gid='leader'：s12 的「文字压曲线」据此跳过）
        ax.axhline(N_BLOCK - 0.5, color='white', lw=2.0, zorder=3, gid='leader')
        ax.set_xticks(range(len(LADDER)))
        ax.set_xticklabels([VSHORT[v] for v in LADDER], fontsize=FS_TICK)
        ax.set_title('(%s)  %s' % ('ab'[k], subt), fontsize=FS_TITLE)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(length=0)

    axes[0].set_yticks(range(len(DIMS)))
    axes[0].set_yticklabels(DIM_LABEL, fontsize=FS_TICK)
    axes[0].set_ylabel('Rubric dimension', fontsize=7.6)
    axes[1].tick_params(labelleft=False)

    # colorbar 必须**在 tight_layout 之后**用手工 axes 放，否则会和右面板的单元文字重叠
    # （matplotlib 的 tight_layout 不认识 colorbar 的 axes —— 会报 "not compatible" 警告）
    fig.tight_layout()
    fig.subplots_adjust(right=0.895)
    pos = axes[1].get_position()
    cax = fig.add_axes([0.912, pos.y0, 0.013, pos.height])
    cb = fig.colorbar(im, cax=cax)
    cb.set_label('Mean dimension score (0$-$5)', fontsize=7.0)
    cb.ax.tick_params(labelsize=FS_VAL, length=2)
    cb.outline.set_visible(False)

    # ★ 2026-10-17（第 14 问 / 布局体检）：**图内说明必须短到不撑画布**。
    #   原来这里是一整行约 165 字符：`(White line separates ...)   n = 287 per cell
    #   for v1-v3; for v4, Block n = 287 and Cross n = 127 (original) / 91 (perturbed).`
    #   实测该 Text 的右端到 2463 px，而画布只有 2220 px —— **超出 243 px = 20.6 mm**。
    #   `bbox_inches='tight'` 会把画布外的 artist 也纳入边界，于是 PDF 被撑到
    #   **212.9 mm**，超出 Elsevier 通栏 190 mm 的上限 22.9 mm（投稿时会被强制缩，
    #   连带最小字号跌破 6 pt）。
    #   ⇒ 统计口径那句（n=287 / v4 的 127/91）**题注里已经写明**（Figure 8 题注末句），
    #      Elsevier 也明确要求图注写进正文而非图片，故此处只保留读者读图必需的
    #     "白线是什么"这一句；单行约 1100 px，远小于画布宽。
    fig.text(0.004, -0.048,
             '(White line separates the 7 Block dimensions from the 3 Cross dimensions.)',
             fontsize=FS_VAL, color='#444444', ha='left', va='top')
    FST.save_fig(fig, outdir, 'fig11_dimension_heatmap')
    plt.close(fig)

    with open(os.path.join(outdir, 'fig11_data.csv'), 'w', newline='',
              encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['rung', 'side', 'dim_key', 'dim_label', 'group', 'n', 'mean'])
        for v in LADDER:
            for side, side_name in (('orig', 'original'), ('pert', 'perturbed')):
                for i, d in enumerate(DIMS):
                    cell = data[v][d][side]
                    w.writerow([v, side_name, d, DIM_LABEL[i].replace('$\\rightarrow$', '->'),
                                'Block' if i < N_BLOCK else 'Cross',
                                cell['n'], '%.4f' % cell['mean']])
    print('✓ fig11_dimension_heatmap + fig11_data.csv')
    return data


# ------------------------------------------------------------------ fig10
def fig10(outdir):
    """论文 Figure 5 —— 配对 2×2 混淆网格（v1 vs v3，降分口径 / 绝对口径）。"""
    k1, k3 = keyed(MAIN_JUDGE, 'v1_baseline'), keyed(MAIN_JUDGE, 'v3_checklist')
    common = sorted(set(k1) & set(k3))
    n = len(common)

    grids, meta = {}, {}
    for crit in ('drop', 'abs'):
        g = np.zeros((2, 2), dtype=int)
        for k in common:
            g[1 - _detected(k1[k], crit), 1 - _detected(k3[k], crit)] += 1
        grids[crit] = g
        a = int(g[0].sum() + g[1].sum())            # 冗余但显式
        sens1 = (g[0, 0] + g[0, 1]) / float(n)
        sens3 = (g[0, 0] + g[1, 0]) / float(n)
        meta[crit] = {'b': int(g[1, 0]), 'c': int(g[0, 1]),
                      'gain_pp': 100.0 * (sens3 - sens1)}

    # 与 compare_*.json 的 b/c 交叉核对（对不上就直接报错，不许悄悄画错）
    cp = json.load(open(os.path.join(RES, 'metrics',
                                     'compare_v1_baseline_vs_v3_checklist.json'),
                        encoding='utf-8'))
    for crit, key in (('drop', 'drop'), ('abs', 'abs')):
        node = cp['results'][key]
        assert meta[crit]['b'] == node['mcnemar_b_only'], (crit, meta[crit], node)
        assert meta[crit]['c'] == node['mcnemar_c_only'], (crit, meta[crit], node)
        meta[crit]['p'] = node['p_value']

    # ★ 面板间距交给 tight_layout：不要在 gridspec_kw 里写 wspace ——
    #   一旦 gridspec 自带 wspace，matplotlib 的 tight_layout 会报
    #   "Axes that are not compatible" 并放弃排布（实测确认）。
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.15))
    p_txt = {'drop': 'p = 4.0e$-$4', 'abs': 'p $\\approx$ 0'}
    for k, crit in enumerate(('drop', 'abs')):
        ax = axes[k]
        g = grids[crit]
        ax.imshow(g, cmap=CMAP_DIM, vmin=0, vmax=float(g.max()), aspect='auto')
        tot = float(g.sum())
        for i in range(2):
            for j in range(2):
                cnt = int(g[i, j])
                tc = _cell_text_color(cnt, 0.0, float(g.max()), CMAP_DIM)
                ax.text(j, i - 0.16, '%d' % cnt, ha='center', va='center',
                        fontsize=FS_CELL, color=tc)
                ax.text(j, i + 0.04, '(%.1f%%)' % (100.0 * cnt / tot),
                        ha='center', va='center', fontsize=FS_VAL, color=tc)
        # 不一致的两格（McNemar 的 b / c）标出来 —— 它们就是「方向相反」的来源。
        # ★ 标签必须写在**格子内部**：写在坐标区外会被 s12 的「标注越出坐标区」判失败，
        #   而且会撞上面板标题（imshow 的行号向下增加，负 y 是「更上面」）。
        for (i, j, tag, lab) in ((1, 0, 'b', 'v3 only'), (0, 1, 'c', 'v1 only')):
            tc = _cell_text_color(g[i, j], 0.0, float(g.max()), CMAP_DIM)
            ax.text(j, i + 0.34, lab, ha='center', va='center',
                    fontsize=FS_VAL, color=tc)
            ax.text(j + 0.42, i - 0.40, tag, ha='right', va='top', fontsize=FST.FS_TICK,
                    color=tc, fontweight='bold')
        ax.set_xticks([0, 1])
        ax.set_xticklabels(['detected', 'not detected'], fontsize=FS_TICK)
        ax.set_yticks([0, 1])
        ax.set_yticklabels(['v1\ndetected', 'v1\nnot detected'], fontsize=FS_TICK)
        ax.set_xlabel('v3 checklist verdict', fontsize=FST.FS_AXLABEL)
        if k == 0:
            ax.set_ylabel('v1 baseline verdict', fontsize=FST.FS_AXLABEL)
        ax.set_title('(%s)  %s criterion\n$\\Delta$ = %s pp   (McNemar %s)'
                     % ('ab'[k], 'Drop' if crit == 'drop' else 'Absolute',
                        ('%+.1f' % meta[crit]['gain_pp']).replace('-', '$-$'),
                        p_txt[crit]), fontsize=FS_TITLE)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(length=0)

    fig.tight_layout()
    fig.text(0.005, -0.055,
             'Cell = number of the 287 paired items (share of all pairs);  '
             'b / c = the two discordant cells (McNemar).',
             fontsize=FS_VAL, color='#444444', ha='left', va='top')
    FST.save_fig(fig, outdir, 'fig10_paired_confusion')
    plt.close(fig)

    with open(os.path.join(outdir, 'fig10_data.csv'), 'w', newline='',
              encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['criterion', 'v1_detected', 'v3_detected', 'count', 'share_pct',
                    'mcnemar_b_v3_only', 'mcnemar_c_v1_only', 'gain_pp', 'p_value'])
        for crit in ('drop', 'abs'):
            g = grids[crit]
            for i in (0, 1):
                for j in (0, 1):
                    w.writerow([crit, 'yes' if i == 0 else 'no',
                                'yes' if j == 0 else 'no', int(g[i, j]),
                                '%.2f' % (100.0 * g[i, j] / float(g.sum())),
                                meta[crit]['b'], meta[crit]['c'],
                                '%.2f' % meta[crit]['gain_pp'],
                                '%.6g' % meta[crit]['p']])
    print('✓ fig10_paired_confusion + fig10_data.csv')
    return grids, meta


# ------------------------------------------------------------------ figS3
def figS3(outdir):
    weak = {v: load_overall_scores(WEAK_JUDGE, v)
            for v in ('v1_baseline', 'v2_reword', 'v3_checklist')}
    strong = {v: load_overall_scores(MAIN_JUDGE, v)
              for v in ('v1_baseline', 'v2_reword', 'v3_checklist')}
    vers = ['v1_baseline', 'v2_reword', 'v3_checklist']

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.05),
                             gridspec_kw={'width_ratios': [1.32, 1.0]})

    # (a) 弱法官整体分分布（分组柱；v3 塌成 5.0）
    ax = axes[0]
    buckets = sorted({s for v in vers for s in weak[v]})
    xs = np.arange(len(buckets))
    bw = 0.26
    for m, (v, col) in enumerate(zip(vers, (C_V1, C_V2, C_V3))):
        tot = float(sum(weak[v].values()))
        sh = [100.0 * weak[v].get(s, 0) / tot for s in buckets]
        ax.bar(xs + (m - 1) * bw, sh, bw, color=col, zorder=2, label=VNAME[v],
               **BAR_KW)
        # ★ 2026-10-16（用户第 2 项）：原来数字横排——`76.7` 宽 59 px 而柱宽只有 35 px，
        #   横向溢出 23.6 px，直接压到相邻柱身上。改成**竖排（rotation=90）**：
        #   文字宽度骤降到约 12 px，远窄于柱宽，结构上不可能侵入邻柱；
        #   同时字号按用户第 3 项**放大**到全篇数值标签档（6.8 pt），更易读。
        # ★ 2026-10-16（用户追问"为什么有的柱没有标注"）：原来还有一道 `if y >= 6.0` 的
        #   **门槛**，于是 15 根非零柱里有 9 根不标数字（最小的一根 0.35% 看得见却没数）。
        #   竖排既已解决横向溢出，这道门槛就没有保留理由 —— 改为**所有非零柱都标**。
        #   实测（改后）：21 根柱里 15 个非零柱全标，0 重叠、0 侵入他柱。
        for x, y in zip(xs + (m - 1) * bw, sh):
            if y <= 0.0:
                continue          # 真·零柱（该分数档无样本）不标，否则满屏 0.0 更乱
            ax.text(x, y + 2.4, '%.1f' % y, ha='center', va='bottom',
                    fontsize=FS_VAL, color='#333333', rotation=90)
    ax.set_xticks(xs)
    ax.set_xticklabels(['%.1f' % s for s in buckets], fontsize=FS_TICK)
    ax.set_xlabel('Overall score assigned to the perturbed answer',
                  fontsize=FST.FS_AXLABEL)
    ax.set_ylabel('Share of the 287 answers (%)', fontsize=FST.FS_AXLABEL)
    # ★ 用户第 12 问：纵轴上限 **138 → 118**（使最高刻度从 120 降到 100）。
    #   原来 138 是为了给顶部横排图例让位，但最高柱只有 94.8%，竖排数值标签
    #   顶到约 108 ⇒ 108~126 之间空出一整条，柱体与图例离得远、图显空。
    #   压到 118 后：刻度为 0/20/…/100，94.8 的标签顶 ≈108，图例底 ≈109，
    #   两者紧邻但不重叠（实测间隙见下方 s12 体检的"压图例"项）。
    ax.set_ylim(0, 118)
    ax.set_title('(a)  Weak judge (internlm2.5-7B): score distribution by rung',
                 fontsize=FS_TITLE)
    ax.legend(fontsize=FST.FS_LEGEND, loc='upper center', frameon=False, ncol=3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # (b) 输出契约合规率（是否恰好 10 个规定维度键）
    axb = axes[1]
    comp = {j: [100.0 * load_contract(j, v)[0] / load_contract(j, v)[1] for v in vers]
            for j in (MAIN_JUDGE, WEAK_JUDGE)}
    xs2 = np.arange(len(vers))
    bw2 = 0.32
    axb.bar(xs2 - bw2 / 2, comp[MAIN_JUDGE], bw2, color=C_MAIN, zorder=2,
            label='Qwen3-14B (main)', **BAR_KW)
    axb.bar(xs2 + bw2 / 2, comp[WEAK_JUDGE], bw2, color=C_WEAK, zorder=2,
            label='internlm2.5-7B (weak)', **BAR_KW)
    for x, y in zip(xs2 - bw2 / 2, comp[MAIN_JUDGE]):
        axb.text(x, y + 2.4, '%.0f' % y, ha='center', va='bottom',
                 fontsize=FS_VAL, color='#333333')
    for x, y in zip(xs2 + bw2 / 2, comp[WEAK_JUDGE]):
        axb.text(x, y + 2.4, '%.0f' % y, ha='center', va='bottom',
                 fontsize=FS_VAL, color='#333333')
    axb.set_xticks(xs2)
    axb.set_xticklabels([VSHORT[v] for v in vers], fontsize=FS_TICK)
    axb.set_ylim(0, 138)
    axb.set_ylabel('Records matching the 10-key\noutput contract (%)',
                   fontsize=FST.FS_AXLABEL)
    axb.set_title('(b)  Output-contract compliance', fontsize=FS_TITLE)
    axb.legend(fontsize=FST.FS_LEGEND, loc='upper center', frameon=False, ncol=1)
    axb.spines['top'].set_visible(False)
    axb.spines['right'].set_visible(False)

    fig.tight_layout()
    fig.text(0.005, -0.055,
             'n = 287 perturbed answers per rung;  `parse_failed` = False and '
             '`api_ok` = True for every weak-judge record.',
             fontsize=FST.FS_NOTE, color='#444444', ha='left', va='top')
    FST.save_fig(fig, outdir, 'fig_supp_weak_judge')
    plt.close(fig)

    with open(os.path.join(outdir, 'fig_supp_weak_judge_data.csv'), 'w',
              newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['block', 'rung', 'key', 'count', 'share_pct'])
        for v in vers:
            tot = float(sum(weak[v].values()))
            for s in sorted(weak[v]):
                w.writerow(['score_dist_weak_judge', v, '%.1f' % s, weak[v][s],
                            '%.2f' % (100.0 * weak[v][s] / tot)])
        for j, jn in ((MAIN_JUDGE, 'Qwen3-14B'), (WEAK_JUDGE, 'internlm2.5-7B')):
            for v in vers:
                ok, nn = load_contract(j, v)
                w.writerow(['contract_compliance', v, jn, ok, '%.2f' % (100.0 * ok / nn)])
    print('✓ fig_supp_weak_judge + fig_supp_weak_judge_data.csv')
    return comp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join('05_paper', 'figures'))
    args = ap.parse_args()
    outdir = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(outdir, exist_ok=True)
    fig10(outdir)
    fig11(outdir)
    figS3(outdir)


if __name__ == '__main__':
    main()
