import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { me, logoutSession } from '../api/platform';
import { sessionCsrfToken } from '../api/client';
import { ErrorState, Loading } from '../components/states';
import PushToggle from '../components/PushToggle';
import { clearPush } from '../notifications/push';
import MailboxConnections from '../components/MailboxConnections';

export default function Account() {
  return <AccountPanel operational={!import.meta.env.DEV} />;
}

export function AccountPanel({ operational }: { operational: boolean }) {
  const cache = useQueryClient();
  const user = useQuery({ queryKey: ['me'], queryFn: me, retry: false });
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [signedOut, setSignedOut] = useState(false);
  const identity = user.isSuccess ? user.data?.data : null;
  async function signOut() {
    if (busy || !operational || !identity || !sessionCsrfToken()) return;
    setBusy(true); setError(null);
    try {
      try { await clearPush(identity.id); } catch { setError(new Error('OS 알림 구독 정리를 확인하지 못했습니다. 로그아웃 후 브라우저 사이트 알림 설정을 확인하세요.')); }
      const current = await me();
      if (current?.data.id !== identity.id) throw new Error('계정이 변경되어 로그아웃을 중단했습니다. 현재 계정을 다시 확인하세요.');
      await logoutSession();
      // Clear all prior user's business data only after confirmed server logout.
      await cache.cancelQueries();
      cache.removeQueries({ predicate: query => query.queryKey[0] !== 'me' });
      cache.setQueryData(['me'], null);
      setSignedOut(true); setConfirming(false);
    } catch (cause) {
      setError(cause);
      await user.refetch();
    } finally { setBusy(false); }
  }
  return <main>
    <header><p className="eyebrow">TEAM4 / ACCOUNT</p><h1>로그인과 계정</h1></header>
    {!operational && <p role="note" className="notice">개발 모드입니다. 시연 계정은 Microsoft 로그인이나 사서함 연결을 확인한 계정이 아닙니다. 이 화면에서 운영 로그인·메일 연결을 실행할 수 없습니다.</p>}
    {user.isPending && <Loading label="세션 확인 중" />}
    {user.isError && <ErrorState error={user.error} onRetry={() => user.refetch()} />}
    {error != null && <ErrorState error={error} />}
    {signedOut && <p role="status">로그아웃했습니다.</p>}
    {user.isSuccess && !identity && <section><h2>{signedOut ? '다시 로그인' : '로그인 필요'}</h2><p>세션이 없거나 만료되었습니다. Microsoft 계정으로 다시 로그인하세요.</p>
      {operational && <a className="button" href="/api/v1/platform/auth/login">Microsoft로 로그인</a>}
    </section>}
    {identity && <section><h2>현재 계정</h2><dl><dt>이름</dt><dd>{identity.display_name}</dd><dt>이메일</dt><dd>{identity.email}</dd><dt>역할</dt><dd>{identity.roles.join(', ')}</dd><dt>부서</dt><dd>{identity.dept ?? '해당 없음'}</dd></dl>
      {operational && <>{!sessionCsrfToken() && <p role="alert">세션 확인 정보가 없어 로그아웃할 수 없습니다. 다시 확인하세요.</p>}
        <button disabled={busy || user.isFetching} onClick={() => user.refetch()}>세션 다시 확인</button>
        {!confirming ? <button disabled={busy || !sessionCsrfToken()} onClick={() => setConfirming(true)}>로그아웃</button> : <div><p>현재 계정에서 로그아웃할까요?</p><button disabled={busy || !sessionCsrfToken()} onClick={() => void signOut()}>{busy ? '로그아웃 중' : '로그아웃 확인'}</button><button disabled={busy} onClick={() => setConfirming(false)}>취소</button></div>}
      </>}
    </section>}
    {identity && <PushToggle key={identity.id} actorId={identity.id} />}
    <section><h2>메일 연결</h2><p>서비스 로그인과 메일 연결은 별도입니다.</p>{operational && identity && <MailboxConnections />}</section>
  </main>;
}
