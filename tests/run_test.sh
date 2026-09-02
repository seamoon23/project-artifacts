#!/usr/bin/env bash
# 픽스처 프로젝트로 end-to-end 검증: 초기 생성 → 변경 없음 → 변경 감지(버전업)
set -eu
cd "$(dirname "$0")/.."
FX=tests/fixture
OUT="$FX/docs/산출물"

echo "### 0. 초기화"
rm -rf "$OUT/_history" "$OUT"/*.xlsx "$OUT"/erd_* "$OUT"/_snapshot*.json \
       "$OUT/_program.json" "$OUT/_changes.json" "$OUT/CHANGELOG.md" 2>/dev/null || true
python3 - "$OUT/.artifacts.yml" <<'PY'
import sys, yaml
p = sys.argv[1]
c = yaml.safe_load(open(p, encoding="utf-8"))
c["version"] = "0.0.0"
yaml.safe_dump(c, open(p, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
PY
git checkout -- "$FX/_raw/columns.csv" 2>/dev/null || true

echo "### 1. 최초 생성 (변경 있음 → v0.1.0 기대)"
python3 scripts/run.py --root "$FX" >/dev/null
grep -q 'version: 0.1.0' "$OUT/.artifacts.yml" || { echo "FAIL: 버전이 0.1.0 이 아님"; exit 1; }
ls "$OUT"/*.xlsx >/dev/null || { echo "FAIL: xlsx 미생성"; exit 1; }
echo "  OK  $(ls "$OUT" | grep -c '\.xlsx$') 개 xlsx 생성"

echo "### 2. 재실행 (변경 없음 → 종료코드 10, 파일 미변경 기대)"
BEFORE=$(md5sum "$OUT"/*.xlsx | md5sum)
set +e
python3 scripts/run.py --root "$FX" >/dev/null
RC=$?
set -e
[ "$RC" = "10" ] || { echo "FAIL: 변경 없음인데 종료코드 $RC"; exit 1; }
AFTER=$(md5sum "$OUT"/*.xlsx | md5sum)
[ "$BEFORE" = "$AFTER" ] || { echo "FAIL: 변경 없는데 파일이 다시 쓰임"; exit 1; }
echo "  OK  파일 미변경, 종료코드 10"

echo "### 3. 컬럼 길이 축소 + 컬럼 추가 (MAJOR → v1.0.0 기대)"
sed -i 's/TB_USER,2,USER_NM,VARCHAR,50/TB_USER,2,USER_NM,VARCHAR,30/' "$FX/_raw/columns.csv"
printf 'TB_DEPT,3,UPPER_DEPT_CD,VARCHAR,10,Y,,N,상위부서코드\n' >> "$FX/_raw/columns.csv"
python3 scripts/run.py --root "$FX" >/dev/null
grep -q 'version: 1.0.0' "$OUT/.artifacts.yml" || { echo "FAIL: MAJOR 판정 실패"; cat "$OUT/.artifacts.yml"; exit 1; }
[ -d "$OUT/_history/v0.1.0" ] || { echo "FAIL: 이전본 미보관"; exit 1; }
grep -q 'v1.0.0' "$OUT/CHANGELOG.md" || { echo "FAIL: CHANGELOG 미기록"; exit 1; }
echo "  OK  v0.1.0 → v1.0.0, 이전본 보관 및 CHANGELOG 기록 확인"

echo "### 4. 프로그램 추적 정확도"
python3 - "$OUT/_program.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
urls = {p["url"] for p in d["programs"]}
assert urls == {"/user/list.do", "/user/insert.do", "/user/detail.do"}, urls
by = {p["url"]: p for p in d["programs"]}
assert by["/user/list.do"]["tables"] == ["TB_DEPT", "TB_USER"], by["/user/list.do"]["tables"]
assert by["/user/list.do"]["sql_id"] == "sample.user.selectUserList"
assert by["/user/list.do"]["http_method"] == "GET"
assert by["/user/list.do"]["program_name"] == "사용자 목록 조회"
assert by["/user/insert.do"]["program_name"] == "사용자 등록 처리", by["/user/insert.do"]["program_name"]
assert by["/user/detail.do"]["program_name"] == "", by["/user/detail.do"]["program_name"]
print("  OK  URL 3건, 사용테이블·SQL ID·프로그램명 매칭 정확")
PY

git checkout -- "$FX/_raw/columns.csv" 2>/dev/null || true
echo
echo "전체 통과"
