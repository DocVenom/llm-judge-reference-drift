# -*- coding: utf-8 -*-
"""
S05：Judge 评分（v1 / v2 / v3 三版prompt）。

★ 本脚本是全流程的核心测量环节，产出论文主表。

设计要点
--------
1. **双后端**：优先走API（qwen3.7-plus，官方原型号，可复现官方数值），
   API 不可用或过慢时可切本地 vLLM（Qwen3-32B）。用--backend切换。
2. **成对评分**：对同一 case 的「原答/ 扰动答」一起送入，
   因为检出率是**配对**指标（McNemar 检验的配对单位）。
3. **三版 prompt**：v1 复现官方规格 / v2 仅改措辞（控制组）/
   v3 加完整性检查表（机制化修复）。
4. **全量留痕**：每条评分含原始理由全文、prompt 版本号、种子、时间戳。
5. **断点续跑**：每条立即落盘 .jsonl + .progress，重跑自动跳过。

输出
----
  03_results/judge_scores/judge_{backend}_{prompt_ver}.jsonl
  03_results/metrics/detection_v1.csv（检出率混淆矩阵）

用法
----
  python 02_scripts/s05_judge_score.py --prompt-ver v1 --backend api \
         --subset calibration -n 99
  python 02_scripts/s05_judge_score.py --prompt-ver v1 --backend vllm \
         --subset perturbation --perturb-types P1_coldheat,M1_omission
"""
import argparse, json, os, re, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '00_config'))

from config import (UPSTREAM, PARSED, RAW, PERT,  # noqa: E402
                    JUDGE as JUDGE_SCORES,
                    METRICS, LOGS,
                    ensure_dirs, set_global_seed, env_snapshot,
                    SEED, ROLE_JUDGE_MODELS, ROLE_JUDGE_OFFICIAL,
                    BLOCKS)
import llm_api# noqa: E402
from judge_prompts import (PROMPTS, get_prompt,  # noqa: E402
                           build_user_prompt, DETECT_RULES)


# ============ 评分结果解析 ============
# 三个 Judge prompt 都**明确要求严格输出 JSON**（见 judge_prompts.py 的【输出格式】）：
#   {"diagnostic_pathway": "...", "key_evidence": [...], "counterevidence": [...],
#    "scores": {"<维度名>": {"score": <0-5>, "reason": "..."}}, "overall": <0-5>}
#
# ⚠️ 2026-10-12 修复的致命 bug：旧版只用正则匹配「总分：4 / Overall Score: 4」，
#   而真实 JSON 的键是 `"overall": 4`（无「总分」「score」字样）→ **一条都匹配不上**
#   → 全部 parse_failed → 检出率恒为 None → S4 决策点拿不到任何数字。
#   故改为：① 先剥 thinking；② 先尝试 JSON 解析；③ JSON 失败再回落正则。
_SCORE_RE = re.compile(
    # ★ 2026-10-12 修复：原正则只认「总分 / Overall **Score**」，
    #   而 prompt 要求的键是 `"overall"`（不带 score）→ **一条都匹配不上**
    #   → 一旦 JSON 解析失败，正则兜底完全失效。这正是 27 条失败的次因。
    r'(?:总分|总评分|综合评分|总评|final\s+score|overall(?:\s+score)?)'
    r'"?\s*[:：]\s*"?([0-5](?:\.\d+)?)', re.I)
_DIM_RE = re.compile(r'^\s*(?:\d+[\.、)]|[-*])\s*(.{2,20}?)\s*[:：]\s*'
                     r'"?(?:score"?\s*[:：]\s*)?([0-5](?:\.\d+)?)', re.M)
# ★ 2026-10-12 新增：JSON 结构损坏时，仍能从原始文本里按**键值对**抠出维度分。
#   背景：s05b 离线重解析后，全部 27 条分数都救回来了，但其中一批的
#   `dims` 仍为空 —— 说明那些文本 JSON 结构不可修复（如字符串内含未转义 ASCII 引号），
#   只能靠 `"overall": N` 正则保底拿到总分。
#   后果：**定向检出口径损失了 13–19 条/类型**，而丢掉的恰好是 hard 层
#   （非随机缺失）→ 该口径的估计被拉偏。
#   维度分是 `{"<维度名>": {"score": N` 的结构，可以脱离完整 JSON 单独抠出来。
_DIM_JSON_RE = re.compile(
    r'"([^"]{2,40})"\s*:\s*\{\s*"score"\s*:\s*([0-5](?:\.\d+)?)')
_THINK_RE = re.compile(r'<think>.*?</think>', re.S)

