# -*- coding: utf-8 -*-
"""
S3：三类扰动构造器（核心工具）。

设计原则（对应 RESEARCH_DESIGN.md 第六节）：
  1. 单点注入 —— 每例只注入 1 处错误，对齐官方 "single targeted errors"
  2. 位置轮转 —— 错误类型均匀分布，避免 Judge 学到固定模式
  3. 长度守恒 —— 扰动后字数变化 ≤ ±5%，排除「变短所以分低」的混淆

★ 真值即构造：扰动位置由本脚本确定 → 无需人工判断「有没有错」，
  直接算检出率。这绕开了「自标金标准 = 循环论证」的死结。

用法：
  python s03_build_perturbations.py --input 01_data/raw_outputs/raw_xxx.jsonl
  python s03_build_perturbations.py --input <path> --types P1,P2,M1
  # 每类可指定强度：--severity light|medium|heavy
"""
import os, sys, json, re, random, argparse, hashlib
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, '00_config'))
from config import (RAW, PERT, LOGS, ensure_dirs, set_global_seed,
                    SEED_PERTURB, BLOCKS)

PERTURB_TYPES = ['P1_coldheat', 'P2_treatment', 'M1_omission']

# ============ 词表 ============
# 寒热方向反转对（用于 P1）
COLD = ['温阳', '温中', '温里', '散寒', '助阳', '回阳', '祛寒', '温经',
        '辛温', '甘温除热', '温阳化气', '扶阳']
HOT = ['清热', '清暑', '泻火', '清胃', '清心', '凉血', '解毒',
       '苦寒', '甘寒', '清热化痰', '清利湿热']

# 攻补方向反转对（用于 P2）
TONIFY = ['补益', '益气', '养血', '滋阴', '温补', '补肾', '补脾', '固表', '敛汗', '涩肠']
PURGE = ['攻下', '峻下', '祛邪', '泻下', '破血', '逐瘀', '攻补兼施',
         '泻火解毒', '消导', '涌吐']

# 遗漏时删掉的方解要素（用于 M1）
# ⚠️ 实测教训：真实方解几乎不写「君药/臣药」这类术语，而写「为君」「为臣」「为佐」
#   若只匹配术语表，会漏掉真正的配伍论证 → 必须同时匹配「君臣佐使角色」与「配伍目的」两类模式。
FORMULA_ANALYSIS_ELEMENTS = [
    '君药', '臣药', '佐药', '使药', '君臣佐使', '引经', '佐制', '反佐',
]
# 角色表述模式（真实医案高频）
ROLE_PATTERNS = [
    r'为君', r'君药', r'作君', r'为臣', r'臣药', r'作臣',
    r'为佐', r'佐药', r'作佐', r'为使', r'使药', r'作使',
    r'君臣', r'佐使', r'君药', r'臣佐',
    #★ 2026-10-12 补充：ShizhenGPT 用「X为君药」而非「君药X」
    r'君药\s*[为是：:]', r'臣药\s*[为是：:]', r'佐药\s*[为是：:]',
]
# 配伍关系/ 功效归因模式（说明「为何这么组方」）
FORMULA_RATIONALE_PATTERNS = [
    r'针对', r'针对乏力之本', r'顾护', r'制约', r'防止', r'助运化',
    r'气血生化', r'增强', r'兼顾', r'为佐', r'调和诸药', r'引经',
    r'化裁', r'相须', r'相使', r'相畏', r'相杀',
]

