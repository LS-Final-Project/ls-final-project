import { useMemo, useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getCaseComparison, getImprovement, listImprovements, removeEvidence, type ImprovementDetail, type ImprovementOrigin } from '../api/cs-improvements';
import { ErrorState, Loading, Preparing } from '../components/states';
import { KvCond, KvCondField, KvCondRow, KvFoot, KvGrid, KvKey, KvModal, KvNext, KvTitle, downloadCsv, useHotkey, type KvColumn } from '../ls-shell/kv';
import { REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL, deptName, formatStamp } from '../ls-shell/cs-labels';

type Row = Awaited<ReturnType<typeof getCaseComparison>>['data']['items'][number];
type Evidence = ImprovementDetail['data']['evidence'][number];
const ORIGIN_LABEL: Record<ImprovementOrigin, string> = { SYSTEM_CANDIDATE: '시스템 후보', MANUAL: '직접 작성' };
const clip = (text: string, size: number) => text.length > size ? `${text.slice(0, size)}…` : text;

// 반복 사례 모음 CS0201. 개선요청의 근거 문의마다 해결 기록(증상·원인 유형·원인·조치·해결 부서)을 한 표에서 비교한다.
// 서버가 활성 근거만 내려 주고, 볼 수 없는 문의는 행이 빠진 채 수만 알려 준다(그 수를 화면에 보인다).
// 근거 빼기는 사유를 적어야 하고 새 판본이 된다. 근거 추가용 후보 문의 목록 API는 없어 추가는 연결하지 않았다.
export default function CsCases() {
  if (!import.meta.env.DEV) return <Preparing title="반복 사례 모음" message="운영 인증 중계와 권한 검증 후 제공됩니다." />;
  return <CasesView />;
}