# 空答案时的占位 parsed（保持与 parse_judge_output 输出同构，避免下游 KeyError）
_EMPTY_PARSED = {'score': None, 'dims': {}, 'reason': '', 'parse_failed': True,
                 'parse_mode': None, 'missing_elements': [],
                 'contradictions': [], 'completeness': {},
                 'key_evidence': [], 'counterevidence': [],
                 # ★ 2026-10-15：CheckEval 式臂（v5）的二元项留痕
                 'binary_checklist': {}, 'yes_count': None,
                 'yes_count_reported': False, 'yes_count_mismatch': False}


def _strip_thinking(text):
    """剥掉 <think>...</think>。

    Qwen3 系列默认 thinking mode（Qwen3-14B / 32B 都会），
    思考段里也会出现 "score" 等字样，会污染正则取值。
    ★ 与 autodl_api.strip_thinking / llm_api.strip_thinking 同义，
      此处独立实现以免s05 依赖那两个模块的导入顺序。
    """
    if not text:
        return ''
    if '</think>' in text:
        return text.split('</think>')[-1].strip()
    return _THINK_RE.sub('', text).strip()


def _loads_lenient(s):
    """尽力把一段文本解析成 JSON。

    ★ 2026-10-12 修复：`json.loads` 默认 **strict=True**，遇到**字符串内部含
      字面换行/制表符**会直接抛 `Invalid control character`。
      而 reason 字段里写多行说明是 LLM 最常见的输出习惯之一
      → 全量 v1 有 **27/287 条**因此判失败，且失败率随难度单调上升
      （hard 13 / medium 10 / easy 4）→ **非随机缺失**，会污染检出率。
      `strict=False` 允许字符串内出现控制字符，可救回这一类。

    依次尝试：strict=False → 再去掉尾随逗号 → 仍失败则返回 None（绝不猜分数）。
    """
    for cand in (s, re.sub(r',(\s*[}\]])', r'\1', s)):
        try:
            return json.loads(cand, strict=False)
        except Exception:
            continue
    return None


def _extract_json_obj(text):
    """从可能带围栏/前后缀的文本中抠出第一个完整 JSON 对象。"""
    t = text.strip()
    # ```json ... ``` 围栏
    m = re.search(r'```(?:json)?\s*(.+?)\s*```', t, re.S)
    if m:
        t = m.group(1).strip()
    # 直接尝试整体解析
    if t.startswith('{'):
        obj = _loads_lenient(t)
        if obj is not None:
            return obj
    # 退而求其次：扫描第一个 { 到配对} 的片段
    start = t.find('{')
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    obj = _loads_lenient(t[start:i + 1])
                    if obj is not None:
                        return obj
                    break
        start = t.find('{', start + 1)
    return None


