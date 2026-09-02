#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spring / eGovFrame 소스 → 프로그램 목록(JSON).

추적 경로: Controller(@RequestMapping) → Service → ServiceImpl → DAO/Mapper
           → MyBatis XML(namespace.queryId) → SQL 본문의 사용 테이블
           + 반환 뷰명 → JSP 경로

추적 실패 항목은 값을 비우고 unresolved 에 사유를 남긴다(추측 금지).

사용:
  python3 scan_program.py --root /path/to/project --out _program.json
"""
import argparse
import json
import os
import re
from collections import OrderedDict

# --------------------------------------------------------------------------
# 정규식
# --------------------------------------------------------------------------
RE_CLASS_ANNO = re.compile(r"@(RestController|Controller)\b")
RE_MAPPING = re.compile(
    r"@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\s*"
    r"(?:\(([^)]*)\))?", re.S)
RE_VALUE = re.compile(r"""(?:value|path)\s*=\s*(\{[^}]*\}|"[^"]*")|^\s*("[^"]*")""", re.S)
RE_METHOD_ATTR = re.compile(r"method\s*=\s*(\{[^}]*\}|[A-Za-z.]+)")
RE_STR = re.compile(r'"([^"]*)"')
RE_CLASS_DECL = re.compile(r"\b(?:public\s+|abstract\s+|final\s+)*class\s+(\w+)"
                           r"(?:\s+extends\s+[\w<>,.\s]+?)?(?:\s+implements\s+([\w<>,.\s]+?))?\s*\{")
RE_FIELD = re.compile(r"(?:private|protected|public)\s+([A-Z][\w.<>]*)\s+(\w+)\s*[;=]")
RE_METHOD_SIG = re.compile(
    r"(?:public|protected)\s+[\w<>\[\],.\s]+?\s+(\w+)\s*\(([^)]*)\)\s*(?:throws [\w.,\s]+)?\{")
RE_JAVADOC = re.compile(r"/\*\*(.*?)\*/", re.S)
RE_MYBATIS_NS = re.compile(r'<mapper[^>]*namespace\s*=\s*"([^"]+)"', re.I)
RE_MYBATIS_STMT = re.compile(r'<(select|insert|update|delete)\b[^>]*\bid\s*=\s*"([^"]+)"(.*?)</\1>',
                             re.I | re.S)
RE_SQL_TABLE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)", re.I)
SQL_NOISE = {"DUAL", "SELECT", "WHERE", "SET", "VALUES", "AS", "ON", "AND", "OR"}


def read(path):
    for enc in ("utf-8", "euc-kr", "cp949", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue
    return ""


def walk(root, exts, skip=("target", "build", "node_modules", ".git", ".svn", "bin")):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip]
        for fn in files:
            if fn.lower().endswith(exts):
                yield os.path.join(base, fn)


def body_of(src, open_brace_idx):
    """중괄호 짝을 맞춰 메서드 본문 반환 (문자열 리터럴 무시)."""
    depth, i, n = 0, open_brace_idx, len(src)
    in_str = in_chr = False
    while i < n:
        c = src[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif in_chr:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                in_chr = False
        elif c == '"':
            in_str = True
        elif c == "'":
            in_chr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace_idx + 1:i]
        i += 1
    return src[open_brace_idx + 1:]


def anno_paths(args_text):
    """@RequestMapping 인자에서 URL 목록 추출."""
    if not args_text:
        return []
    m = RE_VALUE.search(args_text)
    chunk = None
    if m:
        chunk = m.group(1) or m.group(2)
    if chunk is None and "=" not in args_text:
        chunk = args_text
    return RE_STR.findall(chunk or "")


def http_method(args_text, anno):
    fixed = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
             "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}
    if anno in fixed:
        return fixed[anno]
    m = RE_METHOD_ATTR.search(args_text or "")
    if not m:
        return "ALL"
    return ",".join(sorted(set(re.findall(r"RequestMethod\.(\w+)", m.group(1))))) or "ALL"


def join_url(base, path):
    if not base:
        return path if path.startswith("/") else "/" + path
    b = base.rstrip("/")
    p = path if path.startswith("/") else "/" + path
    return (b if b.startswith("/") else "/" + b) + p


# --------------------------------------------------------------------------
# 인덱스 구축
# --------------------------------------------------------------------------
def index_mybatis(root):
    """namespace.id → {sql, tables, file}"""
    idx = {}
    for path in walk(root, (".xml",)):
        src = read(path)
        m = RE_MYBATIS_NS.search(src)
        if not m:
            continue
        ns = m.group(1)
        for _, sid, body in RE_MYBATIS_STMT.findall(src):
            tables = set()
            for t in RE_SQL_TABLE.findall(body):
                t = t.split(".")[-1].upper()
                if t not in SQL_NOISE and not t.startswith("#"):
                    tables.add(t)
            idx["{}.{}".format(ns, sid)] = {"sql_id": sid, "tables": sorted(tables), "file": path}
        idx.setdefault("__ns__", {})[ns] = path
    return idx


def index_java(root):
    """클래스명 → {file, src, implements, fields}"""
    idx = {}
    for path in walk(root, (".java",)):
        src = read(path)
        for m in RE_CLASS_DECL.finditer(src):
            cls, impl = m.group(1), (m.group(2) or "")
            idx[cls] = {
                "file": path, "src": src,
                "implements": [x.strip().split("<")[0] for x in impl.split(",") if x.strip()],
                "fields": {name: typ.split("<")[0] for typ, name in RE_FIELD.findall(src)},
            }
    return idx


def impl_of(iface, java_idx):
    """인터페이스를 구현한 클래스명 반환 (없으면 None)."""
    if iface in java_idx and not java_idx[iface]["implements"]:
        # 인터페이스가 아니라 클래스 자체를 주입한 경우
        pass
    for cls, info in java_idx.items():
        if iface in info["implements"]:
            return cls
    return iface if iface in java_idx else None


def method_body(cls, method, java_idx):
    info = java_idx.get(cls)
    if not info:
        return None
    for m in RE_METHOD_SIG.finditer(info["src"]):
        if m.group(1) == method:
            return body_of(info["src"], info["src"].index("{", m.end() - 1))
    return None


# --------------------------------------------------------------------------
# 추적
# --------------------------------------------------------------------------
def trace(cls, method, java_idx, mybatis, depth=0, seen=None):
    """메서드 본문을 따라가며 (services, daos, sql_ids, tables) 수집."""
    seen = seen or set()
    out = {"services": [], "daos": [], "sql_ids": [], "tables": set()}
    if depth > 3 or (cls, method) in seen:
        return out
    seen.add((cls, method))
    body = method_body(cls, method, java_idx)
    if body is None:
        return out
    fields = java_idx.get(cls, {}).get("fields", {})
    for var, call in re.findall(r"\b(\w+)\s*\.\s*(\w+)\s*\(", body):
        typ = fields.get(var)
        if not typ:
            continue
        target = impl_of(typ, java_idx)
        if not target:
            continue
        if re.search(r"(Service|Manager)$", typ):
            out["services"].append("{}#{}".format(typ, call))
        elif re.search(r"(DAO|Dao|Mapper|Repository)$", typ):
            out["daos"].append("{}#{}".format(typ, call))
        sub = trace(target, call, java_idx, mybatis, depth + 1, seen)
        out["services"] += sub["services"]
        out["daos"] += sub["daos"]
        out["sql_ids"] += sub["sql_ids"]
        out["tables"] |= sub["tables"]
    for sid in RE_STR.findall(body):
        if "." in sid and sid in mybatis:
            out["sql_ids"].append(sid)
            out["tables"] |= set(mybatis[sid]["tables"])
        elif "." in sid:
            # namespace 없이 짧은 id 로 호출한 경우 접미 매칭
            for key, v in mybatis.items():
                if key.endswith("." + sid.split(".")[-1]) and key != "__ns__":
                    out["sql_ids"].append(key)
                    out["tables"] |= set(v["tables"])
                    break
    return out


def find_views(body, jsp_index):
    views = []
    for s in RE_STR.findall(body or ""):
        if s.startswith("redirect:") or s.startswith("forward:"):
            views.append(s)
        elif re.match(r"^[\w./-]+/[\w.-]+$", s) and not s.startswith("/"):
            hit = jsp_index.get(s.split("/")[-1].lower())
            views.append(hit or s)
    return views


def first_doc_line(src, pos):
    """메서드 선언 직전의 Javadoc/주석 첫 문장.

    직전 멤버의 끝(`}` 또는 `;`) 이후만 본다 — 위쪽 다른 메서드의 주석을
    끌어오지 않도록 경계를 둔다.
    """
    start = max(src.rfind("}", 0, pos), src.rfind(";", 0, pos)) + 1
    chunk = src[start:pos]
    docs = RE_JAVADOC.findall(chunk)
    if docs:
        for line in docs[-1].splitlines():
            line = line.strip().lstrip("*").strip()
            if line and not line.startswith("@"):
                return line
    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if line.startswith("//") and len(line) > 2:
            return line[2:].strip()
        if line and not line.startswith("@"):
            break
    return ""


def scan(root):
    java_idx = index_java(root)
    mybatis = index_mybatis(root)
    jsp_index = {}
    for p in walk(root, (".jsp", ".html")):
        jsp_index.setdefault(os.path.basename(p).lower().rsplit(".", 1)[0], os.path.relpath(p, root))

    rows, unresolved = [], []
    for cls, info in sorted(java_idx.items()):
        src = info["src"]
        if not RE_CLASS_ANNO.search(src):
            continue
        decl = RE_CLASS_DECL.search(src)
        body_start = decl.end() if decl else 0
        head = src[:decl.start()] if decl else src[:2000]
        cm = RE_MAPPING.search(head)
        base = (anno_paths(cm.group(2)) or [""])[0] if cm else ""

        for m in RE_MAPPING.finditer(src, body_start):
            anno, args = m.group(1), m.group(2)
            sig = RE_METHOD_SIG.search(src, m.end())
            if not sig or sig.start() - m.end() > 400:
                continue
            paths = anno_paths(args)
            if not paths:
                continue
            mname = sig.group(1)
            body = body_of(src, src.index("{", sig.end() - 1))
            tr = trace(cls, mname, java_idx, mybatis)
            views = find_views(body, jsp_index)
            for p in paths:
                url = join_url(base, p)
                row = OrderedDict(
                    domain=url.strip("/").split("/")[0],
                    program_id="{}#{}".format(cls, mname),
                    program_name=first_doc_line(src, m.start()),
                    url=url,
                    http_method=http_method(args, anno),
                    controller="{}#{}".format(cls, mname),
                    service=", ".join(dict.fromkeys(tr["services"])),
                    dao=", ".join(dict.fromkeys(tr["daos"])),
                    sql_id=", ".join(dict.fromkeys(tr["sql_ids"])),
                    view=", ".join(dict.fromkeys(views)),
                    tables=sorted(tr["tables"]),
                    source_file=os.path.relpath(info["file"], root),
                )
                rows.append(row)
                miss = [k for k in ("program_name", "service", "sql_id", "view") if not row[k]]
                if not row["tables"]:
                    miss.append("tables")
                if miss:
                    unresolved.append({"program_id": row["program_id"], "url": url,
                                       "missing": miss})
    rows.sort(key=lambda r: (r["domain"], r["url"], r["http_method"]))
    return OrderedDict(programs=rows, unresolved=unresolved,
                       stats={"programs": len(rows), "controllers": len(
                           {r["controller"].split("#")[0] for r in rows}),
                           "mybatis_statements": len([k for k in mybatis if k != "__ns__"])})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    res = scan(a.root)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("프로그램 {}건 (컨트롤러 {}개, SQL {}건), 미확인 {}건 → {}".format(
        res["stats"]["programs"], res["stats"]["controllers"],
        res["stats"]["mybatis_statements"], len(res["unresolved"]), a.out))


if __name__ == "__main__":
    main()