# ============ ★ 方解体例识别（2026-10-12 关键修复）============
# 背景：ShizhenGPT-7B 的方解**不用**「君臣佐使」字样，而是写成
#     ①「方名：药名+功效」   例：1. **黄芪建中汤**：黄芪补中益气，桂枝温通阳气…
#     ②「功效标签：药名+功效」 例：2. **温中散寒**：制附子、干姜温补脾肾之阳…
#   这两种都是**规范有效的方解**，但原检测只认「君/臣/佐/使」字面词
#   → M1（方解遗漏）命中率实测 0/30，扰动实验无法进行。
#
# 修复：新增「方解体例模式」，识别「标签 + 冒号 + 药物功效罗列」这一体例。
FORMULA_STYLE_PATTERNS = [
    # 方名 + 冒号（加粗或书名号均可）：**黄芪建中汤**：…
    r'\*\*[^*]{2,12}(?:汤|丸|散|饮|膏|丹|煎|方)\*\*\s*[：:]',
    r'[《【][^》】]{2,12}(?:汤|丸|散|饮|膏|丹|煎|方)[》】]\s*[：:]',
    # 功效标签 + 冒号：**温中散寒**：… / 温中散寒：…
    r'\*\*[^*]{2,10}\*\*\s*[：:]\s*[\u4e00-\u9fa5]{2,}',
    # 药物 + 常见功效动词的罗列（黄芪补中益气 / 桂枝温通阳气）
    r'[\u4e00-\u9fa5]{2,4}(?:补中益气|温通|缓急|温补|温中|健脾|益气|'
    r'补益|温养|清热|凉血|活血|化痰|理气|止呕|安神|收敛|解表|利水|'
    r'固表|升提|降气|养阴|滋阴|温阳|散寒|清热|祛湿|通络|'
    r'生津|润燥|杀虫|解毒)',
]
# 药物功效短语（用于回填，保证回填文本不含配伍逻辑）
_DRUG_ACTION_RE = re.compile(
    r'([\u4e00-\u9fa5]{2,4})(补中益气|温通|缓急|温补|温中|健脾|益气|'
    r'补益|温养|清热|凉血|活血|化痰|理气|止呕|安神|收敛|解表|利水|'
    r'固表|升提|降气|养阴|滋阴|温阳|散寒|祛湿|通络|生津|润燥|解毒)')


def _has_formula_analysis(sent):
    """判断某句是否为「方解的配伍论证」。

    ★ 三类体例都要认（2026-10-12 扩充，原先只认第一类导致 M1 命中率 0/30）：
      ① 君臣佐使明写：「黄芪为君，益气扶正」
      ② 配伍关系词：「针对乏力之本」「相须」「调和诸药」
      ③ 方名/功效标签 + 药物功效罗列（ShizhenGPT 的实际写法）：
         「**黄芪建中汤**：黄芪补中益气，桂枝温通阳气」
    """
    for pat in (ROLE_PATTERNS + FORMULA_RATIONALE_PATTERNS
                + FORMULA_STYLE_PATTERNS):
        if re.search(pat, sent):
            return True
    for e in FORMULA_ANALYSIS_ELEMENTS:
        if e in sent:
            return True
    return False


# ============ 长度守恒用的等长回填句库 ============
# 要求：① 长度可调（按模板补字）；② 语法自然；③ **不含任何君臣佐使配伍论证**
# 这些句子描述药物的来源/炮制/常见用法，与"为何这样配伍"无关。
_FILLER_POOL = [
    # —— 短句（约 20-32 字）——
    '{drug}为常用药，临床应用广泛，性味平和，可按证加减。',
    '本方所用{drug}需依患者体质与兼证情况酌情调整用量。',
    # ★ 措辞纪律（2026-10-12）：回填句必须**不含** _FILLER_FORBIDDEN 里的词，
    #   否则被 _has_formula_analysis 误判为「配伍论证」，
    #   M1 删掉配伍内容后又补回配伍句 → **扰动静默失效**（最危险的失败）。
    '{drug}的用量宜依患者实际情况灵活掌握，不宜拘泥成方。',
    '{drug}的选用须结合当前病程阶段与脾胃耐受情况判断。',
    # —— 中句（约 34-46 字）——
    '方中{drug}的剂量以常规用量为度，急性期可酌情增减，'
    '并在治疗过程中随时观察反应。',
    '选用{drug}时应留意其性味特点与患者体质是否相宜，'
    '亦须留意服药后的主观感受。',
    '关于{drug}的具体用量，可依据患者体重与病情轻重作适度调整，'
    '并在随访中记录效果。',
    '临床实践中{drug}的注意事项须结合合并用药情况进一步确认，'
    '以保证用药安全。',
    # —— 长句（约 48-62 字）——
    #★ 2026-10-12 新增：ShizhenGPT 的方解单句常有 50-70 字
    #   （如「黄芪补中益气，桂枝温通阳气，白芍缓急止痛…」），
    #   原句库最长仅 32 字 → 回填补不上长度，±5% 守恒必然失败。
    '需要说明的是，{drug}的常规用量与加减幅度，应结合患者年龄、体质'
    '强弱以及合并用药情况综合判断，不宜一概而论，临床仍须辨证取舍。',
    '关于{drug}的用量，历代医家论述颇多，意见不尽一致；'
    '本方所取剂量系依据本证特点而设，若证候有变，当随之调整。',
    '从组方角度看，{drug}的作用与其炮制方法、产地及入汤先后有一定关系，'
    '临床应用时须予注意，具体当依据患者实际情况灵活掌握，不宜一概而论。',
    '此外，{drug}在使用过程中还应注意与饮食、起居的配合，'
    '并观察服药后的反应，若出现不适及时调整方案，以求稳妥。',
    '{drug}的现代药理研究提示其可能具有某些相应的作用，'
    '但疗效的判定仍应以辨证准确、组方得当为前提，'
    '不可仅凭现代研究结果而偏离中医辨证规律。',
    # —— 超长句（约 66-82 字）——
    # ★ 2026-10-12 新增：ShizhenGPT「方名体例」的句子可达 70-85 字
    #   （如「**黄芪建中汤**：黄芪补中益气，桂枝温通阳气，白芍缓急止痛，
    #     饴糖甘温补中」整句 74 字），原句库最长 62 字 → 差 12 字。
    '{drug}的具体应用，历代医家多有论述，其炮制方式、产地差异与入汤次序，'
    '均可能影响最终效果；临床运用时当结合患者年龄、体质强弱、'
    '病程阶段以及合并用药情况综合权衡，不宜一概而论，'
    '亦须随时观察反应并及时调整。',
    '关于{drug}的禁忌与不良反应，历来文献记载不尽相同，'
    '凡体虚、年老、儿童及孕妇尤应审慎；'
    '使用期间还须留意饮食起居的配合，避免过食生冷或辛辣之物，'
    '并观察服药后的反应，一旦出现不适应当及时调整用药方案。',
]

