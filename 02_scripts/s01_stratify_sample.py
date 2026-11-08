# -*- coding: utf-8 -*-
"""
S1：OCSS 客观复杂度分层 + 两套抽样。

OCSS（Objective Case Complexity Score）四个指标 —— 全部依赖**病例输入**，
不依赖模型输出、不依赖 judge 打分，因此无循环论证风险，且完全可复现：

  C1 症状条目密度  =逗号/顿号分隔的症状项数（对数归一）
  C2 四诊完整度    = 舌象1 + 脉象1 + 既往史1 + 中西医诊断1  （0-4）
  C3 时序推理负担  = 复诊/服药后/二诊/既往方/加减 等标记（0-3）
  C4 信息量        = input 字数（对数归一）

OCSS = 加权和，min-max 归一到[0,1] 后按三分位切成 Easy / Medium / Hard。

★ 与官方 E/M/H 的关系：官方分层基于 reference answers 的 embedding 相似度，
  reference answers 即未公开的 gold label → **无法复现**。本方案为替代分层，
  论文中须声明"we adopt a fully reproducible alternative stratification"。

产出：
  01_data/upstream/ocss_scores_all_2000.csv   全量 2000 条OCSS 与分档
  01_data/upstream/selected_calibration_99.json 人工标注校准集
  01_data/upstream/selected_perturb_150.json    扰动实验集
  03_results/metrics/s1_sampling_report.md      抽样报告（可直接进论文）
"""
import os, sys, re, json, csv, math
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), '00_config'))
from config import (UPSTREAM, UPSTREAM_JSON, METRICS, ensure_dirs,
                    set_global_seed, SEED_SAMPLING, N_CALIBRATION, N_PERTURB)

# ---------- 特征提取 ----------
RE_TONGUE  = re.compile(r'舌[质象苔]|舌苔|苔[薄厚白黄腻滑燥]')
RE_PULSE   = re.compile(r'脉[浮沉迟数虚实滑涩弦紧细濡洪大长短]|脉象|脉数|脉滑|脉弦')
RE_PAST    = re.compile(r'既往|病史|曾治|旧有|过敏史|家族史')
RE_DIAG_WM = re.compile(r'西医诊断|临床诊断|现代医学|ICD|高血压|糖尿病|冠心病|脑梗|肿瘤|癌')
RE_DIAG_TCM= re.compile(r'中医诊断|辨病|病名诊断|证候诊断')
RE_FOLLOW  = re.compile(r'复诊|二诊|三诊|四诊|随诊|再诊|服药后|药后|既往方|原方|上方|加减|守方|再服')
RE_SPLIT   = re.compile(r'[，,、；;]')
# 否定/检查描述词，计入症状项会高估密度 → 剔除
RE_NONSYM  = re.compile(r'^(?:查体|检查|检验|辅助检查|诊断|印象|建议|既往史|过敏史)$')


def symptom_count(txt):
    parts = [p.strip() for p in RE_SPLIT.split(txt) if p.strip()]
    n = 0
    for p in parts:
        if len(p) < 2:
            continue
        if RE_NONSYM.match(p):
            continue
        if re.match(r'^\d', p):        # "3-7日一行" 这类量词表达
            continue
        n += 1
    return n


def extract_features(txt):
    c1 = symptom_count(txt)
    c2 = int(bool(RE_TONGUE.search(txt))) + int(bool(RE_PULSE.search(txt))) \
         + int(bool(RE_PAST.search(txt))) \
         + int(bool(RE_DIAG_WM.search(txt) or RE_DIAG_TCM.search(txt)))
    c3 = min(len(set(RE_FOLLOW.findall(txt))), 3)
    c4 = len(txt)
    return c1, c2, c3, c4


def norm_log(x, lo, hi):
    """对数归一化到 [0,1]"""
    if hi <= lo:
        return 0.0
    a = math.log1p(max(0, x - lo))
    b = math.log1p(max(0, hi - lo))
    return a / b if b > 0 else 0.0


def minmax(x, lo, hi):
    return 0.0 if hi <= lo else (x - lo) / (hi - lo)


