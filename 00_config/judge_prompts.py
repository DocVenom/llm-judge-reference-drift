# -*- coding: utf-8 -*-
"""
Judge Prompt 三个版本 —— 本项目的核心实验变量。

★ 每个 prompt 都是一个带版本号的常量，任何改动必须新增版本号，禁止原地覆盖
  （否则 v1/v2/v3 的对比就失去意义，评分结果无法归因）。

版本设计（对应 RESEARCH_DESIGN.md 第七节）：
  v1_baseline  严格复现官方 rubric 规格（evidence-constrained / 0-5 / score-cap）
  v2_reword    仅优化措辞 —— **控制变量组**，用于证明「纯 prompt 调优不够」
  v3_checklist v2 + 显式完整性检查表 —— **机制化修复**，核心创新点
"""

BLOCKS = ['思维链', '病因病机分析', '证候诊断', '治法', '处方', '方解', '症状变化与中药加减']

DIMS_BLOCK = [
    '病因病机_Block', '证候诊断_Block', '治则治法_Block', '处方生成_Block',
    '处方解析_Block', '症状变化推理_Block', '处方调整_Block',
]
DIMS_CROSS = ['病机_证候_Cross', '证候_治法_Cross', '治法_处方_Cross']
DIMS_ALL = DIMS_BLOCK + DIMS_CROSS

# ============ 官方 rubric 规格（README 原文摘录）============
# "each judgment must identify the diagnostic pathway, cite case-specific
#  discriminating evidence and the strongest counterevidence, and assign a
#  0 to 5 score bounded by scenario-dependent caps."
OFFICIAL_SPEC_ZH = """你是中医临床推理的评估专家。请依据【原始病历】评估【模型输出】。

评估要求：
1. 必须识别被评估响应的诊断推理路径
2. 必须引用本病例特异性的判别性证据
3. 必须给出最强的反证（counterevidence）
4. 每项评分为 0 到 5 分，并受场景上限（score cap）约束

除上述外，不使用任何外部知识库，不基于个人临床偏好扣分。
只要证据支撑、逻辑自洽，不同方案均可给高分。"""

# ============ v1：严格基线 ============
V1_BASELINE = OFFICIAL_SPEC_ZH + """

【评分维度】共 10 项，逐项独立评分：
Block-level（模型输出 vs 原始病历，判断结论有无病历证据支撑）：
  1. 病因病机_Block
  2. 证候诊断_Block
  3. 治则治法_Block
  4. 处方生成_Block
  5. 处方解析_Block
  6. 症状变化推理_Block
  7. 处方调整_Block
Cross-block（模型内部逻辑自洽，不对照病历真假）：
  8. 病机_证候_Cross
  9. 证候_治法_Cross
  10. 治法_处方_Cross

【评分区间】
  5 = 完全符合，证据充分，无逻辑漏洞
  4 = 基本合格，少量瑕疵，不影响核心推理
  3 = 部分合理，存在明显证据缺失或轻微逻辑矛盾
  2 = 少量沾边，核心推理有明显问题
  1 = 极少沾边，核心推理错误
  0 = 完全错误，无证据支撑，严重矛盾

【重要区分】
  Block 维度看「是否符合病历」；Cross 维度看「模型自身是否自洽」。
  例：病历为风热，模型辨证风寒 → Block 证候诊断低分；
      但若模型自身病机能推出风寒证，则 Cross 可给高分。

【输出格式】严格输出 JSON，不要任何额外文字：
{
  "diagnostic_pathway": "<识别出的诊断推理路径，1-3句>",
  "key_evidence": ["<判别性证据，引用病历原文片段>"],
  "counterevidence": ["<最强反证>"],
  "scores": {
    "<维度名>": {"score": <0-5整数>, "reason": "<简短理由，注明证据位置>"}
  },
  "overall": <0-5整数>
}"""

