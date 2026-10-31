import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getApiMode, setApiMode, request, type ApiMode } from '../api/client';
import { listTickets } from '../api/cs';
import { listProjects } from '../api/tft';
import { clearPush } from '../notifications/push';
import { listAccounts, me, selectAccount, logout } from '../api/platform';

export default function DevCheck() {
  const cache = useQueryClient();
  const [mode, changeMode] = useState<ApiMode>(getApiMode());
  const accounts = useQuery({queryKey: ['accounts'], queryFn: listAccounts, enabled: import.meta.env.DEV});
  const user = useQuery({queryKey: ['me'], queryFn: me, enabled: import.meta.env.DEV});
  const readiness = useQuery({queryKey: ['readiness'], queryFn: () => Promise.all((import.meta.env.DEV ? ['cs','tft','platform'] : ['platform']).map(service => request(`/api/v1/${service}/ready`)))});
  const tickets = useQuery({queryKey: ['tickets',mode], queryFn: () => listTickets(), enabled: import.meta.env.DEV});
  const projects = useQuery({queryKey: ['projects',mode], queryFn: () => listProjects(), enabled: import.meta.env.DEV});
  const account = useMutation({mutationFn: async (id: string) => { await clearPush(); return selectAccount(id); }, onSuccess: () => cache.invalidateQueries({queryKey: ['me']})});
  const signout = useMutation({mutationFn: async () => { await clearPush(); return logout(); }, onSuccess: () => cache.invalidateQueries({queryKey: ['me']})});
  return <main>
    <header><p className="eyebrow">TEAM4 / {import.meta.env.DEV ? 'LOCAL DEVELOPMENT' : 'SHARED ENVIRONMENT'}</p><h1>개발 환경 확인</h1><p>{import.meta.env.DEV ? 'C/S, TFT와 공통 플랫폼의 연결을 확인하고 기능 개발을 시작하세요.' : '공용 웹에서 플랫폼과 데이터베이스의 연결을 확인합니다. 업무 화면과 실제 로그인은 준비 중입니다.'}</p></header>
    {import.meta.env.DEV && <div className="notice">로컬 시연 계정은 DB에서 조회합니다. {mode === 'mock' ? 'C/S와 TFT 목록은 합성한 모의 응답입니다.' : '실제 API를 호출합니다. 미구현 목록 API는 501을 반환합니다.'}</div>}
    <section><h2>서비스 연결</h2><p role="status">{readiness.isPending ? 'DB와 확장 확인 중' : readiness.isError ? '연결 확인 필요: 관리자에게 서비스 상태를 확인해 주세요.' : import.meta.env.DEV ? 'C/S, TFT, 플랫폼의 DB 연결과 pgvector, FTS 확인 완료' : '플랫폼 DB 연결과 pgvector, FTS 확인 완료'}</p><button onClick={() => readiness.refetch()}>다시 확인</button></section>
    {import.meta.env.DEV && <section><h2>시연 계정 선택</h2>
      <p>실제 Microsoft 로그인을 대신하는 로컬 개발 기능입니다.</p>
      <div className="accounts">{accounts.data?.data.items.map(item => <button key={item.id} disabled={account.isPending} onClick={() => account.mutate(item.id)}>{item.display_name}</button>)}</div>
      {accounts.isError && <p className="error">계정 조회 실패: 플랫폼과 로컬 DB를 먼저 실행하세요.</p>}
      {(account.isError || signout.isError) && <p className="error">계정 변경에 실패했습니다. 플랫폼 로그를 확인하세요.</p>}
      <p data-testid="current-user">현재 계정: {user.data ? `${user.data.data.display_name} (${user.data.data.roles.join(', ')})` : '선택 전'}</p>
      <button onClick={() => signout.mutate()}>선택 해제</button>
    </section>}
    {import.meta.env.DEV && <section><div className="section-title"><h2>업무 API 연결 예시</h2><select aria-label="API 모드" value={mode} onChange={e => {const next = e.target.value as ApiMode; setApiMode(next); changeMode(next);}}><option value="mock">모의 응답</option><option value="live">실제 API</option></select></div>
      <div className="columns"><article><h3>C/S 문의</h3>{tickets.data?.data.items.map(item => <p key={item.id}>{item.subject} <small>{item.data_origin}</small></p>)}{tickets.isError && <p className="error">{tickets.error.message}</p>}</article><article><h3>TFT 프로젝트</h3>{projects.data?.data.items.map(item => <p key={item.id}>{item.name} <small>{item.data_origin}</small></p>)}{projects.isError && <p className="error">{projects.error.message}</p>}</article></div>
    </section>}
    <footer><a href="https://github.com/LS-Final-Project/ls-final-project/blob/dev/docs/team-start.md">팀원 실행 안내</a> / <a href="https://github.com/LS-Final-Project/ls-final-project/blob/dev/contracts/README.md">API 계약 수정 안내</a></footer>
  </main>;
}
