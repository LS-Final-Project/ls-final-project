import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { checkSentCopy, listSendIntents, resendIntent, type SendIntent, type SendStatus } from '../api/mail';
import { ErrorState, Loading } from '../components/states';
import Pager, { paged } from '../components/Pager';
import LogTable, { type LogColumn } from './LogTable';
import MailGate from './MailGate';
import { KvModal, KvMore, downloadCsv, useHotkey } from '../ls-shell/kv';
import { formatStamp } from '../ls-shell/cs-labels';
import './ticket-detail-v3.css';
import './list-cond.css';
import './admin-v3.css';
import './mailbox-v3.css';

const STATUS_LABEL: Record<SendStatus, string> = { QUEUED: '대기', SUBMITTING: '보내는 중', ACCEPTED: '접수됨', UNKNOWN: '결과 모름', FAILED: '실패' };
// 상태 칩 색: 결과 모름은 주황, 실패는 빨강, 접수됨은 초록.
const STATUS_TONE: Record<SendStatus, string> = { QUEUED: 'st-wait', SUBMITTING: 'st-wait', ACCEPTED: 'st-ok', UNKNOWN: 'st-warn', FAILED: 'st-bad' };
const PAGE = 20;
const uuid = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const day = (iso: string) => formatStamp(iso).slice(0, 10);
const clock = (iso: string | null) => iso ? formatStamp(iso).slice(5) : '';

// 발송 상태 MB0102. 내가 만든 메일 발송 의도(답변·정보 요청 등)의 상태를 읽어 보인다.
// 보낸 편지함 확인(결과 불명 건)과 다시 보내기(resend_ready인 건, 사유 필수)는 고른 행에 대해 서버에 요청한다.
// 허용 조건은 서버가 막고, 화면은 resend_ready가 아닌 건의 다시 보내기를 켜지 않는다.
export default function MailSent() {
  return <MailGate title="발송 상태"><SentView /></MailGate>;
}