def parse_judge_output(text):
    """从 Judge 输出中提取 overall 分数、各维度分与理由。

    优先级：剥 thinking → JSON 解析（主路径）→ 正则回落（兜底）。
    仍取不到分数时返回 score=None 并标记 parse_failed——
    ★ 绝不猜测分数，否则会污染检出率统计。
    """
    text = _strip_thinking(text or '')
    out = {'score': None, 'dims': {}, 'reason': text,
           'parse_failed': False, 'parse_mode': None,
           # ★ 显式检出字段（v3 checklist 的产出，缺了它 v3 机制无法量化）
           'missing_elements': [], 'contradictions': [],
           'completeness': {}, 'key_evidence': [], 'counterevidence': [],
           # ★ 2026-10-15：CheckEval 式臂（v5）的二元项
           'binary_checklist': {}, 'yes_count': None,
           'yes_count_reported': False, 'yes_count_mismatch': False}

    # ---- 主路径：JSON ----
    obj = _extract_json_obj(text)
    if isinstance(obj, dict):
        # overall 可能是 int/float，也可能被包成 {"score": 4} 或 {"reason":...}
        ov = obj.get('overall', obj.get('total', obj.get('总分')))
        if isinstance(ov, dict):
            ov = ov.get('score', ov.get('value'))
        if isinstance(ov, (int, float)) and 0 <= float(ov) <= 5:
            out['score'] = float(ov)
            out['parse_mode'] = 'json'
        scores = obj.get('scores')
        if isinstance(scores, dict):
            for dim, val in scores.items():
                if isinstance(val, dict):
                    s = val.get('score', val.get('分'))
                else:
                    s = val
                if isinstance(s, (int, float)):
                    out['dims'][str(dim).strip()] = float(s)
                elif isinstance(s, str):
                    try:
                        out['dims'][str(dim).strip()] = float(s)
                    except ValueError:
                        pass

        # ---- ★ 显式检出字段 ----
        # v3 prompt 要求输出 missing_elements / contradictions / completeness，
        # 这是「机制化修复」的**唯一可量化证据**（v1/v2 不产出，属预期缺失）。
        # ⚠️ 必须区分「字段为空」与「字段缺失」：
        #    · missing_elements=[] / ["无"] → Judge 主动声明未发现遗漏
        #    · 字段根本不存在 → v1/v2 prompt 未要求，不能当作「未发现」
        #    这个区分决定 v3 提升是真实检出还是仅仅打分变严。
        for key in ('missing_elements', 'contradictions',
                    'key_evidence', 'counterevidence'):
            v = obj.get(key)
            if isinstance(v, list):
                #归一：把 "无" / "none" / ["无"] 之类视作空列表（主动声明无发现）
                items = [str(x).strip() for x in v
                         if str(x).strip().lower() not in ('无', 'none', 'n/a', 'null', '')]
                out[key] = items
                out[key + '_reported'] = True
            elif isinstance(v, str) and v.strip():
                s = v.strip()
                if s.lower() in ('无', 'none', 'n/a', 'null'):
                    out[key] = []
                else:
                    out[key] = [s]
                out[key + '_reported'] = True
        # ★ 2026-10-12 修复：v3 prompt 的输出键是 **`checklist`**（CE1–CE7），
        #   而原代码只读 `completeness` → 该项在 v3 上永远为空。
        #   两个键都读（不改变 explicit_detect 的判定依据，仅补全留痕）。
        #
        # ★★ 2026-10-15 补实验新增（**必读**）：v5_checkeval 的 `checklist`
        #   存的是 Q1–Q20 的 {"answer": "yes"/"no", "note": ...}，
        #   与 v3/v4 的 CE1–CE7 {"present": bool} **同名不同构**。
        #   若不加区分，Q 项的 val.get('present') 恒为 None
        #   → completeness 会凭空多出 20 条 False、completeness_reported=True
        #   —— 这正是本项目最防的「静默误读」。故先按键名前缀分流：
        #     CE*/C* → 完整性表；Q* → 二元项（另存 binary_checklist）。
        chk = obj.get('checklist')
        ce_items, q_items = {}, {}
        if isinstance(chk, dict):
            for kk, vv in chk.items():
                key = str(kk).strip()
                if key.upper().startswith('Q'):
                    q_items[key] = vv
                else:
                    ce_items[key] = vv
        if q_items and not ce_items:
            for kk, vv in q_items.items():
                ans = vv.get('answer') if isinstance(vv, dict) else vv
                out['binary_checklist'][kk] = str(ans).strip().lower()
        yc = obj.get('yes_count')
        if isinstance(yc, (int, float)):
            out['yes_count'] = int(yc)
            out['yes_count_reported'] = True
        # 模型自报的 yes 数与本地实算对账（不一致只留痕，不改判定）
        _local_yes = sum(1 for v in out['binary_checklist'].values()
                         if v.startswith('y'))
        if out['binary_checklist']:
            if not out['yes_count_reported']:
                out['yes_count'] = _local_yes
            elif out['yes_count'] != _local_yes:
                out['yes_count_mismatch'] = True
        comp = obj.get('completeness')
        if not isinstance(comp, dict):
            comp = ce_items if ce_items else None
        if isinstance(comp, dict):
            for ce, val in comp.items():
                if isinstance(val, dict):
                    out['completeness'][str(ce).strip()] = bool(val.get('present'))
                else:
                    out['completeness'][str(ce).strip()] = bool(val)
            out['completeness_reported'] = True

    # ---- 兜底：正则 ----
    if out['score'] is None:
        ms = _SCORE_RE.findall(text)
        if ms:
            try:
                out['score'] = float(ms[-1])
                out['parse_mode'] = 'regex_score'
            except ValueError:
                pass
    if not out['dims']:
        # ① JSON 键值对形态：{"<维度>": {"score": N
        #    —— 即使整体 JSON 结构已损坏，维度分这一段通常仍完好可抠
        for name, val in _DIM_JSON_RE.findall(text):
            nm = name.strip()
            if nm.startswith('"') or nm in ('overall', 'score', 'reason', 'value'):
                continue
            try:
                out['dims'][nm] = float(val)
            except ValueError:
                pass
        # ② markdown 列表形态：`- 维度名：4`
        if not out['dims']:
            for name, val in _DIM_RE.findall(text):
                try:
                    out['dims'][name.strip()] = float(val)
                except ValueError:
                    pass
        if out['dims'] and out['parse_mode'] is None:
            out['parse_mode'] = 'regex_dims'

    if out['score'] is None:
        out['parse_failed'] = True
    return out


