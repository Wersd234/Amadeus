# -*- coding: utf-8 -*-
"""汇总 144 张牧濑红莉绪立绘的分类结果：
- 表情：按“同脸同一表情码”的最终裁定（同族同编号=同一张脸）
- 衣着：目录判定（风衣目录=A，Lab Coat 目录=B），与目检一致
- 景别：按真实像素尺寸
- 姿势：优先子代理备注，否则按组默认
输出 CSV + XLSX，并复制图片到 按衣着/<衣着>/<表情>/
"""
import csv, glob, os, sys
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR_A = os.path.join(ROOT, "PC _ Computer - Steins;Gate - Characters - Makise Kurisu")
DIR_B = os.path.join(ROOT, "PC _ Computer - Steins;Gate - Characters - Makise Kurisu (Lab Coat)")

EXPR_NAMES = {1:"平静自然",2:"微笑",3:"大笑",4:"惊讶",5:"苦恼不快",6:"愤怒烦躁",7:"悲伤忧郁",8:"害羞窘迫",9:"认真凝视",10:"无法辨识"}
CLOTH_NAMES = {"A":"米色风衣款","B":"白大褂款","C":"便服其他","D":"不可辨识"}
EXPR_DIR = {n: f"{n:02d}-{EXPR_NAMES[n]}" for n in EXPR_NAMES}
CLOTH_DIR = {k: f"{k}-{v}" for k, v in CLOTH_NAMES.items()}

# 最终裁定：族别(a抱臂近景系/b全身抱臂系/c抚领带系) × 表情码 -> 表情编号
CANON = {
 "a": {"01":1,"02":2,"03":5,"04":5,"05":5,"06":2,"07":6,"08":6,"09":8},
 "b": {"01":1,"02":2,"03":5,"04":5,"05":5,"06":6,"07":6,"08":2,"09":8},
 "c": {"01":1,"02":8,"03":2,"04":5,"05":5,"06":5},
}

# 尺寸 -> 景别
def framing(w, h):
    if h >= 750 and w >= 750: return "特写(胸部以上)"
    if h >= 690: return "半身(腰部以上)"
    return "全身(大半身)"

# 代理原始标注（用于备注差异）
agent = {}
for lp in glob.glob(os.path.join(ROOT, "_labels", "*.txt")):
    for line in open(lp, encoding="utf-8-sig"):
        line = line.strip()
        if not line or "|" not in line: continue
        parts = line.split("|")
        fn = parts[0].strip()
        try: e = int(parts[1]); c = parts[2].strip()
        except (ValueError, IndexError): continue
        pose = parts[4].strip() if len(parts) > 4 else (parts[3].strip() if len(parts) > 3 else "")
        agent[fn] = (e, c, pose)

# 尺寸表
dims = {}
for line in open(os.path.join(ROOT, "_dimensions.txt"), encoding="utf-8-sig"):
    line = line.strip()
    if not line: continue
    fn, d, wh = line.split("|")
    w, h = wh.lower().split("x")
    dims[fn] = (int(w), int(h))

rows, adjudicated = [], []
for d, cloth in ((DIR_A, "A"), (DIR_B, "B")):
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".png"): continue
        name = os.path.splitext(f)[0]
        fam = name[4:7]; letter = fam[2]; code = name[7:9]
        expr = CANON[letter][code]
        w, h = dims[f]
        fr = framing(w, h)
        note = ""
        pose = ""
        if f in agent:
            ae, ac, apos = agent[f]
            pose = apos
            if ae != expr:
                note = f"代理原标{ae}-{EXPR_NAMES.get(ae,'?')}，同脸裁定为{expr}-{EXPR_NAMES[expr]}"
                adjudicated.append((f, ae, expr))
        if not pose:
            pose = "未知(手不可见)" if fr.startswith("特写") else {"a":"双手垂立","b":"抱臂","c":"整理领带"}[letter]
        rows.append({
            "文件名": f, "来源目录": "Makise Kurisu(风衣)" if cloth == "A" else "Makise Kurisu(Lab Coat)",
            "衣着类别": CLOTH_DIR[cloth], "表情类别": EXPR_DIR[expr],
            "表情编号": expr, "表情名称": EXPR_NAMES[expr],
            "衣着编号": cloth, "衣着名称": CLOTH_NAMES[cloth],
            "景别": fr, "姿势": pose, "备注": note,
            "_src": os.path.join(d, f), "_cloth": cloth, "_expr": expr,
        })

rows.sort(key=lambda r: (r["衣着编号"], r["表情编号"], r["文件名"]))
print(f"总行数: {len(rows)}  裁定修正数: {len(adjudicated)}")

# ---- CSV ----
cols = ["文件名","来源目录","衣着类别","表情类别","表情编号","表情名称","衣着编号","衣着名称","景别","姿势","备注"]
csv_path = os.path.join(ROOT, "分类结果清单.csv")
with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
    wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    wr.writeheader()
    for r in rows: wr.writerow(r)

# ---- XLSX ----
wb = Workbook()
ws = wb.active; ws.title = "明细"
ws.append(cols)
for r in rows: ws.append([r[c] for c in cols])
for c in ws[1]: c.font = Font(bold=True)
ws.freeze_panes = "A2"

ws2 = wb.create_sheet("统计")
ws2.append(["表情 \\ 衣着", "A-米色风衣款", "B-白大褂款", "合计"])
mx = defaultdict(lambda: defaultdict(int))
for r in rows: mx[r["表情编号"]][r["衣着编号"]] += 1
for n in sorted(EXPR_NAMES):
    a, b = mx[n].get("A",0), mx[n].get("B",0)
    if a or b: ws2.append([f"{n:02d}-{EXPR_NAMES[n]}", a, b, a+b])
ws2.append(["合计", sum(mx[n].get("A",0) for n in EXPR_NAMES), sum(mx[n].get("B",0) for n in EXPR_NAMES), len(rows)])
ws2.append([]); ws2.append(["景别", "张数"])
fx = defaultdict(int)
for r in rows: fx[r["景别"]] += 1
for k, v in sorted(fx.items()): ws2.append([k, v])
ws2.append([]); ws2.append(["组合分布(衣着×表情,张)"])
for r in rows: pass
for cloth in "AB":
    for n in sorted(EXPR_NAMES):
        cnt = mx[n].get(cloth, 0)
        if cnt: ws2.append([f"{CLOTH_DIR[cloth]} × {EXPR_DIR[n]}", cnt])
for c in ws2.iter_rows(min_row=1, max_row=1):
    for cell in c: cell.font = Font(bold=True)
xlsx_path = os.path.join(ROOT, "分类结果清单.xlsx")
wb.save(xlsx_path)

# ---- 复制图片 ----
copied = 0
for r in rows:
    dst_dir = os.path.join(ROOT, "按衣着", CLOTH_DIR[r["_cloth"]], EXPR_DIR[r["_expr"]])
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, r["文件名"])
    with open(r["_src"], "rb") as fi, open(dst, "wb") as fo:
        fo.write(fi.read())
    copied += 1

print("CSV:", csv_path)
print("XLSX:", xlsx_path)
print("已复制:", copied)
print("矩阵:", dict((n, dict(mx[n])) for n in sorted(mx)))
print("裁定修正:")
for f, ae, ex in adjudicated: print(f"  {f}: 代理{ae} -> 裁定{ex}")