# ============ v2：仅优化措辞（控制变量）============
# 维度、评分区间、输出格式**完全不变**，只改指令表达方式。
# 目的：若v2 相对 v1 提升有限，则证明问题不在措辞而在机制。
V2_REWORD = """你是一位资深的中医临床推理评估专家。请针对给定的【原始病历】，
对【模型输出】进行系统评估。

你的任务包含三个层面：
(1) 梳理出该响应的诊断推理路径；
(2) 找出支持该路径的病例特异性判别证据；
(3) 同时给出可能削弱该结论的最强反证。

评分采用 0-5 整数，并遵循场景上限（score cap）。

评估时仅依据提供的病历与输出，不引入外部资料。
请勿因行文风格、术语偏好或临床流派差异扣分：
只要论证有据、逻辑自洽，替代方案同样可以获得高分。

【评分维度】共 10 项，逐项独立评分：
Block-level（模型输出 vs 原始病历，判断结论有无病历证据支撑）：
  1. 病因病机_Block
  2. 证候诊断_Block
  3. 治则治法_Block
  4. 处方生成_Block
  5. 处方解析_Block
  6. 症状变化推理_Block
  7. 处方调整_Block
Cross-block（模型内部逻辑自洽，不对照病历真假）：
  8. 病机_证候_Cross
  9. 证候_治法_Cross
  10. 治法_处方_Cross

【评分区间】
  5 = 完全符合，证据充分，无逻辑漏洞
  4 = 基本合格，少量瑕疵，不影响核心推理
  3 = 部分合理，存在明显证据缺失或轻微逻辑矛盾
  2 = 少量沾边，核心推理有明显问题
  1 = 极少沾边，核心推理错误
  0 = 完全错误，无证据支撑，严重矛盾

【重要区分】
  Block 维度看「是否符合病历」；Cross 维度看「模型自身是否自洽」。
  例：病历为风热，模型辨证风寒 → Block 证候诊断低分；
      但若模型自身病机能推出风寒证，则 Cross 可给高分。

【输出格式】严格输出 JSON，不要任何额外文字：
{
  "diagnostic_pathway": "<识别出的诊断推理路径，1-3句>",
  "key_evidence": ["<判别性证据，引用病历原文片段>"],
  "counterevidence": ["<最强反证>"],
  "scores": {
    "<维度名>": {"score": <0-5整数>, "reason": "<简短理由，注明证据位置>"}
  },
  "overall": <0-5整数>
}"""

# ============ v3：机制化修复（核心创新）============
# 关键改动：加入「完整性检查表」，强制 Judge 逐项核对必答要素。
# 直击核心假设 —— 遗漏型错误不与任何已陈述内容冲突，
# 缺少检查清单就无从发现；显式清单可将其转化为可核对项。
# ★★★ 2026-10-12 关键修复：v3 曾经出现**两个【输出格式】块**
#   —— v2 自带的旧格式块（只含 diagnostic_pathway / key_evidence /
#      counterevidence / scores / overall，**没有** missing_elements /
#      contradictions / checklist）+ 检查表之后的完整格式块。
#   同一个 prompt 里两条互相冲突的格式指令，模型很可能跟从**第一个**
#   → v3 不产出 missing_elements/contradictions
#   → **v3 的核心指标（显式检出 / 新增显式检出）恒为 0，整轮 GPU 白跑。**
#   修法：v3 只取 v2 的**正文部分**（【输出格式】之前），
#        再拼「检查表 + 交叉矛盾 + 完整格式」。
#   ⚠ V2_REWORD 本身**一字未动**（下面仍用它定义 PROMPTS），
#     控制组「仅改措辞」的对照关系完全不受影响。
_V2_HEAD = V2_REWORD.split('【输出格式】')[0].rstrip('\n')