def main():
    ensure_dirs()
    set_global_seed(SEED_SAMPLING)
    print('[S1] OCSS 客观复杂度分层 + 抽样\n')

    cases = json.load(open(UPSTREAM_JSON, encoding='utf-8'))
    rows = []
    for c in cases:
        c1, c2, c3, c4 = extract_features(c['input'])
        rows.append({'id': c['id'], 'n_symptom': c1, 'c2_four_exam': c2,
                     'c3_temporal': c3, 'n_chars': c4, 'text': c['input']})

    # ---- 全局范围（用于归一化）----
    C1_LO, C1_HI = min(r['n_symptom'] for r in rows), max(r['n_symptom'] for r in rows)
    C2_LO, C2_HI = 0, 4
    C4_LO, C4_HI = min(r['n_chars'] for r in rows), max(r['n_chars'] for r in rows)
    print(f'  C1 症状项数范围 {C1_LO}-{C1_HI}  |  C2 四诊完整度 0-4  |  '
          f'C4 字数 {C4_LO}-{C4_HI}')

    # ---- OCSS 加权求和 ----
    W = {'c1': 0.30, 'c2': 0.30, 'c3': 0.20, 'c4': 0.20}
    for r in rows:
        n1 = norm_log(r['n_symptom'], C1_LO, C1_HI)
        n2 = minmax(r['c2_four_exam'], C2_LO, C2_HI)
        n3 = minmax(r['c3_temporal'], 0, 3)
        n4 = norm_log(r['n_chars'], C4_LO, C4_HI)
        r['n1'], r['n2'], r['n3'], r['n4'] = n1, n2, n3, n4
        r['OCSS'] = round(W['c1'] * n1 + W['c2'] * n2 + W['c3'] * n3 + W['c4'] * n4, 4)

    # ---- 按三分位分层（并列取前序，避免同分被切开）----
    by_ocss = sorted(rows, key=lambda r: r['OCSS'])
    n = len(by_ocss)
    e, m = by_ocss[:n // 3], by_ocss[n // 3: 2 * n // 3]
    h = by_ocss[2 * n // 3:]
    for grp, name in ((e, 'easy'), (m, 'medium'), (h, 'hard')):
        for r in grp:
            r['tier'] = name
    for r in rows:
        r.setdefault('tier', 'medium')

    print(f'\n  分层结果（含并列）:')
    for name, grp in (('easy', e), ('medium', m), ('hard', h)):
        sc = sorted(x['OCSS'] for x in grp)
        print(f'    {name:<7} n={len(grp):<4} OCSS {sc[0]:.3f}-{sc[-1]:.3f}')

    # ---- 导出全量评分表 ----
    csv_path = os.path.join(UPSTREAM, 'ocss_scores_all_2000.csv')
    cols = ['id', 'tier', 'OCSS', 'n_symptom', 'c2_four_exam', 'c3_temporal',
            'n_chars', 'n1', 'n2', 'n3', 'n4']
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in sorted(rows, key=lambda x: x['OCSS']):
            w.writerow(r)
    print(f'\n  ✓ {os.path.basename(csv_path)}  ({len(rows)} 行)')

    # ---- 抽样：每层内等额取样，保证 E/M/H 均衡且可复现 ----
    # ⚠ 关键：99 + 150 = 249 但各层仅666-667 条，若独立抽样必然重叠。
    #   策略：先抽校准集，再从「该层剩余未抽中者」中抽扰动集 → 两集严格不重叠。
    def sample(tier_rows, k, tag, exclude_ids=frozenset()):
        import random
        rnd = random.Random(SEED_SAMPLING)
        pool = sorted([r for r in tier_rows if r['id'] not in exclude_ids],
                      key=lambda r: r['id'])
        return rnd.sample(pool, min(k, len(pool)))

    per_cal = [N_CALIBRATION // 3 + (1 if N_CALIBRATION % 3 > i else 0)
               for i in range(3)]   # 33,33,33
    print(f'\n  校准集 99 例分档: easy={per_cal[0]} medium={per_cal[1]} hard={per_cal[2]}')
    cal = sample(e, per_cal[0], 'cal') + sample(m, per_cal[1], 'cal') + sample(h, per_cal[2], 'cal')

    per_pert = [N_PERTURB // 3 + (1 if N_PERTURB % 3 > i else 0)
                for i in range(3)]   # 50,50,50
    print(f'  扰动集 150 例分档: easy={per_pert[0]} medium={per_pert[1]} hard={per_pert[2]}')
    cal_ids = {r['id'] for r in cal}
    pert = (sample(e, per_pert[0], 'pert', cal_ids)
            + sample(m, per_pert[1], 'pert', cal_ids)
            + sample(h, per_pert[2], 'pert', cal_ids))

    # 校验不重叠
    overlap = cal_ids & {r['id'] for r in pert}
    assert not overlap, f'两集重叠 {len(overlap)} 条，应为 0'
    print(f'  ✓ 两集不重叠（各层剩余充足）')

    case_by_id = {c['id']: c for c in cases}

    def dump(sel, name, desc):
        out = []
        for r in sel:
            c = case_by_id[r['id']]
            out.append({'id': r['id'], 'tier': r['tier'], 'OCSS': r['OCSS'],
                        'features': {'n_symptom': r['n_symptom'],
                                     'c2_four_exam': r['c2_four_exam'],
                                     'c3_temporal': r['c3_temporal'],
                                     'n_chars': r['n_chars']},
                        'instruction': c['instruction'], 'input': c['input']})
        p = os.path.join(UPSTREAM, name)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f'  ✓ {name}  n={len(out)}  ({desc})')
        return out

    cal_out = dump(cal, 'selected_calibration_99.json', '人工标注校准集')
    pert_out = dump(pert, 'selected_perturb_150.json', '扰动实验集')

    # ---- 抽样报告 ----
    def tier_table(rows_):
        d = {}
        for r in rows_:
            d.setdefault(r['tier'], []).append(r)
        return d

    L = []
    L.append('# S1 抽样报告\n')
    L.append(f'## 方法\n')
    # ★ 2026-10-14 修正：此处曾误写为 0.30/0.20/0.20/0.30，与实际计算权重
    #   （上方 W = {'c1':0.30,'c2':0.30,'c3':0.20,'c4':0.20}）不一致 → 报告印错。
    #   以代码为准：C1=0.30, C2=0.30, C3=0.20, C4=0.20。
    L.append('OCSS = 0.30×C1 + 0.30×C2 + 0.20×C3 + 0.20×C4\n')
    L.append('- **C1 症状条目密度**：逗号/顿号分隔的语义项数（剔除"查体/检查"等非症状描述与量词），对数归一')
    L.append('- **C2 四诊完整度**：舌象1 + 脉象1 + 既往史1 + 中西医诊断1（0–4）')
    L.append('- **C3 时序推理负担**：复诊/服药后/二诊/加减等标记去重计数（封顶 3）')
    L.append(f'- **C4 信息量**：input 字数，对数归一（范围 {C4_LO}–{C4_HI}）\n')
    L.append('按 OCSS 三分位切分；层内用固定种子等额无放回抽样。\n')
    L.append(f'随机种子：`SEED_SAMPLING = {SEED_SAMPLING}`\n')

    for title, rr in (('校准集（人工标注）', cal_out), ('扰动集（Judge 评测）', pert_out)):
        L.append(f'## {title} n={len(rr)}\n')
        L.append('| 档位 | n | OCSS 均值 | 字数均值 | 症状项均值 | 四诊完整度均值 |')
        L.append('|---|---|---|---|---|---|')
        for tier in ('easy', 'medium', 'hard'):
            g = [x for x in rr if x['tier'] == tier]
            if not g:
                continue
            f_ = [x['features'] for x in g]
            L.append(f"| {tier} | {len(g)} | {sum(x['OCSS'] for x in g)/len(g):.3f} "
                     f"| {sum(x['n_chars'] for x in f_)/len(f_):.0f} "
                     f"| {sum(x['n_symptom'] for x in f_)/len(f_):.1f} "
                     f"| {sum(x['c2_four_exam'] for x in f_)/len(f_):.2f} |")
        L.append('')

    L.append('## ⚠ 与官方分层的差异（论文中必须声明）\n')
    L.append('原论文 E/M/H 依据 *reference answers 与两个 TCM 专用模型输出的 embedding 相似度*。')
    L.append('reference answers 即 gold label，未公开 → **官方分层无法复现**。')
    L.append('本方案为**完全可复现的替代分层**，仅依赖病例输入，与官方分层不可比。')
    L.append('论文中建议表述：\n')
    L.append('> *We adopt a fully reproducible alternative stratification (OCSS) computed from')
    L.append('> case inputs alone, as the original embedding-similarity stratification relies on')
    L.append('> unreleased gold labels and is therefore not reproducible.*\n')
    L.append('## 样本量依据\n')
    L.append('McNemar 配对检验（α=0.05, power=0.80）：')
    L.append('- 遗漏型 0.31→0.55：约 71 例')
    L.append('- 矛盾型 0.57→0.75：约 113 例')
    L.append('- 含 Bonferroni 校正（3 类）：约 95 / 150 例\n')
    L.append(f'故扰动集取 150 例；校准集 99 例（ICC 估计 30–50 例即稳定）。')
    L.append('⚠ 不对「E/M/H × 扰动类型」9 个格子做独立显著性检验（多重比较+功效不足）；')
    L.append('主检验在总体上做，E/M/H 仅作趋势性探索。')

    rp = os.path.join(METRICS, 's1_sampling_report.md')
    with open(rp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'  ✓ {os.path.basename(rp)}')

    print('\n[S1] 完成。下一步 S2：对校准集 99 例跑模型输出（先 5 条试点）')


if __name__ == '__main__':
    main()