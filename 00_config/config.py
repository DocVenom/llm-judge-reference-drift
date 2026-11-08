# -*- coding: utf-8 -*-
"""
全局配置：路径、随机种子、模型注册表、成本表。

所有脚本统一从这里 import，避免路径与魔法数字散落各处。

★ 路径自动适配（2026-10-12新增）
  同一份代码要能在 Windows 本机与 Linux GPU 服务器上直接跑，
  因此 PROJ 不写死，而是按优先级探测：
    1. 环境变量 TCMCR_PROJ（推荐，显式指定）
    2. AutoDL 惯例路径 /root/autodl-tmp/project
    3. 从本文件位置反推（最可靠，跨平台通用）
  → 不再需要手动 sed 改路径。
"""
import os


# ============ 路径（自动适配 Win / Linux）============
def _detect_proj():
    # 1) 显式环境变量优先
    p = os.environ.get('TCMCR_PROJ')
    if p and os.path.isdir(os.path.join(p, '00_config')):
        return os.path.abspath(p)
    # 2) AutoDL 惯例路径
    for cand in ('/root/autodl-tmp/project', '/root/project'):
        if os.path.isdir(os.path.join(cand, '00_config')):
            return cand
    # 3) 从本文件位置反推（config.py 位于 <PROJ>/00_config/）
    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    if os.path.isdir(os.path.join(proj, '00_config')):
        return proj
    # 4) 兜底：按平台猜
    if os.name == 'nt':
        return r'E:\Desktop\LX-TCMClinicalReason-BenchPublic'
    return '/root/autodl-tmp/project'


PROJ = _detect_proj()
IS_REMOTE = not PROJ.startswith(('E:', 'C:', 'D:'))   # 是否跑在 GPU 服务器上

CFG     = os.path.join(PROJ, '00_config')
DATA    = os.path.join(PROJ, '01_data')
UPSTREAM= os.path.join(DATA, 'upstream')
RAW     = os.path.join(DATA, 'raw_outputs')
PARSED  = os.path.join(DATA, 'parsed')
# ★ 2026-10-13：扰动目录支持 TCMCR_PERT 覆盖。
#   动机：s05 的 _load_perturbations 是**扫描整个 PERT 目录**载入的
#   （只按文件名前缀 'perturb_' 过滤）。E3「换被测模型」会产出一份新的
#   扰动文件（perturb_raw_HuatuoGPT-3-8B_..._medium.jsonl），若与主实验的
#   扰动文件混放同一目录，s05 会把两条链的样本并进同一份评分文件，
#   而 case_id 与 ptype 又完全相同 → 全程**不报错**、结果直接失真。
#   → 换被测模型那条链必须把扰动集写到独立目录。
PERT    = os.environ.get('TCMCR_PERT') or os.path.join(DATA, 'perturbations')
if not os.path.isabs(PERT):
    PERT = os.path.abspath(os.path.join(PROJ, PERT))
ANNOT   = os.path.join(DATA, 'annotations')
SCRIPTS = os.path.join(PROJ, '02_scripts')
# ★ 2026-10-13：结果根目录支持 TCMCR_RESULTS 覆盖。
#   动机：s05 的输出文件名只含「后端 + Judge 模型 + prompt 版本」，
#   **不含被测模型**（见 s05_judge_score.py 的 tag 构造）。换被测模型后若
#   仍写同一目录，第二轮会以同名文件续写/覆盖第一轮 → 指标把两个被测模型
#   的数据混在一起，且不报错。→ 换被测模型那条链使用独立结果根目录。
RESULTS = os.environ.get('TCMCR_RESULTS') or os.path.join(PROJ, '03_results')
if not os.path.isabs(RESULTS):
    RESULTS = os.path.abspath(os.path.join(PROJ, RESULTS))
JUDGE   = os.path.join(RESULTS, 'judge_scores')
METRICS = os.path.join(RESULTS, 'metrics')
ANALYSIS= os.path.join(RESULTS, 'analysis')
LOGS    = os.path.join(PROJ, '04_logs')
PAPER   = os.path.join(PROJ, '05_paper')
TABLES  = os.path.join(PAPER, 'tables')
FIGS    = os.path.join(PAPER, 'figures')