function SentView() {
  const mode = getApiMode();
  const [status, setStatus] = useState<SendStatus | ''>('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [pickedId, setPickedId] = useState('');
  const [page, setPage] = useState(0);
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const list = useQuery({ queryKey: ['send-intents', mode, status], queryFn: ({ signal }) => listSendIntents(status, signal), retry: false, staleTime: 0 });
  const fetchAgain = () => { void list.refetch(); };
  useHotkey('F8', fetchAgain, !list.isFetching);

  const rows = useMemo(() => (list.data ?? [])
    .filter(item => (!from || day(item.created_at) >= from) && (!to || day(item.created_at) <= to))
    .sort((a, b) => b.created_at.localeCompare(a.created_at)), [list.data, from, to]);
  const count = (key: SendStatus) => rows.filter(item => item.status === key).length;
  const invalid = from && to && from > to;

  const picked = rows.find(item => item.intent_id === pickedId);
  const canCheck = picked?.status === 'UNKNOWN';
  const canResend = picked?.status === 'UNKNOWN' && picked.resend_ready;
  async function act(done: (picked: SendIntent) => Promise<string>) {
    if (!picked || busy) return;
    setBusy(true); setNotice(null);
    try { setNotice({ kind: 'ok', text: await done(picked) }); setDialog(false); }
    catch (error) {
      setNotice({ kind: 'error', text: error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 ? error.message
        : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 목록을 다시 읽었습니다. 반영 여부를 확인하세요.' });
      setDialog(false);
    } finally { setBusy(false); await list.refetch(); }
  }
  const check = () => act(async item => {
    const result = await checkSentCopy(item.intent_id);
    return result.sent_copy_confirmed ? '보낸 편지함에서 사본을 확인했습니다.' : '보낸 편지함에 사본이 없습니다. 유예 시간이 지나면 다시 보내기를 켤 수 있습니다.';
  });
  const resend = (reason: string) => act(async item => {
    const result = await resendIntent(item.intent_id, reason);
    return result.task_registered ? '다시 보내기를 등록했습니다. 발송 결과는 목록에서 확인하세요.' : '다시 보내기를 접수했습니다. 전송 작업은 곧 다시 등록됩니다.';
  });

  // 한 쪽 20건. 조건이 바뀌면 첫 쪽으로 간다. No는 쪽을 넘겨도 이어진다.
  const { pages, at, shown } = paged(invalid ? [] : rows, page, PAGE);
  // 조건이나 쪽이 바뀌면 고른 발송을 비운다(보이지 않는 행에 버튼이 걸리지 않게).
  useEffect(() => { setPage(0); setPickedId(''); }, [status, from, to]);
  const turn = (next: number) => { setPage(next); setPickedId(''); };

  const columns: LogColumn<SendIntent>[] = [
    { key: 'pick', header: '', width: 34, className: 'c', cell: row => <input type="radio" name="intent" aria-label={`${row.subject} 선택`} checked={row.intent_id === pickedId} onChange={() => setPickedId(row.intent_id)} /> },
    { key: 'no', header: 'No', width: 44, className: 'c no', cell: (_row, index) => at * PAGE + index + 1 },
    { key: 'created', header: '만든 시각 ▼', width: 110, className: 'c', cell: row => formatStamp(row.created_at).slice(5) },
    { key: 'ref', header: '업무', width: 170, cell: row => uuid.test(row.business_ref) ? <Link className="link" to={`/cs/tickets/${row.business_ref}`}>{row.business_ref.slice(0, 8)}</Link> : row.business_ref },
    { key: 'subject', header: '제목', className: 't', cell: row => row.subject },
    { key: 'status', header: '상태', width: 96, className: 'c', cell: row => <span className={`chip ${STATUS_TONE[row.status]}`}>{STATUS_LABEL[row.status]}</span> },
    { key: 'attempts', header: '시도', width: 52, className: 'c', cell: row => row.attempts },
    { key: 'submitted', header: '보낸 시각', width: 96, className: 'c', cell: row => clock(row.submitted_at) },
    { key: 'copy', header: '보낸 편지함', width: 100, className: 'c', cell: row => row.sent_copy_confirmed_at ? clock(row.sent_copy_confirmed_at) : <span className="unc">확인 전</span> },
    { key: 'error', header: '오류', width: 170, cell: row => row.error_code ? <code className="err">{row.error_code}</code> : '' },
  ];
  const exportCsv = () => downloadCsv('발송상태', ['만든 시각', '업무', '제목', '상태', '시도', '보낸 시각', '보낸 편지함', '오류'],
    rows.map(row => [formatStamp(row.created_at), row.business_ref, row.subject, STATUS_LABEL[row.status], row.attempts, row.submitted_at ? formatStamp(row.submitted_at) : '', row.sent_copy_confirmed_at ? formatStamp(row.sent_copy_confirmed_at) : '확인 전', row.error_code ?? '']));
  const loaded = !list.isFetching && list.isSuccess;
  const value = (n: number) => loaded ? n : '…';

  return <main className="td3 as3 ms3 ml3">
    <div className="title">
      <span className="path">메일 ›</span><h1>발송 상태</h1><code className="kv-code">MB0102</code>
      <div className="btns">
        <button className="kb3" disabled={!canCheck || busy} title={canCheck ? undefined : '결과 모름인 발송을 고르세요'} onClick={() => void check()}>보낸 편지함 확인</button>
        <button className="kb3" disabled={!canResend || busy} title={canResend ? undefined : picked ? '결과 모름이고 사본 없음이 확인된 발송만 다시 보낼 수 있습니다' : '발송을 고르세요'} onClick={() => setDialog(true)}>다시 보내기</button>
        <button className="kb3" disabled={rows.length === 0} onClick={exportCsv}>엑셀</button>
        <KvMore items={[{ label: '발송 취소', note: '메일 서버에 넘긴 뒤에는 취소할 수 없음' }, { label: '모두 다시 보내기', note: '한 건씩 확인' }]} />
        <span className="vsep" />
        <button className="kb3 pri" disabled={list.isFetching} onClick={fetchAgain}>조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      {mode === 'mock' && <div className="tier-note" role="status">모의 응답 모드 — 합성 예시이며 실제 발송 기록이 아닙니다.</div>}
      {/* 건수는 지금 조건으로 보이는 행에서 센다. */}
      <section className="kpis" aria-label="발송 요약">
        <div className="kpi"><small>내 발송</small><b>{value(rows.length)}</b><span>건 · 지금 조건 기준</span></div>
        <div className={count('UNKNOWN') ? 'kpi warm' : 'kpi calm'}><small>결과 모름</small><b>{value(count('UNKNOWN'))}</b><span>보낸 편지함 확인 필요</span></div>
        <div className={count('FAILED') ? 'kpi hot' : 'kpi calm'}><small>실패</small><b>{value(count('FAILED'))}</b><span>오류 칸의 사유 확인</span></div>
        <div className="kpi calm"><small>대기</small><b>{value(count('QUEUED'))}</b><span>메일 서버에 넘기기 전</span></div>
      </section>

      <form className="cond lc" onSubmit={event => { event.preventDefault(); fetchAgain(); }}>
        <label><span className="k">상태</span><select aria-label="상태" value={status} onChange={event => setStatus(event.target.value as SendStatus | '')}>
          <option value="">전체</option>
          {(Object.keys(STATUS_LABEL) as SendStatus[]).map(key => <option key={key} value={key}>{STATUS_LABEL[key]}</option>)}
        </select></label>
        <span className="field"><span className="k">만든 일자</span><span className="range"><input type="date" aria-label="시작일" value={from} onChange={event => setFrom(event.target.value)} /><em>~</em><input type="date" aria-label="종료일" value={to} onChange={event => setTo(event.target.value)} /></span></span>
        <span className="hint">메일 서버에 넘긴 뒤 접수돼야 업무 상태가 바뀝니다</span>
        <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
      </form>

      {notice && <div className={notice.kind === 'error' ? 'note err' : 'note'} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</div>}
      {invalid && <div className="note err" role="alert">종료일은 시작일보다 빠를 수 없습니다.</div>}
      {list.isFetching && <Loading />}
      {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
      {loaded && <section className="pane" aria-label="발송 목록">
        <div className="ph">발송 목록
          <div className="r"><span className="meta">내가 보낸 메일 {rows.length}건 · 만든 시각 내림차순 · 보낸 편지함 칸은 사본이 확인된 시각</span></div>
        </div>
        <LogTable label="발송 상태" columns={columns} rows={shown} rowKey={row => row.intent_id} selectedKey={pickedId}
          fallback={<div className="ml-empty"><b>조건에 맞는 발송 기록이 없습니다.</b></div>} />
        <Pager label="발송 쪽" page={at} pages={pages} onPage={turn} />
      </section>}
    {dialog && picked && <KvModal title={`다시 보내기, ${picked.subject}`} onClose={() => !busy && setDialog(false)}>
      <form onSubmit={event => { event.preventDefault(); void resend(String(new FormData(event.currentTarget).get('reason') ?? '').trim()); }}>
        <div className="kv-sub" style={{ margin: '0 0 8px' }}>결과를 알 수 없고 보낸 편지함에 사본이 없는 발송을 다시 보냅니다. 같은 내용이 고객에게 두 번 갈 수 있으니 사유를 남기고 확인하세요. 허용 조건은 서버가 다시 확인합니다.</div>
        <div className="kv-field"><span>대상</span><span className="ro">{picked.business_ref}, 시도 {picked.attempts}회</span></div>
        <div className="kv-field"><span className="req">사유</span><textarea name="reason" aria-label="사유" rows={3} required maxLength={500} /></div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(false)}>취소</button><button type="submit" className="kb primary" disabled={busy}>{busy ? '보내는 중' : '다시 보내기'}</button></div>
      </form>
    </KvModal>}
    </div>
  </main>;
}
