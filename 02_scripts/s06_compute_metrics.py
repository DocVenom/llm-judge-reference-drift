# -*- coding: utf-8 -*-
"""
S06：指标计算 —— 产出论文主表。

核心指标
--------
1. **检出率（sensitivity）**：扰动样本中被Judge 检出「分数显著下降」的比例
   →直接对标官方 57% / 56% / 31%
2. **特异度（specificity）**：原答被误判为「有错误」的比例
   → ★ 防止「凡扰动都打低分」的投机做法
3. **2×2 混淆矩阵**：TP/FP/FN/TN 四格，审稿人必看
4. **McNemar 配对检验**：v1 vs v3 的同一批样本比较（检出率提升是否显著）
5. **ICC(4,1)**：4 名标注者一致性（人工参考真值的可信度）

⚠️ 统计纪律（写在代码里防止自己犯错）
   - 只在**总体**做主检验；E/M/H 分层仅作探索性分析（避免多重比较）
   - 检出判定用 DETECT_RULES.score_drop_threshold，与 judge_prompts 保持一致
   - 每个数字都要能追溯到具体样本 id

用法
----
  python 02_scripts/s06_compute_metrics.py --stage baseline
  python 02_scripts/s06_compute_metrics.py --stage compare \
         --a v1_baseline --b v3_checklist
  python 02_scripts/s06_compute_metrics.py --stage icc \
         --annot-dir01_data/annotations
"""
import argparse, glob, json, math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '00_config'))

from config import (JUDGE as JUDGE_SCORES, METRICS, ANALYSIS, ANNOT,  # noqa: E402
                    ensure_dirs, set_global_seed, SEED, env_snapshot)
from judge_prompts import DETECT_RULES, PROMPTS as JUDGE_PROMPTS  # noqa: E402

DROP = DETECT_RULES['score_drop_threshold']      # 默认 1.0

# ★ 2026-10-15：判分版本清单改为**从 judge_prompts.PROMPTS 动态取**，
#   不再手写死名单 —— 此前加一个臂（如补实验的 v5_checkeval / v3_no_ce / v3_no_cc）
#   必须记得回来改这里，漏改就会「跑了实验但表里没数」。
#   顺序 = PROMPTS 的插入顺序（v1 → v2 → v3 → v4 → 补实验三臂）。
#   无判分数据的版本在本脚本里会自动打印「无评分数据，跳过」，不会报错。
def _derived_vers():
    """无对应 prompt 常量的「派生判分臂」——由脚本合成，如自一致投票 v1_sc_k3。

    ★ 从 `metrics/selfconsistency_*.json` 的 out_ver 自动发现，
      避免再引入一处需要手改的版本名单（这正是本函数存在的原因）。
    """
    found = []
    for p in sorted(glob.glob(os.path.join(METRICS, 'selfconsistency_*.json'))):
        try:
            with open(p, encoding='utf-8') as f:
                v = json.load(f).get('out_ver')
        except Exception:
            continue
        if v and v not in found and v not in JUDGE_PROMPTS:
            found.append(v)
    return found


VER_LIST = list(JUDGE_PROMPTS) + _derived_vers()

# ============ ★ Rubric 语义判定规则（2026-10-12 新增，替代循环定义的特异度）============
# 依据 `00_config/judge_prompts.py` 的【评分区间】原文：
#     5 = 完全符合，证据充分，无逻辑漏洞
#     4 = 基本合格，少量瑕疵，**不影响核心推理**
#     3 = 部分合理，**存在明显证据缺失或轻微逻辑矛盾**
#     2 = 少量沾边，核心推理有明显问题
#     1 = 极少沾边，核心推理错误
#     0 = 完全错误，无证据支撑，严重矛盾
#
# → rubric **自己定义了** 4 分「不影响核心推理」（即未认定实质错误）、
#   3 分「存在明显证据缺失或轻微逻辑矛盾」（即**已认定存在错误**）。
#
# 因此以下判定规则**不是我们拍的阈值，而是直接来自 rubric 语义**：
#     「Judge 认为该答案存在实质错误」  ⟺  overall ≤ 3
#
# 为什么必须换掉旧的 `specificity_10pct`：
#   旧定义用「原答分数分布的 10 分位」当阈值，再数「≤ 该阈值的比例」
#   → 该指标**必然收敛到 ≈0.90，与 Judge 好坏完全无关**（循环定义/恒等式）。
#   而论文的核心主张之一是「v3 提升敏感度**同时特异度不降**」——
#   用一个恒等于 0.9 的假指标支撑它，审稿人一击即中。
FLAG_SCORE = 3      # overall ≤ FLAG_SCORE ⟺ rubric 认定「存在明显证据缺失/逻辑矛盾」

# 阈值敏感性分析用的多档阈值。
# ⚠️ 注意：overall 是 **0-5 整数**，故 score_drop 也是整数差 →
#    「≥1.0」已是最宽松的非零阈值（任何下降都算），再往下调（0.5）无效。
#    所以敏感性分析只能往上调（更严），而它给出的数字只会更低 →
#    我们报告的检出率是**最宽**的估计，结论天然保守。
DROP_THRESHOLDS = [1.0, 2.0, 3.0]

