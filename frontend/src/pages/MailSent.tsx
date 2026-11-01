import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { checkSentCopy, listSendIntents, resendIntent, type SendIntent, type SendStatus } from '../api/mail';
import { ErrorState, Loading } from '../components/states';
import MailGate from './MailGate';
import { KvCond, KvCondField, KvCondRow, KvGrid, KvKey, KvModal, KvMore, KvNext, KvTitle, downloadCsv, useHotkey, type KvColumn } from '../ls-shell/kv';
import { formatStamp } from '../ls-shell/cs-labels';

const STATUS_LABEL: Record<SendStatus, string> = { QUEUED: '대기', SUBMITTING: '보내는 중', ACCEPTED: '접수됨', UNKNOWN: '결과 모름', FAILED: '실패' };
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

  const columns: KvColumn<SendIntent>[] = [
    { key: 'pick', header: '', width: 30, className: 'c', cell: row => <input type="radio" name="intent" aria-label={`${row.subject} 선택`} checked={row.intent_id === pickedId} onChange={() => setPickedId(row.intent_id)} /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_row, index) => index + 1 },
    { key: 'created', header: '만든 시각', width: 110, className: 'd', sort: 'descending', cell: row => formatStamp(row.created_at).slice(5) },
    { key: 'ref', header: '업무', width: 190, className: 'id', cell: row => uuid.test(row.business_ref) ? <Link to={`/cs/tickets/${row.business_ref}`}>{row.business_ref.slice(0, 8)}</Link> : row.business_ref },
    { key: 'subject', header: '제목', className: 't', headClass: '', cell: row => row.subject },
    { key: 'status', header: '상태', width: 86, className: 'c', cell: row => STATUS_LABEL[row.status] },
    { key: 'attempts', header: '시도', width: 44, className: 'c', cell: row => row.attempts },
    { key: 'submitted', header: '보낸 시각', width: 84, className: 'c', cell: row => clock(row.submitted_at) },
    { key: 'copy', header: '보낸 편지함', width: 100, className: 'c', cell: row => row.sent_copy_confirmed_at ? clock(row.sent_copy_confirmed_at) : <span className="unc">확인 전</span> },
    { key: 'error', header: '오류', width: 170, headClass: '', cell: row => row.error_code ?? '' },
  ];
  const exportCsv = () => downloadCsv('발송상태', ['만든 시각', '업무', '제목', '상태', '시도', '보낸 시각', '보낸 편지함', '오류'],
    rows.map(row => [formatStamp(row.created_at), row.business_ref, row.subject, STATUS_LABEL[row.status], row.attempts, row.submitted_at ? formatStamp(row.submitted_at) : '', row.sent_copy_confirmed_at ? formatStamp(row.sent_copy_confirmed_at) : '확인 전', row.error_code ?? '']));

  return <main>
    <KvTitle path="메일" title="발송 상태" code="MB0102">
      <button className="kb primary" disabled={list.isFetching} onClick={fetchAgain}>조회<KvKey>F8</KvKey></button>
      <button className="kb" disabled={!canCheck || busy} title={canCheck ? undefined : '결과 모름인 발송을 고르세요'} onClick={() => void check()}>보낸 편지함 확인</button>
      <button className="kb" disabled={!canResend || busy} title={canResend ? undefined : picked ? '결과 모름이고 사본 없음이 확인된 발송만 다시 보낼 수 있습니다' : '발송을 고르세요'} onClick={() => setDialog(true)}>다시 보내기</button>
      <button className="kb" disabled={rows.length === 0} onClick={exportCsv}>엑셀</button>
      <KvMore items={[{ label: '발송 취소', note: '메일 서버에 넘긴 뒤에는 취소할 수 없음' }, { label: '모두 다시 보내기', note: '한 건씩 확인' }]} />
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 합성 예시이며 실제 발송 기록이 아닙니다.</div>}
    <KvNext>내 발송 <b>{rows.length}</b>건, 결과 모름 <b>{count('UNKNOWN')}</b>, 실패 <b>{count('FAILED')}</b>, 대기 <b>{count('QUEUED')}</b>. 메일 서버에 넘긴 뒤 접수돼야 업무 상태가 바뀝니다</KvNext>
    <KvCond onSearch={fetchAgain}>
      <KvCondRow columns={3}>
        <KvCondField label="상태"><select aria-label="상태" value={status} onChange={event => setStatus(event.target.value as SendStatus | '')}>
          <option value="">전체</option>
          {(Object.keys(STATUS_LABEL) as SendStatus[]).map(key => <option key={key} value={key}>{STATUS_LABEL[key]}</option>)}
        </select></KvCondField>
        <KvCondField label="만든 일자"><div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="date" aria-label="시작일" value={from} onChange={event => setFrom(event.target.value)} />~<input type="date" aria-label="종료일" value={to} onChange={event => setTo(event.target.value)} />
        </div></KvCondField>
      </KvCondRow>
    </KvCond>
    {notice && <div className="kv-sub" role={notice.kind === 'error' ? 'alert' : 'status'} style={{ margin: '6px 14px 0' }}>{notice.text}</div>}
    {invalid && <div className="kv-sub" role="alert" style={{ margin: '6px 14px 0' }}>종료일은 시작일보다 빠를 수 없습니다.</div>}
    {list.isFetching && <Loading />}
    {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
    {!list.isFetching && list.isSuccess && <>
      <div className="kv-gridhead"><span>내가 보낸 메일 {rows.length}건, 만든 시각 내림차순. 보낸 편지함 칸은 사본이 확인된 시각</span></div>
      <KvGrid label="발송 상태" columns={columns} rows={invalid ? [] : rows} rowKey={row => row.intent_id} selectedKey={pickedId} fallback="조건에 맞는 발송 기록이 없습니다." />
    </>}
    {dialog && picked && <KvModal title={`다시 보내기, ${picked.subject}`} onClose={() => !busy && setDialog(false)}>
      <form onSubmit={event => { event.preventDefault(); void resend(String(new FormData(event.currentTarget).get('reason') ?? '').trim()); }}>
        <div className="kv-sub" style={{ margin: '0 0 8px' }}>결과를 알 수 없고 보낸 편지함에 사본이 없는 발송을 다시 보냅니다. 같은 내용이 고객에게 두 번 갈 수 있으니 사유를 남기고 확인하세요. 허용 조건은 서버가 다시 확인합니다.</div>
        <div className="kv-field"><span>대상</span><span className="ro">{picked.business_ref}, 시도 {picked.attempts}회</span></div>
        <div className="kv-field"><span className="req">사유</span><textarea name="reason" aria-label="사유" rows={3} required maxLength={500} /></div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(false)}>취소</button><button type="submit" className="kb primary" disabled={busy}>{busy ? '보내는 중' : '다시 보내기'}</button></div>
      </form>
    </KvModal>}
  </main>;
}
