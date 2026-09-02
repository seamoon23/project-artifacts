#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""산출물 생성/갱신 오케스트레이션.

  수집 → 소스 스캔 → 이전 스냅샷 비교 → (변경 있을 때만) 이전본 보관 · 산출물 생성
  · CHANGELOG 기록 · 버전 갱신

사용:
  ARTIFACTS_DB_PASSWORD=xxx python3 run.py --root /path/to/project
  python3 run.py --root . --skip-db          # DB 미접속, 기존 스냅샷으로 재생성
  python3 run.py --root . --dry-run          # 비교만 하고 파일은 쓰지 않음

종료코드: 0 갱신함 / 10 변경 없음 / 1 오류
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join("docs", "산출물")


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def run(cmd):
    print("$", " ".join(str(c) for c in cmd if not str(c).startswith("--password")))
    return subprocess.call([sys.executable] + cmd)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, help="프로젝트 루트")
    p.add_argument("--config", help="기본: <root>/docs/산출물/.artifacts.yml")
    p.add_argument("--skip-db", action="store_true", help="DB 수집 생략(기존 스냅샷 사용)")
    p.add_argument("--skip-program", action="store_true", help="소스 스캔 생략")
    p.add_argument("--dry-run", action="store_true", help="비교 결과만 출력")
    a = p.parse_args()

    root = os.path.abspath(a.root)
    cfg_path = a.config or os.path.join(root, DEFAULT_DIR, ".artifacts.yml")
    if not os.path.exists(cfg_path):
        sys.exit("설정 파일이 없습니다: {}\ntemplates/artifacts.yml 을 복사해 작성하세요.".format(cfg_path))
    cfg = load_config(cfg_path)
    outdir = os.path.dirname(cfg_path)
    os.makedirs(outdir, exist_ok=True)

    project = cfg.get("project") or os.path.basename(root)
    version = str(cfg.get("version") or "0.0.0")
    outputs = ",".join(cfg.get("outputs") or ["erd", "table", "column", "code", "program"])
    snap_cur = os.path.join(outdir, "_snapshot.json")
    snap_new = os.path.join(outdir, "_snapshot.new.json")
    prog_json = os.path.join(outdir, "_program.json")
    changes = os.path.join(outdir, "_changes.json")
    cl_frag = os.path.join(outdir, "_changelog.part.md")

    # 1) 스키마 수집
    if a.skip_db:
        if not os.path.exists(snap_cur):
            sys.exit("--skip-db 인데 기존 스냅샷이 없습니다: {}".format(snap_cur))
        shutil.copyfile(snap_cur, snap_new)
        print("DB 수집 생략, 기존 스냅샷 사용")
    else:
        db = cfg.get("db") or {}
        cmd = [os.path.join(HERE, "collect_schema.py"),
               "--vendor", db.get("vendor", "mariadb"), "--out", snap_new]
        for key, flag in (("host", "--host"), ("port", "--port"), ("user", "--user"),
                          ("schema", "--schema"), ("service", "--service"),
                          ("dsn", "--dsn"), ("jdbc_jar", "--jdbc-jar")):
            if db.get(key):
                cmd += [flag, str(db[key])]
        if db.get("exclude"):
            cmd += ["--exclude", ",".join(db["exclude"])]
        if db.get("from_csv"):
            cmd += ["--from-csv", os.path.join(outdir, db["from_csv"])]
        code = cfg.get("code_table") or {}
        if code.get("master") and code.get("detail"):
            cmd += ["--code-master", code["master"], "--code-detail", code["detail"]]
            if code.get("columns"):
                cmd += ["--code-columns", ",".join("{}={}".format(k, v)
                                                   for k, v in code["columns"].items())]
        if run(cmd) != 0:
            sys.exit("스키마 수집 실패")

    # 2) 소스 스캔
    if not a.skip_program and "program" in outputs:
        src = os.path.join(root, cfg.get("source_root", "."))
        if run([os.path.join(HERE, "scan_program.py"), "--root", src, "--out", prog_json]) != 0:
            print("[warn] 소스 스캔 실패 — 프로그램설명서는 건너뜁니다.")

    # 3) 비교
    rc = run([os.path.join(HERE, "diff_snapshot.py"), "--new", snap_new,
              "--old", snap_cur, "--current-version", version,
              "--out", changes, "--changelog-out", cl_frag])
    if rc == 10:
        print("변경 없음 (현재 v{}). 산출물을 다시 쓰지 않습니다.".format(version))
        os.remove(snap_new)
        return 10
    if rc != 0:
        sys.exit("비교 실패")

    with open(changes, encoding="utf-8") as f:
        ch = json.load(f)
    new_version = ch["to_version"]
    if a.dry_run:
        print("[dry-run] {} → v{} 로 갱신 예정".format(ch["level"], new_version))
        os.remove(snap_new)
        return 0

    # 4) 이전본 보관
    if os.path.exists(snap_cur):
        hist = os.path.join(outdir, "_history", "v" + version)
        os.makedirs(hist, exist_ok=True)
        for fn in os.listdir(outdir):
            if fn.endswith((".xlsx", ".mmd", ".svg")) or fn.startswith("erd_"):
                shutil.move(os.path.join(outdir, fn), os.path.join(hist, fn))
        shutil.copyfile(snap_cur, os.path.join(hist, "_snapshot.json"))
        print("이전본 보관 →", os.path.relpath(hist, root))

    # 5) 산출물 생성
    if run([os.path.join(HERE, "build_docs.py"), "--snapshot", snap_new,
            "--program", prog_json, "--project", project,
            "--version", new_version, "--outdir", outdir, "--outputs", outputs]) != 0:
        sys.exit("산출물 생성 실패")

    # 6) CHANGELOG · 스냅샷 · 버전 갱신
    cl_path = os.path.join(outdir, "CHANGELOG.md")
    prev = ""
    if os.path.exists(cl_path):
        with open(cl_path, encoding="utf-8") as f:
            prev = f.read()
        prev = prev.split("\n", 1)[1] if prev.startswith("# ") else prev
    with open(cl_frag, encoding="utf-8") as f:
        frag = f.read()
    with open(cl_path, "w", encoding="utf-8") as f:
        f.write("# {} 산출물 변경이력\n\n".format(project) + frag + "\n" + prev.lstrip("\n"))
    os.remove(cl_frag)
    shutil.move(snap_new, snap_cur)
    cfg["version"] = new_version
    save_config(cfg_path, cfg)

    print("\nv{} → v{} ({})".format(version, new_version, ch["level"]))
    print("추가 {} · 변경 {} · 삭제 {}".format(
        len(ch["added"]), len(ch["changed"]), len(ch["removed"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