# ============ 后端调用 ============
# ★ 2026-10-12 提高上限：3000 → 4096
#   原因：v3 的输出比 v1 长出约 600–900 字符（多了 checklist 7 项 +
#   missing_elements + contradictions）。v1 实测输出约 1200–1400 字符，
#   v3 预计 1900–2400，**逼近 3000 token 上限**。
#   而 `overall` 是 JSON 的**最后一个键** → 一旦被截断就必然丢总分，
#   且正则兜底也救不回（被截掉了）→ 整轮 v3 部分白跑。
#   提到 4096 纯粹是**截断保险**，不改变 prompt/评分语义。
#   上限检查：prompt(v3≈2300) + 病历 + 答案 + 输出(4096) ≈ 9000 < max_model_len 16384 ✓
#   （v1 已完成且有 100% 可解析（27 条离线重解析全部救回、含最后的 overall）
#     → 说明 v1 在 3000 下并未发生截断，故 v1 结果不受本改动影响。）
MAX_TOKENS = 4096

# ★ 2026-10-15：v5_checkeval（补实验臂 D）的输出显著更长 —— 20 条二元项**各带一句
#   note** + 10 维理由。预估输出 3000–4500 字符，逼近 4096 token 上限。
#   而 `overall` 是 JSON 的**最后一个键** → 一旦截断必然丢总分，正则兜底也救不回
#   （与 v3 当年踩的坑同型）。故**只对 v5 放宽**，其余版本一律保持 4096 不动。
#   上限检查：prompt(v5≈1800 字符 ≈1200 tok) + 病历 + 答案(~900 tok) + 6144
#            ≈ 8900 < max_model_len 16384 ✓
# ★ 2026-10-16：v1_baseline 在**异族 API judge**（deepseek-v4-pro）上同样撞 4096 ——
#   §7.6 那次试探里 `TCMCR-0078` 的 3 条**全部不可用**（P1/M1 解析失败，P2 虽解析成功
#   但原答分数丢失 → drop 也算不出），截断点均在 `key_evidence` 中途、`overall` 丢失，
#   与下面 v5 的"`overall` 是最后一个键 → 截断必丢总分"是同一机理。
#   而 v1 在本地 Qwen3-14B 上实测输出仅 1200–1400 字符（见上）→ 远低于 8192，
#   **故本项不改变任何既有 vllm 结果**，只是给输出更长的 API judge 留足余量。
#   上限检查：prompt(v1) + 病历 + 答案 + 8192 ≈ 13000 < max_model_len 16384 ✓
MAX_TOKENS_BY_VER = {'v5_checkeval': 6144, 'v1_baseline': 8192}


def max_tokens_for(ver):
    return MAX_TOKENS_BY_VER.get(ver, MAX_TOKENS)


def call_judge_api(prompt_text, model, verbose=False, temperature=0.0,
                   max_tokens=None):
    return llm_api.chat([{'role': 'user', 'content': prompt_text}],
                        model=model,
                        max_tokens=max_tokens or MAX_TOKENS,
                        temperature=temperature,
                        verbose=verbose)


