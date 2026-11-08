#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""figure_style.py —— ★ 全篇柱状图与标注的**唯一排印规范**（single source of truth）

为什么要有这个文件：
    2026-10-16 全局核对发现，同一篇论文里的柱状图存在**三套互不相同的排印**——
    柱体框线有的 `black/0.4`、有的 `white/0.5`（等于没框线）；柱顶数字从 6.2 到 9.5 pt 不等；
    同一张图的两个面板刻度字号甚至不一样（fig9 面板 (a)=7.0、(b)=6.2）。
    审稿人不会去数 pt，但会看出「这批图不是一套做的」。本模块把规范钉死在一处，
    各绘图脚本只许引用，不许各自硬编码新值。

规范（2026-10-16 定，依据 KBS/Elsevier 的 6 pt 硬下限 + 全篇现有众数）：
    ┌ 角色 ──────────────────────── 字号 ─── 依据 ────────────────────────────────┐
    │ 面板标题                      8.0   │ 全篇 fig1/4/5/8/9 一致                    │
    │ 轴名                          7.6   │ 全篇一致                                  │
    │ 刻度标签                      7.0   │ 全篇众数（fig1 的 7.5 偏大，已回调）       │
    │ 柱顶数值标签                  6.8   │ 取「可读」档；fig4/5 原本即 6.8            │
    │ 柱顶辅助标注（n= / retention） 6.2   │ 次要信息，与图例同级                       │
    │ 图例                          6.2   │ 全篇一致（S1/S2 的 5.6 偏小，已上调）      │
    │ 图脚注                        6.2   │ 全篇一致                                  │
    │ 格内主数字（热力图/网格）      8.0   │ fig10 的 9.5 偏大，已回调                  │
    │ 格内次要数字                  6.8   │ 与柱顶数值同级                             │
    └──────────────────────────────────────────────────────────────────────────┘

    ★ 柱体框线：`edgecolor='black', linewidth=0.4`（= BAR_EDGE / BAR_EDGE_KW）
      —— 与 fig1_targeted_M1 / fig_supp_2x2_ablation / fig_supp_external_checkeval
      这三张既有参考图完全一致。**堆叠柱的每一段都要加**，否则相邻段在浅色区间
      （如 fig8 的 score 0 灰与 score 2 浅蓝）之间没有可辨边界。

硬红线（由 `s12_audit_figures.py` 机械核查）：
    · 任何图内文字 ≥ 6.0 pt（MIN_FONT_PT=5.5 是失败线）
    · 柱体框线必须是 black + 0.4（`s12` 会扫描各脚本源码断言，见 `check_bar_edges()`）
    · 同一张图的两个面板，同类元素字号必须相同
