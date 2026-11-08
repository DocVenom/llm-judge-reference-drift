# -*- coding: utf-8 -*-
"""
S2b：本地 vLLM 推理脚本（AutoDL GPU 实例内运行）。

★ 与s02_run_infer.py 的区别：走本地 vLLM OpenAI 兼容服务，**不经过平台 API
  网关，因此不会被内容过滤器拦截**（平台 API 实测拦截 40%）。

用法（在 AutoDL 实例内）：
  python s02b_run_infer_vllm.py --model FreedomIntelligence/ShizhenGPT-7B-LLM --limit 5
  python s02b_run_infer_vllm.py --model FreedomIntelligence/ShizhenGPT-7B-LLM
  # 换模型：Qwen/Qwen3-8B / FreedomIntelligence/HuatuoGPT-3-8B

产出与 s02_run_infer.py 完全一致（同名规则），便于后续脚本统一处理。
"""
import os, re, sys, json, time, argparse, urllib.request, urllib.error
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, '00_config'))
from config import (UPSTREAM, RAW, LOGS, ensure_dirs, set_global_seed, BLOCKS)

VLLM_BASE = 'http://127.0.0.1:8000'

# 模板不接受 enable_thinking 时的报错特征（用于自动退回，不中断整轮）
_TMPL_REJECT_RE = re.compile(
    r'enable_thinking|chat_template_kwargs|unexpected keyword|'
    r'TemplateError|UndefinedError|jinja', re.I)

# =============================================================================
# 官方 JSON 契约（--contract json）  ★ 2026-10-13 新增
# -----------------------------------------------------------------------------
# 根因（实测，见 00_config/E3_FORMAT_FIX.md）：
#   HuatuoGPT-3-8B 在**自由文本**模式下把 7 个块标题写成
#       ### **一、思维链（辨证逻辑）**   ／  ### **二、病因病机分析**
#   —— 加粗 + 中文序号 + 括号补语。s02b 原来的严格标题正则要求"关键词之后
#   立刻是行尾"，对不上 ⇒ 99 条**全部只切出 1 个块**（走 len(marks)<2 的兜底，
#   把整段塞进「思维链」）⇒ 7-block 完整率 0% ⇒ 质量闸门**误判**降级。
#   同时它先写一大段"好的，我现在需要处理…"的独白，把 max_tokens 吃光
#   （实测 raw 长度中位 3031、最长 4003 ≈ 4000 上限）⇒ 处方/方解/加减三块
#   被**截断**，就算放宽正则也补不回来。
#
# 官方的正解（code/inference_vllm.py）根本不解析自由文本：它给 system prompt
#   要求**严格 JSON**，再用 vLLM 的**引导式 JSON 解码**把 7 个键"按构造"约束
#   出来（原文：Both run with guided JSON decoding against the 7-block schema,
#   so the output is structured by construction），且 Huatuo 的配置里明确
#   写着 chat_template_kwargs={"enable_thinking": False}。
#
# 本模式 = 复刻官方契约。产出仍是主实验同款结构（blocks 七键 + raw_clean），
#   下游 s03/s05 **完全无感知**。
# =============================================================================

SECTION_KEYS = ['思维链', '病因病机分析', '证候诊断', '治法', '处方', '方解',
                '症状变化与中药加减']

TCM_SCHEMA = {
    'type': 'object',
    'properties': {k: {'type': 'string', 'minLength': 1} for k in SECTION_KEYS},
    'required': SECTION_KEYS,
    'additionalProperties': False,
}

_SECTION_GUIDE = (
    "1.「思维链」: 你的完整推理过程。结合患者主诉、症状、舌象脉象、病史等, 逐步推演"
    "病因病机、证候、治法及遣方思路 (先想后答, 此处写思考过程)。\n"
    "2.「病因病机分析」: 分析疾病的病因与病机演变, 说明邪正盛衰、脏腑气血津液等病理变化。\n"
    "3.「证候诊断」: 给出明确的中医证候名称 (证型), 必要时标明主证 / 兼证。\n"
    "4.「治法」: 基于证候诊断给出治则治法, 主辅分明。\n"
    "5.「处方」: 给出具体方药 (方名 / 药物及剂量), 紧扣治法。\n"
    "6.「方解」: 逐组分析处方配伍, 说明君臣佐使及各药 (组) 的作用及其对应的证候病机。\n"
    "7.「症状变化与中药加减」: 针对可能出现的症状变化, 给出随症加减的思路与药物调整。\n"
)