# ============ ★ 定向检出：扰动注入位置 ↔ rubric 维度 ============
# 依据 config.BLOCKS（7 个内容块）与 config.DIMS_BLOCK（7 个 Block 维度）的对应：
#     治法 → 治则治法_Block      （P1 寒热颠倒 / P2 治法反转 都注在「治法」块）
#     方解 → 处方解析_Block      （M1 方解遗漏 注在「方解」块）
#
# 为什么要单独算这条口径：
#   实测（2026-10-12）发现 Judge 常**在分维度上察觉异常、但 overall 保持 4 分不变**
#   （例：P1 扰动答的 处方生成/处方解析/处方调整 均掉到 3，overall 仍是 4）
#   → 只用 overall 降分判定，会把真实检出系统性漏掉。
#   「扰动所在维度是否掉分」更贴近"该错误是否被检出"，作为**次要口径**并列报告。
# ⚠️ 报告时必须与 overall 口径分开写，不可混算或择优选取（那是 p-hacking）。
TARGET_DIMS = {
    'P1_coldheat': ['治则治法_Block'],
    'P2_treatment': ['治则治法_Block'],
    'M1_omission': ['处方解析_Block'],
}


# ============ 工具 ============
# ★ 2026-10-13：主 Judge 的模型标签。用于把「异族 Judge 对照」的数据
#   与主实验严格分开 —— 见 load_judge 的说明（不改这里会静默混算）。
DEFAULT_JUDGE_TAG = 'Qwen_Qwen3-14B'


def load_judge(ver, backend=None, model_tag=DEFAULT_JUDGE_TAG):
    """载入某 prompt 版本的评分结果。
    文件名形如 judge_{backend}_{model}_{ver}.jsonl

    ★ 2026-10-13 加 model_tag 过滤（**必须先修再用**）：
      原实现用通配符 judge_*_{ver}.jsonl，在只有主 Judge 时没问题；
      但一旦跑「异族 Judge 对照」（同 287 条、换一个非 Qwen 族模型），
      两个模型的文件会**同时命中** → 记录数翻倍、配对表被覆盖、
      统计全部失真，而且**不会报任何错**（最阴的一类 bug）。
      → 默认按主 Judge 精确过滤；传 model_tag='*' 可显式关闭过滤（旧行为）。
    """
    mtag = model_tag if model_tag is not None else DEFAULT_JUDGE_TAG
    pats = ([f'judge_*_{ver}.jsonl'] if backend is None
            else [f'judge_{backend}_*_{ver}.jsonl'])
    files = []
    for pat in pats:
        files.extend(sorted(glob.glob(os.path.join(JUDGE_SCORES, pat))))
    if mtag != '*':
        files = [f for f in files if f'_{mtag}_{ver}.jsonl' in os.path.basename(f)]
    out = []
    for f in files:
        for line in open(f, encoding='utf-8'):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _score(r):
    """取分数；None 或解析失败视为无效。"""
    s = r.get('score')
    return None if s is None else float(s)


def _wilson(k, n, z=1.96):
    """Wilson 区间（比例的小样本置信区间）。

    ★ 为什么不用标准正态近似：检出率常常接近 0 或 1，
      正态近似在两端会给出负数/超过 1 的边界，且小样本极不准。
      Wilson 区间在 p→0 时仍给出合理的上界，是本项目判读基线所必需的。
    """
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return max(0.0, (c - m) / d), min(1.0, (c + m) / d)


