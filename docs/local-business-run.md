# 각 팀원 PC의 로컬 업무 실행 (KAN-159)

각자 받은 자료와 같은 코드를 사용해 신규 C/S는 UI 등록, RFP는 UI 업로드 후 실제 처리한다. **현재 실행도구는 통합 검증 중이다. 이 안내는 설치·실기동·실모델 처리 성공 기록이 아니다.** 코드와 명령의 최종 대조는 실행도구 담당(coder2)이 통합할 때 수행한다.

명령은 coder2의 `scripts/local_business/{__main__,setup,stack,dispatcher}.py` 작업본을 정적으로 대조했다(당시 HEAD `5389de23baa3cd108b1d6700ca0d7d8584791e1b`, 미커밋 CLI 포함). 이 문서 단독 브랜치에는 실행도구가 없으므로, **runner가 통합된 코드**에서 실행해야 한다. 기존 [데이터 준비 안내](local-business-data.md)와 공통 해시 기준은 PR [#555](https://github.com/LS-Final-Project/ls-final-project/pull/555)에 있다.

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

setup 시 코드 SHA·archive hash·이미지 ID·frontend 산출물 hash를 `artifacts/local-business/$NS/settings.json`에 기록한다. 이후 start/restart는 그때 보관한 source/dist를 사용하므로 저장소 pull만으로 기존 namespace의 실행 코드가 바뀌지는 않는다. 코드 갱신이 필요하면 담당자와 별도 새 namespace를 준비하고 이전 DB를 보존한다.

브라우저의 `/api/v1/platform/auth/login`에서 로컬 fixture의 합성 계정 선택 form으로 이동한다. C/S 담당자·변압기 기술지원·수주 담당자·관리자 4개에 대응하며 제품 세션·역할·CSRF 경계를 이용한다. **Microsoft 로그인·Graph·실제 사서함 검증은 아니다.** chooser/웹 연결은 현재 통합 확인 대상으로, 실제 선택 화면 성공을 이 문서에서 보장하지 않는다. 메일 인증·수집·발송, 평가 실행·푸시·자동 sweep은 비활성으로 설정한다. 실제 Microsoft 계정·토큰을 넣어 이 fixture를 대체하지 않는다.

## 3. 자료 준비 후 필요한 작업만 real 활성화

[데이터 안내](local-business-data.md)에 따라 필수 검색 문서 107개+doc_index의 hash를 대조한다. ref CSV 2종은 선택이다. 자사 23종은 커밋 파생 파일, 협력사는 기본 tracked 합성 프로필을 사용한다. 문의 4,000건·사례 800건 전량 적재나 generated demo-en/smoke 자료는 기본 선행이 아니다.

C/S real 전에는 **해당 namespace DB에 실제 적재된** `execute/chunking-v3` 검색 manifest와 필수 `POL-ORDER/RETURN/SERVICE/WARRANTY` 매핑을 준비한다. dry-run manifest로 대신하지 않는다. 아래는 새 기능이 아닌 기존 `scripts.cs_index_docs --execute`의 **명시적 로컬 DB 적재** 명령이다. 해당 PC의 setup이 끝나고 원자료 hash 대조가 통과한 뒤, real driver가 꺼진 상태에서 실행한다. 실제 명령 검증·적재는 coder2의 새 disposable namespace에서 별도로 수행하며 문서 작성자는 실행하지 않았다.

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

활성화 후 UI에서 새 C/S 분석 또는 RFP 처리 요청을 만든다. dispatcher는 활성화 시각 이후의 해당 owner 신규 작업만 고르고, 정규 worker의 lease·DB 결과를 사용한다. 예전 QUEUED/RUNNING을 자동 재전송하지 않는다. 허용한 작업 수를 쓰면 종료하며 다시 활성화하려면 상태·비용을 먼저 확인한다. 활성화 전에는 모델 작업 요청이 `LOCAL_REAL_DRIVER_DISABLED`로 거부된다. 실제 업무 생성 결과만 확인하고 사용자 사전 시연 결과를 넣지 않는다.

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

**통합 WIP:** driver label/owner 확인과 status의 실제 driver 조회는 coder2 수정본에서 대조했다. 그러나 모든 고아 job을 start/restart에서 자동 거부하는 기능이나 복구 전용 CLI는 없다. 사용자가 status를 확인해 중단하고 담당자와 복구를 판단해야 한다. 비용 불명 차단은 추가 유료 허용에 대한 것으로 앱 재시작만으로 정산되거나 안전해지지 않는다. coder2의 최종 명령 대조·전체 통합 검증은 아직 미완료이며 설치 성공으로 표시하지 않는다.

status는 컨테이너와 실제 DB의 QUEUED/RUNNING/FAILED 상태·실패 코드·lease 유효 여부를 읽는다. `settings.json`의 코드 SHA와 실제 소비 이미지를 함께 기록한다. 로그는 `artifacts/local-business/$NS/logs/`, driver 결과·worker 로그는 `control/`에 있다. 자격 파일·원문·키·전체 환경은 공유하지 말고 고정 오류 코드·시각·job ID·코드 SHA만 전달한다. 본 문서 작성 중 setup/start/적재/모델/DB 실행은 없었으며, 설치 성공·전체 업무 완료·운영 배포를 주장하지 않는다.