# ★ 回填句禁用词表：命中任一即认为该回填句「不干净」（含配伍论证特征）
_FILLER_FORBIDDEN = [
    '兼顾', '针对', '标本缓急', '配伍', '君药', '臣药', '佐药', '使药',
    '君臣佐使', '相须', '相使', '相畏', '相杀', '调和诸药', '引经',
    '温中', '益气', '健脾', '温补', '温通', '缓急', '补中益气',
    '温阳', '散寒', '化裁',
]


def _filler_is_clean(text):
    """回填句是否干净（不含配伍论证特征词）。"""
    return not any(w in text for w in _FILLER_FORBIDDEN)


# 常见中药名（用于判断抽取到的词是否真是药名）
_COMMON_HERBS = {
    '黄芪', '党参', '白术', '茯苓', '甘草', '桂枝', '白芍', '饴糖', '干姜',
    '制附子', '附子', '陈皮', '升麻', '柴胡', '当归', '半夏', '陈皮',
    '川芎', '丹参', '红花', '桃仁', '赤芍', '丹皮', '栀子', '黄芩',
    '黄连', '连翘', '薄荷', '菊花', '葛根', '柴胡', '桔梗', '枳壳',
    '木香', '砂仁', '山楂', '神曲', '麦芽', '鸡内金', '薏苡仁', '莲子',
    '山药', '枸杞子', '杜仲', '续断', '寄生', '独活', '羌活', '秦艽',
    '防风', '细辛', '麻黄', '桂枝', '杏仁', '桃仁', '大黄', '芒硝',
    '茯苓', '泽泻', '猪苓', '车前子', '滑石', '通草', '茵陈', '金钱草',
}


def _is_function_word(w):
    """判断是否为功效词而非药名（温中/散寒/补益/健脾…）。"""
    return w in ('温中', '温补', '温通', '温阳', '温养', '散寒', '清热',
                 '凉血', '补益', '益气', '健脾', '养阴', '滋阴', '活血',
                 '化痰', '理气', '解表', '利水', '固表', '升提', '降气',
                 '补中', '益气', '缓急', '止痛', '安神', '收敛', '生津',
                 '润燥', '解毒', '补中益气', '温通阳气', '补中强脾')


def _looks_like_herb(w):
    """粗判一个 2-4 字词是否像中药名。

    规则：以常见功效字开头/结尾的多为功效短语（如「补中益气」），
    以常见药名结尾的才是药名（如「黄芪」「制附子」）。
    """
    if w in _COMMON_HERBS:
        return True
    _TAIL_HERB_CHARS = '芪参术苓草枝芍姜皮藤叶花子仁芩连翘麻黄'
    _FUNC_CHARS = '补温清散寒益健养滋活理化泄升降安收生润解'
    if not w:
        return False
    # 首字是功能字且末字也是功能字 → 多半是功效短语
    if w[0] in _FUNC_CHARS and w[-1] in _FUNC_CHARS:
        return False
    # 命中常见药名库，或末字是药名字（如「制附子」「黄芪」）
    return w[-1] in _TAIL_HERB_CHARS or any(w in h for h in _COMMON_HERBS)