V3_CHECKLIST = _V2_HEAD + """

【★ 完整性检查表（必须逐项核对并报告）】
以下 7 类要素若在模型输出中缺失，你**必须**在 `missing_elements` 中明确指出，
即使各维度评分看起来仍然很高：

  □ CE1 方解是否说明了君臣佐使的配伍逻辑？
  □ CE2 处方是否包含完整的药物组成与剂量？
  □ CE3 症状变化与中药加减是否覆盖了可能的症状演变？
  □ CE4 病机演变链是否完整（病因 → 病位 → 病性 → 演变过程）？
  □ CE5 是否引用了本病例的特异性证据，而非通用表述？
  □ CE6 是否给出了煎服法/ 用法？
  □ CE7 方解是否结合本病例病机，而非照搬通用教科书模板？

**遗漏与矛盾的区分**（务必分清）：
  · 矛盾型错误 = 输出内容之间**相互冲突**
    例：辨证为「湿热蕴脾」，治法却写「温阳散寒」
    → 这类错误会使相关维度分数显著下降
  · 遗漏型错误 = 某项应有内容**完全缺失**
    例：给出处方但方解未说明君臣佐使逻辑；列出症状变化却未给出对应加减
    → 这类错误**不与任何已陈述内容冲突**，因此只会表现为对应要素的缺失

对遗漏型错误，请同时在 `missing_elements` 与对应维度的 reason 中说明。

【★ 交叉矛盾检查】
除逐项评分外，请额外核查是否存在以下跨模块矛盾（若有，在
`contradictions` 中报告，未发现则明确写"无"）：
  · CC1 病机推论无法导出所给证候
  · CC2 治法与证候方向相反（如虚证用攻伐、实证用补益）
  · CC3 处方药性方向与治法相反（如热证用温热药、寒证用寒凉药）
  · CC4 寒热属性颠倒（热象用温法、寒象用寒法）
  · CC5 病机为虚损却重用峻猛攻伐
  · CC6 病机为实邪却一味补益

【输出格式】严格输出 JSON，不要任何额外文字：
{
  "diagnostic_pathway": "<识别出的诊断推理路径，1-3句>",
  "key_evidence": ["<判别性证据，引用病历原文片段>"],
  "counterevidence": ["<最强反证>"],
  "checklist": {
    "CE1": {"present": true/false, "note": "<说明>"},
    "CE2": {"present": true/false, "note": "<说明>"},
    "CE3": {"present": true/false, "note": "<说明>"},
    "CE4": {"present": true/false, "note": "<说明>"},
    "CE5": {"present": true/false, "note": "<说明>"},
    "CE6": {"present": true/false, "note": "<说明>"},
    "CE7": {"present": true/false, "note": "<说明>"}
  },
  "missing_elements": ["<缺失要素编号与描述，如 CE1: 方解未说明君臣佐使>"],
  "contradictions": ["<如 CC2: 辨证湿热蕴脾但治法温阳散寒>"],
  "scores": {
    "<维度名>": {"score": <0-5整数>, "reason": "<简短理由，注明证据位置>"}
  },
  "overall": <0-5整数>
}"""

# ============ v4：显式聚合规则（把「聚合稀释」从观察升级为干预）============
# ★ 2026-10-13 依据 s06c_agg_diag.py 的**零 GPU 诊断**设计，不是拍脑袋：
#   诊断实测 v3 的 M1 定向察觉已达 89.9%，但整体检出只有 16.2%
#   → 聚合保留率仅 18.0%，**73/89 条「已察觉但没传递到 overall」**。
#   即：信息在**聚合步骤**丢失（「指标设计」问题），而非模型看不出。
#   v4 = 把「局部缺陷必须传递到总体分」写成 prompt 里的**硬约束**。
#
# ⚠️ 规则**只依赖维度分**，刻意不含 missing_elements / contradictions，理由（实测）：
#   · 诊断显示 R4（missing 含核心 CE）在扰动答上触发 32.8%，看着很诱人；
#   · 但 Judge **每次只看见一个答案**（原答或扰动答），无从区分
#     「本次新增的缺失」与「本来就有但不影响可接受性的缺失」；
#   · 若把 R4 写成硬封顶，它会**同等地**压原答的分 → 特异度被打穿
#     （v3 原答有 91.6% 打满分，而 missing 报告率很高 —— 这正是"聚合稀释"本身）。
#   · 维度驱动的 R1/R2 在原答侧实测误报仅 1.0%，与现状持平 → **零代价**。
_V3_HEAD = V3_CHECKLIST.split('【输出格式】')[0].rstrip('\n')

