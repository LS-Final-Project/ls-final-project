# 프론트 실행 경계

로컬 개발은 저장소 `docs/team-start.md`를 따른다. `npm test --prefix frontend`와
`npm run build --prefix frontend`가 서버/UI 회귀, 생성 계약 타입, TypeScript와 Vite 빌드를 검사한다.
별도 lint 스크립트는 없다.

## 운영 계정과 읽기 UI

운영 빌드는 `/account`에서 고정 Microsoft 로그인 경로와 세션 확인·로그아웃을 제공한다.
로그아웃은 `/me`의 세션 CSRF 헤더가 있을 때만 요청하며 성공 후 업무 캐시를 제거한다.
개발 모드 계정은 Microsoft 인증 또는 사서함 연결의 증거로 표시하지 않는다.
메일 연결·만료·재연결 상태는 플랫폼 메일 계약이 제공되기 전까지 미확인이다.

업무 UI는 빌드 시 선택한다. 직접 npm 빌드할 때는
`VITE_BUSINESS_UI_ENABLED=true npm run build --prefix frontend`로 활성화한다.
Docker 빌드는 `BUSINESS_UI_ENABLED=false`가 기본이다. 정상 Cloud Build 웹 빌드는
`--build-arg BUSINESS_UI_ENABLED=true`를 명시하며, Dockerfile은 시험이 통과한 뒤
`npm run build` 명령에만 `VITE_BUSINESS_UI_ENABLED`를 전달한다. 이 값은 번들에 고정되므로
Cloud Run의 런타임 환경 변수로 바뀌지 않는다. 개발 인증을 켜는 설정도 아니다.
서버의 기존 `BUSINESS_RELAY_ENABLED`,
플랫폼 인증·중계 설정과 서비스 권한도 별도로 준비되어야 한다. 이 문서는 배포 승인이나
인증 설정 변경을 대신하지 않는다.

활성화 범위는 `frontend/server.mjs`의 현재 업무 중계 허용 경로다.
운영 live 모드에서는 조회뿐 아니라 허용된 C/S·메일·TFT 쓰기도 실제 API로 요청한다.
세션 확인 전에는 업무 API를 호출하지 않으며, 쓰기에는 실제 세션·역할·CSRF와 서버의
권한·입력 검증이 적용된다. UI 활성화만으로 메일 발송이나 비동기 작업의 백엔드 활성화가
보장되지는 않는다.
새로고침 실패 시 이전 응답을 현재 성공 데이터로 표시하지 않는다.
`최신 상태 조회`는 수동 재조회이며 SSE 재연결·폴백 구현을 뜻하지 않는다.

구성안 검토는 KAN-125의 검증 보고와 KAN-127의 불변 판본/결정 API를 소비한다.
과거 판본은 현재 검증 결과와 구분하고, 최종 결정은 현재 책임자에게만 제공한다.
새 구성안 작성은 모든 역할에 후보 또는 공백 사유를 기록하고 이전 판본을 보존한다. 책임자 지정과 운영 쓰기 경로 연결은 아직 포함되지 않는다. 전체 업무 완료로 보지 않는다.
