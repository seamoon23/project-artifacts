#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""이전/신규 스냅샷 비교 → 변경 목록 + semver 등급 판정 + CHANGELOG 섹션 생성.

등급 규칙 (가장 높은 등급 채택)
  MAJOR : 테이블 삭제, 컬럼 삭제, PK 변경, 타입 변경, 길이 축소, NOT NULL 신설
  MINOR : 테이블 추가, 컬럼 추가, 인덱스/FK 추가, 코드그룹 추가, 길이 확대
  PATCH : 주석/설명 변경, 기본값 변경, 코드값 추가·명칭 변경, 인덱스/FK 삭제
  NONE  : 변경 없음

사용:
  python3 diff_snapshot.py --old _snapshot.json --new _snapshot.new.json --out _changes.json
"""
import argparse
import datetime
import json
import sys
from collections import OrderedDict

LEVELS = ["NONE", "PATCH", "MINOR", "MAJOR"]


def _rank(level):
    return LEVELS.index(level)


def _load(path):
    if not path:
        return {"tables": [], "codes": []}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"tables": [], "codes": []}


def _num(s):
    """길이 문자열('100', '10,2')의 대표 크기. 비교 불가면 None."""
    try:
        return float(str(s).split(",")[0])
    except (ValueError, TypeError):
        return None


def diff(old, new):
    ch = {"added": [], "changed": [], "removed": [], "level": "NONE"}

    def bump(level):
        if _rank(level) > _rank(ch["level"]):
            ch["level"] = level

    ot = {t["name"]: t for t in old.get("tables", [])}
    nt = {t["name"]: t for t in new.get("tables", [])}

    for name in sorted(set(nt) - set(ot)):
        ch["added"].append({"kind": "table", "target": name,
                            "detail": nt[name].get("comment", "")})
        bump("MINOR")
    for name in sorted(set(ot) - set(nt)):
        ch["removed"].append({"kind": "table", "target": name,
                              "detail": ot[name].get("comment", "")})
        bump("MAJOR")

    for name in sorted(set(ot) & set(nt)):
        o, n = ot[name], nt[name]
        if (o.get("comment") or "") != (n.get("comment") or ""):
            ch["changed"].append({"kind": "table_comment", "target": name,
                                  "detail": "{} → {}".format(o.get("comment") or "(없음)",
                                                             n.get("comment") or "(없음)")})
            bump("PATCH")
        if o.get("pk") != n.get("pk"):
            ch["changed"].append({"kind": "pk", "target": name,
                                  "detail": "{} → {}".format(",".join(o.get("pk") or []) or "(없음)",
                                                             ",".join(n.get("pk") or []) or "(없음)")})
            bump("MAJOR")

        oc = {c["name"]: c for c in o.get("columns", [])}
        nc = {c["name"]: c for c in n.get("columns", [])}
        for cn in sorted(set(nc) - set(oc)):
            c = nc[cn]
            ch["added"].append({"kind": "column", "target": "{}.{}".format(name, cn),
                                "detail": _coldesc(c)})
            bump("MINOR")
        for cn in sorted(set(oc) - set(nc)):
            ch["removed"].append({"kind": "column", "target": "{}.{}".format(name, cn),
                                  "detail": _coldesc(oc[cn])})
            bump("MAJOR")
        for cn in sorted(set(oc) & set(nc)):
            a, b = oc[cn], nc[cn]
            tgt = "{}.{}".format(name, cn)
            if a["type"] != b["type"]:
                ch["changed"].append({"kind": "column_type", "target": tgt,
                                      "detail": "{} → {}".format(a["type"], b["type"])})
                bump("MAJOR")
            elif a["length"] != b["length"]:
                na, nb = _num(a["length"]), _num(b["length"])
                shrink = na is not None and nb is not None and nb < na
                ch["changed"].append({"kind": "column_length", "target": tgt,
                                      "detail": "{}({}) → {}({})".format(a["type"], a["length"],
                                                                         b["type"], b["length"])})
                bump("MAJOR" if shrink else "MINOR")
            if a["nullable"] != b["nullable"]:
                ch["changed"].append({"kind": "column_nullable", "target": tgt,
                                      "detail": "{} → {}".format("NULL" if a["nullable"] else "NOT NULL",
                                                                 "NULL" if b["nullable"] else "NOT NULL")})
                bump("MAJOR" if not b["nullable"] else "PATCH")
            if a["default"] != b["default"]:
                ch["changed"].append({"kind": "column_default", "target": tgt,
                                      "detail": "{} → {}".format(a["default"] or "(없음)",
                                                                 b["default"] or "(없음)")})
                bump("PATCH")
            if (a["comment"] or "") != (b["comment"] or ""):
                ch["changed"].append({"kind": "column_comment", "target": tgt,
                                      "detail": "{} → {}".format(a["comment"] or "(없음)",
                                                                 b["comment"] or "(없음)")})
                bump("PATCH")

        for key, label, add_lv in (("indexes", "index", "MINOR"), ("fks", "fk", "MINOR")):
            oi = {i["name"]: i for i in o.get(key, [])}
            ni = {i["name"]: i for i in n.get(key, [])}
            for k in sorted(set(ni) - set(oi)):
                ch["added"].append({"kind": label, "target": "{}.{}".format(name, k),
                                    "detail": ",".join(ni[k].get("cols", []))})
                bump(add_lv)
            for k in sorted(set(oi) - set(ni)):
                ch["removed"].append({"kind": label, "target": "{}.{}".format(name, k),
                                      "detail": ",".join(oi[k].get("cols", []))})
                bump("PATCH")

    # 공통코드
    okey = {(c["code_id"], c["code"]): c for c in old.get("codes", [])}
    nkey = {(c["code_id"], c["code"]): c for c in new.get("codes", [])}
    ogrp, ngrp = {k[0] for k in okey}, {k[0] for k in nkey}
    for g in sorted(ngrp - ogrp):
        ch["added"].append({"kind": "code_group", "target": g, "detail": ""})
        bump("MINOR")
    for g in sorted(ogrp - ngrp):
        ch["removed"].append({"kind": "code_group", "target": g, "detail": ""})
        bump("MAJOR")
    for k in sorted(set(nkey) - set(okey)):
        if k[0] in ngrp - ogrp:
            continue
        ch["added"].append({"kind": "code", "target": "{}.{}".format(*k),
                            "detail": nkey[k]["code_nm"]})
        bump("PATCH")
    for k in sorted(set(okey) - set(nkey)):
        if k[0] in ogrp - ngrp:
            continue
        ch["removed"].append({"kind": "code", "target": "{}.{}".format(*k),
                              "detail": okey[k]["code_nm"]})
        bump("PATCH")
    for k in sorted(set(okey) & set(nkey)):
        if okey[k]["code_nm"] != nkey[k]["code_nm"] or okey[k]["use_at"] != nkey[k]["use_at"]:
            ch["changed"].append({"kind": "code", "target": "{}.{}".format(*k),
                                  "detail": "{}({}) → {}({})".format(
                                      okey[k]["code_nm"], okey[k]["use_at"],
                                      nkey[k]["code_nm"], nkey[k]["use_at"])})
            bump("PATCH")
    return ch


def _coldesc(c):
    t = c["type"] + ("({})".format(c["length"]) if c["length"] else "")
    return "{} {}{}".format(t, "NULL" if c["nullable"] else "NOT NULL",
                            " · " + c["comment"] if c["comment"] else "")


def next_version(current, level):
    try:
        ma, mi, pa = (int(x) for x in str(current).split("."))
    except ValueError:
        ma, mi, pa = 0, 0, 0
    if level == "MAJOR":
        return "{}.0.0".format(ma + 1)
    if level == "MINOR":
        return "{}.{}.0".format(ma, mi + 1)
    if level == "PATCH":
        return "{}.{}.{}".format(ma, mi, pa + 1)
    return "{}.{}.{}".format(ma, mi, pa)


KIND_KO = {
    "table": "테이블", "column": "컬럼", "table_comment": "테이블 주석",
    "column_type": "타입", "column_length": "길이", "column_nullable": "NULL 여부",
    "column_default": "기본값", "column_comment": "컬럼 주석", "pk": "PK",
    "index": "인덱스", "fk": "외래키", "code_group": "코드그룹", "code": "코드",
}


def changelog_section(version, level, ch, date=None):
    d = date or datetime.date.today().isoformat()
    out = ["## v{} ({}) — {}".format(version, d, level)]
    for title, key in (("추가", "added"), ("변경", "changed"), ("삭제", "removed")):
        out.append("")
        out.append("### {}".format(title))
        items = ch.get(key) or []
        if not items:
            out.append("- (없음)")
            continue
        for it in items:
            line = "- {} `{}`".format(KIND_KO.get(it["kind"], it["kind"]), it["target"])
            if it.get("detail"):
                line += " — {}".format(it["detail"])
            out.append(line)
    out.append("")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--old"), p.add_argument("--new", required=True)
    p.add_argument("--current-version", default="0.0.0")
    p.add_argument("--out")
    p.add_argument("--changelog-out")
    a = p.parse_args()

    ch = diff(_load(a.old), _load(a.new))
    ver = next_version(a.current_version, ch["level"])
    result = OrderedDict(level=ch["level"], from_version=a.current_version, to_version=ver,
                         added=ch["added"], changed=ch["changed"], removed=ch["removed"])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    if a.changelog_out and ch["level"] != "NONE":
        with open(a.changelog_out, "w", encoding="utf-8") as f:
            f.write(changelog_section(ver, ch["level"], ch))
    print("{} | v{} → v{} | 추가 {} 변경 {} 삭제 {}".format(
        ch["level"], a.current_version, ver,
        len(ch["added"]), len(ch["changed"]), len(ch["removed"])))
    return 0 if ch["level"] != "NONE" else 10  # 10 = 변경 없음


if __name__ == "__main__":
    sys.exit(main())
