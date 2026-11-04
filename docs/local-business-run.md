# 각 팀원 PC의 로컬 업무 실행 (KAN-159)

## 한 명령으로 준비하기

새 통합 진입점은 아래 명령이다. Docker 엔진, Git, uv/Python 3.12, Node 24/npm, OpenSSL, GWS를 먼저 설치하고 본인 GWS 계정으로 공용 Drive 접근을 준비한다. 키나 비밀번호를 공유하지 않는다. 현재 변경의 실제 새 환경·Mac 검증은 진행 중이며, 아래 명령이 모든 팀원 PC에서 통과했다고 주장하지 않는다.

```bash
uv run --frozen --python 3.12 python -m scripts.local_business.bootstrap --namespace team4-kan159-my-pc
```

최초 실행은 승인된 Drive 입력 다운로드·해시 대조 → 새 전용 Docker 이미지/DB/계정 준비 → 개발 문의 3,403건과 제공 해결 사례 696건 → 가정 장비 34개와 이전 문의 후보 연결 10개 → 반복 기준에 이미 도달한 고장 묶음의 개선요청 초안(KAN-162, 서비스의 후보 수집과 같은 판정을 설치 시각 기준 1회) → 검색 문서·기본 협력사 → RFP 원문 22개와 합성 시연 1개 등록 → localhost 기동 순서다. 가정 장비는 기존 문의를 재사용하며 신규 문의·확정 연결을 만들지 않는다. 평가용 RFP 4개는 등록만 하고 자동 분석하지 않는다. 평가 정답이나 과거 분석·승인·초안 결과를 seed하지 않으며 메일은 OFF다.

C/S 제공 문의는 과거 접수 자료이므로 목록의 날짜 범위를 `2025-10-01`~`2026-09-30`으로 넓혀 확인한다. 기본 최근 기간에는 빈 목록이 보일 수 있다. 분석·초안·부서 회신·개선 요청은 사전 생성하지 않으므로, 화면에서 실제 업무를 진행한 뒤 생긴다. RFP 첫 처리 연결 확인에는 `DEMO-EN` 합성 시연 프로젝트를 사용하고, 평가 라벨 4건은 별도 승인 없이 모델에 넣지 않는다.

`--plan`은 다운로드 계획만 표시하고 파일·DB·네트워크를 변경하지 않는다. `--profile cs`는 RFP 원문 다운로드·등록을 제외한다. `--port 40443`처럼 비어 있는 포트를 선택할 수 있다. 승인된 생성 시연 파일도 공용 Drive 목록에 포함돼 별도 수동 복사가 필요 없다.

성공 후 같은 명령을 다시 실행하면 보관한 코드와 DB를 사용하며 기존 문의·분석·초안·비용 기록을 초기화하거나 다시 적재하지 않는다. 다른 profile이나 깨끗한 새 시험은 다른 namespace로 만든다. 중간 적재가 UNKNOWN으로 기록되면 서버·영수증 대조 전 자동 반복하지 않는다. 명령의 표준 출력은 `.log`, 경고·오류는 같은 이름의 `.stderr.log`에 분리 보존한다. 부분 namespace나 볼륨, 원장 파일을 삭제해 우회하지 않는다. 전원 손실 후 기록의 내구성·중복 방지를 절대 보장하는 구조는 아니므로 불청정 종료는 담당자 대조가 필요하다.

모델은 기본 OFF다. 실제 Gemini API 실행을 원하면 본인의 **Google AI Studio 키 파일**을 본인만 읽도록 저장(`chmod 600`)한 뒤 같은 명령에 명시적으로 옵션을 붙인다. 파일 내용은 명령행이나 채팅에 넣지 않는다.

```bash
uv run --frozen --python 3.12 python -m scripts.local_business.bootstrap \
  --namespace team4-kan159-qa-unlimited --port 41443 --enable-model cs \
  --key-file /absolute/path/to/private/gemini-key --unlimited
```

