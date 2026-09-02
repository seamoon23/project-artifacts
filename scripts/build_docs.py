#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스냅샷 + 프로그램 스캔 결과 → 산출물(xlsx, Mermaid ERD) 생성.

생성물
  <프로젝트>_테이블정의서_v<버전>.xlsx
  <프로젝트>_컬럼정의서_v<버전>.xlsx      (+ 주석누락 시트)
  <프로젝트>_코드정의서_v<버전>.xlsx
  <프로젝트>_프로그램설명서_v<버전>.xlsx  (+ 미확인 시트)
  erd_v<버전>.mmd  또는 erd_v<버전>_<도메인>.mmd (테이블 40개 초과 시 분할)

사용:
  python3 build_docs.py --snapshot _snapshot.json --program _program.json \
      --project kapex --version 1.0.0 --outdir docs/산출물
"""
import argparse
import json
import os
import re
from collections import OrderedDict, defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
HEADER_FONT = Font(bold=True, size=10)
BODY_FONT = Font(size=10)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
ERD_SPLIT_THRESHOLD = 40


# --------------------------------------------------------------------------
def sheet(wb, title, headers, rows, widths=None, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(headers)
    for r in rows:
        ws.append(r)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(headers)):
        for cell in row:
            cell.font, cell.border = BODY_FONT, BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=False)
    for i, h in enumerate(headers, start=1):
        w = (widths or {}).get(h)
        if not w:
            longest = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:500]] or [0])
            w = min(max(longest * 1.4 + 2, 8), 60)
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:{}{}".format(get_column_letter(len(headers)), max(ws.max_row, 1))
    return ws


def save(wb, outdir, project, name, version):
    path = os.path.join(outdir, "{}_{}_v{}.xlsx".format(project, name, version))
    wb.save(path)
    return path


def yn(b):
    return "Y" if b else "N"


def typedisp(c):
    return c["type"] + ("({})".format(c["length"]) if c["length"] else "")


# --------------------------------------------------------------------------
def build_table_def(snap, program, outdir, project, version):
    used = defaultdict(int)
    for p in (program or {}).get("programs", []):
        for t in p.get("tables", []):
            used[t.upper()] += 1
    rows = []
    for i, t in enumerate(snap["tables"], start=1):
        rows.append([
            i, t["name"], t.get("comment", ""), t.get("comment", ""),
            len(t["columns"]), ",".join(t.get("pk") or []),
            len(t.get("indexes") or []),
            ", ".join(sorted({f["ref_table"] for f in t.get("fks") or []})),
            used.get(t["name"].upper(), 0), "",
        ])
    wb = Workbook()
    sheet(wb, "테이블정의서",
          ["No", "테이블명(물리)", "테이블명(논리)", "설명", "컬럼수", "PK",
           "인덱스수", "참조(FK)", "참조프로그램수", "비고"],
          rows, {"설명": 40, "테이블명(물리)": 28, "테이블명(논리)": 24}, first=True)
    return save(wb, outdir, project, "테이블정의서", version)


def build_column_def(snap, outdir, project, version):
    code_ids = {c["code_id"].upper() for c in snap.get("codes", [])}

    def guess_code(col):
        n = col["name"].upper()
        for cand in (n, re.sub(r"_(CD|CODE|SE|SE_CODE)$", "", n)):
            if cand in code_ids:
                return cand
        return ""

    rows, missing = [], []
    no = 0
    for t in snap["tables"]:
        for c in t["columns"]:
            no += 1
            rows.append([
                no, t["name"], t.get("comment", ""), c["ordinal"], c["name"], c["comment"],
                c["type"], c["length"], yn(c["pk"]), yn(c["nullable"]),
                c["default"], guess_code(c), c["comment"], "",
            ])
            if not c["comment"]:
                missing.append([t["name"], t.get("comment", ""), c["name"], typedisp(c)])
    wb = Workbook()
    sheet(wb, "컬럼정의서",
          ["No", "테이블명(물리)", "테이블명(논리)", "순번", "컬럼명(물리)", "컬럼명(논리)",
           "데이터타입", "길이", "PK", "NULL허용", "기본값", "코드ID", "설명", "비고"],
          rows, {"컬럼명(논리)": 24, "설명": 34, "테이블명(물리)": 26}, first=True)
    sheet(wb, "주석누락", ["테이블명", "테이블설명", "컬럼명", "타입"], missing)
    return save(wb, outdir, project, "컬럼정의서", version), len(missing)


def build_code_def(snap, outdir, project, version):
    usage = defaultdict(list)
    code_ids = {c["code_id"].upper() for c in snap.get("codes", [])}
    for t in snap["tables"]:
        for c in t["columns"]:
            n = c["name"].upper()
            base = re.sub(r"_(CD|CODE|SE|SE_CODE)$", "", n)
            for cand in (n, base):
                if cand in code_ids:
                    usage[cand].append("{}.{}".format(t["name"], c["name"]))
                    break
    rows = []
    for i, c in enumerate(snap.get("codes", []), start=1):
        rows.append([
            i, c["code_id"], c["code_id_nm"], c["code"], c["code_nm"],
            c["sort"], c["use_at"], c.get("desc", ""),
            ", ".join(usage.get(c["code_id"].upper(), [])),
        ])
    wb = Workbook()
    sheet(wb, "코드정의서",
          ["No", "코드그룹ID", "코드그룹명", "코드", "코드명", "정렬순서",
           "사용여부", "설명", "사용테이블·컬럼"],
          rows, {"사용테이블·컬럼": 45, "설명": 30}, first=True)
    return save(wb, outdir, project, "코드정의서", version)


def build_program_def(program, outdir, project, version):
    if not program:
        return None, 0
    rows = []
    for i, p in enumerate(program.get("programs", []), start=1):
        rows.append([
            i, p["domain"], p["program_id"], p["program_name"], p["url"], p["http_method"],
            p["controller"], p["service"], p["dao"], p["sql_id"], p["view"],
            ", ".join(p.get("tables") or []), "", p["source_file"],
        ])
    unres = [[u["program_id"], u["url"], ", ".join(u["missing"])]
             for u in program.get("unresolved", [])]
    wb = Workbook()
    sheet(wb, "프로그램설명서",
          ["No", "업무구분", "프로그램ID", "프로그램명", "URL", "HTTP메서드",
           "Controller", "Service", "DAO·Mapper", "SQL ID", "화면(JSP)",
           "사용테이블", "권한", "소스경로"],
          rows, {"프로그램명": 34, "URL": 40, "SQL ID": 36, "사용테이블": 30,
                 "소스경로": 45, "Controller": 30}, first=True)
    sheet(wb, "미확인", ["프로그램ID", "URL", "미확인 항목"], unres, {"미확인 항목": 30})
    return save(wb, outdir, project, "프로그램설명서", version), len(unres)


# --------------------------------------------------------------------------
# ERD (Mermaid)
# --------------------------------------------------------------------------
def _domain(name):
    parts = re.split(r"[_]", name)
    return parts[0] if len(parts) > 1 else name[:4]


def infer_relations(snap):
    """FK 제약 + (제약 없는 레거시용) PK 컬럼명 일치 기반 추정 관계."""
    explicit, inferred = [], []
    pk_owner = {}
    for t in snap["tables"]:
        for c in t.get("pk") or []:
            pk_owner.setdefault(c.upper(), []).append(t["name"])
    for t in snap["tables"]:
        for f in t.get("fks") or []:
            explicit.append((t["name"], f["ref_table"], ",".join(f["cols"])))
        fkcols = {c for f in (t.get("fks") or []) for c in f["cols"]}
        own_pk = {c.upper() for c in (t.get("pk") or [])}
        for c in t["columns"]:
            n = c["name"].upper()
            if c["name"] in fkcols or n in own_pk:
                continue
            owners = pk_owner.get(n, [])
            if len(owners) == 1 and owners[0] != t["name"]:
                inferred.append((t["name"], owners[0], c["name"]))
    return explicit, inferred


def build_erd(snap, outdir, version):
    tables = snap["tables"]
    explicit, inferred = infer_relations(snap)
    groups = OrderedDict()
    if len(tables) > ERD_SPLIT_THRESHOLD:
        for t in tables:
            groups.setdefault(_domain(t["name"]), []).append(t)
        # 1개짜리 도메인은 기타로 합침
        etc = [t for k, v in list(groups.items()) if len(v) < 2 for t in v]
        for k in [k for k, v in list(groups.items()) if len(v) < 2]:
            del groups[k]
        if etc:
            groups["ETC"] = etc
    else:
        groups["ALL"] = tables

    written = []
    for gname, gtables in groups.items():
        names = {t["name"] for t in gtables}
        lines = ["erDiagram"]
        for t in gtables:
            lines.append("    {} {{".format(t["name"]))
            for c in t["columns"]:
                key = "PK" if c["pk"] else ""
                label = (c["comment"] or "").replace('"', "'")
                typ = re.sub(r"_+$", "", re.sub(r"[^A-Za-z0-9_]+", "_", typedisp(c)))
                lines.append('        {} {} {}{}'.format(
                    typ, c["name"], key, ' "{}"'.format(label) if label else ""))
            lines.append("    }")
        for src, dst, col in explicit:
            if src in names and dst in names:
                lines.append('    {} }}o--|| {} : "{}"'.format(src, dst, col))
        for src, dst, col in inferred:
            if src in names and dst in names:
                lines.append('    {} }}o..o| {} : "{} (추정)"'.format(src, dst, col))
        suffix = "" if gname == "ALL" else "_" + gname
        path = os.path.join(outdir, "erd_v{}{}.mmd".format(version, suffix))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        written.append(path)

    legend = os.path.join(outdir, "erd_v{}_README.md".format(version))
    with open(legend, "w", encoding="utf-8") as f:
        f.write(
            "# ERD 범례 (v{})\n\n"
            "- 실선 `}}o--||` : FK 제약이 실제로 존재하는 관계\n"
            "- 점선 `}}o..o|` : **추정 관계** — FK 제약은 없으나 컬럼명이 다른 테이블의 PK와 "
            "일치하여 추론한 것. 검수 필요.\n\n"
            "생성 파일: {}\n\n"
            "렌더링: VS Code Mermaid 확장, https://mermaid.live , 또는 "
            "`mmdc -i erd_v{}.mmd -o erd_v{}.svg`\n".format(
                version, ", ".join(os.path.basename(p) for p in written), version, version))
    return written, len(explicit), len(inferred)


# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", required=True)
    p.add_argument("--program")
    p.add_argument("--project", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument("--outputs", default="erd,table,column,code,program")
    a = p.parse_args()
    outs = {x.strip() for x in a.outputs.split(",")}
    os.makedirs(a.outdir, exist_ok=True)

    with open(a.snapshot, encoding="utf-8") as f:
        snap = json.load(f)
    program = None
    if a.program and os.path.exists(a.program):
        with open(a.program, encoding="utf-8") as f:
            program = json.load(f)

    made = []
    if "table" in outs:
        made.append(build_table_def(snap, program, a.outdir, a.project, a.version))
    miss = unres = 0
    if "column" in outs:
        path, miss = build_column_def(snap, a.outdir, a.project, a.version)
        made.append(path)
    if "code" in outs:
        made.append(build_code_def(snap, a.outdir, a.project, a.version))
    if "program" in outs and program:
        path, unres = build_program_def(program, a.outdir, a.project, a.version)
        if path:
            made.append(path)
    erds = exp = inf = 0
    if "erd" in outs:
        erds, exp, inf = build_erd(snap, a.outdir, a.version)
        made += erds

    for m in made:
        print("  생성:", os.path.basename(m))
    print("보완필요: 컬럼 주석 누락 {}건, 프로그램 미확인 {}건, 추정 관계 {}건(명시 FK {}건)".format(
        miss, unres, inf, exp))


if __name__ == "__main__":
    main()