V4_AGGREGATE = _V3_HEAD + """

【★★ 总分聚合规则（本版本的核心要求，必须严格遵守）】
`overall` **不是**十个维度的印象平均分，而是「该响应作为整体能否被接受」的判定。
一个致命的局部缺陷**不得**被其他维度的高分平均掉。

确定 `overall` 时，请先找出**全部 7 个 Block 维度中的最低分 min_block**，
再按下表确定 `overall` 的**上限**。这是**硬约束**，不得因为其余维度良好而放宽：

  R1  min_block ≤ 1  →  overall **≤ 2**
  R2  min_block = 2  →  overall **≤ 3**
  R3  min_block ≥ 3  →  overall 可自由给出（4 或 5）

语义依据（直接来自上面的评分区间定义，请勿改动）：
  · Block 维度 = 2  表示「核心推理有明显问题」 → 属实质错误 → overall ≤ 3
  · Block 维度 ≤ 1  表示「核心推理错误」       → overall ≤ 2
  换言之：**只有当 Block 维度全部 ≥ 3 时，overall 才允许达到 4 或 5。**

⚠️ 输出前必须自检（三步）：
  1. 列出 7 个 Block 维度及各自分数，标出最低分 min_block；
  2. 确认 `overall` 未超过上表规定的上限；
  3. 若你在 `contradictions` / `missing_elements` 中报告了某项缺陷，
     指向该缺陷的 Block 维度**分数本身**也应当已经体现它 ——
     不得出现「报告了矛盾 / 缺失，但对应维度仍给 4-5 分」的自相矛盾。

【输出格式】严格输出 JSON，不要任何额外文字：
{
  "diagnostic_pathway": "<识别出的诊断推理路径，1-3句>",
  "key_evidence": ["<判别性证据，引用病历原文片段>"],
  "counterevidence": ["<最强反证>"],
  "checklist": {
    "CE1": {"present": true/false, "note": "<说明>"},
    "CE2": {"present": true/false, "note": "<说明>"},
    "CE3": {"present": true/false, "note": "<说明>"},
    "CE4": {"present": true/false, "note": "<说明>"},
    "CE5": {"present": true/false, "note": "<说明>"},
    "CE6": {"present": true/false, "note": "<说明>"},
    "CE7": {"present": true/false, "note": "<说明>"}
  },
  "missing_elements": ["<缺失要素编号与描述，如 CE1: 方解未说明君臣佐使>"],
  "contradictions": ["<如 CC2: 辨证湿热蕴脾但治法温阳散寒>"],
  "scores": {
    "<维度名>": {"score": <0-5整数>, "reason": "<简短理由，注明证据位置>"}
  },
  "aggregation_check": "<列出 7 个 Block 维度分数与最低分 min_block，并说明 overall 受 R1 / R2 / R3 中哪一条约束>",
  "overall": <0-5整数>
}"""

# ============================================================================
# ★★★ 2026-10-15 新增：补实验三臂（设计与事前预期见 00_config/SUPP_EXPERIMENTS.md）
#
#   v5_checkeval  外部方法对照（忠实复现 CheckEval 配方）        ↔ 臂 D
#   v3_no_ce      v3 去掉 CE 段（保留 CC）→ 2×2 的 CE−CC+ 格    ↔ 臂 A1
#   v3_no_cc      v3 去掉 CC 段（保留 CE）→ 2×2 的 CE+CC− 格    ↔ 臂 A2
#
# ★ 纪律：以下三个常量**全部由既有常量的切片拼成**，不手抄正文 ——
#   以保证「相对 v3 恰好只去掉一个段落」，符合本项目"每步只动一个变量"的要求。
#   任何手抄都可能引入不可归因的差异。
# ============================================================================

# ---- v3 正文的两段切出来（v3 正文 = v2 正文 + CE 段 + CC 段）----
# ⚠ 命名澄清：上方供 v4 使用的 `_V3_HEAD` 实际含义是「v3 在【输出格式】之前的**整段正文**」
#   （名字有历史误导性），它 = v2 正文 + CE 段 + CC 段，**不是**本处的 _V3_PRE。
_V3_BODY = V3_CHECKLIST.split('【输出格式】')[0].rstrip('\n')
_CE_START = '【★ 完整性检查表'
_CC_START = '【★ 交叉矛盾检查】'
_V3_PRE = _V3_BODY[:_V3_BODY.index(_CE_START)].rstrip('\n')
_CE_SECTION = _V3_BODY[_V3_BODY.index(_CE_START):_V3_BODY.index(_CC_START)].rstrip('\n')
_CC_SECTION = _V3_BODY[_V3_BODY.index(_CC_START):].rstrip('\n')
# 硬断言，三条（任一不成立则 2×2 的可归因性不成立，直接让 import 失败）：
assert _V3_PRE == _V2_HEAD, \
    'v3 正文前段 ≠ v2 正文 —— 2×2 对照会失去可归因性'
