import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listSendIntents, type SendIntent, type SendStatus } from '../api/mail';
import { ErrorState, Loading, Preparing } from '../components/states';
import { KvCond, KvCondField, KvCondRow, KvGrid, KvKey, KvMore, KvNext, KvTitle, downloadCsv, useHotkey, type KvColumn } from '../ls-shell/kv';
import { formatStamp } from '../ls-shell/cs-labels';

const STATUS_LABEL: Record<SendStatus, string> = { QUEUED: '대기', SUBMITTING: '보내는 중', ACCEPTED: '접수됨', UNKNOWN: '결과 모름', FAILED: '실패' };
const uuid = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const day = (iso: string) => formatStamp(iso).slice(0, 10);
const clock = (iso: string | null) => iso ? formatStamp(iso).slice(5) : '';

// 발송 상태 MB0102. 내가 만든 메일 발송 의도(답변·정보 요청 등)의 상태를 읽어 보인다.
// 보낸 편지함 확인·다시 보내기는 메일을 외부로 내거나 서버 상태를 바꾸는 동작이라 아직 연결하지 않는다.
export default function MailSent() {
  if (!import.meta.env.DEV) return <Preparing title="발송 상태" message="운영 인증 중계와 권한 검증 후 제공됩니다." />;
  return <SentView />;
}

function SentView() {
  const mode = getApiMode();
  const [status, setStatus] = useState<SendStatus | ''>('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const list = useQuery({ queryKey: ['send-intents', mode, status], queryFn: ({ signal }) => listSendIntents(status, signal), retry: false, staleTime: 0 });
  const fetchAgain = () => { void list.refetch(); };
  useHotkey('F8', fetchAgain, !list.isFetching);

  const rows = useMemo(() => (list.data ?? [])
    .filter(item => (!from || day(item.created_at) >= from) && (!to || day(item.created_at) <= to))
    .sort((a, b) => b.created_at.localeCompare(a.created_at)), [list.data, from, to]);
  const count = (key: SendStatus) => rows.filter(item => item.status === key).length;
  const invalid = from && to && from > to;

  const columns: KvColumn<SendIntent>[] = [
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
      <button className="kb" disabled title="메일 서버에 접속하는 동작이라 아직 연결하지 않았습니다">보낸 편지함 확인</button>
      <button className="kb" disabled title="메일을 다시 내보내는 동작이라 아직 연결하지 않았습니다">다시 보내기</button>
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
    {invalid && <div className="kv-sub" role="alert" style={{ margin: '6px 14px 0' }}>종료일은 시작일보다 빠를 수 없습니다.</div>}
    {list.isFetching && <Loading />}
    {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
    {!list.isFetching && list.isSuccess && <>
      <div className="kv-gridhead"><span>내가 보낸 메일 {rows.length}건, 만든 시각 내림차순. 보낸 편지함 칸은 사본이 확인된 시각</span></div>
      <KvGrid label="발송 상태" columns={columns} rows={invalid ? [] : rows} rowKey={row => row.intent_id} fallback="조건에 맞는 발송 기록이 없습니다." />
    </>}
  </main>;
}
