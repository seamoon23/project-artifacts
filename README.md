# project-artifacts

레거시 웹 프로젝트(eGovFrame / Spring + MyBatis)에서 **SI 표준 산출물**을 뽑아내고,
호출할 때마다 이전 상태와 비교해 **변경이 있을 때만 버전을 올리는** Claude 스킬 + CLI 도구.

- ERD (Mermaid)
- 테이블정의서 / 컬럼정의서 / 코드정의서 (xlsx)
- 프로그램설명서 (URL ↔ Controller ↔ Service ↔ DAO ↔ SQL ↔ 화면 ↔ 사용테이블, xlsx)

DB: MariaDB / MySQL / Oracle / Cubrid / CSV(수동 추출분)

## 왜 만들었나

기존 DB 문서화 도구(tbls, SchemaSpy 등)는 스키마 문서까지는 잘 만들지만,
국내 SI 제출 양식(테이블정의서·컬럼정의서·코드정의서·프로그램설명서)과
**산출물 버전 관리**를 다루지 않는다. 그 간극을 메운다.

## 설치

```bash
git clone https://github.com/seamoon23/project-artifacts.git
pip install openpyxl PyYAML
# DB 벤더별 (필요한 것만)
pip install pymysql          # MariaDB / MySQL
pip install oracledb         # Oracle (thin 모드, JDK 불필요)
pip install JayDeBeApi       # Cubrid (JDBC 드라이버 jar 별도 필요)
```

Claude 스킬로 쓰려면 저장소를 `~/.claude/skills/project-artifacts/` 로 두거나 심볼릭 링크한다.

## 사용

```bash
# 1) 설정 배치
mkdir -p <프로젝트루트>/docs/산출물
cp templates/artifacts.yml <프로젝트루트>/docs/산출물/.artifacts.yml
$EDITOR <프로젝트루트>/docs/산출물/.artifacts.yml

# 2) 갱신 (비밀번호는 환경변수로만)
ARTIFACTS_DB_PASSWORD='***' python3 scripts/run.py --root <프로젝트루트>
```

| 옵션 | 설명 |
|---|---|
| `--dry-run` | 비교 결과만 출력, 파일 미변경 |
| `--skip-db` | DB 접속 없이 기존 스냅샷으로 문서만 재생성 |
| `--skip-program` | 소스 스캔 생략 |

종료코드: `0` 갱신함 · `10` 변경 없음 · `1` 오류 → CI에서 `10` 은 성공으로 처리할 것.

## 버전 규칙

| 등급 | 사유 |
|---|---|
| MAJOR | 테이블·컬럼 삭제, PK 변경, 타입 변경, 길이 축소, NOT NULL 신설, 코드그룹 삭제 |
| MINOR | 테이블·컬럼 추가, 인덱스·FK 추가, 길이 확대, 코드그룹 추가 |
| PATCH | 주석·설명 변경, 기본값 변경, 코드값 추가/명칭 변경, 인덱스·FK 삭제 |

변경이 없으면 파일을 다시 쓰지 않는다(재생성으로 인한 무의미한 diff 방지).

## 생성 결과

```
docs/산출물/
├── .artifacts.yml                       # 설정 + 현재 버전
├── _snapshot.json                       # 다음 비교 기준
├── CHANGELOG.md                         # 버전별 추가/변경/삭제
├── kapex_테이블정의서_v1.2.0.xlsx
├── kapex_컬럼정의서_v1.2.0.xlsx          # + 주석누락 시트
├── kapex_코드정의서_v1.2.0.xlsx
├── kapex_프로그램설명서_v1.2.0.xlsx      # + 미확인 시트
├── erd_v1.2.0.mmd                       # 40개 초과 시 도메인별 분할
└── _history/v1.1.0/                     # 이전 산출물
```

## 스크립트 구성

| 파일 | 역할 | 단독 실행 |
|---|---|---|
| `scripts/run.py` | 전체 오케스트레이션 | ✔ |
| `scripts/collect_schema.py` | DB/CSV → 정규화 스냅샷 JSON | ✔ |
| `scripts/scan_program.py` | 소스 → 프로그램 목록 JSON | ✔ |
| `scripts/diff_snapshot.py` | 스냅샷 비교 + semver 판정 + CHANGELOG | ✔ |
| `scripts/build_docs.py` | 스냅샷+프로그램 → xlsx/ERD | ✔ |

## 설계 원칙

- **추측하지 않는다.** 컬럼 주석이 없으면 논리명을 비우고 `주석누락` 시트에 남긴다.
  프로그램 추적이 끊기면 `미확인` 시트에 사유를 남긴다.
- FK 제약이 없는 레거시 스키마는 `컬럼명 = 다른 테이블 PK` 규칙으로 관계를 **추정**하되,
  ERD에 점선으로 구분 표기하고 범례에 검수 대상임을 명시한다.
- 비밀번호는 인자로 받지 않는다. `ARTIFACTS_DB_PASSWORD` 환경변수만 사용하며
  설정 파일·산출물·로그에 기록하지 않는다.
- 원본 소스는 수정하지 않는다. 쓰기는 `docs/산출물/` 아래로 한정한다.

## 테스트

```bash
bash tests/run_test.sh
```

픽스처 프로젝트로 최초 생성 → 무변경 재실행(파일 미변경 + 종료코드 10)
→ 스키마 변경 감지(MAJOR 승격, 이전본 보관, CHANGELOG 기록) → 프로그램 추적 정확도를 검증한다.

## 제약

- Cubrid는 컬럼 주석을 지원하지 않는 버전이 있어 논리명이 비어 나올 수 있다.
- Oracle 12c 미만은 `DATA_DEFAULT`(LONG) 추출이 CLI 경로에서 깨질 수 있다.
- 소스 스캔은 정규식 기반이다. 어노테이션 상수 참조, 동적 프록시, 리플렉션 호출은 추적되지 않는다.

## 라이선스

MIT