assert _V3_HEAD == _V3_BODY, '缓存变量 _V3_HEAD 应等于 v3 整段正文'
assert _CE_SECTION.count('【★') == 1 and _CC_SECTION.count('【★') == 1, \
    'v3 的 CE / CC 段切分点有误（段落内不应再出现 【★ 标记）'

_V5_FMT_TAIL = """
【输出格式】严格输出 JSON，不要任何额外文字：
{
  "checklist": {
    "Q1": {"answer": "yes" 或 "no", "note": "<一句话依据>"},
    "Q2": {"answer": "yes" 或 "no", "note": "<一句话依据>"},
    ...  （Q1 至 Q20，一条不漏，顺序与上文一致）
  },
  "yes_count": <0-20 的整数>,
  "scores": {
    "<维度名>": {"score": <0-5整数>, "reason": "<简短理由，注明证据位置>"}
  },
  "overall": <0-5整数>
}"""

# ---- 输出格式：v3_no_ce（有 contradictions，无 checklist / missing_elements）----
_FMT_NO_CE = """
【输出格式】严格输出 JSON，不要任何额外文字：
{
  "diagnostic_pathway": "<识别出的诊断推理路径，1-3句>",
  "key_evidence": ["<判别性证据，引用病历原文片段>"],
  "counterevidence": ["<最强反证>"],
  "contradictions": ["<如 CC2: 辨证湿热蕴脾但治法温阳散寒>"],
  "scores": {
    "<维度名>": {"score": <0-5整数>, "reason": "<简短理由，注明证据位置>"}
  },
  "overall": <0-5整数>
}"""

# ---- 输出格式：v3_no_cc（有 checklist / missing_elements，无 contradictions）----
_FMT_NO_CC = """
【输出格式】严格输出 JSON，不要任何额外文字：
{
  "diagnostic_pathway": "<识别出的诊断推理路径，1-3句>",
  "key_evidence": ["<判别性证据，引用病历原文片段>"],
  "counterevidence": ["<最强反证>"],
  "checklist": {
    "CE1": {"present": true/false, "note": "<说明>"},
    "CE2": {"present": true/false, "note": "<说明>"},
    "CE3": {"present": true/false, "note": "<说明>"},
    "CE4": {"present": true/false, "note": "<说明>"},
    "CE5": {"present": true/false, "note": "<说明>"},
    "CE6": {"present": true/false, "note": "<说明>"},
    "CE7": {"present": true/false, "note": "<说明>"}
  },
  "missing_elements": ["<缺失要素编号与描述，如 CE1: 方解未说明君臣佐使>"],
  "scores": {
    "<维度名>": {"score": <0-5整数>, "reason": "<简短理由，注明证据位置>"}
  },
  "overall": <0-5整数>
}"""

# ---- 臂 A1：v3 − CE 段（保留 CC）----
V3_NO_CE = _V3_PRE + '\n\n' + _CC_SECTION + '\n' + _FMT_NO_CE

# ---- 臂 A2：v3 − CC 段（保留 CE）----
V3_NO_CC = _V3_PRE + '\n\n' + _CE_SECTION + '\n' + _FMT_NO_CC