def _filler_for(sent):
    """为被删除的配伍论证句生成**严格等长**的回填句（不含配伍逻辑）。

    ★ 长度必须与原句几乎一致，否则 ±5% 长度守恒会被破坏。
      实现：从多个模板中选最接近的，再微调补字/截断。
    """
    core = sent.strip()
    target = len(core)
    # ★ 药名提取：不能直接取开头 2-4 字。
    #   方解体例 ② 的句子以「功效标签」开头（如「温中散寒：制附子、干姜…」），
    #   直接取会得到「温中散寒」这种功效词而非药名
    #   → 回填句变成「温中散寒为临床常用药」，语义荒谬且会被检测器误判。
    drug = '药物'
    # 优先取「功效标签：」之后的第一个药名
    body = re.sub(r'^[0-9一二三四五六七八九十\.、\s]*', '', core)
    body = re.sub(r'^[*【《（(][^】》）)]{0,14}[】》）)]?\s*[：:]\s*', '', body)
    m_after = re.search(r'([\u4e00-\u9fa5]{2,4})[、，]', body)
    if m_after:
        cand_drug = m_after.group(1)
        # ★ 也要校验：正则贪婪可能取到「黄芪补中」这种跨词片段
        if _looks_like_herb(cand_drug) and not _is_function_word(cand_drug):
            drug = cand_drug
        else:
            # 退化：截取该片段的前 2-3 字再试一次（如「黄芪补中」→「黄芪」）
            for k in (3, 2):
                sub = cand_drug[:k]
                if _looks_like_herb(sub) and not _is_function_word(sub):
                    drug = sub
                    break
    if drug == '药物':
        # 无列表结构时，取首个 2-4 字，但排除常见功效词/功效短语
        m_first = re.match(r'([\u4e00-\u9fa5]{2,4})', body)
        if m_first:
            cand_drug = m_first.group(1)
            if _looks_like_herb(cand_drug) and not _is_function_word(cand_drug):
                drug = cand_drug

    best, best_diff = None, 10 ** 9
    # ★ 选择策略：在**干净**候选里选长度最接近 target 的。
    #   （早期版本对"偏短"加权 2 倍，结果系统性偏长 → 长度反增到 +14%。）
    for tpl in _FILLER_POOL:
        cand = tpl.format(drug=drug)
        # 双保险：既要求「不含禁用词」，也要求不被检测器判为配伍论证
        if not _filler_is_clean(cand) or _has_formula_analysis(cand):
            continue
        d = abs(len(cand) - target)
        if d < best_diff:
            best, best_diff = cand, d
    if best is None:
        # ★ 用中性占位「药物」，**绝不能**用功效词（如"补中益气"）——
        #   功效词本身会被 _has_formula_analysis 判为配伍论证，
        #   导致回填文本反而"补回"了配伍内容，扰动静默失效。
        best = (f'该{drug}为临床常用药，其用量当依患者实际情况确定，'
                f'并在治疗过程中随时观察调整。') if drug != '药物' else \
               ('该药为临床常用药，其用量当依患者实际情况确定，'
                '并在治疗过程中随时观察调整。')

    # ★ 精确等长调节（2026-10-12）：
    #   句子库粒度有限，不可能刚好命中 target。
    #   偏短 → 反复追加**中性短语**直到恰好达标（每次都重验干净度）。
    #   偏长 → 后续的截断逻辑处理。
    # ★ 全部不带句号 —— 避免追加后形成「。。」，也便于精确控长
    _PAD_TAIL = ['，供参考', '，当随时观察', '，用量宜酌情增减',
                 '，临床据证取舍', '，不必拘泥', '，宜辨证使用']
    guard = 0
    while len(best) < target and guard < 20:
        need = target - len(best)
        placed = False
        for t in _PAD_TAIL:
            if len(t) <= need:
                trial = best + t
                if _filler_is_clean(trial) and not _has_formula_analysis(trial):
                    best, placed = trial, True
                    break
        if not placed:
            # 缺口小于所有短语 → 允许略微超出，交给下一步的裁剪兜底
            t = min(_PAD_TAIL, key=len)
            trial = best + t
            if _filler_is_clean(trial) and not _has_formula_analysis(trial):
                best = trial
            break
        guard += 1
    # 严格等长调整
    if len(best) > target:
        # ★ 裁剪时优先保住「药名 + 前半句」，从**末尾的标点边界**回退，
        #   避免把药名切成碎片（曾出现「黄芪补中为常用药」）。
        #   若回退后仍偏长，才逐字裁到 target。
        cut = best[:target]
        for sep in ('，', '、', '；', '。'):
            k = cut.rfind(sep)
            if k >= max(6, target * 0.5):
                cut = cut[:k]
                break
        if len(cut) < target * 0.8:
            # 标点回退会掉太多 → 改用逐字裁剪（药名已在句首，尾部裁剪安全）
            cut = best[:max(6, target - 1)]
        best = cut.rstrip('，、；') + '。'
        # 裁剪后可能又偏短 → 再补一轮
        g2 = 0
        while len(best) < target and g2 < 6:
            need = target - len(best)
            t = next((x for x in _PAD_TAIL if len(x) <= need), None)
            if t is None:
                break
            trial = best + t
            if _filler_is_clean(trial) and not _has_formula_analysis(trial):
                best = trial
            g2 += 1
    elif len(best) < target:
        tail_pool = ['，并随证调整。', '，需结合病程判断。', '，用量宜适当。',
                     '，临床据证取舍。', '，不可一概而论。']
        for t in tail_pool:
            if len(best) + len(t) <= target:
                cand2 = best + t
                if _filler_is_clean(cand2) and not _has_formula_analysis(cand2):
                    best = cand2
                break
        if not best.rstrip().endswith('。'):
            best = best.rstrip('，、；') + '。'
    # ★ 标点清理（2026-10-12）：
    #   补足短语追加到已带句号的句子后会产生「。。」或「。，供参考。」
    #   这类瑕疵 —— 虽不影响扰动语义，但会让审稿人质疑文本质量。
    while '。。' in best:
        best = best.replace('。。', '。')
    best = re.sub(r'[，。；、]{2,}', '，', best)      # 连续标点
    best = re.sub(r'。[^。]{0,6}$', lambda mm2: '，' + mm2.group(0)[1:].rstrip('。'),
                  best) if best.endswith('。') and '。，' in best else best
    # 若结尾是「…确认。，供参考。」这类，合并成一句
    best = re.sub(r'。，', '，', best)
    if best.endswith('，'):
        best = best[:-1] + '。'
    if not best.rstrip().endswith('。'):
        best = best.rstrip('，、；') + '。'
    return best

