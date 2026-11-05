import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode, sessionCsrfToken } from '../api/client';
import { listMailboxes, startMailboxLink, type MailboxConnection } from '../api/mailbox';
import { ErrorState, Loading } from '../components/states';
import MailGate from './MailGate';
import { KvMore, useHotkey } from '../ls-shell/kv';
import { formatStamp } from '../ls-shell/cs-labels';
import './ticket-detail-v3.css';
import './mailbox-v3.css';

// 재연결 필요 사유. 공급자 오류 이름 그대로 받는다.
const REASONS: Record<string, string> = {
  invalid_grant: 'Microsoft가 저장된 연결 승인을 더 이상 받지 않음',
  interaction_required: 'Microsoft 로그인 확인이 다시 필요함',
  login_required: 'Microsoft 로그인 확인이 다시 필요함',
  consent_required: '메일 읽기 동의가 다시 필요함',
  token_missing: '저장된 연결 정보를 쓸 수 없음',
};
const MAIL_ICON = <svg viewBox="0 0 20 20"><path d="M3 5h14v10H3V5zm0 0 7 6 7-6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;
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

  const stateOf = (item: MailboxConnection) => item.state === 'disconnected' ? { label: '해제됨', tone: 'off' } : needsReconnect(item) ? { label: '끊김', tone: 'bad' } : { label: '연결됨', tone: 'ok' };
  const enabled = list.isSuccess && list.data !== null;

  return <main className="td3 ma3 ml3">
    <div className="title">
      <span className="path">메일 ›</span><h1>메일 계정</h1><code className="kv-code">MB0103</code>
      <div className="btns">
        <button className="kb3" disabled={busy || !csrf || broken.length === 0} onClick={() => void reconnect()}>다시 연결</button>
        <KvMore items={[{ label: '지금 가져오기', note: '손으로 당기는 버튼은 없음. 5분마다 자동' }, { label: '계정 추가', note: '관리자가 등록' }, { label: '계정 삭제', note: '관리자가 처리' }]} />
        <span className="vsep" />
        <button className="kb3 pri" disabled={list.isFetching} onClick={fetchAgain}>조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      {mode === 'mock' && <div className="tier-note" role="status">모의 응답 모드 — 계약 예시이며 실제 계정이 아닙니다.</div>}
      {list.isSuccess && list.data === null && <div className="ml-empty box"><b>사서함 연결이 이 배포에서 활성화되지 않았습니다.</b></div>}
      {!csrf && list.isSuccess && <div className="note err" role="status">세션 확인 정보가 없어 다시 연결할 수 없습니다. 세션을 다시 확인하세요.</div>}
      {list.isFetching && <Loading label="사서함 연결 확인 중" />}
      {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
      {error != null && <ErrorState error={error instanceof ApiError || error instanceof Error ? error : new Error('다시 연결을 시작하지 못했습니다.')} />}
      {!list.isFetching && enabled && <>
        <section className="pane" aria-label="연결 상태">
          <div className="ph">연결 상태
            <div className="r"><span className="meta">내 계정 <b>{items.length}</b>건, 다시 연결 필요 <b>{broken.length}</b>. 받기와 보내기는 5분마다 자동이고, 연결이 끊기면 그 계정은 건너뜁니다</span></div>
          </div>
          {items.length === 0 ? <div className="ml-empty"><span className="ic" aria-hidden="true">{MAIL_ICON}</span><b>연결된 사서함이 없습니다.</b></div>
            : <ul className="ma-cards" aria-label="메일 계정">{items.map(item => { const st = stateOf(item); return <li key={item.mailbox_id} className={`ma-card ${st.tone}`}>
              <div className="ma-top">
                <span className="ic" aria-hidden="true">{MAIL_ICON}</span>
                <div className="ma-id"><small>메일 계정</small><b title={item.mailbox_id}>{item.mailbox_id}</b></div>
                <span className={`ma-chip ${st.tone}`}><i aria-hidden="true" />{st.label}</span>
              </div>
              <dl>
                <dt>다시 연결</dt><dd>{needsReconnect(item) ? <span className="bad">필요</span> : <span className="unc">필요 없음</span>}</dd>
                <dt>사유</dt><dd>{needsReconnect(item) ? REASONS[item.reconnect_reason ?? ''] ?? '사유 미확인' : <span className="unc">-</span>}</dd>
                <dt>마지막 변경</dt><dd>{formatStamp(item.updated_at)}</dd>
              </dl>
            </li>; })}</ul>}
        </section>
        <div className="cols2">
          <section className="pane" aria-label="가져오기와 분류">
            <div className="ph">가져오기와 분류<div className="r"><span className="meta">시스템 기록</span></div></div>
            <dl className="ma-kv">
              <dt>받은 메일 가져오기</dt><dd><span className="unc">마지막 가져온 기록을 주는 API가 아직 없음</span></dd>
              <dt>보낸 편지함 확인</dt><dd><span className="unc">기록을 주는 API가 아직 없음</span></dd>
            </dl>
            <p className="fnote">연결이 필요한 고객 답장은 메일함에, 보낸 메일은 발송 상태에 나타납니다</p>
          </section>
          <section className={broken.length ? 'pane alert' : 'pane'} aria-label="다시 연결">
            <div className="ph">다시 연결</div>
            {broken.length === 0 ? <div className="ml-empty sm"><span className="ic ok" aria-hidden="true">✓</span><b>다시 연결이 필요한 계정이 없습니다</b></div> : <>
              <dl className="ma-kv">
                <dt>계정</dt><dd>{broken[0].mailbox_id}</dd>
                <dt>사유</dt><dd>{REASONS[broken[0].reconnect_reason ?? ''] ?? '사유 미확인'}</dd>
              </dl>
              <div className="ma-act">
                <small>Microsoft 로그인 창이 열립니다. 로그인하면 멈춘 발송이 자동으로 이어집니다</small>
                <button className="kb3 pri big" disabled={busy || !csrf} onClick={() => void reconnect()}>{busy ? '여는 중' : '다시 연결'}</button>
              </div>
            </>}
          </section>
        </div>
      </>}
    </div>
  </main>;
}