# ---- 臂 D：CheckEval 式二元分解（基座 = 官方规格，不用 v2 措辞）----
# 为什么基座不用 _V2_HEAD：CheckEval 是**判分程序的替换**，不是措辞优化。
# 若基座用 v2 的措辞，"二元分解"的效果会与"改措辞"混淆；用官方规格可让
# v1 → v5 的对比**只隔离"二元分解"这一件事**。
# 刻意不含：CE1–CE7 条目、CC1–CC6 检查、missing_elements / contradictions 字段、
#           min_block 聚合规则（那是 v4 的机制）—— 否则与 v3/v4 同构到没有信息量。
# 刻意不含 0–5 语义锚点表：CheckEval 以二元项替代整体标量评分，锚点由聚合规则取代。
_CHECK_QUESTIONS = [
    ('病因病机_Block', '是否给出了明确的病因？'),
    ('病因病机_Block', '病位与病性的判断是否由病历中的具体证据支持？'),
    ('证候诊断_Block', '是否给出了明确的证候诊断名称？'),
    ('证候诊断_Block', '证候诊断是否由病历中的症状与舌脉证据支持？'),
    ('治则治法_Block', '是否给出了明确的治则？'),
    ('治则治法_Block', '治法方向是否与病历所见证候的属性一致？'),
    ('处方生成_Block', '是否给出了完整处方（药味与剂量）？'),
    ('处方生成_Block', '处方是否仅由病历证据支持、未引入未经证实的臆测？'),
    ('处方解析_Block', '是否给出了方解（组方逻辑）？'),
    ('处方解析_Block', '方解是否说明了君臣佐使的配伍关系？'),
    ('症状变化推理_Block', '是否给出了症状演变的推理？'),
    ('症状变化推理_Block', '该推理是否基于病历信息而非泛化套话？'),
    ('处方调整_Block', '是否给出了随症状变化的加减方案？'),
    ('处方调整_Block', '加减方案是否与前述病机与治法一致？'),
    ('病机_证候_Cross', '所给证候能否由所给病机推出？'),
    ('病机_证候_Cross', '病机与证候之间是否不存在冲突？'),
    ('证候_治法_Cross', '治法方向与证候属性是否一致（无相反）？'),
    ('证候_治法_Cross', '治法的峻缓程度是否与证候虚实相符？'),
    ('治法_处方_Cross', '处方药性方向是否与治法一致？'),
    ('治法_处方_Cross', '处方组成是否完整回应了治法的要求？'),
]

_V5_HEAD = """

【评估标准（共 10 个维度）】
Block-level（模型输出 vs 原始病历，判断结论有无病历证据支撑）：
  1. 病因病机_Block      2. 证候诊断_Block      3. 治则治法_Block
  4. 处方生成_Block      5. 处方解析_Block      6. 症状变化推理_Block
  7. 处方调整_Block
Cross-block（模型内部逻辑自洽，不对照病历真假）：
  8. 病机_证候_Cross     9. 证候_治法_Cross     10. 治法_处方_Cross

【★ 二元检查项（共 20 条，逐条独立作答 yes / no，不得跳过、不得合并）】
以下每条子问题都**只依据**【原始病历】与【待评估的模型输出】独立判断：

"""

_V5_AGG = """

【★ 聚合规则（必须严格执行，不得凭印象调整）】
  1. 每个维度各由 2 条子问题构成：**2 条全 yes → 该维度记 5 分；恰 1 条 yes → 3 分；0 条 yes → 1 分**。
  2. `overall` = round(5 × yes 总数 ÷ 20)，取整数，范围 0–5。

除上述外，不使用任何外部知识库，不基于个人临床偏好扣分。
"""
_V5_Q_BLOCK = '\n'.join(
    '  Q%-2d [%s] %s' % (i, dim, q)
    for i, (dim, q) in enumerate(_CHECK_QUESTIONS, 1))

V5_CHECKEVAL = (OFFICIAL_SPEC_ZH + _V5_HEAD + _V5_Q_BLOCK + _V5_AGG
                + _V5_FMT_TAIL)

PROMPTS = {
    'v1_baseline': V1_BASELINE,
    'v2_reword': V2_REWORD,
    'v3_checklist': V3_CHECKLIST,
    'v4_aggregate': V4_AGGREGATE,
    # ★ 2026-10-15 补实验（见 00_config/SUPP_EXPERIMENTS.md）
    'v5_checkeval': V5_CHECKEVAL,
    'v3_no_ce': V3_NO_CE,
    'v3_no_cc': V3_NO_CC,
}