# ============ 阶段 1：检出率基线 ============
def stage_baseline(args):
    """对每个 prompt 版本算敏感度/特异度 + 2×2 混淆矩阵。"""
    ensure_dirs()
    rows = []
    for ver in VER_LIST:
        recs = load_judge(ver, model_tag=args.judge_model)
        if not recs:
            print(f'  ⚠ {ver}: 无评分数据，跳过')
            continue

        # ---- 配对：同一 case 的原答 vs 扰动答 ----
        #★ 2026-10-12：s05 已改为成对评分，每条记录同时含
        #   score_original（原答）/ score（扰动答）/ score_drop（分差）。
        #   这里优先直接用 score_drop；缺失时才回落到自建配对。
        orig, pert = {}, {}
        has_drop = False
        # ★ 显式检出证据（仅 v3 产出）：key -> 该条的显式判定
        explicit_raw = {}
        # ★ 维度分（用于定向检出）：key -> {维度名: 分数}
        dims_o, dims_p = {}, {}
        for r in recs:
            k = r['case_id'] + '|' + str(r.get('ptype'))
            so = r.get('score_original')
            sp = _score(r)
            if so is not None:
                has_drop = True
                orig[k] = float(so)
            if sp is not None:
                pert[k] = sp
            if isinstance(r.get('dims'), dict) and r['dims']:
                dims_p[k] = {str(a): float(b)
                             for a, b in r['dims'].items()
                             if isinstance(b, (int, float))}
            if isinstance(r.get('dims_original'), dict) and r['dims_original']:
                dims_o[k] = {str(a): float(b)
                             for a, b in r['dims_original'].items()
                             if isinstance(b, (int, float))}
            # 「产出了该字段」= missing_elements 或 contradictions 至少一个 reported
            n_rep = bool(r.get('missing_elements_reported')
                         or r.get('contradictions_reported'))
            if n_rep or ('explicit_detect' in r):
                explicit_raw[k] = dict(
                    n_reported=n_rep,
                    explicit_detect=bool(r.get('explicit_detect')),
                    explicit_detect_new=bool(r.get('explicit_detect_new')))
        if not has_drop:
            # 回落：旧格式（原答与扰动答分成两条记录）
            for r in recs:
                k = r['case_id'] + '|' + str(r.get('ptype'))
                v = _score(r)
                if v is None:
                    continue
                if r.get('ptype') is None:
                    orig.setdefault(k.replace('|None', ''), v)
                else:
                    pert[k] = v
        print(f'    （成对格式: {has_drop}）')

        # ---- 敏感度：扰动答是否比原答低 DROP 分以上 ----
        #同时收集**显式检出**（v3 checklist 的产出），两者分开统计。
        by_ptype = {}
        explicit_by_ptype = {}
        for k, s_pert in pert.items():
            cid, ptype = k.split('|', 1)
            # ★ 成对格式下 orig 的 key 与 pert 相同（含 ptype）；
            #   旧格式下 orig 只按 cid 存。两边都试。
            s_orig = orig.get(k)
            if s_orig is None:
                s_orig = orig.get(cid)
            if s_orig is None:
                continue
            detected = (s_orig - s_pert) >= DROP
            d = by_ptype.setdefault(ptype, dict(n=0, tp=0, drops=[]))
            d['n'] += 1
            d['tp'] += int(detected)
            d['drops'].append(round(s_orig - s_pert, 2))
            # ★ 绝对检出：rubric 语义下「扰动答被判定存在实质错误」
            d['flagged'] = d.get('flagged', 0) + int(s_pert <= FLAG_SCORE)
            # ★ 阈值敏感性：多档降分阈值下的检出数（≥1 即最宽松）
            md = d.setdefault('multi', {})
            for th in DROP_THRESHOLDS:
                md[th] = md.get(th, 0) + int((s_orig - s_pert) >= th)

            # ---- ★ 定向检出：扰动**注入位置**对应的维度是否掉分 ----
            # 与 overall 口径并列报告（不替代、不择优）。
            # 需同时有原答与扰动答的该维度分，缺任一则不计入分母。
            do_, dp_ = dims_o.get(k), dims_p.get(k)
            if do_ and dp_:
                for dim in TARGET_DIMS.get(ptype, []):
                    if dim in do_ and dim in dp_:
                        t = by_ptype.setdefault(
                            ptype, dict(n=0, tp=0, drops=[],
                                        tn=0, ttp=0, tdim=None))
                        t.setdefault('tn', 0)
                        t.setdefault('ttp', 0)
                        t['tdim'] = dim
                        t['tn'] += 1
                        td = int((do_[dim] - dp_[dim]) >= DROP)
                        t['ttp'] += td
                        # ★★ 关键交叉表：定向是否检出 × 整体是否检出
                        #    「反射率」= P(整体检出 | 定向已检出)
                        #    它直接量化「Judge 察觉了，但聚合分没反映」的比例 ——
                        #    这是本项目最有力的机制证据（比"某类检出率最低"稳得多，
                        #    因为它是在**同一条样本内**做条件比较，不受组间样本量影响）。
                        ct = t.setdefault('xtab', {})
                        ct[(td, int(detected))] = ct.get((td, int(detected)), 0) + 1
                        break

            # ---- 显式检出（隐式口径的补充，不替代）----
            # 只统计 Judge **确实产出**该字段的样本（v3 才会产出）。
            # v1/v2 的 prompt 未要求该字段 → 不计入分母，
            # 否则会把「v1 没这个字段」误算成「v1 检不出」，虚增 v3 的提升。
            er = explicit_raw.get(k)
            if er is not None and er.get('n_reported'):
                e = explicit_by_ptype.setdefault(
                    ptype, dict(n=0, tp=0, n_new=0, tp_new=0))
                e['n'] += 1
                e['tp'] += int(bool(er.get('explicit_detect')))
                e['n_new'] += 1
                e['tp_new'] += int(bool(er.get('explicit_detect_new')))

        # ---- ★ 特异度（2026-10-12 重定义，替换循环定义的旧指标）----
        # 旧定义：拿「原答分数分布的 10 分位」当阈值，再数「≤ 该阈值的比例」
        #   → 该指标**必然收敛到 ≈0.90，与 Judge 好坏完全无关**（恒等式）。
        #     用它支撑「v3 特异度不降」这条核心主张，审稿人一击即中。
        # 新定义直接来自 rubric 语义（见文件头 FLAG_SCORE 注释）：
        #   「Judge 判定该答案存在实质错误」 ⟺ overall ≤ FLAG_SCORE
        #   · 判定单元 = **去重后的原答**：同一病例的原答会在 3 类扰动里各出现
        #     一次，重复计入会虚增 n 且破坏独立性。
        #   · 顺便给出 FPR 的 Wilson 区间，便于与 v2/v3 比较「不降」是否成立。
        orig_uniq = {}
        for k, s in orig.items():
            orig_uniq.setdefault(k.split('|', 1)[0], s)
        uo = list(orig_uniq.values())
        orig_scores = list(orig.values())
        n_fp = sum(1 for s in uo if s <= FLAG_SCORE)
        spec = (1 - n_fp / len(uo)) if uo else None
        fpr_lo, fpr_hi = _wilson(n_fp, len(uo))

        # 扰动侧「绝对检出」（与特异度同一判定规则 → 可构成有效 2×2）
        n_pert_flagged = sum(d.get('flagged', 0) for d in by_ptype.values())
        n_pert_all = sum(d['n'] for d in by_ptype.values())
        sens_abs = (n_pert_flagged / n_pert_all) if n_pert_all else None

        rec = dict(prompt_ver=ver, n_orig=len(orig), n_pert=len(pert),
                   n_orig_unique=len(uo),
                   orig_score_mean=round(sum(orig_scores) / len(orig_scores), 3)
                   if orig_scores else None,
                   # ★ rubric 口径（主）
                   flag_score=FLAG_SCORE,
                   specificity=round(spec, 4) if spec is not None else None,
                   specificity_n=len(uo),
                   false_positive_rate=round(n_fp / len(uo), 4) if uo else None,
                   false_positive_ci=[
                       round(fpr_lo, 4) if fpr_lo is not None else None,
                       round(fpr_hi, 4) if fpr_hi is not None else None],
                   sensitivity_absolute=round(sens_abs, 4)
                   if sens_abs is not None else None,
                   # ⚠️ 敏感度与特异度的**分母天然不同**（前者在扰动侧、后者在原答侧），
                   #    这是敏感度/特异度的标准报告方式，**不是**一张 2×2 混淆矩阵
                   #    （真实 2×2 要求同一总体的四格可加，此处 297+99 不可加）。
                   #    故命名为 sensitivity_specificity，避免误称为混淆矩阵。
                   sensitivity_specificity=dict(
                       sens_n=n_pert_all, sens_tp=n_pert_flagged,
                       sens_fn=n_pert_all - n_pert_flagged,
                       spec_n=len(uo), spec_tn=len(uo) - n_fp, spec_fp=n_fp),
                   by_ptype={})
        for ptype, d in sorted(by_ptype.items()):
            rec['by_ptype'][ptype] = dict(
                n=d['n'], detected=d['tp'],
                sensitivity=round(d['tp'] / d['n'], 4) if d['n'] else None,
                mean_score_drop=round(sum(d['drops']) / len(d['drops']), 3)
                if d['drops'] else None,
                # ★ 绝对检出（rubric 口径，与特异度同一判定规则）
                sensitivity_absolute=round(d.get('flagged', 0) / d['n'], 4)
                if d['n'] else None,
                # ★ 阈值敏感性（≥1 已是最宽松的非零阈值）
                sensitivity_by_threshold={
                    str(th): round(d.get('multi', {}).get(th, 0) / d['n'], 4)
                    for th in DROP_THRESHOLDS} if d['n'] else None)
            # ★ 定向检出（次要口径）：扰动注入维度是否掉分
            if d.get('tn'):
                rec['by_ptype'][ptype]['targeted_dim'] = d.get('tdim')
                rec['by_ptype'][ptype]['targeted_n'] = d['tn']
                rec['by_ptype'][ptype]['targeted_detection_rate'] = round(
                    d['ttp'] / d['tn'], 4)
                # ★ 反射率：定向已检出中，整体也检出的比例
                xt = d.get('xtab') or {}
                t1o1 = xt.get((1, 1), 0)
                t1o0 = xt.get((1, 0), 0)
                rec['by_ptype'][ptype]['xtab'] = dict(
                    noticed_and_reflected=t1o1, noticed_but_lost=t1o0,
                    not_noticed_but_reflected=xt.get((0, 1), 0),
                    neither=xt.get((0, 0), 0))
                if (t1o1 + t1o0):
                    rec['by_ptype'][ptype]['reflection_rate'] = round(
                        t1o1 / (t1o1 + t1o0), 4)
            e = explicit_by_ptype.get(ptype)
            if e and e['n']:
                rec['by_ptype'][ptype]['explicit_n'] = e['n']
                rec['by_ptype'][ptype]['explicit_detection_rate'] = round(
                    e['tp'] / e['n'], 4)
                rec['by_ptype'][ptype]['explicit_detection_rate_new'] = round(
                    e['tp_new'] / e['n_new'], 4) if e['n_new'] else None
        rec['n_explicit_reported'] = sum(1 for v in explicit_raw.values()
                                         if v['n_reported'])
        rows.append(rec)

        print(f'\n  【{ver}】原答 {rec["n_orig"]}（去重 {rec["n_orig_unique"]}）'
              f' / 扰动 {rec["n_pert"]}')
        print(f'    原答均分 {rec["orig_score_mean"]}')
        _fx, _tx = rec['false_positive_ci']
        print(f'    ★ 特异度(rubric: overall > {rec["flag_score"]}) '
              f'{rec["specificity"]}'
              + (f'  （误报率 {rec["false_positive_rate"]:.1%}'
                 f' 95%CI [{_fx:.1%},{_tx:.1%}]，n={rec["specificity_n"]}）'
                 if rec['false_positive_rate'] is not None else ''))
        print(f'    ★ 绝对检出(rubric 同一判定规则) '
              f'{rec["sensitivity_absolute"]:.1%}'
              f'（{rec["sensitivity_specificity"]["sens_tp"]}'
              f'/{rec["sensitivity_specificity"]["sens_n"]}）'
              f'   特异度 {rec["specificity"]:.1%}'
              f'（{rec["sensitivity_specificity"]["spec_tn"]}'
              f'/{rec["sensitivity_specificity"]["spec_n"]}）')
        print(f'    {"":<8}注：敏感度在扰动侧、特异度在原答侧，分母天然不同'
              f'（标准做法，非同一张 2×2）。')
        print(f'    {"":<8}注意：「降分检出」与「绝对检出」是两条不同口径，'
              f'分开报告，不择优。')
        for ptype, d in rec['by_ptype'].items():
            lo, hi = _wilson(d['detected'], d['n'])
            ci = f'  95%CI [{lo:.0%},{hi:.0%}]' if lo is not None else ''
            print(f'    {ptype:<16} n={d["n"]:<4} '
                  f'降分检出 {d["sensitivity"]:.1%}{ci}'
                  f'  绝对检出 {d["sensitivity_absolute"]:.1%}'
                  f'  均值降分 {d["mean_score_drop"]}')
            if d.get('sensitivity_by_threshold'):
                sb = d['sensitivity_by_threshold']
                print(f'    {"":<16}阈值敏感性 '
                      + '  '.join(f'≥{th}:{sb[str(th)]:.1%}'
                                  for th in DROP_THRESHOLDS))
            if d.get('targeted_n'):
                print(f'    {"":<16}定向检出 '
                      f'{d["targeted_detection_rate"]:.1%}'
                      f'（维度 {d.get("targeted_dim")}，n={d["targeted_n"]}）')
                if d.get('reflection_rate') is not None:
                    xt = d['xtab']
                    print(f'    {"":<16}★ 反射率 '
                          f'{d["reflection_rate"]:.1%}'
                          f' —— 察觉后整体分也降的比例'
                          f'（察觉到 {xt["noticed_and_reflected"] + xt["noticed_but_lost"]}'
                          f' → 反映 {xt["noticed_and_reflected"]}'
                          f' / 丢失 {xt["noticed_but_lost"]}）')
            if d.get('explicit_n'):
                print(f'    {"":<16}显式检出 '
                      f'{d["explicit_detection_rate"]:.1%} / 新增检出 '
                      f'{d["explicit_detection_rate_new"]:.1%}'
                      f'（仅 {d["explicit_n"]} 条产出该字段）')

    # ---- 与官方基线对比 ----
    OFFICIAL = {'P1_coldheat': 0.57, 'P2_treatment': 0.56, 'M1_omission': 0.31}
    print('\n' + '=' * 66)
    print(' 与官方自曝检出率对比')
    print('=' * 66)
    print(f' {"扰动类型":<14}{"n":>4}{"官方":>8}{"我们v1":>9}'
          f'{"差值":>9}{"95% 置信区间":>20}')
    base = next((r for r in rows if r['prompt_ver'] == 'v1_baseline'), None)
    cmp_rows = []
    for ptype, off in OFFICIAL.items():
        ours, n_pt, lo, hi = None, 0, None, None
        if base and ptype in base['by_ptype']:
            d = base['by_ptype'][ptype]
            ours = d['sensitivity']
            n_pt = d['n']
            lo, hi = _wilson(d['detected'], n_pt)
        diff = round(ours - off, 4) if ours is not None else None
        ci = f'[{lo:.0%}, {hi:.0%}]' if lo is not None else '—'
        print(f' {ptype:<14}{n_pt:>4}{off:>8.0%}'
              f'{(f"{ours:.1%}" if ours is not None else "—"):>9}'
              f'{(f"{diff:+.1%}" if diff is not None else "—"):>9}{ci:>20}')
        cmp_rows.append(dict(ptype=ptype, official=off, ours=ours, diff=diff,
                             n=n_pt,
                             ci=[round(lo, 4) if lo is not None else None,
                                 round(hi, 4) if hi is not None else None],
                             official_in_ci=(lo is not None and lo <= off <= hi)))

    # ★★ 判定必须考虑样本量（2026-10-12 修正）
    # 旧版直接比点估计：「差值 >10pp 就算不吻合」→ 在 n=6 时会喊出
    # 「差异过大」，而实际上官方值稳稳落在 [0%, 39%] 里，**完全相容**。
    # 这种误判会直接把项目推向错误止损。故：
    #   ① 一律给 Wilson 95% 置信区间；
    #   ② 只有当 官方值落在区间外 且 该类型 n ≥ 30 时，才允许说「不吻合」；
    #   ③ 否则如实写「样本量不足，不下结论」。
    MIN_N = 30
    if base:
        inside = [r for r in cmp_rows if r['official_in_ci']]
        enough = [r for r in cmp_rows if r['n'] >= MIN_N]
        print(f'\n ★ 官方值落在 95% 置信区间内：{len(inside)}/{len(cmp_rows)}')
        print(f' ★ 样本量达标（n≥{MIN_N}）的类型：{len(enough)}/{len(cmp_rows)}')
        if len(enough) < len(cmp_rows):
            print(' → ⚠ 样本量不足，**不下结论**（当前区间过宽，无法区分）')
            print(f'   建议：跑全量扰动集（287 条）后再判读')
        elif len(inside) == len(cmp_rows):
            print(' → 官方数值全部落在我们区间内：可用于 NUMERIC 定位')
        elif inside:
            print(' → 部分吻合：以内部对比为主，官方数值作动机引用（PATTERN_ONLY）')
        else:
            print(' → 官方数值全部落在区间外：**不得声称复现官方数值**')
            print('   原因已定位（2026-10-12 全量实测）：三类**一致偏低**，属系统性差异，')
            print('   不是扰动协议故障。成因为 Judge 更弱（本地 14B vs 官方 qwen3.7-plus）')
            print('   + overall 聚合稀释局部信号（见 RESEARCH_DESIGN「二·五之三」）。')
            print('   ⚠ 不要据此去改扰动协议 —— 那是修错地方（上一版提示语误导，已更正）。')
            print('   → 定位 PATTERN_ONLY：官方数值只作动机引用，')
            print('     论文主张落在**内部对比**（同一 Judge、同一数据、只改 prompt）。')

    # ★ 2026-10-13：加 Judge 模型标签后缀（与 compare 阶段口径一致）。
    #   原来固定写 baseline_detection.json → 跑异族 Judge 的 baseline 会
    #   **静默覆盖**主实验的头号产物（论文主表就来自它）。
    _sfx = '' if args.judge_model == DEFAULT_JUDGE_TAG else f'__{args.judge_model}'
    out = os.path.join(METRICS, f'baseline_detection{_sfx}.json')
    json.dump(dict(seed=SEED, drop_threshold=DROP, results=rows,
                   official_comparison=cmp_rows, env=env_snapshot()),
              open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'\n  → {out}')
    return rows


