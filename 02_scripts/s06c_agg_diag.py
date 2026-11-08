# -*- coding: utf-8 -*-
"""
s06c_agg_diag.py —— 「聚合稀释」假设的零 GPU 诊断 + v4 方案可行性筛查

背景
----
RESEARCH_DESIGN 已把「聚合保留率 reflectance = P(整体分下降 | 该维度已被察觉)」
定为核心机制指标，并指出修复方向 = 「让 overall 反映局部缺陷」。
v4 = checklist + **显式聚合规则**（总分必须反映最弱维度）就是这条修复的干预实验。

但直接写「任一维度 ≤3 → overall ≤3」有**把特异度打穿**的风险：
v3 原答本身就常有若干维度落在 3（rubric 里 3 = 「部分合理，存在明显证据缺失」），
一刀切会把大量正常原答也压到 overall≤3 → 假阳性暴涨。

因此本脚本用**已有 v1/v2/v3 数据**先扫一遍聚合规则：
对每个阈值 k，算「缩放到 k 的检出/误报」，找出**既有增益又不伤特异度**的区间。
→ 结论直接决定 v4 的 prompt 该怎么写（甚至决定 v4 值不值得跑）。

用法（纯 CPU，本地/服务器均可）
    python 02_scripts/s06c_agg_diag.py
    python 02_scripts/s06c_agg_diag.py --versions v1_baseline,v2_reword,v3_checklist
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ★ 2026-10-13：结果根目录支持 TCMCR_RESULTS 覆盖（与 00_config/config.py 同口径）。
#   同 s06b：写死路径会在「换被测模型」那条链上读到主实验数据而不报错。
_R = os.environ.get('TCMCR_RESULTS') or os.path.join(ROOT, '03_results')
if not os.path.isabs(_R):
    _R = os.path.join(ROOT, _R)
JUDGE = os.path.join(_R, 'judge_scores')
METRICS = os.path.join(_R, 'metrics')

# 扰动类型 → 被注入扰动所在维度（定向维度）
PTYPE_DIM = {
    'M1_omission': '处方解析_Block',
    'P1_coldheat': '治则治法_Block',
    'P2_treatment': '治则治法_Block',
}
BLOCK_DIMS = ['病因病机_Block', '证候诊断_Block', '治则治法_Block', '处方生成_Block',
              '处方解析_Block', '症状变化推理_Block', '处方调整_Block']
CROSS_DIMS = ['病机_证候_Cross', '证候_治法_Cross', '治法_处方_Cross']

PERT_FLOOR = 3.0  # 锁定口径：overall ≤ 3 ⟺ 判定存在实质错误


def load(ver, model_tag='Qwen_Qwen3-14B'):
    p = os.path.join(JUDGE, f'judge_vllm_{model_tag}_{ver}.jsonl')
    if not os.path.exists(p):
        return None
    rows = []
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _dims(d):
    """过滤出数值维度，返回 dict"""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def agg_min(d, keys=None):
    vals = [v for k, v in d.items() if (keys is None or k in keys)]
    return min(vals) if vals else None


def agg_mean(d, keys=None):
    vals = [v for k, v in d.items() if (keys is None or k in keys)]
    return sum(vals) / len(vals) if vals else None


def safe_pct(n, d):
    return f'{100.0 * n / d:5.1f}%' if d else '   -  '


def section_reflectance(rows, ver):
    """聚合保留率：P(overall≤3 | 定向维度≤3)，按 ptype。

    ★ 2026-10-14：本函数此前**只 print、不落盘** → 论文主机制证据
      （reflectance 绝对版）在任何 json 里都找不到，违反「数值单一来源」。
      现改为返回结构化 dict（含 noticed_but_lost 绝对条数），由 main() 写入
      agg_diag.json → s08 可直接读到。
    """
    print(f'\n  ── 聚合保留率（reflectance）── {ver}')
    print('    ptype           n    定向察觉      整体检出     聚合保留率   noticed_but_lost')
    out = {}
    for pt in ('M1_omission', 'P1_coldheat', 'P2_treatment'):
        sub = [r for r in rows if r.get('ptype') == pt]
        if not sub:
            continue
        dim = PTYPE_DIM.get(pt)
        n_noticed = n_lost = n_overall = n_both = 0
        for r in sub:
            dp = _dims(r.get('dims'))
            if dim not in dp:
                continue
            noticed = dp[dim] <= PERT_FLOOR
            over = (r.get('score') is not None) and (float(r['score']) <= PERT_FLOOR)
            if noticed:
                n_noticed += 1
                if not over:
                    n_lost += 1
            if over:
                n_overall += 1
            if noticed and over:
                n_both += 1
        # ★ 2026-10-14 修正：reflectance 是**条件概率** P(overall≤3 | 定向维度≤3)，
        #   分子必须是「已察觉 **且** overall≤3」的交集（n_both），
        #   而不是无条件的 overall≤3 计数（n_overall）——旧写法会使数值 >100%（例如 P2=126%），
        #   因为存在 overall≤3 但定向维度>3 的记录（聚合侧另有来源）。
        refl_rate = (n_both / n_noticed) if n_noticed else None
        refl = f'{100.0 * n_both / n_noticed:5.1f}%' if n_noticed else '   -  '
        print(f'    {pt:14s} {len(sub):4d}  {safe_pct(n_noticed, len(sub))}     '
              f'{safe_pct(n_overall, len(sub))}     {refl}      {n_lost}/{n_noticed}')
        out[pt] = dict(
            n=len(sub),
            n_noticed=n_noticed,
            noticed_rate=round(n_noticed / len(sub), 4) if sub else None,
            n_overall_detected=n_overall,
            overall_detect_rate=round(n_overall / len(sub), 4) if sub else None,
            n_noticed_and_detected=n_both,
            reflectance_abs=(round(refl_rate, 4) if refl_rate is not None else None),
            noticed_but_lost=n_lost,
            noticed_but_lost_ratio=(round(n_lost / n_noticed, 4) if n_noticed else None),
        )
    return out


def _wilson(k, n, z=1.96):
    """Wilson score interval for a binomial proportion.

    ★ 与 `s06_compute_metrics.py::_wilson` **同一公式、同一常数**（z = 1.96）。
      本脚本刻意复制而不 import，理由：s06 是可独立运行的入口脚本（无 `if __name__`
      保护、顶层会 import 一堆重模块），import 它会连带执行；且这是 6 行数学，
      复制比分叉行为更安全。两处若日后改公式，必须**同时**改（s12 的数值对账会兜底）。
    """
    if not n:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return [round(max(0.0, (c - m) / d), 4), round(min(1.0, (c + m) / d), 4)]


def section_targeted_abs(rows, ver):
    """定向维度「绝对≤3」检出（主口径），按 ptype —— 落盘版。

    与 s06b (3) 节同口径（dim_pert ≤ 3），数值应完全一致；
    这里落进 agg_diag.json，便于 s08 汇总进 Table 1。

    ★ 2026-10-17（第 14 问）：补 **Wilson 95% 区间**。
      论文 §3.6 明文承诺「四个简单计数比 $D_drop / D_abs / T_abs / S$ 一律带 Wilson 区间」，
      但 `T_abs` 此前**只落盘点估计**（`targeted_abs` / `targeted_rel` 两个裸浮点数），
      承诺与产物对不上。此处补 `targeted_abs_ci` / `targeted_rel_ci`；
      `reflectance` **不**加 Wilson —— §3.6 对它另有安排（配对 bootstrap，见 s15）。
    """
    out = {}
    for pt in ('M1_omission', 'P1_coldheat', 'P2_treatment'):
        dim = PTYPE_DIM.get(pt)
        n = ab = rel = 0
        for r in rows:
            if r.get('ptype') != pt:
                continue
            do_, dp_ = _dims(r.get('dims_original')), _dims(r.get('dims'))
            if dim not in do_ or dim not in dp_:
                continue
            n += 1
            ab += int(dp_[dim] <= PERT_FLOOR)
            rel += int((do_[dim] - dp_[dim]) >= 1)
        if n:
            out[pt] = dict(n=n,
                           targeted_abs=round(ab / n, 4),
                           targeted_rel=round(rel / n, 4),
                           targeted_abs_ci=_wilson(ab, n),
                           targeted_rel_ci=_wilson(rel, n))
    return out


def sweep(rows, ver):
    """扫聚合规则：min(全部维度) / min(Block) / mean，阈值 1..4"""
    print(f'\n  ── 聚合规则扫描 ── {ver}')
    print('    规则                检出(扰动答)   误报(原答)   分离度(检出-误报)')
    n = len(rows)
    rows_ok = [r for r in rows
               if _dims(r.get('dims')) and _dims(r.get('dims_original'))
               and r.get('score') is not None and r.get('score_original') is not None]
    if not rows_ok:
        print('    （无可用记录）')
        return {}
    res = {}
    cands = []
    for label, keys in (('min_all', None), ('min_block', BLOCK_DIMS)):
        for k in (1.0, 2.0, 3.0, 4.0):
            det = sum(1 for r in rows_ok
                      if (agg_min(_dims(r['dims']), keys) or 9) <= k)
            fp = sum(1 for r in rows_ok
                     if (agg_min(_dims(r['dims_original']), keys) or 9) <= k)
            cands.append((f'min({label}) <= {k:.0f}', det, fp, len(rows_ok)))
    # Judge 自带的 overall（锁定口径，作参照）
    det = sum(1 for r in rows_ok if float(r['score']) <= PERT_FLOOR)
    fp = sum(1 for r in rows_ok if float(r['score_original']) <= PERT_FLOOR)
    cands.append(('★ 现状 overall <= 3', det, fp, len(rows_ok)))

    for label, det, fp, m in cands:
        sep = 100.0 * (det - fp) / m
        print(f'    {label:20s} {safe_pct(det, m)}       {safe_pct(fp, m)}      {sep:+6.1f}pp')
        res[label] = dict(detect=det, fp=fp, n=m,
                          detect_pct=round(100.0 * det / m, 2),
                          fp_pct=round(100.0 * fp / m, 2),
                          separation_pp=round(sep, 2))
    return res


def section_paired_floor(rows, ver):
    """★ 配对下界：同一 case 内比较 min(原答维度) 与 min(扰动维度) → 消除尺度漂移。

    动机：上面的 min 规则用「扰动答 min ≤k」比「原答 min ≤k」，两组是**不同记录**，
    整体抬分（v3 的平均分上移）会同时压低两边的 FP → 分离度的改善可能只是漂移的副产品。
    配对口径只看**同一条记录内部**下界是否被扰动打穿，漂移自动抵消。
    """
    print(f'\n  ── 配对下界（尺度漂移免疫）── {ver}')
    print('    指标                                  n      命中率')
    n = 0
    hit_k2 = hit_k3 = hit_drop1 = 0
    hit_dir_k2 = hit_dir_drop = 0
    for r in rows:
        dp, do = _dims(r.get('dims')), _dims(r.get('dims_original'))
        if not dp or not do:
            continue
        mp, mo = agg_min(dp), agg_min(do)
        if mp is None or mo is None:
            continue
        n += 1
        # 扰动把「下界」按到 ≤k，而原答下界仍 >k → 定向打穿
        if mp <= 2.0 < mo:
            hit_k2 += 1
        if mp <= 3.0 < mo:
            hit_k3 += 1
        if (mo - mp) >= 1.0:
            hit_drop1 += 1
        # 定向维度层面：扰动所在维度被打穿而下界未变（更纯净）
        dm = PTYPE_DIM.get(r.get('ptype'))
        if dm and dm in dp and dm in do:
            if dp[dm] <= 2.0 < do[dm]:
                hit_dir_k2 += 1
            if (do[dm] - dp[dm]) >= 1.0:
                hit_dir_drop += 1
    if not n:
        print('    （无可用记录）')
        return {}
    res = {
        'n': n,
        'paired_floor_le2': round(100.0 * hit_k2 / n, 2),
        'paired_floor_le3': round(100.0 * hit_k3 / n, 2),
        'paired_floor_drop1': round(100.0 * hit_drop1 / n, 2),
        'paired_target_le2': round(100.0 * hit_dir_k2 / n, 2),
        'paired_target_drop1': round(100.0 * hit_dir_drop / n, 2),
    }
    print(f'    配对下界打穿 min(扰)≤2 < min(原)       {n:5d}  {res["paired_floor_le2"]:6.1f}%')
    print(f'    配对下界打穿 min(扰)≤3 < min(原)       {n:5d}  {res["paired_floor_le3"]:6.1f}%')
    print(f'    配对下界下降 ≥1 分                     {n:5d}  {res["paired_floor_drop1"]:6.1f}%')
    print(f'    定向维度打穿 扰≤2 < 原                 {n:5d}  {res["paired_target_le2"]:6.1f}%')
    print(f'    定向维度下降 ≥1 分                     {n:5d}  {res["paired_target_drop1"]:6.1f}%')
    return res


CORE_CE = {'CE1', 'CE2', 'CE5', 'CE7'}


def _ce_set(rec):
    """从 missing_elements 中抽出 CE 编号集合。"""
    me = rec.get('missing_elements')
    if isinstance(me, str):
        try:
            me = json.loads(me)
        except Exception:
            me = []
    if not isinstance(me, list):
        return set()
    out = set()
    for item in me:
        for ce in ('CE1', 'CE2', 'CE3', 'CE4', 'CE5', 'CE6', 'CE7'):
            if ce in str(item):
                out.add(ce)
    return out


def _contra_nonempty(rec):
    c = rec.get('contradictions')
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except Exception:
            c = []
    if isinstance(c, list):
        c = [x for x in c if str(x).strip() not in ('', '无', 'None')]
    return bool(c)


def section_v4_simulate(rows, ver):
    """★ v4 预演：把 v4 的 R1–R5 **机械套用**在现有 v3 数据上，预测检出/误报。

    能算的：
      · R1/R3（Block 维度 ≤1 / =2）→ 扰动答与原答**都**能算（dims / dims_original 都有）
      · R2（contradictions 非空）、R4（missing 含核心 CE）→ **只有扰动答侧有落盘字段**
    不能算的：
      · 原答侧的 R2/R4 —— `dims_original` 有，但原答的 missing/contradictions 未落盘
        ⇒ 原答的误报只是**下界估计**，真实 FP ≥ 本值（R2/R4 会再加）。
    """
    print(f'\n  ── v4 规则预演（R1–R5 机械套用）── {ver}')
    n_rec = n_est = 0
    r1p = r3p = r4p = r2p = 0
    r1o = r3o = 0
    for r in rows:
        dp, do = _dims(r.get('dims')), _dims(r.get('dims_original'))
        if not dp or not do:
            continue
        n_rec += 1
        bp = {k: v for k, v in dp.items() if k in BLOCK_DIMS}
        bo = {k: v for k, v in do.items() if k in BLOCK_DIMS}
        if not bp or not bo:
            continue
        n_est += 1
        # 扰动答侧（R1–R4 全可算）
        hit_any = False
        if min(bp.values()) <= 1:
            r1p += 1
            hit_any = True
        if _contra_nonempty(r):
            r2p += 1
            hit_any = True
        if min(bp.values()) == 2:
            r3p += 1
            hit_any = True
        if _ce_set(r) & CORE_CE:
            r4p += 1
            hit_any = True
        # 原答侧（只有 R1/R3 可算）
        if min(bo.values()) <= 1:
            r1o += 1
        if min(bo.values()) == 2:
            r3o += 1
    if not n_est:
        print('    （无可用记录）')
        return {}
    pct = lambda x: 100.0 * x / n_est
    print(f'    R1 任一Block维度≤1   : 扰动 {pct(r1p):5.1f}%   '
          f'原答 {pct(r1o):5.1f}%   ← 原答侧可算')
    print(f'    R2 contradictions非空 : 扰动 {pct(r2p):5.1f}%   '
          f'原答   n/a    ← 原答未落盘')
    print(f'    R3 任一Block维度=2   : 扰动 {pct(r3p):5.1f}%   '
          f'原答 {pct(r3o):5.1f}%   ← 原答侧可算')
    print(f'    R4 missing含核心CE    : 扰动 {pct(r4p):5.1f}%   '
          f'原答   n/a    ← 原答未落盘')
    fp_lo = r1o + r3o  # 可能重叠，作上界；下面算的是并集
    n_fp_lo = 0
    for r in rows:
        bo = {k: v for k, v in _dims(r.get('dims_original')).items() if k in BLOCK_DIMS}
        if bo and min(bo.values()) <= 2:
            n_fp_lo += 1
    print(f'    ── 预测 overall_v4 ≤ 3 ──')
    print(f'       扰动答（R1–R4 并集，可算） : {pct(r4p + r3p + r2p + r1p):5.1f}%'
          f'   ⚠ 含重叠，仅粗估')
    n_det = 0
    for r in rows:
        bp = {k: v for k, v in _dims(r.get('dims')).items() if k in BLOCK_DIMS}
        if not bp:
            continue
        if (min(bp.values()) <= 2) or _contra_nonempty(r) or (_ce_set(r) & CORE_CE):
            n_det += 1
    print(f'       扰动答（真实并集）        : {pct(n_det):5.1f}%')
    print(f'       原答（仅 R1/R3，下界）    : {pct(n_fp_lo):5.1f}%'
          f'   ← 真实 FP ≥ 此值')
    return dict(n=n_est,
                R1_pert=round(pct(r1p), 2), R1_orig=round(pct(r1o), 2),
                R2_pert=round(pct(r2p), 2),
                R3_pert=round(pct(r3p), 2), R3_orig=round(pct(r3o), 2),
                R4_pert=round(pct(r4p), 2),
                pred_detect=round(pct(n_det), 2),
                pred_fp_lower=round(pct(n_fp_lo), 2))


def main():
    ap = argparse.ArgumentParser()
    # ★ 2026-10-15：默认版本清单加入补实验三臂（v5_checkeval / v3_no_ce / v3_no_cc）。
    #   缺判分文件的版本会在主循环里打印「未找到评测文件，跳过」，不会中断。
    ap.add_argument('--versions',
                    default='v1_baseline,v2_reword,v3_checklist,v4_aggregate,'
                            'v5_checkeval,v3_no_ce,v3_no_cc,'
                            # ★ 2026-10-17：`v1_sc_k3`（自一致性 k=3）此前**漏在默认清单外**。
                            #   症状：重跑一次就把它从 agg_diag.json 里**静默抹掉**
                            #   （该臂仍有 compare_*.json，但 agg_diag 侧无数据）。
                            #   影响面已核实（2026-10-17）：**当前没有任何下游脚本取这一臂**
                            #   —— 图脚本 VERS 只到 v1/v2/v3，SCALE_PAIRS 是 v1↔v3，附表按
                            #   显式 spec 取数；Table S3 读的是 s05_selfconsistency.py 产出的
                            #   selfconsistency_*.json，不经过本文件。故这是「聚合臂静默丢失」，
                            #   不是论文数字受损。补进默认清单 + 下方「已有键保护」双保险。
                            'v1_sc_k3')
    ap.add_argument('--model-tag', default='Qwen_Qwen3-14B')
    # ★ 2026-10-13：默认输出名必须带 Judge 模型标签。
    #   原来固定写 agg_diag.json → 跑「异族 Judge 对照」时会**静默覆盖**
    #   主实验的 agg_diag.json（那是论文的核心机制产物，见 HANDOFF 3.7）。
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    if not args.out:
        _sfx = '' if args.model_tag == 'Qwen_Qwen3-14B' else f'__{args.model_tag}'
        args.out = os.path.join(METRICS, f'agg_diag{_sfx}.json')

    out = {}
    for ver in [v.strip() for v in args.versions.split(',') if v.strip()]:
        rows = load(ver, args.model_tag)
        if rows is None:
            print(f'\n{"=" * 74}\n【{ver}】未找到评测文件，跳过\n{"=" * 74}')
            continue
        print(f'\n{"=" * 74}')
        print(f'【{ver}】  model={args.model_tag}  n={len(rows)}  '
              f'ptype 分布={dict(Counter(r.get("ptype") for r in rows))}')
        print(f'{"=" * 74}')
        refl = section_reflectance(rows, ver)
        tgt = section_targeted_abs(rows, ver)
        paired = section_paired_floor(rows, ver)
        v4sim = section_v4_simulate(rows, ver)
        out[ver] = dict(reflectance=refl, targeted_abs=tgt,
                        paired_floor=paired, v4_sim=v4sim, sweep=sweep(rows, ver))

    os.makedirs(METRICS, exist_ok=True)
    # ★ 2026-10-17「已有键保护」：本脚本的输出是**覆盖写**，而 `--versions` 是白名单 ——
    #   只要默认清单漏了某个臂（`v1_sc_k3` 就这么丢过一次），重跑就会把那一臂
    #   **静默删掉**，且没有任何报错（下游 Table 4 / Figure 11 只会少一格）。
    #   故覆盖前先读旧文件，凡旧有而本次未产的顶层键一律告警。
    if os.path.exists(args.out):
        try:
            _old = set(json.load(open(args.out, encoding='utf-8')).keys())
        except Exception:
            _old = set()
        _lost = sorted(_old - set(out.keys()))
        if _lost:
            print(f'\n⚠️  警告：本次未产出 {_lost}，但旧文件里有 —— 覆盖后将丢失这些臂。')
            print('    若其中有意的，请在 --versions 里显式列出；否则请 Ctrl-C 中止。')
            print(f'    （旧文件：{args.out}）')
    # ★ `newline='\n'` 是必须的：Windows 下默认把 '\n' 写成 CRLF，
    #   而这批 JSON 在仓库里是 LF —— 不写这一条，一次重跑就会让整份文件
    #   「全行变更」（实测 2466 行 diff，语义其实只多了 44 个 CI 键）。
    with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n→ 结果已写入 {args.out}')

    print(f'''
{"=" * 74}
怎么读这份结果（决定 v4 怎么写 / 值不值得跑）
{"=" * 74}
· 看「现状 overall <= 3」那一行的**分离度** = 当前口径的实际判别力。
· 若某条 min 规则在**误报不升**的前提下把检出拉高 → 该阈值就是 v4 的规则写法。
· 若所有 min 规则都伴随误报暴涨（误报 >> 现状）→ 说明「一刀切最弱维度」
  会把正常原答也压下去，v4 的 prompt 必须写成**分档规则**（如只在 ≤2 时才封顶），
  或直接把 v4 降级为「不强推作者力推的干预实验」。
· 聚合保留率那节的 noticed_but_lost = 「已察觉但没传递到 overall」的绝对条数，
  它是 v4 想消灭的目标量。''')


if __name__ == '__main__':
    main()
