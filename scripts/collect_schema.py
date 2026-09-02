#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB 스키마/공통코드 → 정규화 스냅샷(JSON) 수집.

지원: mariadb/mysql(pymysql), oracle(oracledb), cubrid(JayDeBeApi+JDBC), csv(수동 추출분)
비밀번호는 인자로 받지 않고 환경변수 ARTIFACTS_DB_PASSWORD 를 사용한다(로그·스냅샷 미기록).

사용:
  ARTIFACTS_DB_PASSWORD=xxx python3 collect_schema.py \
      --vendor mariadb --host 10.0.0.5 --port 3306 --user readonly \
      --schema kapex --exclude "TMP_%,BAK_%" --out _snapshot.json
"""
import argparse
import fnmatch
import json
import os
import sys
from collections import OrderedDict

SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# 공통 유틸
# --------------------------------------------------------------------------
def _excluded(name, patterns):
    """SQL LIKE 패턴(%, _)을 fnmatch 패턴으로 바꿔 제외 여부 판정."""
    up = name.upper()
    for p in patterns:
        g = p.upper().replace("%", "*").replace("_", "?")
        # '_' 를 단일문자 와일드카드로 바꾸면 TMP_ 같은 리터럴도 매칭되므로 원문도 함께 검사
        if fnmatch.fnmatch(up, g) or fnmatch.fnmatch(up, p.upper().replace("%", "*")):
            return True
    return False


def _empty_table(name, comment=""):
    return OrderedDict(
        name=name, comment=comment or "", columns=[], pk=[], indexes=[], fks=[]
    )


def _finalize(tables_map, codes):
    tables = []
    for t in tables_map.values():
        t["columns"].sort(key=lambda c: c.get("ordinal", 0))
        t["indexes"].sort(key=lambda i: i["name"])
        t["fks"].sort(key=lambda f: f["name"])
        tables.append(t)
    tables.sort(key=lambda t: t["name"])
    return OrderedDict(
        schema_version=SCHEMA_VERSION,
        tables=tables,
        codes=sorted(codes, key=lambda c: (c["code_id"], str(c["sort"]), c["code"])),
    )


def _col(ordinal, name, dtype, length, nullable, default, pk, comment):
    return OrderedDict(
        ordinal=int(ordinal or 0),
        name=name,
        type=(dtype or "").upper(),
        length=str(length or ""),
        nullable=bool(nullable),
        default="" if default is None else str(default).strip(),
        pk=bool(pk),
        comment=(comment or "").strip(),
    )


# --------------------------------------------------------------------------
# MariaDB / MySQL
# --------------------------------------------------------------------------
def collect_mysql(a, password):
    import pymysql  # noqa

    conn = pymysql.connect(
        host=a.host, port=a.port, user=a.user, password=password,
        database=a.schema, charset="utf8mb4", cursorclass=pymysql.cursors.Cursor,
        connect_timeout=10,
    )
    ex = a.exclude
    tables = OrderedDict()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, IFNULL(TABLE_COMMENT,'') FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
                (a.schema,),
            )
            for name, comment in cur.fetchall():
                if _excluded(name, ex):
                    continue
                tables[name] = _empty_table(name, comment)

            cur.execute(
                "SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, "
                "       COLUMN_DEFAULT, COLUMN_KEY, IFNULL(COLUMN_COMMENT,'') "
                "  FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=%s "
                " ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (a.schema,),
            )
            for tn, pos, cn, ctype, isnull, default, ckey, comment in cur.fetchall():
                if tn not in tables:
                    continue
                base, length = ctype, ""
                if "(" in ctype:
                    base, length = ctype.split("(", 1)[0], ctype.split("(", 1)[1].split(")")[0]
                pk = ckey == "PRI"
                tables[tn]["columns"].append(
                    _col(pos, cn, base, length, isnull == "YES", default, pk, comment)
                )
                if pk:
                    tables[tn]["pk"].append(cn)

            cur.execute(
                "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME "
                "  FROM INFORMATION_SCHEMA.STATISTICS WHERE TABLE_SCHEMA=%s "
                " ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
                (a.schema,),
            )
            idx = OrderedDict()
            for tn, iname, nonuniq, seq, cn in cur.fetchall():
                if tn not in tables:
                    continue
                key = (tn, iname)
                idx.setdefault(key, {"name": iname, "cols": [], "unique": not nonuniq})
                idx[key]["cols"].append(cn)
            for (tn, _), v in idx.items():
                tables[tn]["indexes"].append(OrderedDict(name=v["name"], cols=v["cols"], unique=v["unique"]))

            cur.execute(
                "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
                "  FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
                " WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL "
                " ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION",
                (a.schema,),
            )
            fks = OrderedDict()
            for tn, fname, cn, rt, rc in cur.fetchall():
                if tn not in tables:
                    continue
                key = (tn, fname)
                fks.setdefault(key, {"name": fname, "cols": [], "ref_table": rt, "ref_cols": []})
                fks[key]["cols"].append(cn)
                fks[key]["ref_cols"].append(rc)
            for (tn, _), v in fks.items():
                tables[tn]["fks"].append(OrderedDict(**v))

            codes = _collect_codes(cur, a, "%s")
    finally:
        conn.close()
    return _finalize(tables, codes)


# --------------------------------------------------------------------------
# Oracle
# --------------------------------------------------------------------------
def collect_oracle(a, password):
    import oracledb  # noqa

    dsn = a.dsn or "{}:{}/{}".format(a.host, a.port, a.service or a.schema)
    conn = oracledb.connect(user=a.user, password=password, dsn=dsn)
    owner = (a.schema or a.user).upper()
    ex = a.exclude
    tables = OrderedDict()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT t.TABLE_NAME, NVL(c.COMMENTS,' ') FROM ALL_TABLES t "
            " LEFT JOIN ALL_TAB_COMMENTS c ON c.OWNER=t.OWNER AND c.TABLE_NAME=t.TABLE_NAME "
            " WHERE t.OWNER=:o ORDER BY t.TABLE_NAME", o=owner)
        for name, comment in cur.fetchall():
            if _excluded(name, ex):
                continue
            tables[name] = _empty_table(name, (comment or "").strip())

        # PK 먼저 확보
        cur.execute(
            "SELECT ac.TABLE_NAME, cc.COLUMN_NAME FROM ALL_CONSTRAINTS ac "
            "  JOIN ALL_CONS_COLUMNS cc ON cc.OWNER=ac.OWNER AND cc.CONSTRAINT_NAME=ac.CONSTRAINT_NAME "
            " WHERE ac.OWNER=:o AND ac.CONSTRAINT_TYPE='P' ORDER BY ac.TABLE_NAME, cc.POSITION", o=owner)
        pkmap = {}
        for tn, cn in cur.fetchall():
            pkmap.setdefault(tn, []).append(cn)
        for tn, cols in pkmap.items():
            if tn in tables:
                tables[tn]["pk"] = cols

        cur.execute(
            "SELECT c.TABLE_NAME, c.COLUMN_ID, c.COLUMN_NAME, c.DATA_TYPE, c.DATA_LENGTH, "
            "       c.DATA_PRECISION, c.DATA_SCALE, c.NULLABLE, NVL(m.COMMENTS,' ') "
            "  FROM ALL_TAB_COLUMNS c "
            "  LEFT JOIN ALL_COL_COMMENTS m ON m.OWNER=c.OWNER AND m.TABLE_NAME=c.TABLE_NAME "
            "        AND m.COLUMN_NAME=c.COLUMN_NAME "
            " WHERE c.OWNER=:o ORDER BY c.TABLE_NAME, c.COLUMN_ID", o=owner)
        for tn, pos, cn, dtype, dlen, prec, scale, nullable, comment in cur.fetchall():
            if tn not in tables:
                continue
            if dtype in ("NUMBER",) and prec:
                length = "{},{}".format(prec, scale or 0)
            elif dtype in ("DATE", "CLOB", "BLOB", "TIMESTAMP"):
                length = ""
            else:
                length = str(dlen or "")
            tables[tn]["columns"].append(
                _col(pos, cn, dtype, length, nullable == "Y", "", cn in pkmap.get(tn, []), (comment or "").strip())
            )

        cur.execute(
            "SELECT i.TABLE_NAME, i.INDEX_NAME, i.UNIQUENESS, ic.COLUMN_NAME "
            "  FROM ALL_INDEXES i JOIN ALL_IND_COLUMNS ic "
            "    ON ic.INDEX_OWNER=i.OWNER AND ic.INDEX_NAME=i.INDEX_NAME "
            " WHERE i.TABLE_OWNER=:o ORDER BY i.TABLE_NAME, i.INDEX_NAME, ic.COLUMN_POSITION", o=owner)
        idx = OrderedDict()
        for tn, iname, uniq, cn in cur.fetchall():
            if tn not in tables:
                continue
            key = (tn, iname)
            idx.setdefault(key, {"name": iname, "cols": [], "unique": uniq == "UNIQUE"})
            idx[key]["cols"].append(cn)
        for (tn, _), v in idx.items():
            tables[tn]["indexes"].append(OrderedDict(**v))

        cur.execute(
            "SELECT ac.TABLE_NAME, ac.CONSTRAINT_NAME, cc.COLUMN_NAME, rc.TABLE_NAME, rcc.COLUMN_NAME "
            "  FROM ALL_CONSTRAINTS ac "
            "  JOIN ALL_CONS_COLUMNS cc ON cc.OWNER=ac.OWNER AND cc.CONSTRAINT_NAME=ac.CONSTRAINT_NAME "
            "  JOIN ALL_CONSTRAINTS rc ON rc.OWNER=ac.R_OWNER AND rc.CONSTRAINT_NAME=ac.R_CONSTRAINT_NAME "
            "  JOIN ALL_CONS_COLUMNS rcc ON rcc.OWNER=rc.OWNER AND rcc.CONSTRAINT_NAME=rc.CONSTRAINT_NAME "
            "       AND rcc.POSITION=cc.POSITION "
            " WHERE ac.OWNER=:o AND ac.CONSTRAINT_TYPE='R' "
            " ORDER BY ac.TABLE_NAME, ac.CONSTRAINT_NAME, cc.POSITION", o=owner)
        fks = OrderedDict()
        for tn, fname, cn, rt, rc in cur.fetchall():
            if tn not in tables:
                continue
            key = (tn, fname)
            fks.setdefault(key, {"name": fname, "cols": [], "ref_table": rt, "ref_cols": []})
            fks[key]["cols"].append(cn)
            fks[key]["ref_cols"].append(rc)
        for (tn, _), v in fks.items():
            tables[tn]["fks"].append(OrderedDict(**v))

        codes = _collect_codes(cur, a, ":1", oracle=True)
    finally:
        conn.close()
    return _finalize(tables, codes)


# --------------------------------------------------------------------------
# Cubrid (JDBC 경유)
# --------------------------------------------------------------------------
def collect_cubrid(a, password):
    import jaydebeapi  # noqa

    if not a.jdbc_jar:
        sys.exit("cubrid 는 --jdbc-jar 로 JDBC 드라이버 경로가 필요합니다.")
    url = a.dsn or "jdbc:cubrid:{}:{}:{}:::".format(a.host, a.port, a.schema)
    conn = jaydebeapi.connect("cubrid.jdbc.driver.CUBRIDDriver", url, [a.user, password], a.jdbc_jar)
    ex = a.exclude
    tables = OrderedDict()
    try:
        cur = conn.cursor()
        cur.execute("SELECT class_name FROM db_class WHERE is_system_class='NO' AND class_type='CLASS' ORDER BY class_name")
        for (name,) in cur.fetchall():
            if _excluded(name, ex):
                continue
            tables[name] = _empty_table(name)

        cur.execute(
            "SELECT class_name, def_order, attr_name, data_type, prec, scale, is_nullable, default_value "
            "  FROM db_attribute ORDER BY class_name, def_order")
        for tn, pos, cn, dtype, prec, scale, nullable, default in cur.fetchall():
            if tn not in tables:
                continue
            length = "{},{}".format(prec, scale) if scale else (str(prec) if prec else "")
            tables[tn]["columns"].append(
                _col(pos, cn, dtype, length, str(nullable).upper() == "YES", default, False, "")
            )

        cur.execute(
            "SELECT i.class_name, i.index_name, i.is_unique, i.is_primary_key, k.key_attr_name, k.key_order "
            "  FROM db_index i JOIN db_index_key k "
            "    ON k.index_name=i.index_name AND k.class_name=i.class_name "
            " ORDER BY i.class_name, i.index_name, k.key_order")
        idx = OrderedDict()
        for tn, iname, uniq, ispk, cn, _ in cur.fetchall():
            if tn not in tables:
                continue
            key = (tn, iname)
            idx.setdefault(key, {"name": iname, "cols": [], "unique": str(uniq).upper() == "YES",
                                 "pk": str(ispk).upper() == "YES"})
            idx[key]["cols"].append(cn)
        for (tn, _), v in idx.items():
            if v.pop("pk", False):
                tables[tn]["pk"] = list(v["cols"])
                for c in tables[tn]["columns"]:
                    if c["name"] in v["cols"]:
                        c["pk"] = True
            tables[tn]["indexes"].append(OrderedDict(**v))

        codes = _collect_codes(cur, a, "?")
    finally:
        conn.close()
    return _finalize(tables, codes)


# --------------------------------------------------------------------------
# 공통코드
# --------------------------------------------------------------------------
def _collect_codes(cur, a, ph, oracle=False):
    """공통코드 마스터/상세 테이블에서 코드 목록 수집. 실패해도 빈 리스트로 진행."""
    if not a.code_master or not a.code_detail:
        return []
    m, d = a.code_master, a.code_detail
    cm = {k: v for k, v in (p.split("=", 1) for p in (a.code_columns or "").split(",") if "=" in p)}
    c = {
        "id": cm.get("id", "CODE_ID"),
        "id_nm": cm.get("id_nm", "CODE_ID_NM"),
        "code": cm.get("code", "CODE"),
        "nm": cm.get("nm", "CODE_NM"),
        "sort": cm.get("sort", "CODE_ORDR"),
        "use": cm.get("use", "USE_AT"),
        "desc": cm.get("desc", "CODE_DC"),
    }
    sql = (
        "SELECT d.{id}, m.{id_nm}, d.{code}, d.{nm}, d.{sort}, d.{use}, d.{desc} "
        "  FROM {det} d LEFT JOIN {mst} m ON m.{id} = d.{id} "
        " ORDER BY d.{id}, d.{sort}, d.{code}"
    ).format(det=d, mst=m, **c)
    try:
        cur.execute(sql)
        rows = cur.fetchall()
    except Exception as e:  # 컬럼명이 다른 커스텀 코드테이블
        sys.stderr.write("[warn] 공통코드 수집 실패 ({}). --code-columns 로 컬럼 매핑을 지정하세요.\n".format(e))
        return []
    out = []
    for r in rows:
        out.append(OrderedDict(
            code_id=str(r[0] or ""), code_id_nm=str(r[1] or ""), code=str(r[2] or ""),
            code_nm=str(r[3] or ""), sort=str(r[4] or ""), use_at=str(r[5] or ""),
            desc=str(r[6] or "").strip(),
        ))
    return out


# --------------------------------------------------------------------------
# CSV 폴백 (수동 추출분)
# --------------------------------------------------------------------------
def collect_csv(a, _password=None):
    """_raw/ 에 tables.csv, columns.csv, (선택) codes.csv 를 두고 읽는다.

    tables.csv  : TABLE_NAME,TABLE_COMMENT
    columns.csv : TABLE_NAME,ORDINAL,COLUMN_NAME,DATA_TYPE,LENGTH,NULLABLE,DEFAULT,PK,COMMENT
    codes.csv   : CODE_ID,CODE_ID_NM,CODE,CODE_NM,SORT,USE_AT,DESC
    """
    import csv

    d = a.from_csv
    tables = OrderedDict()
    with open(os.path.join(d, "tables.csv"), encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            n = row["TABLE_NAME"].strip()
            if _excluded(n, a.exclude):
                continue
            tables[n] = _empty_table(n, row.get("TABLE_COMMENT", ""))
    with open(os.path.join(d, "columns.csv"), encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            tn = row["TABLE_NAME"].strip()
            if tn not in tables:
                continue
            pk = str(row.get("PK", "")).strip().upper() in ("Y", "YES", "1", "TRUE", "PRI")
            tables[tn]["columns"].append(_col(
                row.get("ORDINAL", 0), row["COLUMN_NAME"].strip(), row.get("DATA_TYPE", ""),
                row.get("LENGTH", ""), str(row.get("NULLABLE", "")).upper() in ("Y", "YES", "1", "TRUE"),
                row.get("DEFAULT", ""), pk, row.get("COMMENT", "")))
            if pk:
                tables[tn]["pk"].append(row["COLUMN_NAME"].strip())
    codes = []
    cpath = os.path.join(d, "codes.csv")
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                codes.append(OrderedDict(
                    code_id=row.get("CODE_ID", ""), code_id_nm=row.get("CODE_ID_NM", ""),
                    code=row.get("CODE", ""), code_nm=row.get("CODE_NM", ""),
                    sort=row.get("SORT", ""), use_at=row.get("USE_AT", ""),
                    desc=row.get("DESC", "")))
    return _finalize(tables, codes)


COLLECTORS = {
    "mariadb": collect_mysql, "mysql": collect_mysql,
    "oracle": collect_oracle, "cubrid": collect_cubrid, "csv": collect_csv,
}


def main():
    p = argparse.ArgumentParser(description="DB 스키마 → 스냅샷 JSON")
    p.add_argument("--vendor", required=True, choices=sorted(COLLECTORS))
    p.add_argument("--host"), p.add_argument("--port", type=int)
    p.add_argument("--user"), p.add_argument("--schema")
    p.add_argument("--service", help="Oracle 서비스명")
    p.add_argument("--dsn", help="직접 DSN/JDBC URL 지정")
    p.add_argument("--jdbc-jar", help="Cubrid JDBC 드라이버 경로")
    p.add_argument("--exclude", default="", help="쉼표구분 LIKE 패턴 (예: TMP_%%,BAK_%%)")
    p.add_argument("--code-master"), p.add_argument("--code-detail")
    p.add_argument("--code-columns", help="id=,id_nm=,code=,nm=,sort=,use=,desc= 매핑")
    p.add_argument("--from-csv", help="csv 벤더일 때 _raw 디렉터리")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    a.exclude = [x.strip() for x in a.exclude.split(",") if x.strip()]

    password = os.environ.get("ARTIFACTS_DB_PASSWORD", "")
    if a.vendor != "csv" and not password:
        sys.exit("환경변수 ARTIFACTS_DB_PASSWORD 가 필요합니다.")

    snap = COLLECTORS[a.vendor](a, password)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    ncol = sum(len(t["columns"]) for t in snap["tables"])
    print("수집 완료: 테이블 {}개, 컬럼 {}개, 코드 {}건 → {}".format(
        len(snap["tables"]), ncol, len(snap["codes"]), a.out))


if __name__ == "__main__":
    main()