# 上游官方数据（仓库内已有 data/TCMCR-Reasoning.json，此处指向副本）
UPSTREAM_JSON = os.path.join(UPSTREAM, 'TCMCR-Reasoning.json')

# 密钥：绝不硬编码，运行时从外部文件读
# ★ 优先项目内（服务器上只传这个），本机则回落到桌面
API_KEY_FILE   = os.path.join(PROJ, 'API.TXT')
API_KEY_FALLBACK = [
    API_KEY_FILE,
    os.path.expanduser('~/API.TXT'),
    r'E:\Desktop\API.TXT',
]
API_KEY_FOUND = next((p for p in API_KEY_FALLBACK if os.path.exists(p)), None)
AUTODL_BASE = 'https://www.autodl.art/api/v1'
GALAXY_BASE = 'https://token.ai-galaxy.com/v1'

ALL_DIRS = [CFG, UPSTREAM, RAW, PARSED, PERT, ANNOT,
            SCRIPTS, JUDGE, METRICS, ANALYSIS, LOGS, TABLES, FIGS]

# ============ 随机种子 ============
# 论文需报告：所有随机源统一在此声明
SEED = 20261010# 主种子
SEED_SAMPLING = 20261010   # 抽样
SEED_PERTURB  = 20261010   # 扰动位置选择

# ============ 实验规模 ============
N_CALIBRATION = 99# 人工标注校准集
N_PERTURB     = 150       # 扰动实验集
PERTURB_TYPES = ['P1_coldheat', 'P2_treatment', 'M1_omission']  # 3 类
N_ANNOTATORS  = 4         # D1,D2,M1,M2

# ============ GPU 配置（2026-10-11 决策：租 A800 80GB）============
# 决策理由：
#   ① 24GB 卡跑不开 32B Judge（需 77.5GB），只能退而用 8B 异族模型，
#      会被审稿人质疑"Judge 太弱导致检出率低"
#   ② A800 80GB 可承载 32B Judge，方法学上更站得住
#   ③ 资金不是约束（用户 2026-10-11 明确）
GPU_CONFIG = dict(
    name='A800-80GB', vram_gb=80, price_per_hour=5.59,
    # ★ Judge 型号可用环境变量 TCMCR_JUDGE 覆盖，无需改代码：
    #     TCMCR_JUDGE=Qwen/Qwen3-14B python 02_scripts/s05_judge_score.py ...
    #   磁盘约束（2026-10-12 实测）：数据盘剩 35GB，Qwen3-32B 权重 64GB装不下，
    #   即使删掉 ShizhenGPT 也只有 50GB → 默认退回 14B（权重 28GB）。
    #   若日后把数据盘扩到 150GB，可直接 TCMCR_JUDGE=Qwen/Qwen3-32B 切回，无需改文件。
    judge_model=os.environ.get('TCMCR_JUDGE', 'Qwen/Qwen3-14B'),
    max_model_len=16384,          # 大显存可放开上下文
    gpu_memory_utilization=0.92,
    dtype='bfloat16',
)
# A800 为 Ampere 架构（sm_80），对 vLLM 兼容性好；
# 但仍可能遇到 0.31.0 的 minimax_m3 import bug → **保持 vllm==0.17.0**

# ============ 本地被测模型（走本地 vLLM，绕开平台 40% 内容拦截）============
LOCAL_MODELS = {
    'ShizhenGPT-7B-LLM': dict(
        hf='FreedomIntelligence/ShizhenGPT-7B-LLM', size='7.6B',
        vram_need_gb=19, in_official_leaderboard=True,
        official_content=41.8, official_logic=62.4,
        role='★ 官方对照组（环境已调通）'),
    'Qwen3-8B': dict(
        hf='Qwen/Qwen3-8B', size='8B',
        vram_need_gb=20, in_official_leaderboard=False,
        official_content=None, official_logic=None,
        role='同架构控制组（ShizhenGPT 基座为 Qwen2.5-7B）'),
    'HuatuoGPT-3-8B': dict(
        hf='FreedomIntelligence/HuatuoGPT-3-8B', size='8B',
        vram_need_gb=20, in_official_leaderboard=True,
        official_content=40.7, official_logic=60.8,
        role='★ 官方扰动测试主角'),
    'Qwen3-14B': dict(
        hf='Qwen/Qwen3-14B', size='14B',
        vram_need_gb=29, in_official_leaderboard=False,
        official_content=None, official_logic=None,
        role='可选：更强的同族对照'),
}