def call_judge_vllm(prompt_text, model, host='http://127.0.0.1:8000',
                    verbose=False, temperature=0.0, max_tokens=None):
    """调用本地 vLLM Judge。

    ★ Qwen3 系列（14B/32B）默认开启 thinking mode，思考 token 会先吃光
      max_tokens，导致正式 JSON 正文为空（与 llm_api.py 侧遇到的同一问题）。
      故显式传 chat_template_kwargs.enable_thinking=False 关闭思考，
      并保留 _strip_thinking 作为二次兜底。
    ★ served-model-name：vLLM 默认接受任意 model 字段值，但为避免
      「启动的是 A 模型、请求写B 模型」的静默错配，这里默认透传调用方给的 model。
    ★ 2026-10-15：temperature 由调用方显式传入（默认 0.0，保持既有主实验语义）。
      自一致臂需要 T>0 采样；**T>0 时不得启用原答分缓存**（见 main 里的 _use_cache）。
    """
    import urllib.request
    payload = {'model': model,
               'messages': [{'role': 'user', 'content': prompt_text}],
               'max_tokens': max_tokens or MAX_TOKENS, 'temperature': temperature,
               'chat_template_kwargs': {'enable_thinking': False}}

    req = urllib.request.Request(
        host + '/v1/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as f:
        r = json.loads(f.read().decode())
    if verbose:
        u = r.get('usage', {})
        print(f"      [vllm/{model}] {time.time()-t0:.1f}s "
              f"in={u.get('prompt_tokens')} out={u.get('completion_tokens')}")
    out = r['choices'][0]['message']['content']
    # 二次兜底：若thinking 未被 chat_template 关掉，这里剥掉
    return _strip_thinking(out)


# ============ 样本载入 ============
def _load_perturbations(perturb_types):
    """载入扰动对。

    ★ 字段名与路径必须与 s03_build_perturbations.py 的实际产物一致：
        路径  01_data/perturbations/perturb_<tag>_<severity>.jsonl
        字段  case_text / original_output / perturbed_output
      （早期版本误按 'perturbation_pairs.jsonl' + 'input'/'original' 读取，
        与产物对不上，会静默读到 0 条样本。）
    """
    rows = []
    n_missing_id = 0
    n_missing_pair = 0
    if not os.path.isdir(PERT):
        print(f'  ⚠ 扰动目录不存在：{PERT}')
        return rows
    files = sorted(f for f in os.listdir(PERT)
                   if f.startswith('perturb_') and f.endswith('.jsonl')
                   and '_failures_' not in f)
    for fn in files:
        # 文件名形如perturb_<tag>_<severity>.jsonl
        sev = fn.rsplit('_', 1)[-1].replace('.jsonl', '')
        for line in open(os.path.join(PERT, fn), encoding='utf-8'):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if perturb_types and r.get('ptype') not in perturb_types:
                continue
            # ★ case_id 容错：s03 当前写 'case_id'，早期版本写 'id'。
            #   直接 r['case_id'] 在旧产物上会 KeyError 崩掉整轮评分，
            #   而这是要花钱的 GPU 任务 → 宁可报错计数，也不静默跳过。
            cid = r.get('case_id') or r.get('id')
            if cid is None:
                n_missing_id += 1
                continue
            if not r.get('original_output') or not r.get('perturbed_output'):
                n_missing_pair += 1
                continue
            rows.append(dict(
                case_id=cid, tier=r.get('tier'),
                instruction='',
                input=r.get('case_text', ''),
                answer=r.get('original_output', ''),
                perturbed=r.get('perturbed_output', ''),
                ptype=r.get('ptype'),
                severity=r.get('severity', sev),
                block=(r.get('perturb_meta') or {}).get('block'),
                len_change_pct=r.get('len_change_pct')))
    if n_missing_id or n_missing_pair:
        print(f'  ⚠ 扰动集载入告警：缺 id {n_missing_id} 条 / 缺原答或扰动答 {n_missing_pair} 条')
    print(f'  扰动集载入：{len(rows)} 条（来自 {len(files)} 个文件）')
    if files:
        from collections import Counter
        print('    分布：', dict(Counter(x['ptype'] for x in rows)))
    return rows


def load_pairs(subset, perturb_types=None):
    """返回 [{case_id, tier, input, answer, perturbed, ptype, ...}]"""
    rows = []
    #别名归一：calibration_99 → calibration，perturb_150 → perturbation
    subset = {'calibration_99': 'calibration',
              'perturb_150': 'perturbation'}.get(subset, subset)
    if subset in ('calibration', 'all'):
        cases = {c['id']: c for c in json.load(
            open(os.path.join(UPSTREAM, 'selected_calibration_99.json'),
                 encoding='utf-8'))}
        # 优先读vLLM 产出的 raw_outputs（含 raw_output 与 blocks）
        for tag in ('raw_Qwen3-8B', 'raw_ShizhenGPT-7B-LLM',
                    'raw_HuatuoGPT-3-8B'):
            pf = os.path.join(RAW, f'{tag}_calibration_99.jsonl')
            if not os.path.exists(pf):
                continue
            for line in open(pf, encoding='utf-8'):
                r = json.loads(line)
                # ★ s02b 写出的字段是 'id'（不是 'case_id'）——2026-10-12 实测确认
                cid = r.get('case_id') or r.get('id')
                c = cases.get(cid)
                if not c:
                    continue
                rows.append(dict(case_id=cid, tier=r.get('tier'),
                                 instruction=c['instruction'],
                                 input=c['input'],
                                 answer=r.get('raw_clean')
                                 or r.get('raw_output', ''),
                                 perturbed=None, ptype=None))
            break                      # 只用第一个存在的模型
    if subset in ('perturbation', 'all'):
        rows.extend(_load_perturbations(perturb_types))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prompt-ver', default='v1_baseline',
                    choices=list(PROMPTS.keys()),
                    help='v1_baseline=复现官方规格 / v2_reword=仅改措辞 / '
                         'v3_checklist=加完整性检查表 / v4_aggregate=加聚合规则 / '
                         'v5_checkeval=CheckEval 式二元分解（外部对照）/ '
                         'v3_no_ce / v3_no_cc=2×2 组件消融。'
                         '详见 00_config/SUPP_EXPERIMENTS.md')
    ap.add_argument('--backend', default='api', choices=['api', 'vllm'])
    ap.add_argument('--model', default=None)
    ap.add_argument('--subset', default='calibration',
                    # ★ 加 calibration_99 / perturb_150 别名，与 s02b 保持一致
                    #   （2026-10-12 因三个脚本三套命名导致 server_init 传参被拒）
                    choices=['calibration', 'perturbation', 'all',
                             'calibration_99', 'perturb_150'])
    ap.add_argument('--perturb-types', default=None,
                    help='逗号分隔，如 P1_coldheat,M1_omission')
    ap.add_argument('--host', default='http://127.0.0.1:8000')
    ap.add_argument('-n', type=int, default=0, help='0 = 全部')
    ap.add_argument('--seed', type=int, default=None)
    # ★ 2026-10-15 新增：自一致臂（补实验臂 S，见 00_config/SUPP_EXPERIMENTS.md §3）
    #   默认 0.0 = 与既有主实验完全一致的确定性设置，**不影响任何既有行为**。
    ap.add_argument('--temperature', type=float, default=0.0,
                    help='采样温度。默认 0.0（确定性，主实验口径）。'
                         '自一致臂需 >0（建议 0.7）；此时会关闭原答分缓存并'
                         '按 run 序号分文件落盘，避免覆盖既有结果。')
    ap.add_argument('--run-idx', type=int, default=None,
                    help='多次采样的轮次序号（自一致臂用）。非空时输出文件与'
                         '断点文件名会追加 __runN，与既有产物完全隔离。')
    args = ap.parse_args()

    if args.temperature > 0 and args.run_idx is None:
        ap.error('--temperature > 0 时必须同时给 --run-idx（否则多次采样会'
                 '写进同一个文件、互相覆盖）')

    ensure_dirs()
    seed = args.seed if args.seed is not None else SEED
    if args.run_idx is not None:
        # 自一致臂：每轮用互不相同的种子，保证可复现（同一 run-idx 重跑同结果）
        seed = seed + args.run_idx * 1000
    set_global_seed(seed)

    # 默认模型：API 用官方原型号（仅趋势验证）；vLLM 用本地 32B（主实验）
    if args.model:
        model = args.model
    elif args.backend == 'api':
        model = ROLE_JUDGE_OFFICIAL[0]
    else:
        model = ROLE_JUDGE_MODELS[0]
    ptypes = ([t.strip() for t in args.perturb_types.split(',')]
              if args.perturb_types else None)
    rows = load_pairs(args.subset, ptypes)
    if args.n:
        rows = rows[:args.n]
    if not rows:
        print('✗ 没有可评分的样本。')
        print('  校准集需先跑S02/S03；扰动集需先跑 S04 扰动构造。')
        return

    tag = f'{args.backend}_{model.replace("/", "_")}_{args.prompt_ver}'
    if args.run_idx is not None:
        # ★ 自一致臂：每轮独立文件 + 独立断点，绝不与既有产物混淆
        tag = f'{tag}__run{args.run_idx}'
    out = os.path.join(JUDGE_SCORES, f'judge_{tag}.jsonl')
    prog = out + '.progress'
    logf = os.path.join(LOGS, f's05_judge_{tag}.log')

    done = set()
    if os.path.exists(prog):
        done = set(json.load(open(prog, encoding='utf-8')))
    if os.path.exists(out):
        for line in open(out, encoding='utf-8'):
            try:
                done.add(json.loads(line)['key'])
            except Exception:
                pass

    todo = [r for r in rows
            if f"{r['case_id']}|{r['ptype']}" not in done]
    print('=' * 66)
    print(' S05  Judge 评分')
    print('=' * 66)
    print(f' prompt 版: {args.prompt_ver}')
    print(f' 后端     : {args.backend}  模型: {model}')
    print(f' 采样     : temperature={args.temperature}'
          + (f'  run-idx={args.run_idx}' if args.run_idx is not None else ''))
    print(f' 样本     : {len(todo)} 待评 / {len(rows)} 总计'
          f'（已完成 {len(rows)-len(todo)}）')
    print(f' 种子     : {seed}')
    print(f' 原答缓存 : {"启用（T=0 确定性，可安全复用）" if args.temperature <= 0 else "★ 关闭（T>0 时每轮必须独立采样，否则自一致投票会被伪造成 100% 一致）"}')
    print(f' 输出     : {out}')
    if args.backend == 'api':
        p = llm_api.load_provider()
        print(f' 平台     : {p["label"]}')
        print(' ⚠ 提醒：该平台对含病例文本的 prompt 可能挂起（实测二分定位到'
              '纯中医术语）')
        print('   → API 仅用于子集趋势验证；主实验请用 --backend vllm')
    print('-' * 66)

    t0_all = time.time()
    n_fail = n_parse = 0
    n_cached = 0
    n_yesmm = 0      # ★ yes_count 自报数与本地实算不一致的条目（仅 v5 可能非零）
    # ★ 原答分数缓存（2026-10-12 新增）
    #   同一病例会被注入 3 类扰动 → 落到 3 条记录 → 原答被**重复评 3 次**。
    #   temperature=0 时同输入同输出，重复调用纯属烧钱。
    #   实测：287 条扰动 = 574 次调用，缓存后降到 ~386 次（省 33%）。
    #   ⚠ 删掉 s05 输出文件时，缓存也随之失效重建 —— 不会与 .progress 冲突：
    #     缓存命中只影响「本次运行内不再重复调用」，落盘字段完全一样。
    # ★★ 2026-10-15：**T>0 时必须关闭缓存**。自一致投票的全部意义是「同一输入
    #   独立采样 k 次」；若沿用 T=0 时的缓存假设，同一病例的原答在 k 轮里会被
    #   复用成同一份结果 → 投票被人为伪造成"完全一致"，基线实验直接白跑。
    _use_cache = (args.temperature <= 0.0)
    orig_cache = {}
    fh_log = open(logf, 'a', encoding='utf-8')
    fh_log.write(f'\n===== {time.strftime("%Y-%m-%d %H:%M:%S")} '
                 f'{tag} seed={seed} T={args.temperature} =====\n')
    with open(out, 'a', encoding='utf-8') as f:
        for i, r in enumerate(todo, 1):
            t_pair = time.time()
            # ---- ★ 成对评分（2026-10-12 修正）----
            # 检出率是**配对指标**：同一 case 的原答与扰动答必须各评一次，
            # 靠两者分数之差判断「是否检出扰动」。
            # 早期版本只评了 answer（原答），perturbed 被载入却从未使用
            # → 算不出检出率，实验无法进行。
            # kind=original → score_orig；kind=perturbed → score_pert
            pair = [(r.get('perturbed') or '', 'perturbed'),
                    (r['answer'], 'original')]
            got = {}
            for text_ans, kind in pair:
                ckey = r['case_id'] if kind == 'original' else None
                if _use_cache and ckey and ckey in orig_cache:
                    got[kind] = orig_cache[ckey]
                    n_cached += 1
                    continue
                if not text_ans:
                    got[kind] = ('', None, True, False, _EMPTY_PARSED)
                    if _use_cache and ckey:
                        orig_cache[ckey] = got[kind]
                    continue
                prompt = (get_prompt(args.prompt_ver) + '\n\n'
                          + build_user_prompt(r['input'], text_ans))
                t1 = time.time()
                try:
                    _mt = max_tokens_for(args.prompt_ver)
                    raw = (call_judge_api(prompt, model,
                                          temperature=args.temperature,
                                          max_tokens=_mt)
                           if args.backend == 'api'
                           else call_judge_vllm(prompt, model, args.host,
                                                temperature=args.temperature,
                                                max_tokens=_mt))
                    ok = True
                except Exception as e:
                    raw, ok = '', False
                    n_fail += 1
                    fh_log.write(
                        f'[FAIL] {r["case_id"]}/{kind} '
                        f'{type(e).__name__}: {str(e)[:200]}\n')
                    fh_log.flush()
                parsed = parse_judge_output(raw)
                if ok and parsed['parse_failed']:
                    n_parse += 1
                    fh_log.write(f'[PARSE] {r["case_id"]}/{kind} 未提取到分数\n'
                                 f'        raw: {raw[:200]}\n')
                    fh_log.flush()
                got[kind] = (parsed['reason'], parsed['score'],
                             parsed['parse_failed'], ok, parsed)
                if _use_cache and ckey:
                    orig_cache[ckey] = got[kind]
            parsed = {'reason': got['perturbed'][0], 'score': got['perturbed'][1],
                      'parse_failed': got['perturbed'][2]}
            if got['perturbed'][4].get('yes_count_mismatch'):
                n_yesmm += 1
                fh_log.write(f"[YESMM] {r['case_id']} 自报 yes_count 与本地实算不一致"
                             f"（自报 {got['perturbed'][4].get('yes_count')}）\n")
                fh_log.flush()
            ok = got['original'][3] and got['perturbed'][3]
            s_o, s_p = got['original'][1], got['perturbed'][1]
            drop = (round(s_o - s_p, 3)
                    if (s_o is not None and s_p is not None) else None)

            # ---- 检出判定（扰动样本专用）----
            # 两条口径并行落盘，s06 分别统计：
            #   ① 隐式：score_drop ≥ 1.0（v1/v2/v3 都可用）
            #   ② 显式：Judge 主动报告 contradictions / missing_elements
            #     （仅 v3 产出该字段 —— v1/v2 的 prompt 未要求，
            #      所以 *_reported=False 表示「未要求」而非「未发现」，
            #      s06 统计时必须排除，否则会把 v3 的机制优势算成 v1 的缺陷。）
            pp, po = got['perturbed'][4], got['original'][4]

            def _explicit_detect(p):
                """显式检出：报告了矛盾，或报告了遗漏要素。"""
                if not p.get('contradictions') and not p.get('missing_elements'):
                    return False
                return bool(p.get('contradictions_reported')
                            or p.get('missing_elements_reported'))

            def _explicit_detect_perturbed(p):
                """扰动答相对原答**新增**的显式报告 → 才是真检出。"""
                if not p.get('contradictions_reported') and \
                   not p.get('missing_elements_reported'):
                    return False
                new_c = set(p.get('contradictions') or []) - set(po.get('contradictions') or [])
                new_m = set(p.get('missing_elements') or []) - set(po.get('missing_elements') or [])
                return bool(new_c or new_m)

            rec = dict(key=f"{r['case_id']}|{r['ptype']}",
                       case_id=r['case_id'], tier=r.get('tier'),
                       ptype=r.get('ptype'), severity=r.get('severity'),
                       perturbed_block=r.get('block'),
                       prompt_ver=args.prompt_ver, backend=args.backend,
                       model=model, seed=seed,
                       # ★ 2026-10-15：自一致臂的可追溯字段（既有主实验为 0.0 / None）
                       temperature=args.temperature, run_idx=args.run_idx,
                       # ★ 成对分数：原答 / 扰动答 / 分差 —— 检出率由此计算
                       score=s_p, score_original=s_o, score_drop=drop,
                       dims=pp['dims'],
                       # ★ 原答的维度分（2026-10-12 新增）
                       #   只存扰动答的 dims 时，「扰动所针对的那个维度有没有掉分」
                       #   这个更贴近"检出"的定义根本算不出来 —— 而实测发现
                       #   Judge 常在**分维度**上察觉、但 overall 保持 4 分不变，
                       #   只靠 overall 会把真实检出全部漏掉。
                       dims_original=po['dims'],
                       reason=pp['reason'],
                       reason_original=po['reason'],
                       parse_mode=pp['parse_mode'],
                       parse_failed=pp['parse_failed'],
                       # ★ 显式检出证据（v3 checklist 的产出）
                       missing_elements=pp['missing_elements'],
                       contradictions=pp['contradictions'],
                       missing_elements_reported=bool(pp.get('missing_elements_reported')),
                       contradictions_reported=bool(pp.get('contradictions_reported')),
                       completeness=pp.get('completeness') or {},
                       # ★ 2026-10-15：CheckEval 式臂（v5）的二元项留痕。
                       #   其余版本恒为空 —— 便于事后核验「该臂到底产出了什么」。
                       binary_checklist=pp.get('binary_checklist') or {},
                       yes_count=pp.get('yes_count'),
                       yes_count_reported=bool(pp.get('yes_count_reported')),
                       yes_count_mismatch=bool(pp.get('yes_count_mismatch')),
                       key_evidence=pp.get('key_evidence') or [],
                       explicit_detect=_explicit_detect(pp),
                       explicit_detect_new=_explicit_detect_perturbed(pp),
                       api_ok=ok, elapsed=round(time.time() - t_pair, 2),
                       ts=time.strftime('%Y-%m-%d %H:%M:%S'))
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()
            if i % 5 == 0 or i == len(todo):
                json.dump([r_['case_id'] for r_ in todo[:i]], open(prog, 'w'),
                          ensure_ascii=False)
                el = time.time() - t0_all
                eta = el / i * (len(todo) - i) / 60
                print(f'  [{i:>4}/{len(todo)}] {r["case_id"]:<14} '
                      f'orig={s_o if s_o is not None else "N/A":>5} '
                      f'pert={s_p if s_p is not None else "N/A":>5} '
                      f'drop={drop if drop is not None else "N/A":>6} '
                      f'{time.time()-t_pair:5.1f}s  ETA {eta:.0f}min', flush=True)
    fh_log.close()

    print('-' * 66)
    print(f' 完成 {len(todo)} 条，耗时 {(time.time()-t0_all)/60:.1f} 分钟')
    print(f' API 失败 {n_fail} 条 | 解析失败 {n_parse} 条'
          + (f' | 原答缓存命中 {n_cached} 次（省去重复调用）' if n_cached else '')
          + (f' | ★ yes_count 自报/实算不一致 {n_yesmm} 条' if n_yesmm else ''))
    print(f' → {out}')
    print(f' → 日志 {logf}')
    if n_parse:
        print(f'\n ⚠ {n_parse} 条未能提取分数，须人工检查（见日志 [PARSE]）。')
    print('=' * 66)


if __name__ == '__main__':
    main()