# ============ 阶段 2：版本对比（McNemar）============
def _mcnemar_p(b, c):
    """McNemar 精确检验（双侧），b/c 为不一致对数。"""
    n = b + c
    if n == 0:
        return 1.0
    # 用正态近似 + 连续性校正（n 大时足够）
    if n < 25:
        # 精确二项检验
        from math import comb
        k = min(b, c)
        p = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n) * 2
        return min(1.0, p)
    chi2 = (abs(b - c) - 1) ** 2 / n
    # chi2 1 自由度 p 值
    from math import erfc, sqrt
    return min(1.0, erfc(sqrt(chi2 / 2)))


def _det_set(recs, rule='drop'):
    """把某版本的评分记录转成 {case_id|ptype: 0/1} 的检出集合。

    ★ 2026-10-12 修复的静默 bug：旧版只认「ptype is None 的记录 = 原答」，
      而 s05 早已改为**成对记录**（score_original 内联在同一条里）
      → 旧代码的 orig 永远为空 → compare 会直接报「两版无共同样本」。
      现在优先读内联的 score_original，仅在缺失时才回落旧格式。

    rule='drop' → 降分检出（original − perturbed ≥ DROP）
    rule='abs'  → 绝对检出（rubric 语义：perturbed overall ≤ FLAG_SCORE）
    """
    orig_fallback = {}
    for r in recs:
        if r.get('ptype') is None:            # 旧格式：原答单列一条
            s = _score(r)
            if s is not None:
                orig_fallback[r['case_id']] = s

    det = {}
    for r in recs:
        if r.get('ptype') is None:            # 跳过原答记录本身
            continue
        sp = _score(r)
        if sp is None:
            continue
        key = f"{r['case_id']}|{r.get('ptype')}"
        if rule == 'abs':
            det[key] = int(sp <= FLAG_SCORE)
            continue
        so = r.get('score_original')
        if so is None:
            so = orig_fallback.get(r['case_id'])
        if so is None:
            continue
        det[key] = int((float(so) - sp) >= DROP)
    return det