# ============ API 模型（智星云 ai-galaxy，2026-11 实测可用）============
# ★ 重大变更：新增 智星云 平台，其 qwen3.7-plus 可用
#   → 官方 Judge 原型号可调用 → **能复现官方数值**，论文定位可升级
#价格单位：元 / M token（按平台标价，务必以控制台为准）
API_MODELS = {
    'qwen3.7-plus': dict(
        provider='galaxy', pi=0.812, po=4.872,
        note='★★ 官方 Judge 原型号 —— 可直接对比官方 57/56/31%'),
    'qwen3.7-max': dict(
        provider='galaxy', pi=0.812, po=4.872,
        note='qwen3.7 系列增强（官方 Judge 同系列）'),
    'qwen3.8-max': dict(
        provider='galaxy', pi=None, po=None,
        note='更新更强，备选主 Judge'),
    'qwen3.6-plus': dict(
        provider='galaxy', pi=None, po=None,
        note='上一代 plus，作版本敏感性分析'),
    'deepseek-v4-pro': dict(
        provider='galaxy', pi=2.0, po=8.0,
        note='★ Judge 正交对照（异族，官方榜单 48.5%）'),
    'kimi-k3': dict(
        provider='galaxy', pi=2.0, po=10.0,
        note='Judge 备选（异族）'),
    'glm-5.3': dict(
        provider='galaxy', pi=None, po=None,
        note='Judge 备选（异族）'),
    'minimax-m3': dict(
        provider='galaxy', pi=None, po=None,
        note='Judge 备选（异族）'),
    'oai/gpt-6-astra': dict(
        provider='galaxy', pi=40.6, po=203.0,
        note='最强对照（贵，仅小样本正交验证）'),
    'deepseek-v4-flash': dict(
        provider='galaxy', pi=None, po=None,
        note='通用被测模型备选'),
}
API_MODELS_CN = dict(API_MODELS)      # 智星云直接用英文 id

# 各平台实测不可用记录（保留原因，便于论文 limitation 引用）
API_UNAVAILABLE = {
    ('autodl', 'qwen3.7-plus'): '503 模型已删除 → 已由智星云替代',
    ('autodl', 'hy4-preview'):  '402 Payment Required',
}

# ============ 角色分配（★ Judge 与被测模型必须分离）============
# 理由：官方论文明确指出 HuatuoGPT 作 Judge 时 "rated answers from its own
#       model family noticeably higher"。若 Judge = 被测模型，结果无效。
#
# ★★ 2026-10-11 决策修订（智星云 qwen3.7-plus 可用后）：
#   主 Judge = qwen3.7-plus（**官方原型号**）
#     → 这是本次最重要的方法学改进：可直接复现官方 57/56/31% 三个基线数字，
#       论文可从「pattern-level replication」升级为「数值级直接对比」。
#   被测模型仍走本地 vLLM（开源权重需可复现，且避免任何平台的内容过滤）。
#   正交对照 = deepseek-v4-pro（异族，规避 Qwen 同族偏置的质疑）。
ROLE_TARGET_MODELS = ['ShizhenGPT-7B-LLM', 'HuatuoGPT-3-8B', 'Qwen3-8B']