function CasesView() {
  const mode = getApiMode();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [origin, setOrigin] = useState<ImprovementOrigin | ''>('');
  const [pickedRow, setPickedRow] = useState('');
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const list = useQuery({ queryKey: ['cs-cases-requests', mode, origin], queryFn: ({ signal }) => listImprovements(signal, origin || undefined), retry: false, staleTime: 0 });
  const items = list.data?.data.items ?? [];
  const requestId = params.get('request') && items.some(item => item.id === params.get('request')) ? params.get('request')! : items[0]?.id ?? '';
  const comparison = useQuery({ queryKey: ['cs-cases-comparison', mode, requestId], queryFn: ({ signal }) => getCaseComparison(requestId, signal), enabled: requestId !== '', retry: false, staleTime: 0 });
  const detail = useQuery({ queryKey: ['cs-cases-detail', mode, requestId], queryFn: ({ signal }) => getImprovement(requestId, signal), enabled: requestId !== '', retry: false, staleTime: 0 });
  const fetchAgain = () => { void list.refetch(); void comparison.refetch(); void detail.refetch(); };
  useHotkey('F8', fetchAgain, !list.isFetching);

  const rows = comparison.data?.data.items ?? [];
  const hidden = comparison.data?.data.hidden_case_count ?? 0;
  const evidence = detail.data?.data.evidence ?? [];
  const request = detail.data?.data.request;
  const activeEvidence = (row: Row) => evidence.find(item => item.ticket_id === row.ticket_id && !item.removed_at);
  const selected = rows.find(row => row.ticket_id === pickedRow);
  const selectedEvidence = selected ? activeEvidence(selected) : undefined;
  const noCase = rows.filter(row => !row.case_id).length;
  const canWrite = import.meta.env.DEV && mode === 'live';
  const rowKey = (row: Row) => row.ticket_id;

  // 근거 변경 기록: 추가와 제외를 시간순으로 한 표에 보인다(제외한 근거도 행이 남는다).
  const history = useMemo(() => evidence.flatMap((item: Evidence) => [
    { key: `${item.id}-add`, at: item.added_at, kind: '추가', item, by: item.added_by, reason: '' },
    ...(item.removed_at ? [{ key: `${item.id}-del`, at: item.removed_at, kind: '뺌', item, by: item.removed_by, reason: item.removal_reason ?? '' }] : []),
  ]).sort((a, b) => a.at.localeCompare(b.at)), [evidence]);

  async function submitRemoval(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reason = String(new FormData(event.currentTarget).get('reason') ?? '').trim();
    if (!reason || !selectedEvidence || !request || busy) return;
    setBusy(true); setNotice(null);
    try {
      await removeEvidence(requestId, selectedEvidence.id, { version: request.version, reason }, new AbortController().signal);
      setNotice({ kind: 'ok', text: '근거에서 뺐습니다. 새 판본으로 저장됐습니다.' });
      setPickedRow('');
    } catch (error) {
      const status = error instanceof ApiError ? error.status : 0;
      setNotice({ kind: 'error', text: error instanceof ApiError && error.code === 'CS_REQUEST_VERSION_CONFLICT' ? '다른 사용자가 먼저 바꿨습니다. 최신 내용을 다시 읽었으니 확인한 뒤 다시 실행하세요.'
        : status >= 400 && status < 500 && status !== 408 ? (error as ApiError).message
          : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 내용을 읽었습니다. 반영 여부를 확인하세요.' });
    } finally {
      setDialog(false); setBusy(false);
      await Promise.allSettled([detail.refetch(), comparison.refetch(), client.invalidateQueries({ queryKey: ['cs-improvements', mode] })]);
    }
  }

  const columns: KvColumn<Row>[] = [
    { key: 'pick', header: '선택', width: 44, className: 'c', cell: row => <input type="radio" name="case-row" aria-label={`${row.ticket_external_id ?? row.ticket_id.slice(0, 8)} 선택`} checked={row.ticket_id === pickedRow} onChange={() => setPickedRow(row.ticket_id)} /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_row, index) => index + 1 },
    { key: 'ticket', header: '문의번호', width: 110, className: 'id', cell: row => <Link to={`/cs/tickets/${encodeURIComponent(row.ticket_id)}`}>{row.ticket_external_id ?? row.ticket_id.slice(0, 8)}</Link> },
    { key: 'case', header: '해결 기록', width: 112, className: 'id', cell: row => row.case_external_id ?? '없음' },
    { key: 'customer', header: '고객', width: 90, headClass: '', cell: row => row.customer_external_id },
    { key: 'symptom', header: '증상', className: 't', headClass: '', cell: row => row.symptom ?? '미기록' },
    { key: 'type', header: '원인 유형', width: 84, className: 'c', cell: row => row.root_cause_type ?? '미기록' },
    { key: 'cause', header: '원인', width: 150, headClass: '', cell: row => row.root_cause ?? '미기록' },
    { key: 'action', header: '조치', width: 150, headClass: '', cell: row => row.action_taken ?? '미기록' },
    { key: 'dept', header: '해결 부서', width: 140, headClass: '', cell: row => row.resolved_by_dept ? deptName(row.resolved_by_dept) : '미기록' },
  ];
  const exportCsv = () => downloadCsv('반복_사례_비교', ['문의번호', '해결 기록', '고객', '제품', '증상', '원인 유형', '원인', '조치', '해결 부서'],
    rows.map(row => [row.ticket_external_id ?? row.ticket_id, row.case_external_id ?? '', row.customer_external_id, row.product_code, row.symptom ?? '', row.root_cause_type ?? '', row.root_cause ?? '', row.action_taken ?? '', row.resolved_by_dept ?? '']));
  const historyColumns: KvColumn<(typeof history)[number]>[] = [
    { key: 'at', header: '일시', width: 130, className: 'd', headClass: '', cell: row => formatStamp(row.at) },
    { key: 'kind', header: '구분', width: 56, className: 'c', cell: row => row.kind },
    { key: 'case', header: '해결 기록', width: 100, className: 'id', cell: row => row.item.case_external_id ?? '없음' },
    { key: 'ticket', header: '문의', width: 110, className: 'id', cell: row => row.item.ticket_external_id ?? row.item.ticket_id.slice(0, 8) },
    { key: 'by', header: '처리자', width: 100, headClass: '', cell: row => row.by ? row.by.slice(0, 8) : '-' },
    { key: 'reason', header: '사유', className: 't', headClass: '', cell: row => row.reason },
  ];
  const shown = request ? `${REQUEST_TYPE_LABEL[request.request_type]} · ${request.product_code} · ${REQUEST_STATUS_LABEL[request.status] ?? request.status} · 판본 ${request.version}` : '';

  return <main>
    <KvTitle path="C/S 관리" title="반복 사례 모음" code="CS0201">
      <button className="kb primary" disabled={list.isFetching || comparison.isFetching} onClick={fetchAgain}>조회<KvKey>F8</KvKey></button>
      {requestId && <Link className="kb" to={`/cs/improvements/${encodeURIComponent(requestId)}`}>요청으로 이동</Link>}
      <button className="kb" disabled={rows.length === 0} onClick={exportCsv}>엑셀</button>
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 비교표 예시가 없어 비어 있습니다. 실제 API 모드에서 확인하세요.</div>}
    {notice && <div className={notice.kind === 'error' ? 'kv-err' : 'kv-next'} role={notice.kind === 'error' ? 'alert' : 'status'} style={{ margin: '6px 14px 0', padding: '6px 10px' }}>{notice.text}</div>}
    {list.isPending && <Loading />}
    {list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
    {list.isSuccess && <>
      <KvNext>{items.length === 0 ? '비교할 개선요청이 없습니다.' : <>근거 <b>{rows.length}</b>건 비교 중{noCase > 0 && <>, 해결 기록 없는 문의 <b>{noCase}</b>건</>}. {shown}</>}</KvNext>
      <KvCond onSearch={fetchAgain}>
        <KvCondRow columns={3}>
          <KvCondField label="개선요청"><select aria-label="개선요청" value={requestId} onChange={event => { setParams({ request: event.target.value }); setPickedRow(''); setNotice(null); }}>
            {items.length === 0 && <option value="">없음</option>}
            {items.map(item => <option key={item.id} value={item.id}>{item.product_code} · {clip(item.problem_statement, 32)} ({REQUEST_STATUS_LABEL[item.status] ?? item.status})</option>)}
          </select></KvCondField>
          <KvCondField label="출처"><select aria-label="출처" value={origin} onChange={event => { setOrigin(event.target.value as ImprovementOrigin | ''); setParams({}); setPickedRow(''); }}>
            <option value="">전체</option>{(Object.keys(ORIGIN_LABEL) as ImprovementOrigin[]).map(key => <option key={key} value={key}>{ORIGIN_LABEL[key]}</option>)}
          </select></KvCondField>
        </KvCondRow>
      </KvCond>
      {requestId && comparison.isPending && <Loading />}
      {requestId && comparison.isError && <ErrorState error={comparison.error} onRetry={fetchAgain} />}
      {requestId && comparison.isSuccess && <>
        {hidden > 0 && <div className="kv-sub" role="note" style={{ margin: '6px 14px 0' }}>볼 수 있는 범위 밖의 문의 {hidden}건은 표에서 빠졌습니다. 해결 내용은 이관받은 문의만 볼 수 있습니다.</div>}
        <div className="kv-gridhead"><span>근거 문의 {rows.length}건. 활성 근거만, 해결 기록이 없으면 칸이 비어 미기록으로 보임</span>
          <button className="kb" disabled={!canWrite || !selectedEvidence || busy || !!request?.completed_at} title={!canWrite ? '실제 API 개발 모드에서만 실행할 수 있습니다' : !selectedEvidence ? '표에서 뺄 근거를 고르세요' : undefined} onClick={() => setDialog(true)}>근거에서 빼기</button></div>
        <KvGrid label="반복 사례 비교" columns={columns} rows={rows} rowKey={rowKey} selectedKey={pickedRow} fallback="비교할 근거가 없습니다." scroll />
        <KvFoot count={`근거 ${rows.length}건${hidden > 0 ? `, 범위 밖 ${hidden}건` : ''}`} />
        <div className="kv-gridhead"><span>근거 변경 기록 {history.length}건(제외한 근거도 남음)</span></div>
        <KvGrid label="근거 변경 기록" columns={historyColumns} rows={history} rowKey={row => row.key} fallback="근거 변경 기록이 없습니다." />
      </>}
    </>}
    {dialog && selected && selectedEvidence && <KvModal title={`근거에서 빼기, ${selected.ticket_external_id ?? selected.ticket_id.slice(0, 8)}`} onClose={() => !busy && setDialog(false)}>
      <form onSubmit={event => void submitRemoval(event)}>
        <div className="kv-sub" style={{ margin: '0 0 8px' }}>이 문의를 근거에서 뺍니다. 행은 지워지지 않고 제외자·시각·사유가 기록되며, 요청은 새 판본이 됩니다. 문의 상태는 바뀌지 않습니다.</div>
        <div className="kv-field"><span>해결 기록</span><span className="ro">{selected.case_external_id ?? '없음'}</span></div>
        <div className="kv-field"><span className="req">빼는 사유</span><textarea name="reason" aria-label="빼는 사유" rows={3} required disabled={busy} /></div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(false)}>취소</button><button type="submit" className="kb primary" disabled={busy}>빼기</button></div>
      </form>
    </KvModal>}
  </main>;
}