def _mcnemar_report(a_name, b_name, A, B, label):
    """对两版检出集合做配对 McNemar，返回结果 dict（并可打印）。"""
    keys = sorted(set(A) & set(B))
    if not keys:
        return None
    b_only = sum(1 for k in keys if A[k] == 0 and B[k] == 1)   # B 独检出
    c_only = sum(1 for k in keys if A[k] == 1 and B[k] == 0)   # A 独检出
    p = _mcnemar_p(b_only, c_only)
    sa = sum(A[k] for k in keys) / len(keys)
    sb = sum(B[k] for k in keys) / len(keys)
    lo_a, hi_a = _wilson(sum(A[k] for k in keys), len(keys))
    lo_b, hi_b = _wilson(sum(B[k] for k in keys), len(keys))
    print(f'\n  【{label}】共同样本 {len(keys)} 对')
    print(f'    {a_name}: {sa:.1%}  95%CI [{lo_a:.0%},{hi_a:.0%}]')
    print(f'    {b_name}: {sb:.1%}  95%CI [{lo_b:.0%},{hi_b:.0%}]'
          f'   提升 {sb - sa:+.1%}')
    print(f'    McNemar 不一致对：仅新检出 {b_only} / 仅旧检出 {c_only}')
    print(f'    p = {p:.4f}  →  {"★ 显著 (p<0.05)" if p < 0.05 else "不显著"}'
          f'  |  净变化 {b_only - c_only:+d} 对')
    return dict(n=len(keys), sens_a=round(sa, 4), sens_b=round(sb, 4),
                gain=round(sb - sa, 4), ci_a=[round(lo_a, 4), round(hi_a, 4)],
                ci_b=[round(lo_b, 4), round(hi_b, 4)],
                mcnemar_b_only=b_only, mcnemar_c_only=c_only,
                p_value=round(p, 6),
                significant=bool(p < 0.05),
                improved=bool(p < 0.05 and sb > sa))