SYSTEM_PROMPT_JSON = (
    "你是一位资深中医师, 精通中医辨证论治。请根据用户提供的患者病例, 进行完整、严谨的"
    "中医临床分析, 并严格以结构化 JSON 输出。\n"
    "输出必须是一个 JSON 对象, 严格且仅包含以下 7 个字段, 缺一不可, 顺序固定:\n"
    + _SECTION_GUIDE +
    "要求: 紧扣患者实际症状、舌脉与病史, 辨证准确、方证相应、推理具体, 避免泛泛而谈。"
    "所有字段的值均为非空字符串。"
)

# 引导式解码的参数名随 vLLM 版本变过三代 → 依次探测（与官方脚本同一策略）。
# 第一个就成功则锁定；第一轮全失败就**立刻退出**，绝不白烧 45 分钟。
STRUCT_CANDIDATES = ['structured_outputs', 'guided_json', 'guided_decoding']

_FENCE_RE = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.S)
_BRACE_RE = re.compile(r'\{.*\}', re.S)
# "结构化解码参数被拒"的特征（版本不认这个参数名 / extra fields 被禁）
_STRUCT_REJECT_RE = re.compile(
    r'guided|structured|response_format|extra_forbidden|extra fields|'
    r'unexpected keyword|unknown|unsupported|validation error', re.I)


def struct_body(name):
    """把结构化解码参数包成请求体片段。"""
    if name == 'structured_outputs':
        return {'structured_outputs': {'json': TCM_SCHEMA}}
    if name == 'guided_decoding':
        return {'guided_decoding': {'json': TCM_SCHEMA}}
    return {'guided_json': TCM_SCHEMA}


def extract_json(content):
    """尽力从模型输出里取出 JSON 对象（直接解析 → 去 ``` 围栏 → 最外层 {...}）。"""
    content = (content or '').strip()
    if not content:
        return None
    cands = [content]
    cands += [m.group(1) for m in _FENCE_RE.finditer(content)]
    cands += [m.group(0) for m in _BRACE_RE.finditer(content)]
    for c in cands:
        try:
            obj = json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def normalize_output(parsed):
    """归一成 7 键齐全的 dict（缺的补空串）。"""
    if not isinstance(parsed, dict):
        parsed = {}
    out = {}
    for k in SECTION_KEYS:
        v = parsed.get(k)
        out[k] = v.strip() if isinstance(v, str) else ''
    return out


def blocks_to_text(blocks):
    """把 7 键渲染成主实验同款 markdown。

    这样 s03（扰动直接吃 raw_clean 的正文）与 s05 对两种契约**无感知**；
    且该文本能被本文件的 parse_blocks() 原样切回 7 块（自洽）。
    """
    parts = []
    for k in SECTION_KEYS:
        v = (blocks.get(k) or '').strip()
        if v:
            parts.append('### %s\n%s' % (k, v))
    return '\n\n'.join(parts)


def strip_thinking(text):
    """剥掉 Qwen3 系的 <think>…</think>。

    ★ 2026-10-13 修 bug：原文只在**有闭合标签**时切分；若 <think> 未闭合
      （思考被 max_tokens 截断，实测 E3 的 HuatuoGPT-3-8B 就是这样）则
      re.sub 也匹配不到 → <think> 残留在 raw_clean 里 → parse_blocks 一个块
      都切不出来 → 7-block 完整率 0%，**错误地触发质量闸门**。
    """
    if not text:
        return ''
    if '</think>' in text:
        return text.split('</think>')[-1].strip()
    if '<think>' in text:
        # 未闭合：丢掉 <think> 起的全部内容（含标签本身）
        return text.split('<think>')[0].strip()
    return re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()