# 严重程度定义
SEVERITY_LEVELS = ['light', 'medium', 'heavy']
SEVERITY_DESC = {
    'light': '仅替换 1 个术语，语义冲突最轻',
    'medium': '替换整个治法表述，并联动调整方药方向词',
    'heavy': '在 medium 基础上，同步改动治法+方解中的相关表述',
}

# ★ 长度守恒阈值：扰动前后全文字数变化不得超过 ±5%
#   理由：若扰动样本明显更短，Judge 打的低分可能来自"内容少"而非"逻辑错"，
#         会污染实验结论（这是 LLM-as-Judge 研究的常见陷阱）。
MAX_LEN_CHANGE = 0.05


def pick_pair(rnd, pairs, text):
    """从词表中找一组「原文含其一、改为其对偶」的替换对。"""
    for a, b in pairs:
        if a in text:
            return a, b
    # 也检测反向
    for a, b in pairs:
        if b in text:
            return b, a
    return None, None


def perturb_P1(text, rnd, severity='medium'):
    """P1 寒热颠倒：治法中的温↔清互换。矛盾型。"""
    for kw in ('治法', '证候诊断'):
        pass
    a, b = pick_pair(rnd, list(zip(COLD, HOT)), text)
    if a is None:
        a, b = pick_pair(rnd, list(zip(HOT, COLD)), text)
    if a is None:
        return None
    n = 1 if severity == 'light' else (2 if severity == 'medium' else 3)
    out = text.replace(a, b, n)
    return {'type': 'P1_coldheat', 'severity': severity,
            'block': '治法', 'detail': f'{a} → {b}',
            'pairs': [{'from': a, 'to': b}]}


def perturb_P2(text, rnd, severity='medium'):
    """P2 治法方向反转：补↔泻。矛盾型。"""
    a, b = pick_pair(rnd, list(zip(TONIFY, PURGE)), text)
    if a is None:
        a, b = pick_pair(rnd, list(zip(PURGE, TONIFY)), text)
    if a is None:
        return None
    n = 1 if severity == 'light' else (2 if severity == 'medium' else 3)
    out = text.replace(a, b, n)
    return {'type': 'P2_treatment', 'severity': severity,
            'block': '治法', 'detail': f'{a} → {b}',
            'pairs': [{'from': a, 'to': b}]}