RFP는 `--enable-model tft`로 선택한다. 키는 해당 worker에만 전달되고 API·웹에는 전달하지 않는다. 이 옵션은 작업 허가만 열며 화면에서 접수하기 전 자동 모델 호출은 하지 않는다. 한 사람이 한 작업씩 완료·비용을 확인하고 다음 작업을 진행한다. `--unlimited`는 작업 수·SDK 호출 수·도구의 누적 USD 예산·알려진 호출별 예약 초과 차단을 모두 해제한다. 개인이 사용량과 비용을 관리하며 `disable-real`로 종료한다. Google 제공자 쿼터·요율·오류가 해제되는 것은 아니다.

`--unlimited`를 생략한 기존 제한 모드는 그대로다. bootstrap 기본값은 작업 1개/호출 20회/추정 예약 USD 0.15/누적 예산 USD 1.50이고, 직접 `enable-real`의 기본 호출 수는 8회다. 제한 모드만 `--max-jobs 1~20`, `--max-calls 1~100`, `--budget-usd`를 선택할 수 있다. unlimited와 이 세 옵션을 함께 쓰면 모순된 설정으로 거절한다. `--reserve-usd`(기본 0.15)는 unlimited에서도 불명 호출을 원장에 남길 추정 예약이며 cap이 아니다. 알려진 실제 비용은 예약을 넘어도 전액 SETTLED로 기록한다.

UNKNOWN/SDK 실패/중단 예약은 두 모드 모두 보존하고 추가 호출·새 작업·새 driver 허가를 차단한다. 이는 금액 제한이 아니라 실행·정산 불확실성 경계이며, 원장을 지우거나 새 토큰으로 자동 재시도하지 않는다. 원장·단일 driver·중복 작업 방지·lease·인증·CSRF·production 거부는 유지한다. 합성 계정 로그인과 실제 Microsoft 로그인은 별개다.

**기존 namespace 주의:** host에서 `git pull`해도 설치 때 보관한 `source`는 바뀌지 않는다. 구 snapshot에 `--unlimited`를 주면 키 접근·Permit 변경·key mount 전에 `LOCAL_UNLIMITED_SNAPSHOT_UNSUPPORTED`로 거절한다. 일반 팀원용 데이터 보존 업데이트 CLI는 아직 없으므로 이 기능을 포함한 코드를 받은 뒤 위 예처럼 **사용하지 않은 새 namespace와 포트**로 준비한다. 예시의 41443도 해당 PC에서 비어 있어야 하며, 기존 40443/38443 환경을 중지하거나 덮어쓰지 않는다. 기존 DB·결과·원장을 보존하고, 새 namespace로 기존 업무 결과를 자동 이동하지 않는다. 감독의 별도 운영 업데이트 사례를 자동 재실행하거나 기존 namespace를 삭제·재초기화하지 않는다.

이미 unlimited 지원 코드로 설치된 namespace는 같은 bootstrap 명령을 재사용하거나 아래 직접 `enable-real --unlimited`를 쓸 수 있다. 기존 driver가 있으면 먼저 정상 종료·정산을 확인해야 한다. 중단 예약이 남은 채 재활성화해 우회할 수 없다. `limit_mode=unlimited`/`remaining_jobs=null`은 명시 프로토콜이며, 구 marker에 null·문자열·큰 숫자를 수동 주입하지 않는다.

각자 받은 자료와 같은 코드를 사용해 신규 C/S는 UI 등록, RFP는 UI 업로드 후 실제 처리한다. **source `46b750fc32c2c25d83385d5ea8bee4af52a92032` 실행 스냅샷에 로컬 worker CONNECT 권한을 보정한 환경의 실증 기록이 있다. 최신 통합 코드 전체 E2E·품질·실제 Microsoft/메일·운영 완료와는 구분한다.**

감독이 확인한 범위는 C/S 등록 → 분석 → 팝업, RFP 처리 20 / 초안 48 → 사용자 승인 OWN → 검토본 → DOCX 완료다. 누적 모델 호출은 8회, 기록 비용은 USD 0.083790이며 DOCX export의 추가 모델 호출은 0회다. 해당 검증 종료·본 정정 시점에 모델은 OFF다. 이 기록을 다른 PC나 최신 통합 코드의 전체 재실행 성공으로 확대하지 않는다.