# 补实验各臂的角色（供脚本/文档引用，避免键名歧义）
SUPP_ARMS = {
    'v5_checkeval': dict(role='D',  family='external-baseline',
                         desc='CheckEval 式二元分解 + 规则聚合（外部方法对照）'),
    'v3_no_ce':     dict(role='A1', family='ablation-2x2',
                         desc='v3 去掉 CE 完整性枚举段，保留 CC 交叉矛盾检查（CE−CC+）'),
    'v3_no_cc':     dict(role='A2', family='ablation-2x2',
                         desc='v3 去掉 CC 交叉矛盾检查段，保留 CE 完整性枚举（CE+CC−）'),
}

# ============ v3 独有：判定检出用的规则 ============
# 用于从 Judge 输出中自动判定「是否检出了扰动」
DETECT_RULES = {
    # P1 寒热颠倒 / P2 治法反转→ 属于矛盾型，靠 contradictions 字段与维度分下降
    'contradiction_fields': ['contradictions'],
    # M1 方解遗漏 → 属于遗漏型，靠 missing_elements 含 CE1/CE5/CE7
    'omission_elements': ['CE1', 'CE5', 'CE7'],
    # 所有类型通用：分数显著下降
    'score_drop_threshold': 1.0,   # perturbed 比 original 低 ≥1.0 分视为隐式检出
}

# ============ 用户提示模板 ============
USER_PROMPT_TEMPLATE = """【原始病历】
{case_text}

【待评估的模型输出】
{model_output}

请按上述要求评估，只输出 JSON。"""


def build_user_prompt(case_text, model_output):
    return USER_PROMPT_TEMPLATE.format(
        case_text=case_text.strip(),
        model_output=model_output.strip(),
    )


def get_prompt(version):
    if version not in PROMPTS:
        raise KeyError(f'未知版本 {version}；可选：{list(PROMPTS)}')
    return PROMPTS[version]


def check_v3_diff():
    """自检：v1/v2 维度与格式必须一致，v3 必须新增 checklist 字段。
    用于防止误改导致对比失效。"""
    issues = []
    for v in ('v1_baseline', 'v2_reword'):
        p = PROMPTS[v]
        for d in DIMS_ALL:
            if d not in p:
                issues.append(f'{v} 缺维度 {d}')
        if 'missing_elements' in p:
            issues.append(f'{v} 不应含 checklist 字段（会污染控制变量）')
    v3 = PROMPTS['v3_checklist']
    for ce in ('CE1', 'CE2', 'CE3', 'CE4', 'CE5', 'CE6', 'CE7'):
        if ce not in v3:
            issues.append(f'v3 缺检查项 {ce}')
    if 'contradictions' not in v3:
        issues.append('v3 缺 contradictions 字段')

    # ---- v4 自检（2026-10-13 新增）----
    v4 = PROMPTS['v4_aggregate']
    for ce in ('CE1', 'CE2', 'CE3', 'CE4', 'CE5', 'CE6', 'CE7'):
        if ce not in v4:
            issues.append(f'v4 缺检查项 {ce}')
    for tok in ('aggregation_check', 'min_block', 'R1', 'R2', 'R3'):
        if tok not in v4:
            issues.append(f'v4 缺聚合规则要素 {tok}')
    # ★ v3 踩过的坑：一个 prompt 里出现**两个【输出格式】块** → 模型跟从第一个，
    #   新增字段永不产出、整轮 GPU 白跑。这里对**所有版本**统一设防。
    for name in PROMPTS:
        n_fmt = PROMPTS[name].count('【输出格式】')
        if n_fmt != 1:
            issues.append(f'{name} 出现 {n_fmt} 个【输出格式】块（必须恰好 1 个）')
    # v4 必须以 v3 为基底：删掉聚合规则段后应还原成 v3 的正文
    if V3_CHECKLIST.split('【输出格式】')[0].rstrip('\n') not in v4:
        issues.append('v4 未以 v3 正文为基底（对比关系会被破坏）')

    # ---- 补实验三臂自检（2026-10-15 新增，见 SUPP_EXPERIMENTS.md）----
    # 臂 A1 / A2：相对 v3 恰好去掉一个段落，字段必须互斥，否则 2×2 不可归因
    a1, a2 = PROMPTS['v3_no_ce'], PROMPTS['v3_no_cc']
    for cc in ('CC1', 'CC2', 'CC3', 'CC4', 'CC5', 'CC6'):
        if cc not in a1:
            issues.append(f'v3_no_ce 缺矛盾检查项 {cc}')
    for tok in ('CE1', 'CE2', 'CE5', 'missing_elements', 'checklist'):
        if tok in a1:
            issues.append(f'v3_no_ce 不应含 {tok}（已去掉 CE 段，混入即失去可归因性）')
    if 'contradictions' not in a1:
        issues.append('v3_no_ce 缺 contradictions 字段')
    for ce in ('CE1', 'CE2', 'CE3', 'CE4', 'CE5', 'CE6', 'CE7'):
        if ce not in a2:
            issues.append(f'v3_no_cc 缺检查项 {ce}')
    for tok in ('CC1', 'CC2', 'contradictions'):
        if tok in a2:
            issues.append(f'v3_no_cc 不应含 {tok}（已去掉 CC 段，混入即失去可归因性）')
    if 'missing_elements' not in a2:
        issues.append('v3_no_cc 缺 missing_elements 字段')
    for nm, txt in (('v3_no_ce', a1), ('v3_no_cc', a2)):
        if not txt.startswith(_V2_HEAD):
            issues.append(f'{nm} 未以 v2 正文为基底（2×2 对照会失效）')
        if _CE_SECTION not in V3_CHECKLIST or _CC_SECTION not in V3_CHECKLIST:
            issues.append(f'{nm} 的段落切片未能回溯到 v3 原文')

    # 臂 D：v5 必须与 v3/v4 的机制彻底分开，否则对照没有信息量
    v5b = PROMPTS['v5_checkeval']
    if not v5b.startswith(OFFICIAL_SPEC_ZH):
        issues.append('v5_checkeval 未以官方规格为基座（会与 v2 措辞混淆）')
    n_q = sum(1 for i in range(1, len(_CHECK_QUESTIONS) + 1) if f'Q{i} ' in v5b)
    if n_q != len(_CHECK_QUESTIONS):
        issues.append(f'v5_checkeval 二元项应为 {len(_CHECK_QUESTIONS)} 条，'
                      f'实际检出 {n_q} 条')
    for tok in ('CE1', 'CC1', 'missing_elements', 'contradictions',
                'min_block', 'aggregation_check'):
        if tok in v5b:
            issues.append(f'v5_checkeval 不应含 {tok}（会与 v3/v4 机制混同）')
    for tok in ('yes_count', '二元检查项', 'round(5'):
        if tok not in v5b:
            issues.append(f'v5_checkeval 缺聚合规则要素 {tok}')
    return issues