def stage_compare(args):
    """比较两个 prompt 版本的检出率提升是否显著。

    ★ 2026-10-12：两条口径**都算**（降分 / rubric 绝对），分开报告。
      只报一条会被质疑「挑口径」；两条并列才站得住。
    """
    recs_a, recs_b = (load_judge(args.a, model_tag=args.judge_model),
                      load_judge(args.b, model_tag=args.judge_model))
    if not recs_a or not recs_b:
        print(f'  ✗ 缺评分数据：{args.a}={len(recs_a)} 条, {args.b}={len(recs_b)} 条'
              f'（judge_model={args.judge_model}）')
        return

    print('=' * 66)
    print(f' 版本对比：{args.a}  →  {args.b}')
    print(f' Judge  : {args.judge_model}')
    print('=' * 66)
    print(' ★ 两条口径并列报告，不择优（择优 = p-hacking）')

    res = {}
    for rule, label in [('drop', '降分检出（主口径，对齐官方模式）'),
                        ('abs', f'绝对检出（rubric: overall ≤ {FLAG_SCORE}）')]:
        r = _mcnemar_report(args.a, args.b,
                            _det_set(recs_a, rule), _det_set(recs_b, rule), label)
        if r:
            res[rule] = r

    if not res:
        print('\n  ✗ 两版无共同样本，无法比较')
        return

    d = res.get('drop')
    if d:
        print(f'\n★ 主口径判读：'
              + ('机制化修复有效（提升显著）' if d['improved']
                 else ('检出率提升但未达显著（样本或效应不足）'
                       if d['gain'] > 0
                       else '未见提升，需检查 v3 的设计')))
        if res.get('abs'):
            ag = res['abs']
            print(f'  次要口径（绝对检出）：提升 {ag["gain"]:+.1%}，'
                  f'p = {ag["p_value"]:.4f} —— '
                  + ('与主口径一致' if (ag['gain'] > 0) == (d['gain'] > 0)
                     else '⚠ 与主口径**方向不一致**，须如实呈现并讨论'))

    # ★ 2026-10-13：异族 Judge 对照不能覆盖主实验的结果文件
    sfx = '' if args.judge_model == DEFAULT_JUDGE_TAG else f'__{args.judge_model}'
    out = os.path.join(METRICS, f'compare_{args.a}_vs_{args.b}{sfx}.json')
    json.dump(dict(a=args.a, b=args.b, judge_model=args.judge_model, results=res,
                   drop_threshold=DROP, flag_score=FLAG_SCORE,
                   env=env_snapshot()),
              open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'\n  → {out}')