def parse_blocks(text):
    """切成官方 7 个 block（与 s02_run_infer.py 同一实现）。"""
    import re
    out = {k: '' for k in BLOCKS}
    if not text:
        return out
    alt = {
        '思维链': ['思维链', '推理过程', '辨证思路', '思维路径'],
        '病因病机分析': ['病因病机分析', '病机分析', '病因病机'],
        '证候诊断': ['证候诊断', '证型', '辨证', '证候'],
        '治法': ['治法', '治则', '治疗法则'],
        '处方': ['处方', '方剂', '用药'],
        '方解': ['方解', '方义', '配伍分析'],
        '症状变化与中药加减': ['症状变化与中药加减', '中药加减', '症状变化', '加减'],
    }
    marks = []
    for k in BLOCKS:
        for a in alt[k]:
            pat = rf'(?:^|\n)\s*(?:#{{1,4}}\s*|[一二三四五六七八九十]+[、.]\s*)?{a}\s*[:：]?\s*(?=\n|$)'
            m = re.search(pat, text, re.M)
            if m:
                marks.append((m.start(), k, m.end()))
                break
    if len(marks) < 2:
        out['思维链'] = text.strip()
        return out
    seen, uniq = set(), []
    for st, k, e in sorted(marks):
        if k in seen:
            continue
        seen.add(k); uniq.append((st, k, e))
    uniq.sort()
    for i, (st, k, e) in enumerate(uniq):
        end = uniq[i + 1][0] if i + 1 < len(uniq) else len(text)
        out[k] = text[e:end].strip()
    return out


def call_vllm(prompt, model, max_tokens=4000, temperature=0.0, timeout=600, retries=2,
              extra_body=None, system=None, top_p=None, top_k=None, seed=None):
    payload = {'model': model,
               'messages': ([{'role': 'system', 'content': system}] if system else [])
                           + [{'role': 'user', 'content': prompt}],
               'max_tokens': max_tokens, 'temperature': temperature}
    if top_p is not None:
        payload['top_p'] = top_p
    if top_k is not None:
        payload['top_k'] = top_k
    if seed is not None:
        payload['seed'] = seed
    if extra_body:
        # 例：{'chat_template_kwargs': {'enable_thinking': False}}
        payload.update(extra_body)
    data = json.dumps(payload).encode('utf-8')
    last = None
    for att in range(retries):
        try:
            req = urllib.request.Request(VLLM_BASE + '/v1/chat/completions',
                                         data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=timeout) as f:
                r = json.loads(f.read().decode())
            return r['choices'][0]['message']['content']
        except Exception as e:
            # ★ 2026-10-13：HTTPError 的 str() 只有 "HTTP Error 400: Bad Request"，
            #   真正的原因（哪个字段不被接受）在响应体里 → 必须捞出来，
            #   否则"结构化解码参数被拒"与"网络抖动"分不清。
            detail = ''
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = e.read().decode('utf-8', 'replace')[:300]
                except Exception:
                    detail = ''
            if detail:
                last = '%s: %s' % (type(e).__name__, detail)
            else:
                last = '%s: %s' % (type(e).__name__, str(e)[:150])
            if att + 1 < retries and not detail:
                time.sleep(3)          # 网络类才退避；参数类重试无意义
    raise RuntimeError(last)