"""
import os

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt

# ---------------------------------------------------------------- 字号
FS_TITLE = 8.0      # 面板标题
FS_AXLABEL = 7.6    # 轴名
FS_TICK = 7.0       # 刻度标签
FS_VALUE = 6.8      # 柱顶数值标签
FS_SUB = 6.2        # 柱顶辅助标注（n= / retention …）
FS_LEGEND = 6.2     # 图例
FS_NOTE = 6.2       # 图脚注
FS_CELL = 8.0       # 格内主数字（热力图 / 混淆网格）
FS_CELL_SUB = 6.8   # 格内次要数字

# ---------------------------------------------------------------- 示意图专用档（fig7 流程图）
# fig7 的文字被**框体物理宽度**约束（框宽 1.15–4.80 英寸不等），不能直接套柱状图档位：
# 放大到 8 pt 会让 'A5 verdict' 这类窄框文字溢出边框。故示意图单独成档，
# 但其**下限仍受同一条 6 pt 红线约束**，且分区标题/框内文字/注释三级关系必须保持一致。
FS_FLOW_SECTION = 9.0   # 分区标题（A / B / C 三个大区）
FS_FLOW_SUB = 7.4       # 分区副标题
FS_FLOW_BOX = 7.2       # 框内主文字
FS_FLOW_BOX_S = 7.0     # 窄框内文字（x/w 受限的框）
FS_FLOW_BOX_L = 6.8     # 宽框内的多行说明（Band C 的两块，行数多故取更小档）
FS_FLOW_NOTE = 6.4      # 框内注释
FS_FLOW_FOOT = 6.2      # 图脚注

# ---------------------------------------------------------------- 柱体框线
# ★ 全篇唯一定义。三个参考图（fig1 / fig_supp_2x2_ablation / fig_supp_external_checkeval）
#   用的就是这一组值，直接复用而不是"重新设计一个更好看的"。
BAR_EDGE = 'black'
BAR_EDGE_LW = 0.4
BAR_EDGE_KW = dict(edgecolor=BAR_EDGE, linewidth=BAR_EDGE_LW)

# 空心 + 斜线填充的对照柱（S1/S2 的 "overall drop" 那根）：同一色系、线略粗以便看清 hatch
BAR_HATCH_LW = 0.7
BAR_HATCH_KW = dict(facecolor='none', edgecolor=BAR_EDGE,
                    linewidth=BAR_HATCH_LW, hatch='///')

# ---------------------------------------------------------------- 面板标号
# ★ 多面板图的子图标题统一写成 "(a)  <面板名>"（字母 + **两个空格** + 名字）。
#   这不是新设计 —— fig8 / fig9 / fig10 / figS3 早就是这个写法，只有 fig2/fig3/fig4/fig5
#   漏了标号（2026-10-17 用户第 13 问指出）。放在这里是为了让"下一张新图"不必再问一遍。
PANEL_LETTERS = 'abcdefgh'


def panel_title(idx, name):
    """第 idx 个（0 起）面板的标题：`(a)  <name>`。"""
    return '(%s)  %s' % (PANEL_LETTERS[idx], name)


# ---------------------------------------------------------------- rcParams
# 各脚本 `plt.rcParams.update(STYLE_RCPARAMS)` 即可拿到一致的字体与轴样式。
STYLE_RCPARAMS = {
    'font.family': 'DejaVu Sans',
    'font.size': 8,
    'axes.titlesize': FS_TITLE,
    'axes.labelsize': FS_AXLABEL,
    'axes.linewidth': 0.6,      # 轴线/刻度线宽（统一；与参考图 S1/S2 一致）
    'axes.spines.top': False,
    'axes.spines.right': False,
    'figure.dpi': 300,
    'pdf.fonttype': 42,
    # ★ EPS 必须用 42 号字体（TrueType，字体内嵌）；matplotlib 默认是 3 号（Type 3）。
    #   Type 3 的轮廓是「画出来的字形」，排版系统认不出它对应哪个字体，Elsevier 的
    #   artwork 检查会把「字体未嵌入」判为不合格。同一份图存 png/pdf 都正常，
    #   只有 eps 会被退回 —— 这类问题在本地看不见，必须提前钉死。
    'ps.fonttype': 42,
    'ps.useafm': False,         # True 会把文字换成 14 个标准 PS 字体（= 不内嵌）
}


def apply_rcparams(extra=None):
    """套用 rcParams（`extra` 可覆盖个别键，例如 mathtext.fontset）。"""
    d = dict(STYLE_RCPARAMS)
    if extra:
        d.update(extra)
    plt.rcParams.update(d)


# ---------------------------------------------------------------- 落盘（三格式）
#: 投稿三件套：png（Word 初稿/预览）、pdf（Elsevier 主推的矢量）、eps（只收 eps 的
#: 期刊与老排版系统）。三者必须**同一张图、同一次裁剪**，否则物理尺寸会互不相同。
FORMATS = ('png', 'pdf', 'eps')
SAVE_DPI = 300


def flat(color, alpha, bg='#FFFFFF'):
    """把「颜色 + 透明度」预合成到纯白底，返回不透明的 `#RRGGBB`。

    ★ 为什么必须这么做（2026-10-27）：**EPS 的 PostScript 后端不支持半透明**。
    带 `alpha` 的 artist 存成 eps 时会被画成**实心**（matplotlib 自己的告警：
    "The PostScript backend does not support transparency; partially transparent
    artists will be rendered opaque."）。于是同一张图：
        · png / pdf —— 网格线是淡灰（alpha 0.25）
        · eps      —— 网格线变成实心灰，比柱体还抢眼
    三种格式不一致比"少一种格式"严重得多。**在源头把 alpha 合成掉**是唯一
    干净解法：视觉等价（白色底上的 alpha 混合 = 不透明色），三格式同源。

    只对**背景为白**的普通数据图成立；若有深色底或底图上叠半透明，需传 `bg`。
    """
    c = mcolors.to_rgb(color)
    b = mcolors.to_rgb(bg)
    return '#%02X%02X%02X' % tuple(
        int(round(255.0 * (alpha * c[i] + (1.0 - alpha) * b[i]))) for i in range(3))


#: 网格线的「本色」。实际落盘时按 alpha 预合成（见 `flat`），不再用真 alpha。
GRID_COLOR = '#B0B0B0'


def grid(ax, axis='both', alpha=0.25, lw=0.4, **kw):
    """打浅灰网格线（EPS 安全版）。

    与 `ax.grid(alpha=0.25, lw=0.4)` 的唯一区别：**不传 alpha**，改为把
    `#B0B0B0` 按该 alpha 预合成到白底后当作实色画。白底上两者视觉等价，
    但只有后者能原样存进 eps（PostScript 不支持半透明）。
    """
    ax.grid(axis=axis, color=flat(GRID_COLOR, alpha), linewidth=lw, **kw)
    ax.set_axisbelow(True)


def save_fig(fig, outdir, stem, dpi=SAVE_DPI, facecolor='white', **kw):
    """把 `fig` 同时落成 png / pdf / eps，返回三个路径。

    统一走这里，是为了让三种格式共享同一组落盘参数：
        · `bbox_inches='tight'` —— 否则 eps 的 `%%BoundingBox` 与 pdf 的
          `MediaBox` 对不上，期刊按 eps 的 bbox 裁切时会把图边切掉；
        · `facecolor='white'`  —— eps 没有"透明画布"这一说，底色必须是白；
        · `dpi`                —— 只影响 eps 内嵌的位图层（本仓库仅 fig10/fig11
          的 imshow 热力图有位图内容），矢量部分与 dpi 无关。
    """
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for ext in FORMATS:
        p = os.path.join(outdir, '%s.%s' % (stem, ext))
        fig.savefig(p, dpi=dpi, bbox_inches='tight',
                    facecolor=facecolor, **kw)
        paths.append(p)
    return paths


# ---------------------------------------------------------------- 色阶
# 顺序色阶：低值浅、高值深。fig8（整体分 0–5）与 Figure S3（弱法官分档）都用这一支，
# 于是「分数越高颜色越深」这条视觉规则在全篇恒定 —— 读者在两图之间不需要重新学习色标。
CMAP_SCORE = ['#F2F6FA', '#D6E4F1', '#BFD3E6', '#9DBEE0', '#7BA3D3',
              '#5B8FC7', '#3F76B3', '#2E5F9E']


def score_color_map(keys):
    """把一组（已排序的）分数键均匀映射到 CMAP_SCORE，返回 {key: '#rrggbb'}。

    键少时取色阶两端与中间，键多时自动加密；保证**最小键最浅、最大键最深**。
    """
    ks = sorted(keys)
    n = len(ks)
    if n == 1:
        return {ks[0]: CMAP_SCORE[len(CMAP_SCORE) // 2]}
    span = len(CMAP_SCORE) - 1
    return {k: CMAP_SCORE[int(round(span * i / float(n - 1)))] for i, k in enumerate(ks)}


def text_on(facecolor, dark='#1A1A1A', light='white', thresh=0.62):
    """按底色亮度选黑/白字（深底白字、浅底黑字）—— 全篇统一这一套阈值。"""
    s = facecolor.lstrip('#')
    r, g, b = (int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return light if lum < thresh else dark


# ---------------------------------------------------------------- 自适应标注落位
def _densify(xs, ys, n=25):
    """把折线按每段 n 个采样点加密（纯 Python，不引 numpy）。"""
    pts = []
    for i in range(len(xs) - 1):
        for t in range(n):
            f = t / float(n)
            pts.append((xs[i] + (xs[i + 1] - xs[i]) * f,
                        ys[i] + (ys[i + 1] - ys[i]) * f))
    if xs:
        pts.append((xs[-1], ys[-1]))
    return pts


def annotate_clear(ax, x, y, s, cand, fs=FS_VALUE, color='#555555', pad=1.2,
                   margin=2.0, bound=True, **kw):
    """在候选偏移里挑一个**不压数据折线、不出坐标区**的落位来放这个标注。

    为什么要有它：`fig9` 面板 (b) 的 `73.7 pp` 原本用固定偏移 `i+0.07` 落位，
    它正落在 `overall verdict` 折线的陡升段上 —— 把面板刻度字号统一到 7.0 pt 后
    布局位移，这条标注就压到线上了。**固定偏移的标注天生会随字号/数据变化而回归**，
    所以改成求解式落位：把候选位逐个试，取第一个几何上干净的。

    cand 每一项是 `(dx, dy, ha)`：dx/dy 为点偏移（pt），ha 为该候选的水平对齐方式。
    返回 `(annotation, 命中的候选索引, 压线点数)`；若全部候选都压线，
    返回压线**最少**的那个（并在 docstring 所述意义上降级，不抛异常）。
    """
    fig = ax.figure
    try:
        rend = fig.canvas.get_renderer()
    except Exception:                       # 非 Agg / 未绘制
        fig.canvas.draw()
        rend = fig.canvas.get_renderer()

    pts = []
    for ln in ax.lines:
        xy = ln.get_xydata()
        if len(xy) < 2:
            continue
        pts.extend(_densify([p[0] for p in xy], [p[1] for p in xy]))
    disp = ax.transData.transform(pts) if pts else []

    ax_bb = ax.get_window_extent(renderer=rend)
    cv = kw.pop('clip_on', False)
    zord = kw.pop('zorder', 5)
    best = (None, 0, len(disp) + 1)
    for idx, (dx, dy, ha) in enumerate(cand):
        t = ax.annotate(s, (x, y), textcoords='offset points', xytext=(dx, dy),
                        fontsize=fs, color=color, ha=ha, va='center',
                        zorder=zord, clip_on=cv, **kw)
        bb = t.get_window_extent(renderer=rend)
        n_hit = sum(1 for px, py in disp
                    if bb.x0 - pad < px < bb.x1 + pad
                    and bb.y0 - pad < py < bb.y1 + pad)
        out = (bb.x0 < ax_bb.x0 + margin or bb.x1 > ax_bb.x1 - margin
               or bb.y0 < ax_bb.y0 + margin or bb.y1 > ax_bb.y1 - margin)
        score = n_hit + (1000 if (bound and out) else 0)
        if score < best[2]:
            best = (t, idx, score)
        if score == 0:
            return t, idx, 0
        t.remove()                          # 该候选不合格，撤掉再试下一个
    t, idx, score = best
    if t is None:                           # 极端情况：一个都没建起来
        t = ax.annotate(s, (x, y), textcoords='offset points', xytext=cand[0][:2],
                        fontsize=fs, color=color, ha=cand[0][2], va='center')
        idx = 0
    return t, idx, score