def _split_sections(text):
    """返回 {block_name: (start, end)}，覆盖 7 个 block 的原文区间。
    start 为标题标签结束处，end 为下一 block 标题起始处。"""
    marks = {}
    for k in BLOCKS:
        alt = {'方解': ['方解', '方义', '配伍分析']}.get(k, [k])
        for a in alt:
            m = re.search(rf'(?:^|\n)[ \t]*(?:#{{1,4}}[ \t]*|[一二三四五六七八九十]+[、.][ \t]*)?{a}[ \t]*[:：]?[ \t]*(?=\n|$)',
                          text, re.M)
            if m:
                marks[k] = m.end()
                break
    keys = [k for k in BLOCKS if k in marks]
    keys.sort(key=lambda k: marks[k])
    spans = {}
    for i, k in enumerate(keys):
        st = marks[k]
        en = marks[keys[i + 1]] if i + 1 < len(keys) else len(text)
        spans[k] = (st, en)
    return spans


def perturb_M1(text, rnd, severity='medium'):
    """M1 方解遗漏：删除君臣佐使配伍论证（遗漏型，官方最弱仅 31%）。

    实现要点：
      ① 用 _split_sections 精确定位方解段区间（避免切片偏移）；
      ② 逐句判定是否含君臣佐使配伍论证；
      ③ 按 severity 删除前 N 句；
      ④ 为守住 ±5% 长度约束，超预算处用等长非配伍陈述回填；
      ⑤ 返回 old_section/new_section 供 apply_perturb 整段替换。
    """
    spans = _split_sections(text)
    if '方解' not in spans:
        return None
    st, en = spans['方解']
    section = text[st:en]
    if len(section.strip()) < 40:
        return None

    spans_sent = [(mm.start(), mm.end(), mm.group(0))
                  for mm in re.finditer(r'[^。；;]*[。；;]?', section)
                  if mm.group(0).strip()]
    analysis = [i for i, (_, _, s) in enumerate(spans_sent) if _has_formula_analysis(s)]
    if not analysis:
        return None      # 非君臣佐使体例 → 放弃（勿乱删）

    frac = {'light': 0.34, 'medium': 0.67, 'heavy': 1.0}[severity]
    n_del = max(1, round(len(analysis) * frac))
    delete_idx = analysis[:n_del]

    budget = int(len(text) * MAX_LEN_CHANGE)
    removed, fillers = [], []
    work = section

    # ★ 一律「等长回填」，故不再需要"多删几句"来凑长度
    #   （该逻辑是为整句删除设计的，等长回填后净变化恒为 0，
    #     保留反而会让 heavy 过度删除 → -9.72%）。
    # 从后往前改，避免前面改动导致后面位置漂移
    for i in reversed(delete_idx):
        s0, e0, raw = spans_sent[i]
        # 位置可能因前面改动漂移 → 重新定位该句
        mm = re.search(re.escape(raw), work)
        if not mm:
            continue
        s0, e0 = mm.start(), mm.end()
        core = raw.strip()
        removed.insert(0, core)
        # ★ 一律用「等长回填」，不做整句删除。
        #   整句删除会累积超限（heavy 实测删4 句 → -9.72%），
        #   而回填句由 _filler_for 保证与原句等长 → 净变化为 0。
        cand = _filler_for(core)
        work = work[:s0] + cand + work[e0:]
        fillers.insert(0, cand.strip())

    if work == section:
        return None

    # ★ 校正 removed_sentences（2026-10-12）：
    #   「回填」也算删除（配伍论证已被替换掉），但若某句因 re.search
    #   未命中而根本没改动，则不得计入 removed —— 否则论文里
    #   声称"删了 N 句"会与实际文本不符。
    removed = [r for r in removed if r.strip() and r.strip() not in work]

    # ★ 长度守恒：整段替换（2026-10-12 重写）
    #   早期版本靠「多轮回填 + 段末补足句」凑长度，实测三次失败：
    #     ① 补足句被插到**下一个 block**（段末紧邻下一标题）
    #     ② 补足句使整段从 82 → 97 字，长度反增 +14%
    #   现在改为**最简策略**：回填句严格等长（_filler_for 负责），
    #   若整段净变短，则**放弃**该条扰动（记录到failures），不硬凑。
    #   宁可少一条样本，也不产出长度失真的样本。

    # ★ 换行保护（2026-10-12）：
    #   section 形如 '\n<内容>\n\n'（首尾各带换行，因为区间从标题后取到下一标题前）。
    #   若回填文本恰好替换掉首尾换行，拼接后会变成
    #   「## 方解补中益气为临床常用药…」→ **标题被吞，Judge 读不到该block**。
    #   实测出现过，故显式补回。
    lead_nl = '\n' if section.startswith('\n') else ''
    trail_nl = ''
    m_trail = re.search(r'(\n+)$', section)
    if m_trail:
        trail_nl = m_trail.group(1)
    if not work.startswith('\n') and lead_nl:
        work = lead_nl + work
    if not work.endswith('\n'):
        work = work + (trail_nl or '\n')

    return {'type': 'M1_omission', 'severity': severity,
            'block': '方解',
            'detail': (f'删除 {len(removed)} 句配伍论证'
                       + (f'；{len(fillers)} 处用等长非配伍陈述回填以守住 ±5% 长度约束'
                          if fillers else '')),
            'removed_sentences': removed,
            'filler_sentences': fillers,
            'old_section': section,
            'new_section': work,
            'pairs': []}