# ============ 阶段 3：ICC（标注一致性）============
def _icc21_numpy(M):
    """ICC(2,1) 双向随机效应、绝对一致、单测量（Shrout & Fleiss 1979）。

    M: (n_subjects, k_raters) 数值矩阵，须无缺失。
    pingouin 缺失时用它兜底 —— 不引入外部依赖，公式固定、可复核。
    """
    import numpy as np
    n, k = M.shape
    if n < 2 or k < 2:
        return float('nan')
    gm = M.mean()
    row_m, col_m = M.mean(axis=1), M.mean(axis=0)
    ss_tot = float(((M - gm) ** 2).sum())
    ss_row = float(k * ((row_m - gm) ** 2).sum())
    ss_col = float(n * ((col_m - gm) ** 2).sum())
    ss_err = ss_tot - ss_row - ss_col
    ms_row = ss_row / (n - 1)
    ms_col = ss_col / (k - 1)
    ms_err = max(ss_err, 0.0) / ((n - 1) * (k - 1))
    den = ms_row + (k - 1) * ms_err + k * (ms_col - ms_err) / n
    return float((ms_row - ms_err) / den) if den else float('nan')


def _icc21(wide, pg=None):
    """优先用 pingouin；不可用则退回 numpy 实现（结果一致）。"""
    import numpy as np
    M = np.asarray(wide, dtype=float)
    if M.shape[0] < 2 or M.shape[1] < 2:
        return float('nan'), 'n/a'
    if pg is not None:
        try:
            icc = pg.intraclass_corr(M, raters='all', nan_policy='omit')
            row = icc[icc['Type'].astype(str).str.contains('ICC2|ICC1')]
            if len(row):
                return float(row['ICC'].iloc[0]), 'pingouin'
        except Exception:
            pass
    return _icc21_numpy(M), 'numpy'