def main():
    global VLLM_BASE
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True,
                    help='HF 模型 id，如 FreedomIntelligence/ShizhenGPT-7B-LLM')
    ap.add_argument('--subset', default='calibration_99',
                    choices=['calibration_99', 'perturb_150',
                             # ★ 兼容别名：s05/s07 用的是 calibration / perturbation。
                             #   2026-10-12 因命名不一致导致 server_init.sh 传 'calibration'
                             #   被argparse 拒绝、白跑一次初始化，此处加别名杜绝复发。
                             'calibration', 'perturbation'],
                    help='校准集99 或 扰动集150（calibration/perturbation 为兼容别名）')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--host', default=VLLM_BASE)
    ap.add_argument('--no-thinking', action='store_true', default=False,
                    help='对支持 enable_thinking 的模板（Qwen3 系，如 '
                         'FreedomIntelligence/HuatuoGPT-3-8B）传 '
                         'chat_template_kwargs={"enable_thinking":false}；'
                         '若该模型模板不接受会自动退回默认模式并告警，不中断。'
                         '等价于环境变量 TCMCR_NO_THINKING=1')
    # ★ 2026-10-13 新增：官方 JSON 契约（见文件头注释）
    ap.add_argument('--contract', choices=['text', 'json'], default='text',
                    help='text=自由文本 + 解析 markdown 7 块标题（主实验口径，默认）；'
                         'json=复刻官方 code/inference_vllm.py：system prompt 要求严格 JSON '
                         '+ vLLM 引导式 JSON 解码，7 个键按构造产出（HuatuoGPT 这类'
                         '"标题体例不兼容"的模型必须用这个）')
    ap.add_argument('--max-tokens', type=int, default=0,
                    help='0=按契约取默认（text 4000 / json 4096，与官方一致）')
    ap.add_argument('--temperature', type=float, default=None,
                    help='默认：text=0.0（可复现）；json=0.6（官方 HuatuoGPT 推荐值）')
    ap.add_argument('--top-p', type=float, default=None)
    ap.add_argument('--top-k', type=int, default=None)
    args = ap.parse_args()
    VLLM_BASE = args.host
    no_think = bool(args.no_thinking) or \
        os.environ.get('TCMCR_NO_THINKING', '').strip() == '1'
    contract = (os.environ.get('TCMCR_CONTRACT', '').strip() or args.contract)
    if contract not in ('text', 'json'):
        print(f'✗ TCMCR_CONTRACT 取值非法：{contract}（只能 text / json）'); sys.exit(2)
    if contract == 'json':
        max_tokens = args.max_tokens or 4096          # 与官方 MODEL_CONFIGS 一致
        temperature = 0.6 if args.temperature is None else args.temperature
        top_p = 0.95 if args.top_p is None else args.top_p
        top_k = 20 if args.top_k is None else args.top_k
        if not no_think:
            # 官方对 Huatuo 明确要求关闭思考（leading <think> 会和 JSON 开括号冲突）
            print('  ⚠ --contract json 但未开 --no-thinking：官方对 HuatuoGPT 用的是'
                  ' enable_thinking=false → 这里自动打开。')
            no_think = True
    else:
        max_tokens = args.max_tokens or 4000
        temperature = 0.0 if args.temperature is None else args.temperature
        top_p = args.top_p
        top_k = args.top_k
    SEED = int(os.environ.get('TCMCR_SEED', '20261013'))

    ensure_dirs()
    set_global_seed()

    # 连通性检查
    try:
        with urllib.request.urlopen(VLLM_BASE + '/v1/models', timeout=10) as f:
            models = json.loads(f.read().decode())
        print('✓ vLLM 就绪，服务模型：',
              [m['id'] for m in models.get('data', [])])
    except Exception as e:
        print(f'✗ 连不上 vLLM({VLLM_BASE})：{e}')
        print('  请先运行 02_scripts/setup_gpu_vllm.sh')
        sys.exit(1)

    # 别名 → 真实抽样集名（selected_<name>.json）
    SUBSET_FILE = {'calibration_99': 'calibration_99', 'calibration': 'calibration_99',
                   'perturb_150': 'perturb_150', 'perturbation': 'perturb_150'}
    subset_file = SUBSET_FILE[args.subset]
    cases = json.load(open(os.path.join(UPSTREAM, f'selected_{subset_file}.json'),
                           encoding='utf-8'))
    if args.limit:
        cases = cases[:args.limit]
    n = len(cases)

    # ★ 文件名统一用真实集合名，避免同一实验因别名不同产生两份结果
    tag = os.path.basename(args.model) + (f'_n{n}' if args.limit else '')
    out_path = os.path.join(RAW, f'raw_{tag}_{subset_file}.jsonl')
    prog_path = out_path + '.progress'
    fail_path = os.path.join(RAW, f'failures_{tag}_{subset_file}.jsonl')
    log_path = os.path.join(LOGS, f's02b_run_{tag}_{subset_file}.log')

    logf = open(log_path, 'w', encoding='utf-8')
    def log(*a):
        s = ' '.join(str(x) for x in a)
        print(s)
        logf.write(s + '\n'); logf.flush()

    log(f'[S2b] 本地 vLLM 推理  {datetime.now().isoformat()}')
    log(f'  模型    : {args.model}')
    log(f'  集合    : {args.subset}  n={n}' + ('  ← 试点' if args.limit else ''))
    log(f'  服务    : {VLLM_BASE}')
    log(f'  temperature={temperature}（text 契约默认 0 = 可复现；json 契约默认 0.6 = 官方 HuatuoGPT 推荐）')
    log(f'  采样      : top_p={top_p}  top_k={top_k}  seed={SEED}')
    log(f'  输出契约  : ' + ('官方 JSON + 引导式解码（system prompt + structured outputs）'
                              if contract == 'json' else '自由文本 + 解析 markdown 7 块标题'))
    log(f'  思考模式: ' + ('关闭（enable_thinking=false）' if no_think
                          else '模型默认（Qwen3 系默认开思考）'))
    log('')

    # ★ 2026-10-13 新增：模式标记 + 反向保护。
    #   开/关思考两轮的产出内容完全不同；若沿用上一轮的断点文件，s02b 会把
    #   全部条目当作"已完成"跳过 → 命令显示成功，实际一条都没重跑（静默事故）。
    #   ★ 契约也计入模式：同一模型从 text 换成 json 是**换口径重跑**，
    #     沿用上一轮的断点同样会静默全跳过。
    mode = ('nothink' if no_think else 'thinking') + '-' + contract
    mode_path = out_path + '.mode'
    if os.path.isfile(mode_path):
        prev = open(mode_path, encoding='utf-8').read().strip()
        if prev and prev != mode:
            print(f'✗ 输出文件 {os.path.basename(out_path)} 上一轮模式 = {prev}，'
                  f'本次 = {mode}')
            print(f'  断点续跑会跳过全部已完成条目（等于什么都没跑）。')
            print(f'  处理：先备份/移走下面这几个文件，再重跑——')
            print(f'    {out_path}')
            print(f'    {prog_path}')
            print(f'    {mode_path}          ← 模式标记也要移走，否则永远对不上')
            print(f'    {fail_path}')
            print(f'  （run_all_e3_e1_e2.sh 的 E3_NO_THINKING/契约切换会自动备份，'
                  f'手动跑时请照着上面移）')
            sys.exit(2)

    done = set()
    if os.path.isfile(prog_path):
        done = set(json.load(open(prog_path, encoding='utf-8')))
        if done:
            log(f'  断点续跑：已完成 {len(done)} 条')

    if not os.path.isfile(mode_path):
        with open(mode_path, 'w', encoding='utf-8') as f:
            f.write(mode + '\n')

    fout = open(out_path, 'a', encoding='utf-8')
    ffail = open(fail_path, 'a', encoding='utf-8')
    t0 = time.time(); ok = fail = 0; comp = []
    extra_base = {'chat_template_kwargs': {'enable_thinking': False}} if no_think else {}
    tmpl_rejected = False   # 该模型模板不接受 enable_thinking → 已退回默认
    si = 0                  # json 契约：结构化解码参数名下标
    struct_locked = None    # 探测成功后锁定，后续不再变

    def do_call(p):
        """发一次请求。json 契约下自动探测"这版 vLLM 认哪个参数名"。"""
        nonlocal tmpl_rejected, si, extra_base
        while True:
            extra = dict(extra_base)
            if contract == 'json':
                extra.update(struct_body(STRUCT_CANDIDATES[si]))
            try:
                return call_vllm(
                    p, args.model, max_tokens=max_tokens, temperature=temperature,
                    top_p=top_p, top_k=top_k, seed=SEED,
                    extra_body=(extra or None),
                    system=(SYSTEM_PROMPT_JSON if contract == 'json' else None),
                    retries=(1 if contract == 'json' else 2))
            except RuntimeError as e:
                msg = str(e)
                if extra_base and not tmpl_rejected and _TMPL_REJECT_RE.search(msg):
                    tmpl_rejected = True
                    extra_base = {}
                    log('  ⚠ 该模型 chat template 不接受 enable_thinking '
                        '→ 自本轮起退回默认思考模式（仅告警一次）')
                    continue
                if contract == 'json' and si + 1 < len(STRUCT_CANDIDATES) \
                        and _STRUCT_REJECT_RE.search(msg):
                    si += 1
                    log(f'  ⚠ {STRUCT_CANDIDATES[si - 1]} 不被本版 vLLM 接受 '
                        f'→ 改试 {STRUCT_CANDIDATES[si]}   （报错：{msg[:120]}）')
                    continue
                raise

    for i, c in enumerate(cases, 1):
        cid = c['id']
        if cid in done:
            continue
        prompt = c['instruction'] + '\n\n患者病例：\n' + c['input']
        try:
            raw = None; blocks = {}; clean = ''; nb = -1
            for _tr in range(len(STRUCT_CANDIDATES) + 1):
                raw = do_call(prompt)
                if contract == 'json':
                    blocks = normalize_output(extract_json(strip_thinking(raw)))
                    clean = blocks_to_text(blocks)
                else:
                    clean = strip_thinking(raw)
                    blocks = parse_blocks(clean)
                nb = sum(1 for v in blocks.values() if v.strip())
                # ★ json 契约下，第一条就是"参数名到底认不认"的判据：
                #   引导式解码若被**静默忽略**，模型不会吐 JSON → nb=0。
                #   这时换下一个候选参数名重试**同一条**，绝不等 99 条跑完才发现。
                if contract == 'json' and nb == 0 and not struct_locked:
                    if si + 1 < len(STRUCT_CANDIDATES):
                        si += 1
                        log(f'  ⚠ {STRUCT_CANDIDATES[si - 1]} 没约束出 JSON（疑似被静默忽略）'
                            f' → 改试 {STRUCT_CANDIDATES[si]}')
                        continue
                    raise RuntimeError(
                        '引导式 JSON 解码全部候选都失败（%s）：模型既没吐 JSON，'
                        '也切不出 7 块。首条输出前 200 字：%s'
                        % (STRUCT_CANDIDATES, (raw or '')[:200]))
                if contract == 'json' and not struct_locked:
                    struct_locked = STRUCT_CANDIDATES[si]
                    log(f'  ✓ 结构化解码参数锁定：{struct_locked}')
                break
            fout.write(json.dumps({
                'id': cid, 'tier': c['tier'], 'OCSS': c['OCSS'],
                'features': c['features'], 'model': args.model,
                'input': c['input'],
                'contract': contract,
                'no_thinking': bool(no_think and not tmpl_rejected),
                'sampling': {'temperature': temperature, 'top_p': top_p,
                             'top_k': top_k, 'max_tokens': max_tokens, 'seed': SEED},
                'raw_with_thinking': raw, 'raw_clean': clean,
                'blocks': blocks, 'n_blocks_filled': nb,
                'ts': datetime.now().isoformat(),
            }, ensure_ascii=False) + '\n')
            fout.flush()
            comp.append(nb); ok += 1; done.add(cid)
            json.dump(sorted(done), open(prog_path, 'w'), ensure_ascii=False)
            log(f'  [{i}/{n}] {cid} tier={c["tier"]} blocks={nb}/7')
        except Exception as e:
            ffail.write(json.dumps({'id': cid, 'error': f'{type(e).__name__}: {str(e)[:200]}',
                                    'ts': datetime.now().isoformat()}, ensure_ascii=False) + '\n')
            ffail.flush(); fail += 1
            log(f'  [{i}/{n}] {cid} ✗ {type(e).__name__}: {str(e)[:120]}')

    fout.close(); ffail.close()
    el = time.time() - t0
    full = sum(1 for x in comp if x == 7)
    log('')
    log(f'完成 {ok} 条，失败 {fail} 条，耗时 {el/60:.1f} min')
    log(f'输出契约: {contract}' + (f'（结构化解码参数={struct_locked}）'
                                  if contract == 'json' else ''))
    if no_think:
        log(f'思考模式: ' + ('已关闭（enable_thinking=false）' if not tmpl_rejected
                            else '⚠ 模板不接受 → 实际为默认思考模式'))
    if comp:
        log(f'7-block 完整率: {full}/{len(comp)} = {full/len(comp)*100:.0f}%')
        log(f'平均填充: {sum(comp)/len(comp):.2f}/7')
        log('分布: ' + ' '.join(f'{k}block×{comp.count(k)}' for k in sorted(set(comp))))
    log(f'输出: {out_path}')
    logf.close()

    print(f'\n[S2b] 完成 {ok}/{n}，失败 {fail}')
    if comp:
        print(f'  7-block 完整率 {full}/{len(comp)} = {full/len(comp)*100:.0f}%')
        print(f'  平均 {sum(comp)/len(comp):.2f}/7block')
        rate = ok / el if el else 0
        print(f'  {el/max(ok,1):.1f}s/条')
    print(f'  日志: {log_path}')


if __name__ == '__main__':
    main()