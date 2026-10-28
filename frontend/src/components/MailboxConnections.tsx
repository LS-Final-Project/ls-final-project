import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { disconnectMailbox, listMailboxes, startMailboxLink } from '../api/mailbox';
import { sessionCsrfToken } from '../api/client';
import { ErrorState, Loading } from './states';

const STATES = { connected: '연결됨', disconnected: '해제됨' } as const;
// 재연결 필요 사유별 안내. 공급자 오류 이름 그대로 받는다.
const REASONS: Record<string, string> = {
  invalid_grant: 'Microsoft가 저장된 연결 승인을 더 이상 받지 않습니다.',
  interaction_required: 'Microsoft 로그인 확인이 다시 필요합니다.',
  login_required: 'Microsoft 로그인 확인이 다시 필요합니다.',
  consent_required: '메일 읽기 동의가 다시 필요합니다.',
  token_missing: '저장된 연결 정보를 쓸 수 없습니다.',
};

/**
 * 현재 세션 소유자의 사서함 연결 상태와 연결·재연결·해제 버튼만 보여준다.
 * Args: navigate는 Microsoft 동의 주소로 이동하는 함수다. 검사에서만 바꾼다.
 * Returns: 계정 화면의 메일 연결 구역 내용.
 */
export default function MailboxConnections({ navigate = (url: string) => window.location.assign(url) }: { navigate?: (url: string) => void }) {
  const list = useQuery({ queryKey: ['mailboxes'], queryFn: listMailboxes, retry: false });
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [disconnected, setDisconnected] = useState(false);
  const csrf = sessionCsrfToken();
  async function run(action: () => Promise<void>) {
    if (busy || !csrf) return;
    setBusy(true); setError(null); setDisconnected(false);
    try { await action(); }
    catch (cause) { setError(cause); }
    finally { setBusy(false); }
  }
  const connect = () => run(async () => { navigate(await startMailboxLink()); });
  const disconnect = (id: string) => run(async () => { await disconnectMailbox(id); setConfirming(null); setDisconnected(true); await list.refetch(); });
  if (list.isPending) return <Loading label="사서함 연결 확인 중" />;
  // 이미 받은 목록이 있으면 재조회 실패에도 표를 유지한다. 처음부터 못 받았을 때만 전체 오류다.
  if (list.data === undefined) return <ErrorState error={list.error} onRetry={() => list.refetch()} />;
  if (list.data === null) return <p>사서함 연결이 이 배포에서 활성화되지 않았습니다.</p>;
  return <div>
    {disconnected && <p role="status">사서함 연결을 해제했습니다.</p>}
    {list.isError && <p role="alert">사서함 연결 목록을 다시 불러오지 못했습니다. <button onClick={() => list.refetch()}>다시 불러오기</button></p>}
    {error != null && <ErrorState error={error} />}
    {!csrf && <p>세션 확인 정보가 없어 사서함 연결을 바꿀 수 없습니다. 세션을 다시 확인하세요.</p>}
    {list.data.length === 0 ? <p>연결된 사서함이 없습니다.</p> : <table>
      <thead><tr><th>연결 ID</th><th>상태</th><th>개정</th><th>변경 시각</th><th>작업</th></tr></thead>
      <tbody>{list.data.map(item => <tr key={item.mailbox_id}>
        <td>{item.mailbox_id}</td>
        <td>{item.state === 'connected' && item.reconnect_required
          ? <>재연결 필요<p>재연결이 필요합니다. {REASONS[item.reconnect_reason ?? ''] ?? ''} Microsoft에서 동의를 복구해도 여기서 다시 연결해야 메일을 가져옵니다.</p></>
          : STATES[item.state]}</td>
        <td>{item.revision}</td><td>{item.updated_at}</td>
        <td>{item.state === 'connected' && item.reconnect_required && <button disabled={busy || !csrf} onClick={() => void connect()}>재연결</button>}
        {item.state !== 'connected' ? '-' : confirming !== item.mailbox_id
          ? <button disabled={busy || !csrf} onClick={() => setConfirming(item.mailbox_id)}>해제</button>
          : <><span>이 사서함 연결을 해제할까요?</span><button disabled={busy || !csrf} onClick={() => void disconnect(item.mailbox_id)}>{busy ? '해제 중' : '해제 확인'}</button><button disabled={busy} onClick={() => setConfirming(null)}>취소</button></>}
        </td>
      </tr>)}</tbody>
    </table>}
    <button disabled={busy || !csrf} onClick={() => void connect()}>사서함 연결</button>
  </div>;
}