def stage_icc(args):
    """计算标注者一致性 ICC(2,1)（双向随机、绝对一致、单测量）。"""
    try:
        import pandas as pd
    except ImportError:
        print('  ✗ 缺 pandas，请先安装')
        return
    try:
        import pingouin as pg
    except Exception:
        pg = None
        print('  · 未装 pingouin（或不可用），改用内置 numpy 实现计算 ICC(2,1)')
    ensure_dirs()
    csvs = sorted(glob.glob(os.path.join(args.annot_dir, 'annot_*.csv')))
    if len(csvs) < 2:
        print(f'  ✗ 需要 ≥2 份标注文件，当前 {len(csvs)}')
        return
    dfs = []
    for f in csvs:
        df = pd.read_csv(f)
        # ★ 2026-10-13 修 bug：原用 basename.split('_')[-1] 取标注者名，
        #   而文件名形如 annot_D1_calibration_99.csv → 取到的是 "99"，
        #   4 份文件全变成同一个标注者 → pivot 只剩一列 → ICC 直接失效。
        base = os.path.basename(f)
        tok = base[len('annot_'):] if base.startswith('annot_') else base
        df['annotator'] = tok.split('_')[0]
        dfs.append(df)
    all_df = pd.concat(dfs, ignore_index=True)

    # ★ 2026-10-13 加固：若「资格考核」与「正式标注」的 CSV 混在同一目录，
    #   同一标注者会出现重复的 (case_id, annotator) 组合 → 下面 pivot() 会直接抛
    #   “Index contains duplicate entries”，报错完全看不出真实原因。
    #   这里提前判掉，并给出可直接照抄的命令。
    if 'case_id' in all_df.columns:
        dup = int(all_df.duplicated(subset=['case_id', 'annotator']).sum())
        if dup:
            print(f'  ✗ 发现 {dup} 组重复的 (case_id, 标注者)。')
            print('    最常见原因：「资格考核(10 例)」与「正式标注(99 例)」的 CSV')
            print('    混在了同一个目录 —— 两者 case_id 有交集，同一人就被算了两遍。')
            print('    请把两个阶段分目录存放，用 --annot-dir 分别计算：')
            print('      python 02_scripts/s06_compute_metrics.py --stage icc '
                  '--annot-dir 01_data/annotations/qual')
            print('      python 02_scripts/s06_compute_metrics.py --stage icc '
                  '--annot-dir 01_data/annotations')
            return

    # ★ 2026-10-13 修 bug：原 dims 排除了 case_id/annotator/ts/note，
    #   但**没排除 tier**（字符串列）→ 会对 'easy/medium/hard' 求 ICC。
    #   改为「只保留可转成数值、且非空值足够」的列」。
    EXCLUDE = {'case_id', 'annotator', 'ts', 'note', 'tier'}
    dims = []
    for c in all_df.columns:
        if c in EXCLUDE:
            continue
        num = pd.to_numeric(all_df[c], errors='coerce')
        if num.notna().sum() == 0:
            continue
        all_df[c] = num
        dims.append(c)

    n_ann = all_df['annotator'].nunique()
    if n_ann < 2:
        print(f'  ✗ 解析出的标注者只有 {n_ann} 个：{list(all_df["annotator"].unique())}')
        print('    请检查文件名是否为 annot_<编号>_*.csv')
        return
    print('=' * 66)
    print(f' ICC(2,1) —— {n_ann} 名标注者 · 双向随机 · 绝对一致 · 单测量')
    print('=' * 66)
    print(f' 标注文件 {len(csvs)} 份 | 标注者 {sorted(all_df["annotator"].unique())}')
    print(f' 维度 {len(dims)} 个\n')
    print(f' {"维度":<22}{"ICC":>10}{"判定":>10}')
    out = []
    for d in dims:
        wide = all_df.pivot(index='case_id', columns='annotator', values=d)
        wide = wide.dropna()
        if len(wide) < 3:
            continue
        icc_v, how = _icc21(wide.to_numpy(), pg=pg)
        v = icc_v
        judge = ('优' if v >= 0.75 else '良' if v >= 0.6 else
                 '中' if v >= 0.4 else '差')
        print(f' {d:<22}{v:>10.3f}{judge:>10}')
        out.append(dict(dim=d, icc21=round(v, 4), n=len(wide), level=judge,
                        engine=how))
    if out:
        worst = min(out, key=lambda x: x['icc21'])
        print(f'\n ★ 最弱维度：{worst["dim"]} (ICC={worst["icc21"]})')
        print('   → 该维度须在论文 limitation 中说明，或重新培训后重标')
        json.dump(out, open(os.path.join(METRICS, 'icc_results.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', required=True,
                    choices=['baseline', 'compare', 'icc'])
    ap.add_argument('-a', default='v1_baseline')
    ap.add_argument('-b', default='v3_checklist')
    ap.add_argument('--judge-model', default=DEFAULT_JUDGE_TAG,
                    help='Judge 的模型标签（文件名里那段），默认主 Judge；'
                         "跑「异族 Judge 对照」时必须显式指定，否则会与主实验混算。"
                         "传 '*' 可关闭过滤（旧行为，仅调试用）")
    ap.add_argument('--annot-dir', default=ANNOT)
    ap.add_argument('--seed', type=int, default=None)
    args = ap.parse_args()
    ensure_dirs()
    set_global_seed(args.seed if args.seed is not None else SEED)
    {'baseline': stage_baseline, 'compare': stage_compare,
     'icc': stage_icc}[args.stage](args)


if __name__ == '__main__':
    main()