명령은 `scripts/local_business/{__main__,setup,stack,dispatcher}.py`의 CLI 기준이다. 새 setup에는 통합된 코드를 사용하고 namespace에 기록된 `source_sha`를 확인한다. 기존 실행 스냅샷과 현재 Git HEAD를 혼동하지 않는다. 기존 [데이터 준비 안내](local-business-data.md)와 공통 해시 기준은 PR [#555](https://github.com/LS-Final-Project/ls-final-project/pull/555)에 있다.

## 1. PC 준비

- Linux 또는 WSL2 Ubuntu 터미널에서 Git, Python 3.12 실행용 uv, Node 22.12 이상·npm(Node 24 권장), OpenSSL, 실행 중인 Docker 엔진을 준비한다. 다른 OS의 이 runner 실행은 미검증이다. 프로그램 설치는 [공통 온보딩](team-start.md)을 참고한다.
- 각 PC의 별도 코드 경로에서 작업한다. 기존 미커밋 변경·DB·컨테이너를 보존한다. runner는 코드의 backend/infra/scripts/frontend/잠금 파일에 변경이 있으면 setup을 거부한다.
- **로컬 runtime 이미지와 Node 24 web 이미지**, `pgvector/pgvector:0.8.7-pg17-trixie`를 먼저 준비한다. setup은 이미지를 자동 pull하지 않고 실제 image ID를 기록한다. 아래 runtime 빌드는 기존 Dockerfile의 `dependencies` target이며 제품 전체 Docker 검사 성공을 의미하지 않는다. 이미지 다운로드/빌드 자체도 이 문서 작성에서는 실행하지 않았다.
- 최초 setup은 해당 코드의 Git archive를 만들고 `npm ci`와 업무 UI를 켠 frontend build를 실행한다. 패키지 다운로드 연결과 로컬 디스크가 필요하다. 기존 `.env`·원자료·과거 업무 결과를 archive 입력으로 사용하지 않는다.

저장소 루트에서 개인 namespace를 정한다. 형식은 `team4-kan159-` 뒤에 소문자/숫자/하이픈이며 아래 예는 각 PC에서 구분되는 값으로 바꾼다.

```bash
export NS='team4-kan159-my-pc'
export RUNTIME_IMAGE="${NS}-runtime"
export WEB_IMAGE='node:24-bookworm-slim'
docker build --target dependencies -t "$RUNTIME_IMAGE" .
docker pull "$WEB_IMAGE"
docker pull pgvector/pgvector:0.8.7-pg17-trixie
```

## 2. 새 setup과 일반 기동

**새 namespace에 한 번만** 실행한다. 이 명령은 새 로컬 DB·스키마·개발 계정 4개·worker 로그인·로컬 인증 키·TLS 인증서를 만든다. 문의/프로젝트/분석·비교 결과를 사전 seed하지 않는다.

```bash
uv run --frozen --python 3.12 python -m scripts.local_business setup \
  --namespace "$NS" --runtime-image "$RUNTIME_IMAGE" --web-image "$WEB_IMAGE" \
  --web-port 38443
uv run --frozen --python 3.12 python -m scripts.local_business start --namespace "$NS"
uv run --frozen --python 3.12 python -m scripts.local_business status --namespace "$NS"
```

한 명령이 실패하면 다음 명령으로 넘어가지 않는다. `LOCAL_STATE_EXISTS_USE_START`, `LOCAL_NAMESPACE_EXISTS_PRESERVE`, `LOCAL_SETUP_INCOMPLETE_PRESERVED`는 기존 상태를 삭제하라는 뜻이 아니다. 부분 생성 상태와 로그를 보존하고 담당자에게 확인한다. 시작한 주소는 `https://localhost:38443`이며 지정 포트가 사용 중이면 중단한다. localhost 전용 자체 TLS 인증서의 출처·주소를 확인한다. 공개 공유서버로 노출하지 않는다.

setup에서 만드는 자체 TLS 인증서의 유효기간은 **7일**이다. 만료되면 브라우저의 만료 경고·접속 거부가 발생할 수 있으며 start/restart가 인증서를 자동 갱신하지 않는다. 현재 인증서 갱신 CLI는 없다. 새 namespace 준비가 필요하면 담당자와 기존 DB·업로드 파일·비용/작업 상태를 보존한 뒤 진행한다. 인증서 만료를 해결하려고 기존 namespace·볼륨을 자동 삭제하거나 재초기화하지 않는다.

setup 시 코드 SHA·archive hash·이미지 ID·frontend 산출물 hash를 `artifacts/local-business/$NS/settings.json`에 기록한다. 이후 start/restart는 그때 보관한 source/dist를 사용하므로 저장소 pull만으로 기존 namespace의 실행 코드가 바뀌지는 않는다. 코드 갱신이 필요하면 담당자와 별도 새 namespace를 준비하고 이전 DB를 보존한다.

브라우저의 `/api/v1/platform/auth/login`에서 로컬 fixture의 합성 계정 선택 form으로 이동한다. C/S 담당자·변압기 기술지원·수주 담당자·관리자 4개에 대응하며 제품 세션·역할·CSRF 경계를 이용한다. **Microsoft 로그인·Graph·실제 사서함 검증은 아니다.** 앞선 source46 실증은 이 로컬 fixture 범위의 기록이며 실제 Microsoft 인증 성공의 증거로 쓰지 않는다. 메일 인증·수집·발송, 평가 실행·푸시·자동 sweep은 비활성으로 설정한다. 실제 Microsoft 계정·토큰을 넣어 이 fixture를 대체하지 않는다.

## 3. 자료 준비 후 필요한 작업만 real 활성화

[데이터 안내](local-business-data.md)에 따라 필수 검색 문서 107개+doc_index의 hash를 대조한다. ref CSV 2종은 선택이다. 자사 23종은 커밋 파생 파일, 협력사는 기본 tracked 합성 프로필을 사용한다. 문의 4,000건·사례 800건 전량 적재나 generated demo-en/smoke 자료는 기본 선행이 아니다.

### C/S 검색 근거

C/S real 전에는 **해당 namespace DB에 실제 적재된** `execute/chunking-v3` 검색 manifest와 필수 `POL-ORDER/RETURN/SERVICE/WARRANTY` 매핑을 준비한다. dry-run manifest로 대신하지 않는다. 아래는 새 기능이 아닌 기존 `scripts.cs_index_docs --execute`의 **명시적 로컬 DB 적재** 명령이다. 해당 PC의 setup이 끝나고 원자료 hash 대조가 통과한 뒤, real driver가 꺼진 상태에서 실행한다. 아래는 각 새 namespace의 재현 명령이다. 앞선 source46 실증을 각 PC의 적재 성공으로 대신하지 않으며 문서 작성자는 직접 적재를 실행하지 않았다.

저장소 루트에서 `NS`를 앞 단계와 동일하게 지정한다. `CS_INPUT_DOCS`는 받은 묶음의 실제 `docs/` 절대 경로로 바꾼다. 첫 Python 구간은 기존 `owned()`로 namespace 소유권·DB 이미지/볼륨·frontend 산출물 등을 읽기 확인하고, settings의 `source_sha`가 현재 코드 HEAD인지 확인한 뒤 **settings에 기록된 runtime image ID**를 선택한다. 다른 namespace나 현재 태그의 이미지로 임의 대체하지 않는다. 이 확인이 실패하면 뒤의 적재를 실행하지 않는다.

```bash
(
set -eu
CODE_ROOT="$(pwd -P)"
LOCAL_STATE_DIR="$CODE_ROOT/artifacts/local-business/$NS"
CS_INPUT_DOCS='/absolute/path/to/received/CS_AI_Agent_Training_Data_v1/docs'
RUNTIME_IMAGE_ID="$(uv run --frozen --python 3.12 python -B - "$NS" <<'PY'
import re
import subprocess
import sys
from scripts.local_business.config import state_path
from scripts.local_business.setup import owned
from scripts.local_business.stack import no_active_driver

config = owned(state_path(sys.argv[1]))
no_active_driver(config)
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
image = config["images"]["runtime"]
if (config["namespace"] != sys.argv[1] or config["source_sha"] != head
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)):
    raise SystemExit("LOCAL_CORPUS_SOURCE_OR_IMAGE_MISMATCH")
print("source_sha=" + head + " runtime_image=" + image, file=sys.stderr)
print(image)
PY
)"
docker run --rm --pull=never \
  --network "container:${NS}-pg" --user "$(id -u):$(id -g)" \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m --workdir /tmp \
  --mount "type=bind,src=$LOCAL_STATE_DIR/source,dst=/workspace,readonly" \
  --mount "type=bind,src=$CS_INPUT_DOCS,dst=/input,readonly" \
  --mount "type=bind,src=$LOCAL_STATE_DIR/corpus,dst=/local/corpus" \
  --entrypoint /usr/bin/env "$RUNTIME_IMAGE_ID" -i \
  PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin HOME=/tmp \
  PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1 ENVIRONMENT=local \
  DATABASE_URL=postgresql://cs_app@127.0.0.1:5432/team4 \
  python -m scripts.cs_index_docs --docs /input --doc-index /input/doc_index.csv \
  --data-origin synthetic --manifest-out /local/corpus/cs-index-execute.json --execute
)
```

이 연결 문자열은 해당 신규 로컬 DB의 loopback trust와 `cs_app` 권한을 사용하며 비밀번호·모델 키를 전달하지 않는다. DB 컨테이너의 네트워크만 공유하고 호스트 DB 포트를 공개하거나 외부 egress를 추가하지 않는다. source와 받은 docs는 읽기 전용, namespace의 corpus만 영구 출력이 가능하고 `/tmp`는 임시 공간이다. 원자료·`data/`·다른 namespace의 DB를 변경하지 않는다. `--embed`는 없으며 FTS만 준비하고 벡터 검색은 계속 OFF다.

종료코드 0만으로 개별 문서 적재 성공을 판단하지 않는다. 호스트의 `artifacts/local-business/$NS/corpus/cs-index-execute.json`에서 `mode=execute`, `index_version=chunking-v3`, 모든 문서의 `reason=null`·`stored>0`, 필수 정책 4종의 `doc_id → sha256`을 확인한다. 기존 `policy_mapping()`도 같은 manifest를 검사하고 dispatcher는 C/S real 시작 시 현재 DB의 대응 청크 존재를 읽기 대조한다. 실패·누락·다른 DB에서 복사한 manifest로 다음 단계에 진행하지 않는다. worker는 같은 corpus를 읽기 전용 연결한 `/local/corpus/cs-index-execute.json`을 사용한다. 기존 dry-run 107문서/794청크는 예상 입력 근거이며 이 명령의 실제 실행 결과나 각 PC의 적재 성공 기록이 아니다.

### RFP 기본 협력사 프로필

같은 신규 namespace의 internal PG에 기본 tracked 합성 프로필 **12곳(P/L/M/T)**을 명시 적재한다. 검증용 demo-en Q-A~F 6곳은 선택하지 않으며 `--profiles`로 교체하지 않는다. 자사 23종 파생 파일과 달리 협력사 프로필은 DB 행이 필요하지만, 등급·선택·비교·분석 결과는 seed하지 않는다. 기존 `scripts.rfp_seed_partner_profiles`만 사용하며 새 importer나 자동 seed를 추가하지 않는다. 아래는 각 새 namespace의 재현 명령이며 source46의 업무 결과를 사전 seed하지 않는다. 문서 작성자는 직접 적재를 실행하지 않았다.

저장소 루트의 같은 터미널에서 실행한다. 앞 C/S 블록의 변수는 subshell을 벗어나면 남지 않으므로 여기서 namespace와 기록된 runtime 이미지를 다시 확인한다. 이후 단계가 끝날 때까지 `NS`·아래 변수를 바꾸지 않는다. 확인이 실패하면 중단한다. 함수는 같은 Docker 명령을 반복 호출하기 위한 현재 터미널용 정의이며 파일을 생성하지 않는다.

```bash
CODE_ROOT="$(pwd -P)"
LOCAL_STATE_DIR="$CODE_ROOT/artifacts/local-business/$NS"
RUNTIME_IMAGE_ID="$(uv run --frozen --python 3.12 python -B - "$NS" <<'PY'
import re
import subprocess
import sys
from scripts.local_business.config import state_path
from scripts.local_business.setup import owned
from scripts.local_business.stack import no_active_driver

config = owned(state_path(sys.argv[1]))
no_active_driver(config)
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
image = config["images"]["runtime"]
if (config["namespace"] != sys.argv[1] or config["source_sha"] != head
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)):
    raise SystemExit("LOCAL_PARTNER_SOURCE_OR_IMAGE_MISMATCH")
print("source_sha=" + head + " runtime_image=" + image, file=sys.stderr)
print(image)
PY
)"
local_partner_seed() {
  test -n "$RUNTIME_IMAGE_ID" || return 1
  docker run --rm --pull=never \
    --network "container:${NS}-pg" --user "$(id -u):$(id -g)" \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m --workdir /tmp \
    --mount "type=bind,src=$LOCAL_STATE_DIR/source,dst=/workspace,readonly" \
    --entrypoint /usr/bin/env "$RUNTIME_IMAGE_ID" -i \
    PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin HOME=/tmp \
    PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1 ENVIRONMENT=local \
    DATABASE_URL=postgresql://tft_app@127.0.0.1:5432/team4 \
    python -m scripts.rfp_seed_partner_profiles "$@"
}
local_partner_seed --dry-run
```

dry-run은 DB에 접속하지 않는다. 12곳의 `PLANNED`·synthetic 요약과 종료코드 0을 확인한다. 키·비밀번호·원자료를 마운트하지 않으며 snapshot source는 읽기 전용이다. 다음 명령은 confirm이 없으므로 **`TARGET` 출력 후 DB 접속 전에 예상 종료코드 1로 거부**되어야 한다. `--check`에서는 TARGET이 출력되지 않는다.

```bash
local_partner_seed --apply
```

`TARGET host=127.0.0.1 port=5432 dbname=team4`와 `TFT_SEED_TARGET_MISMATCH`, 종료코드 1을 확인한다. 다른 오류가 나면 중단한다. 이 거부는 적재 실패나 성공을 의미하지 않는다. host/port/dbname 외에도 위에서 확인한 namespace·source SHA·runtime image가 자기 신규 DB의 것인지 대조한다. 모두 맞을 때만 다음 단계에서 정확한 토큰을 명시한다.

```bash
local_partner_seed --apply --confirm-target 'host=127.0.0.1 port=5432 dbname=team4'
local_partner_seed --check
```

명시 적용은 없는 판본만 추가하고 기존 판본을 덮어쓰지 않는다. 두 명령을 각각 확인하여 12곳 모두 `SAME`, `MISSING=0`, `DIFFERENT=0`, 종료코드 0인지 확인한다. 새 DB에서 `inserted=12`가 예상되지만 실제 출력으로 확인해야 하며 재실행은 0일 수 있다. `--check`는 읽기 전용 DB 대조다. 차이·누락·권한 오류가 있으면 멈추고 담당자에게 보고하며 임의 판본 변경·DB reset으로 우회하지 않는다. 여기까지는 모델 호출·업무 결과 seed 없이 입력 프로필만 준비하는 단계다.

### 실제 모델 작업 허용

real 활성화는 실제 유료 모델 요청을 허용한다. 본인의 비공개 키 파일(타 사용자 접근 불가, 4,096바이트 이하)과 사용 모델의 USD 단가 파일을 실행 담당과 확인한다. 키를 환경 덤프·명령 인수·브라우저에 붙이지 않는다. `--key-file`에는 값이 아닌 파일 경로를 지정한다. 단가 파일은 공통 `ModelPrice` 형식의 입력/출력/캐시 단가·통화·기준일을 갖춰야 하며, 미확인 모델 가격을 만들지 않는다.

아래는 **C/S 한 작업, 최대 8회 호출의 명시 허용 예시**다. 모델 ID·가격과 예상 호출 범위를 확인한 뒤 한 owner만 선택한다. RFP 작업은 같은 명령의 `--owner tft`를 사용한다. 호출 허용량이 업무 전체에 충분한지는 별도 확인 대상이다.

```bash
uv run --frozen --python 3.12 python -m scripts.local_business enable-real \
  --namespace "$NS" --owner cs \
  --key-file '/absolute/path/to/private/key-file' \
  --prices-file '/absolute/path/to/confirmed/prices.json' \
  --model '<가격이 확인된 모델 ID>' \
  --max-jobs 1 --max-calls 8 --reserve-usd 0.15 --budget-usd 1.50
```

작업 수는 1~20, 호출 수는 1~100이며, 금액은 `0 < reserve <= budget < 3.00`이어야 한다. 예약은 **호출 허용 제한이며 공급자 USD hard cap이 아니다.** 응답 사용량 누락·SDK 실패·예약 초과는 예약을 유지하거나 불명 상태로 남기고 추가 호출을 차단한다. 정상 응답 비용도 공급자 최종 청구 확정으로 취급하지 않는다.

위 범위와 예약 초과 차단은 기존 **제한 모드**다. 이미 지원 snapshot으로 설치된 namespace에서 작업·호출·도구 비용 cap 없이 QA하려면 다음처럼 명시한다. 기존 구 snapshot에는 적용되지 않으며 새 namespace 준비 안내는 문서 앞부분을 따른다.

```bash
uv run --frozen --python 3.12 python -m scripts.local_business enable-real \
  --namespace "$NS" --owner cs \
  --key-file '/absolute/path/to/private/key-file' \
  --prices-file '/absolute/path/to/confirmed/prices.json' \
  --model '<가격이 확인된 모델 ID>' --unlimited
```

`--owner tft`도 같은 계약이다. 정상 비용은 제한 없이 기록하되 UNKNOWN/미정산은 그대로 차단한다. 종료 명령은 다음과 같다. 이는 해당 owner의 로컬 driver를 끄며 업무 DB·원장·결과를 초기화하지 않는다.

```bash
uv run --frozen --python 3.12 python -m scripts.local_business disable-real --namespace "$NS"
```

활성화 후 UI에서 새 C/S 분석 또는 RFP 처리 요청을 만든다. dispatcher는 활성화 시각 이후의 해당 owner 신규 작업만 고르고, 정규 worker의 lease·DB 결과를 사용한다. 예전 QUEUED/RUNNING을 자동 재전송하지 않는다. 제한 모드에서는 허용한 작업 수를 쓰면 종료하며 다시 활성화하려면 상태·비용을 먼저 확인한다. unlimited는 사용자가 끄기 전까지 다음 신규 작업을 기다리되 실패·UNKNOWN·worker 중단 경계에서는 보존 후 중단한다. 활성화 전에는 모델 작업 요청이 `LOCAL_REAL_DRIVER_DISABLED`로 거부된다. 실제 업무 생성 결과만 확인하고 사용자 사전 시연 결과를 넣지 않는다.

## 4. 중지·재시작과 미완료 작업

real 처리를 먼저 비활성화한 후 앱을 중지한다. 실행 중 요청을 중단할 수 있으므로 원격 처리·비용·lease가 정리됐다고 가정하지 않는다.

```bash
uv run --frozen --python 3.12 python -m scripts.local_business disable-real --namespace "$NS"
uv run --frozen --python 3.12 python -m scripts.local_business status --namespace "$NS"
uv run --frozen --python 3.12 python -m scripts.local_business stop --namespace "$NS"
```

stop은 앱과 로컬 tunnel을 중지하고 **DB 컨테이너·볼륨·업로드 파일·비용 기록은 보존**한다. start/stop/restart는 해당 namespace/owner의 active driver가 있으면 거부하므로 먼저 disable-real을 수행한다. start/restart는 real 허용을 자동 복구하지 않는다. 종료된 작업만 있고 담당자의 확인이 끝났을 때 다음 중 하나를 사용한다.

```bash
uv run --frozen --python 3.12 python -m scripts.local_business start --namespace "$NS"
uv run --frozen --python 3.12 python -m scripts.local_business restart --namespace "$NS"
```

고아 QUEUED/RUNNING, 살아 있는 lease, 중단된 driver, 비용 RESERVED/UNKNOWN이 있으면 **enable-real·재시작을 중단하고 담당자에게 보고**한다. UI 재요청·setup 재실행·DB/permit 행 삭제·토큰 초기화로 우회하지 않는다. 현재 비용 journal은 미정산 예약을 발견하면 `LOCAL_COST_UNKNOWN_REVIEW_REQUIRED` 등으로 추가 허용을 거부한다. 고아 job의 일괄 자동 복구 CLI는 없다. 정규 stale lease 복구도 살아 있는 holder를 지우지 않는 기존 절차에 따라 담당자가 판단한다.

**남은 운영 한계(P3):** POST 허용 확인은 원자적으로 예약하는 슬롯이 아니다. UI 작업은 한 번에 하나씩 요청하고 종료·허용 상태를 확인한 후 다음 작업을 보낸다. stop 도중 오류가 나면 전체 종료로 간주하지 말고 status와 실제 컨테이너·tunnel·lease·비용의 잔류 상태를 확인해 담당자에게 보고한다. 모든 고아 job의 start/restart 자동 거부나 복구 전용 CLI는 없다. usage·비용이 불명인 호출은 추가 허용 차단을 유지하며 disable-real·앱 재시작으로 자동 정산하거나 예약을 지우지 않는다. source46의 한정 실증 이후에도 최신 통합 전체 E2E와 각 PC의 재현은 별도 확인 대상이다.

status는 컨테이너와 실제 DB의 QUEUED/RUNNING/FAILED 상태·실패 코드·lease 유효 여부를 읽는다. `settings.json`의 코드 SHA와 실제 소비 이미지를 함께 기록한다. 로그는 `artifacts/local-business/$NS/logs/`, driver 결과·worker 로그는 `control/`에 있다. 자격 파일·원문·키·전체 환경은 공유하지 말고 고정 오류 코드·시각·job ID·코드 SHA만 전달한다. 이번 문서 정정·dev 정합 작업에서는 setup/start/적재/모델/DB를 추가 실행하지 않았다. 앞선 source46 실증, 최신 코드 정합, 각 PC의 재현과 전체 업무·운영 완료를 각각 구분해 기록한다.

### 각 PC의 한국어 요약 QA (KAN-159)

요약 bridge를 포함한 새 코드로 준비한 namespace에서는 문의 상세의 한국어 요약을 사용할 수 있다. 저장본 조회와 같은 원문·프롬프트의 재사용(POST 200)은 CS driver가 꺼져 있어도 가능하다. 새 요약 생성에는 **CS `enable-real` driver**가 필요하다. RFP driver만 켠 상태에서는 생성할 수 없다. 제한 모드에서는 요약 한 번도 분류와 같은 작업 1개·모델 호출 1회로 합산하며, 명시한 `--unlimited` 모드에서도 사용량·비용 기록과 불명 비용 차단은 유지된다.

새 snapshot을 포함한 코드를 받은 뒤, 기존에 사용하지 않은 namespace와 해당 PC의 빈 포트로 실행한다. 개인 키 경로는 각자 소유한 파일로 바꾼다.

```sh
uv run --frozen --python 3.12 python -m scripts.local_business.bootstrap \
  --namespace team4-kan159-summary-qa --port 42443 --enable-model cs \
  --key-file "$HOME/.config/team4/gemini-api-key" --unlimited
```

기존 namespace는 설치 시 보관한 source를 사용한다. `git pull`이나 `restart`만으로 요약 bridge가 추가되지 않는다. 기존 DB·업로드·결과·비용 원장을 보존하고 새 namespace를 준비한다. 기존 40443 환경이나 별도 RFP 실험판을 교체하는 명령이 아니다.

모델 키는 driver에만 제공된다. API는 내부 Docker망의 인증된 요약 bridge에 마스킹된 입력을 보내며, 표준 요약 API의 권한·입력/출력 검사·저장 계약을 그대로 적용한다. 분류 실행 중에는 새 요약 요청이 503 `LOCAL_SUMMARY_UNAVAILABLE`로 거절될 수 있으므로 진행 중인 작업이 끝난 뒤 다시 누른다. 같은 성공 요약을 다시 만들 때는 모델을 재호출하지 않는다. HTTP나 모델 실패에 자동 재전송하지 않으며 비용이 불명확하면 driver를 중지하고 원장을 대조한다.

기존 업무 중계의 응답 대기 제한은 20초다. 긴 모델 호출은 화면에서 먼저 실패로 보이더라도 서버에서 완료·저장될 수 있으므로 새로고침하여 저장본을 먼저 확인한다. fake SDK·로컬 HTTP 회귀 통과는 실제 모델의 한국어 요약 품질이나 업무 QA 완료를 뜻하지 않는다. 요약 시도에는 driver가 선택한 요청 모델과 사용량을 기록하며, 로컬 비용 원장의 정산에는 driver에 명시한 가격표를 사용한다.
