import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode, sessionCsrfToken } from '../api/client';
import { listMailboxes, startMailboxLink, type MailboxConnection } from '../api/mailbox';
import { ErrorState, Loading } from '../components/states';
import MailGate from './MailGate';
import { KvBox, KvField, KvGrid, KvKey, KvMore, KvNext, KvSplit, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { formatStamp } from '../ls-shell/cs-labels';

// 재연결 필요 사유. 공급자 오류 이름 그대로 받는다.
const REASONS: Record<string, string> = {
  invalid_grant: 'Microsoft가 저장된 연결 승인을 더 이상 받지 않음',
  interaction_required: 'Microsoft 로그인 확인이 다시 필요함',
  login_required: 'Microsoft 로그인 확인이 다시 필요함',
  consent_required: '메일 읽기 동의가 다시 필요함',
  token_missing: '저장된 연결 정보를 쓸 수 없음',
};
const needsReconnect = (item: MailboxConnection) => item.state === 'connected' && item.reconnect_required;

// 메일 계정 MB0103. 내 사서함 연결 상태와 다시 연결을 보인다.
// 서버가 사서함 주소와 가져오기·분류 기록을 주지 않아 그 부분은 표시하지 않는다.
export default function MailAccount({ navigate = (url: string) => window.location.assign(url) }: { navigate?: (url: string) => void }) {
  return <MailGate title="메일 계정"><AccountView navigate={navigate} /></MailGate>;
}

function AccountView({ navigate }: { navigate: (url: string) => void }) {
  const mode = getApiMode();
  const list = useQuery({ queryKey: ['mailboxes', mode], queryFn: listMailboxes, retry: false, staleTime: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const csrf = sessionCsrfToken();
  const fetchAgain = () => { void list.refetch(); };
  useHotkey('F8', fetchAgain, !list.isFetching);

  const items = list.data ?? [];
  const broken = items.filter(needsReconnect);
  const reconnect = async () => {
    if (busy || !csrf) return;
    setBusy(true); setError(null);
    try { navigate(await startMailboxLink()); }
    catch (cause) { setError(cause); }
    finally { setBusy(false); }
  };

  const columns: KvColumn<MailboxConnection>[] = [
    { key: 'account', header: '메일 계정', width: 280, headClass: '', cell: item => item.mailbox_id },
    { key: 'state', header: '상태', width: 90, className: 'c', cell: item => item.state === 'disconnected' ? '해제됨' : needsReconnect(item) ? <span className="unc">끊김</span> : '연결됨' },
    { key: 'reconnect', header: '다시 연결', width: 80, className: 'c', cell: item => needsReconnect(item) ? '필요' : '' },
    { key: 'reason', header: '사유', headClass: '', cell: item => needsReconnect(item) ? REASONS[item.reconnect_reason ?? ''] ?? '' : '' },
    { key: 'at', header: '마지막 변경', width: 150, className: 'd', headClass: '', cell: item => formatStamp(item.updated_at) },
  ];

  return <main>
    <KvTitle path="메일" title="메일 계정" code="MB0103">
      <button className="kb primary" disabled={list.isFetching} onClick={fetchAgain}>조회<KvKey>F8</KvKey></button>
      <button className="kb" disabled={busy || !csrf || broken.length === 0} onClick={() => void reconnect()}>다시 연결</button>
      <KvMore items={[{ label: '지금 가져오기', note: '손으로 당기는 버튼은 없음. 5분마다 자동' }, { label: '계정 추가', note: '관리자가 등록' }, { label: '계정 삭제', note: '관리자가 처리' }]} />
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 계약 예시이며 실제 계정이 아닙니다.</div>}
    {list.isSuccess && list.data === null && <div className="kv-sub" style={{ margin: '6px 14px 0' }}>사서함 연결이 이 배포에서 활성화되지 않았습니다.</div>}
    {list.isSuccess && list.data !== null && <KvNext>
      내 계정 <b>{items.length}</b>건, 다시 연결 필요 <b>{broken.length}</b>. 받기와 보내기는 5분마다 자동이고, 연결이 끊기면 그 계정은 건너뜁니다. 다시 연결은 Microsoft 로그인 창에서 합니다
    </KvNext>}
    {!csrf && list.isSuccess && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>세션 확인 정보가 없어 다시 연결할 수 없습니다. 세션을 다시 확인하세요.</div>}
    {list.isFetching && <Loading label="사서함 연결 확인 중" />}
    {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
    {error != null && <ErrorState error={error instanceof ApiError || error instanceof Error ? error : new Error('다시 연결을 시작하지 못했습니다.')} />}
    {!list.isFetching && list.isSuccess && list.data !== null && <>
      <div className="kv-gridhead"><span>메일 계정 {items.length}건</span></div>
      <KvGrid label="메일 계정" columns={columns} rows={items} rowKey={item => item.mailbox_id} fallback="연결된 사서함이 없습니다." />
      <div style={{ margin: '8px 14px 0' }}>
        <KvSplit columns="1fr 1fr">
          <KvBox title="가져오기와 분류" note="시스템 기록">
            <KvField label="받은 메일 가져오기"><span className="unc">마지막 가져온 기록을 주는 API가 아직 없음</span></KvField>
            <KvField label="보낸 편지함 확인"><span className="unc">기록을 주는 API가 아직 없음</span></KvField>
            <div className="kv-sub" style={{ margin: '8px 0 0' }}>가져온 메일은 받은 메일 검토에, 보낸 메일은 발송 상태에 나타납니다</div>
          </KvBox>
          <KvBox title="다시 연결">
            {broken.length === 0 ? <div className="kv-sub" style={{ margin: 0 }}>다시 연결이 필요한 계정이 없습니다</div> : <>
              <KvField label="계정">{broken[0].mailbox_id}</KvField>
              <KvField label="사유">{REASONS[broken[0].reconnect_reason ?? ''] ?? '사유 미확인'}</KvField>
              <div className="kv-sub" style={{ margin: '8px 0 0' }}>Microsoft 로그인 창이 열립니다. 로그인하면 멈춘 발송이 자동으로 이어집니다</div>
              <div className="kv-actions"><button className="kb primary" disabled={busy || !csrf} onClick={() => void reconnect()}>{busy ? '여는 중' : '다시 연결'}</button></div>
            </>}
          </KvBox>
        </KvSplit>
      </div>
    </>}
  </main>;
}