# ---- Judge 角色（2026-10-12 最终定版）----
# 三条约束互相冲突，最终按优先级取舍：
#   ① 智星云 qwen3.7-plus（含病例文本稳定挂起）→ 主实验 Judge 必须本地跑
#   ② 数据盘50GB 装不下 Qwen3-32B（权重 64GB）→ 主 Judge 退为 Qwen3-14B
#   ③ Qwen3-14B 与被测模型（ShizhenGPT←Qwen2.5、HuatuoGPT-3←Qwen3-8B）同族
#      → 同族偏置无法靠换本地模型消除，只能靠**异族正交对照**排除质疑。
#
# ★ 关键推论（写论文时必须体现）：换成 14B 并不改变「主 Judge 与被测同族」这一
#   已声明的局限 —— 32B 时代也是 Qwen 族，同样同族。所以 14B 是**零方法学代价**
#   的降级（省 ¥30+ 与数小时等待），不是新的偏置来源。
#   真正的防线仍是 deepseek-v4-pro 子集正交对照：若 v1→v3 的提升趋势在异族
#   Judge 上同样成立，即可排除「提升只是同族偏袒」。
ROLE_JUDGE_MODELS  = [GPU_CONFIG['judge_model']]   # ★ 主 Judge：本地 Qwen3-14B
ROLE_JUDGE_CONTROL = ['deepseek-v4-pro']            # 异族正交对照（API，子集验证趋势）
ROLE_JUDGE_OFFICIAL= ['qwen3.7-plus']               # 官方原型号（API，仅无病例短样本）

# ============ 论文定位（随 Judge 可用性切换）============
# 'NUMERIC'     = 可做数值级对比（需 Judge 可处理含病例文本的 prompt）
# 'PATTERN_ONLY'= 只能做模式级对比（官方数值仅作动机引用）
# ★实测智星云存在内容过滤 → 主实验 Judge 为本地 32B → 退回 PATTERN_ONLY。
#   若换成无过滤的渠道（如自建部署官方权重），可改回 'NUMERIC'。
REPLICATION_LEVEL = 'PATTERN_ONLY'


# ============ 官方 7 个 block键名（不可改动）============
BLOCKS = ['思维链', '病因病机分析', '证候诊断', '治法', '处方', '方解', '症状变化与中药加减']

# ============ Rubric 10 维度（对齐人工标注手册）============
DIMS_BLOCK = [
    '病因病机_Block', '证候诊断_Block', '治则治法_Block', '处方生成_Block',
    '处方解析_Block', '症状变化推理_Block', '处方调整_Block',
]
DIMS_CROSS = ['病机_证候_Cross', '证候_治法_Cross', '治法_处方_Cross']
DIMS_ALL = DIMS_BLOCK + DIMS_CROSS


def ensure_dirs():
    for d in ALL_DIRS:
        os.makedirs(d, exist_ok=True)


def set_global_seed(seed=SEED):
    """统一设置所有随机源。必须在 import numpy/torch 之后、抽样之前调用。"""
    import random
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:
        pass
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.use_deterministic_algorithms(False)  # 部分算子无确定性实现
    except ImportError:
        pass
    os.environ['PYTHONHASHSEED'] = str(seed)
    return seed


def env_snapshot():
    """记录环境与版本，供论文 reproducibility 章节使用。"""
    import sys, platform, json, importlib
    snap = {
        'timestamp': __import__('datetime').datetime.now().isoformat(),
        'python': sys.version.split()[0],
        'platform': platform.platform(),
        'machine': platform.machine(),
        'seeds': {'main': SEED, 'sampling': SEED_SAMPLING, 'perturbation': SEED_PERTURB},
        # ★ 2026-10-13：把「本轮跑的是哪条链」记进环境快照。
        #   换被测模型（E3）会切 RESULTS/PERT，论文复现章节必须能看出这一点。
        'paths': {'results': RESULTS, 'perturbations': PERT, 'proj': PROJ},
        'packages': {},
    }
    for m in ['numpy', 'pandas', 'scipy', 'torch', 'transformers', 'pingouin', 'openai', 'requests']:
        try:
            snap['packages'][m] = importlib.import_module(m).__version__
        except Exception:
            snap['packages'][m] = None
    return snap


if __name__ == '__main__':
    ensure_dirs()
    print('项目目录已就绪：', PROJ)
    for d in ALL_DIRS:
        print('  ✓', os.path.relpath(d, PROJ))
    print('\n种子：', SEED)
    print('模型角色：')
    print('  被测：', ROLE_TARGET_MODELS)
    print('  Judge：', ROLE_JUDGE_MODELS)