if __name__ == '__main__':
    print('Judge Prompt 版本清单：')
    for k, v in PROMPTS.items():
        tag = ''
        if k in SUPP_ARMS:
            a = SUPP_ARMS[k]
            tag = f'   ← 补实验臂 {a["role"]}｜{a["family"]}'
        print(f'  {k:16s} {len(v):>5} 字符{tag}')
    print('\n维度（10 项）：')
    for d in DIMS_ALL:
        print(f'  - {d}')
    issues = check_v3_diff()
    print('\n自检：' + ('✓ 全部通过' if not issues else '✗ ' + '; '.join(issues)))
    print('\n★ v1 与 v2 的差异应仅为措辞（维度/格式/区间完全相同）')
    print('★ v2 与 v3 的差异应为新增 checklist + contradictions')

    print('\n★ 补实验臂相对基座"恰好去掉一个段落"核验：')
    print(f'  v3 正文      = {len(_V3_BODY):>5} 字符'
          f'（含 CE 段 {len(_CE_SECTION)} + CC 段 {len(_CC_SECTION)}）')
    print(f'  v3_no_ce     = {len(V3_NO_CE):>5} 字符   ➜ = v3 − CE 段 + 无遗漏字段')
    print(f'  v3_no_cc     = {len(V3_NO_CC):>5} 字符   ➜ = v3 − CC 段 + 无矛盾字段')
    print(f'  v5_checkeval = {len(V5_CHECKEVAL):>5} 字符   ➜ = 官方规格 + '
          f'{len(_CHECK_QUESTIONS)} 条二元项 + 显式聚合规则')
    print('  ✓ v3 正文头与 v2 正文头逐字符一致（断言已通过），2×2 可归因')