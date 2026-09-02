---
name: project-artifacts
description: 프로젝트 폴더에서 SI 표준 산출물(ERD, 테이블정의서, 컬럼정의서, 코드정의서, 프로그램설명서)을 생성/갱신한다. 이전 스냅샷과 비교해 변경이 있을 때만 semver를 올린다. "산출물 갱신", "테이블정의서 뽑아줘", "ERD 갱신", "산출물 버전업" 등에 사용.
---

# 프로젝트 산출물 생성/갱신

비정기 호출형. 매 호출마다 **수집 → 이전 스냅샷과 비교 → 변경 있을 때만 갱신 + 버전업**.
판정·생성 로직은 전부 `scripts/` 의 결정형 스크립트가 담당한다. **직접 파싱하거나 손으로 표를 만들지 말 것.**

## 스크립트 위치

`https://github.com/seamoon23/project-artifacts` (로컬 클론: `~/git/project-artifacts`).
없으면 먼저 clone 하고 `pip install openpyxl PyYAML` + 벤더 드라이버를 설치한다.
아래 `<스킬경로>` 는 그 클론 디렉터리를 가리킨다.

## 실행

```bash
# 최초 1회: 설정 파일 배치
cp <스킬경로>/templates/artifacts.yml <프로젝트루트>/docs/산출물/.artifacts.yml
# 편집: project, db.vendor/host/port/user/schema, code_table, exclude

# 갱신 (표준 경로)
ARTIFACTS_DB_PASSWORD='***' python3 <스킬경로>/scripts/run.py --root <프로젝트루트>

python3 scripts/run.py --root <루트> --dry-run      # 비교만, 파일 미변경
python3 scripts/run.py --root <루트> --skip-db      # 기존 스냅샷으로 문서만 재생성
```

종료코드: `0` 갱신함 / `10` 변경 없음(정상) / `1` 오류.
**`10` 이면 "변경 없음(현재 vX.Y.Z)" 만 보고하고 끝낸다.** 파일을 다시 쓰지 않는다.

## 실행 위치

DB가 사내망이면 클라우드에서 접속 불가하다. **사용자 컴퓨터의 셸(device_bash)에서 실행**한다.
사용 가능한 첫 번째 수단을 쓴다 — `which` 로 확인:

1. Python 드라이버: `pymysql`(mariadb/mysql) · `oracledb`(oracle) · `jaydebeapi`+JDBC jar(cubrid)
2. 드라이버 설치가 막혔으면 CLI(`mysql`, `sqlplus`, `csql`)로 메타데이터를 CSV 추출 후
   `db.vendor: csv` + `from_csv` 모드
3. 그것도 불가하면 `collect_schema.py` 의 SQL을 사용자에게 제시하고
   `docs/산출물/_raw/{tables,columns,codes}.csv` 로 받는다

`openpyxl`, `PyYAML` 이 없으면 설치한다. 로컬 설치가 전부 막힌 경우에만 소스를 클라우드로 스테이징해 처리한다.

## 산출물

`<프로젝트루트>/docs/산출물/` 아래 생성:

| 파일 | 내용 |
|---|---|
| `<프로젝트>_테이블정의서_v<버전>.xlsx` | 1행=1테이블. 물리/논리명, 컬럼수, PK, 인덱스수, FK, 참조프로그램수 |
| `<프로젝트>_컬럼정의서_v<버전>.xlsx` | 1행=1컬럼 + `주석누락` 시트 |
| `<프로젝트>_코드정의서_v<버전>.xlsx` | 1행=1코드값, 사용 테이블·컬럼 역매핑 포함 |
| `<프로젝트>_프로그램설명서_v<버전>.xlsx` | 1행=1프로그램(URL) + `미확인` 시트 |
| `erd_v<버전>[_도메인].mmd` | Mermaid ERD. 테이블 40개 초과 시 도메인별 분할 |
| `CHANGELOG.md` | 버전별 추가/변경/삭제 |
| `_snapshot.json` · `.artifacts.yml` | 다음 비교 기준·버전 (커밋 대상) |
| `_history/v<이전버전>/` | 이전 산출물 보관 |

## 버전 규칙 (`diff_snapshot.py`)

| 등급 | 사유 |
|---|---|
| MAJOR | 테이블·컬럼 삭제, PK 변경, 타입 변경, 길이 축소, NOT NULL 신설, 코드그룹 삭제 |
| MINOR | 테이블·컬럼 추가, 인덱스·FK 추가, 길이 확대, 코드그룹 추가 |
| PATCH | 주석·설명 변경, 기본값 변경, 코드값 추가/명칭 변경, 인덱스·FK 삭제 |

## 보고 형식

```
v1.1.0 → v1.2.0 (MINOR)
변경: 테이블 +2, 컬럼 +5 / 변경 1 / 삭제 0, 코드 +1그룹
생성: 테이블정의서, 컬럼정의서, 코드정의서, 프로그램설명서, ERD(3분할)
보완필요: 컬럼 주석 누락 14건, 프로그램 추적 실패 3건
```

`보완필요` 는 항상 보고한다. 주석 누락과 추적 실패는 **추측으로 채우지 않는다** —
빈칸으로 두고 `주석누락` / `미확인` 시트에 남긴다.

## 확장 산출물

`.artifacts.yml` 의 `outputs` 로 제어. 기본 5종 외에 요청 시 아래를 추가 구현한다:
`menu`(메뉴·화면목록) · `api`(REST 명세) · `batch`(스케줄러 목록) · `interface`(연계 정의서) ·
`deps`(라이브러리·라이선스 현황, pom.xml/build.gradle 기준) · `structure`(디렉터리 구조도) ·
`history`(git 커밋 기반 변경이력서).

초안만 가능(수기 보완 필요): 요구사항추적표(RTM), 단위테스트 결과서, 사용자·운영자 매뉴얼 골격.

## 주의

- **읽기 전용 DB 계정**을 쓰고 DDL/DML은 절대 실행하지 않는다. 운영 DB 대상이면 실행 전 확인받는다.
- 비밀번호는 `ARTIFACTS_DB_PASSWORD` 환경변수로만 전달. 설정 파일·산출물·로그·커밋에 남기지 않는다.
- 원본 소스는 수정하지 않는다. 쓰기는 `docs/산출물/` 아래로 한정.
- 테이블 300개 초과 시 수집을 분할 실행한다(로컬 셸은 호출당 시간 제한이 있다).
- ERD의 점선(`}o..o|`)은 FK 제약 없이 컬럼명으로 **추정한** 관계다. 검수 대상임을 반드시 알린다.