PERTURB_FUNCS = {
    'P1_coldheat': perturb_P1,
    'P2_treatment': perturb_P2,
    'M1_omission': perturb_M1,
}


def len_change_pct(orig, pert):
    if not orig:
        return 0.0
    return (len(pert) - len(orig)) / len(orig) * 100


def build_one(rec, ptype, severity, rnd):
    """对单条记录注入一种扰动。输入 rec 为原始模型输出记录。"""
    original_text = rec['raw_clean']
    func = PERTURB_FUNCS[ptype]
    meta = func(original_text, rnd, severity)
    if meta is None:
        return None

    pert_text = PERTURB_FUNCS[ptype](original_text, rnd, severity) and None
    # 重新调用以拿到新文本（保持实现简单直观；两次调用结果一致因 rnd 已重置见下）
    return meta


def apply_perturb(text, meta):
    """根据 meta 实际执行替换/删除，返回扰动后文本。
    （拆开是为了让build_one 能先探测可行性再执行，保证记录准确）"""
    if meta['type'] == 'M1_omission':
        # ★ 整段替换：perturb_M1 已把方解段的新旧文本都算好，此处直接照搬，
        #   不做二次推断，避免"删除列表 / 回填列表"混淆导致扰动失效。
        old_seg = meta.get('old_section')
        new_seg = meta.get('new_section')
        if old_seg is None or new_seg is None:
            return None
        if old_seg not in text:
            return None          # 原文不匹配（不应发生）
        return text.replace(old_seg, new_seg, 1)
    else:
        out = text
        for p in meta['pairs']:
            out = out.replace(p['from'], p['to'])
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, help='原始输出 jsonl（s02b 产出）')
    ap.add_argument('--types', default='P1_coldheat,P2_treatment,M1_omission')
    ap.add_argument('--severity', default='medium', choices=SEVERITY_LEVELS)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    ensure_dirs()
    set_global_seed(SEED_PERTURB)
    rnd = random.Random(SEED_PERTURB)

    types = args.types.split(',')
    for t in types:
        if t not in PERTURB_FUNCS:
            ap.error(f'未知扰动类型 {t}；可选 {list(PERTURB_FUNCS)}')

    src_tag = os.path.basename(args.input).replace('.jsonl', '')
    out_path = os.path.join(PERT, f'perturb_{src_tag}_{args.severity}.jsonl')
    prog_path = out_path + '.progress'
    fail_path = os.path.join(PERT, f'perturb_failures_{src_tag}_{args.severity}.jsonl')
    log_path = os.path.join(LOGS, f's03_perturb_{src_tag}_{args.severity}.log')

    logf = open(log_path, 'w', encoding='utf-8')
    def log(*a):
        s = ' '.join(str(x) for x in a)
        print(s); logf.write(s + '\n'); logf.flush()

    log(f'[S3] 扰动构造  {datetime.now().isoformat()}')
    log(f'  输入      : {args.input}')
    log(f'  类型      : {", ".join(types)}')
    log(f'  强度      : {args.severity}  ({SEVERITY_DESC[args.severity]})')
    log(f'  随机种子  : SEED_PERTURB = {SEED_PERTURB}')
    log('')

    recs = [json.loads(l) for l in open(args.input, encoding='utf-8') if l.strip()]
    if args.limit:
        recs = recs[:args.limit]
    log(f'  待扰动    : {len(recs)} 条 → 每条最多 3 种')

    done = set()
    if os.path.isfile(prog_path):
        done = set(json.load(open(prog_path, encoding='utf-8')))
        if done:
            log(f'  断点续跑：已完成 {len(done)} 条')

    fout = open(out_path, 'a', encoding='utf-8')
    ffail = open(fail_path, 'a', encoding='utf-8')
    stats = {t: {'ok': 0, 'skip': 0, 'lenviol': 0} for t in types}
    len_changes = []

    for i, rec in enumerate(recs, 1):
        cid = rec['id']
        if cid in done:
            continue
        for ptype in types:
            rnd2 = random.Random(f'{SEED_PERTURB}_{cid}_{ptype}')  # 按例可复现
            meta = PERTURB_FUNCS[ptype](rec['raw_clean'], rnd2, args.severity)
            if meta is None:
                stats[ptype]['skip'] += 1
                ffail.write(json.dumps({
                    'id': cid, 'ptype': ptype, 'severity': args.severity,
                    'reason': '未找到可扰动位置（该类型词表未命中）',
                    'ts': datetime.now().isoformat()}, ensure_ascii=False) + '\n')
                continue
            pert_text = apply_perturb(rec['raw_clean'], meta)
            if pert_text is None or pert_text == rec['raw_clean']:
                stats[ptype]['skip'] += 1
                ffail.write(json.dumps({
                    'id': cid, 'ptype': ptype, 'severity': args.severity,
                    'reason': '扰动执行失败或文本未变化',
                    'ts': datetime.now().isoformat()}, ensure_ascii=False) + '\n')
                continue

            lp = len_change_pct(rec['raw_clean'], pert_text)
            len_changes.append(abs(lp))
            if abs(lp) > 5.0:
                stats[ptype]['lenviol'] += 1

            fout.write(json.dumps({
                'pair_id': f'{cid}::{ptype}::{args.severity}',
                'case_id': cid, 'tier': rec.get('tier'), 'OCSS': rec.get('OCSS'),
                'ptype': ptype, 'severity': args.severity,
                'perturb_meta': meta,
                'case_text': rec['input'],
                'original_output': rec['raw_clean'],
                'perturbed_output': pert_text,
                'original_blocks': rec.get('blocks', {}),
                'len_change_pct': round(lp, 2),
                'target_model': rec.get('model'),
                'seed': SEED_PERTURB,
                'ts': datetime.now().isoformat(),
            }, ensure_ascii=False) + '\n')
            stats[ptype]['ok'] += 1
        fout.flush()
        done.add(cid)
        json.dump(sorted(done), open(prog_path, 'w'), ensure_ascii=False)
        if i % 20 == 0:
            log(f'  [{i}/{len(recs)}] ' + '  '.join(
                f'{t.split("_")[0]}:{stats[t]["ok"]}' for t in types))

    fout.close(); ffail.close()

    log('')
    log('=== 统计 ===')
    for t in types:
        s = stats[t]
        rate = s['ok'] / max(len(recs), 1) * 100
        log(f'  {t:16s} 成功 {s["ok"]:>4} ({rate:>5.1f}%)  '
            f'不可扰动 {s["skip"]:>4}  超长守恒 {s["lenviol"]:>4}')
    if len_changes:
        log(f'  长度守恒: 平均 |Δ| {sum(len_changes)/len(len_changes):.2f}%  '
            f'最大 {max(len_changes):.2f}%  超 5% 的比例 '
            f'{sum(1 for x in len_changes if x > 5.0)/len(len_changes)*100:.1f}%')
    total = sum(s['ok'] for s in stats.values())
    log('')
    log(f'  总扰动样本: {total} 条 → {out_path}')
    log(f'  ⚠ 无法扰动的记录（已记录，勿忽略）: {fail_path}')
    log('  ★ 下一步：用 judge_prompts.py 的 v1 给original 打分，建立基线')
    logf.close()

    print(f'\n[S3] 完成')
    for t in types:
        s = stats[t]
        print(f'  {t:16s} {s["ok"]:>4}/{len(recs)} ({s["ok"]/max(len(recs),1)*100:.0f}%)'
              f'  不可扰动 {s["skip"]}')
    print(f'  总计 {total} 条扰动样本')
    print(f'  输出: {out_path}')


if __name__ == '__main__':
    main()