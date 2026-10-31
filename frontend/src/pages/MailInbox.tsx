import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getMailTriage, listReplyReviews, type MailTriage, type ReplyReview } from '../api/mail';
import { ErrorState, Loading, Preparing } from '../components/states';
import { KvBox, KvField, KvGrid, KvKey, KvMore, KvNext, KvSplit, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { formatStamp } from '../ls-shell/cs-labels';

const REASON_LABEL: Record<ReplyReview['reason'], string> = {
  AMBIGUOUS: '후보가 여럿', UNMATCHED_REPLY: '맞는 후보 없음', CONFLICT: '근거가 서로 다름', TRIAGE_DISAGREES: '자동 분류와 다름',
};
const TYPE_LABEL: Record<MailTriage['mail_type'], string> = {
  NEW_RFP: '새 제안요청', EXISTING_CHANGE: '기존 제안 변경', CANDIDATE_REPLY: '협력사 회신', CS: 'C/S 문의', OTHER: '기타',
};
const SUGGESTION_LABEL = { NEW_PROJECT: '새 제안 프로젝트', EXISTING_THREAD: '기존 건에 이어 붙이기', NEW_CS_TICKET: '새 문의로 접수' } as const;
const METHOD_LABEL: Record<ReplyReview['candidates'][number]['method'], string> = {
  HEADER: '답장 헤더가 가리킴', THREAD: '같은 대화로 묶임', HUMAN: '사람이 지정', BUSINESS: '업무 기록과 일치',
};
const SOURCE_LABEL = { subject: '제목', body: '본문', reply_header: '답장 헤더' } as const;
const uuid = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const day = (iso: string) => formatStamp(iso).slice(0, 10);

// 받은 메일 검토 MB0101. 연결 대상을 사람이 정해야 하는 고객 답장을 읽어 보인다.
// 이 API는 보낸 사람·제목·첨부를 주지 않아 보드의 해당 열은 없다. 연결·무시·다시 분류는 서버 상태를 바꾸는 동작이라 아직 연결하지 않는다.
export default function MailInbox() {
  if (!import.meta.env.DEV) return <Preparing title="받은 메일 검토" message="운영 인증 중계와 권한 검증 후 제공됩니다." />;
  return <InboxView />;
}

function InboxView() {
  const mode = getApiMode();
  const [reason, setReason] = useState<ReplyReview['reason'] | ''>('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [picked, setPicked] = useState('');
  const list = useQuery({ queryKey: ['reply-reviews', mode], queryFn: ({ signal }) => listReplyReviews(signal), retry: false, staleTime: 0 });
  const fetchAgain = () => { void list.refetch(); };
  useHotkey('F8', fetchAgain, !list.isFetching);

  const rows = useMemo(() => (list.data ?? [])
    .filter(item => (!reason || item.reason === reason) && (!from || day(item.received_at) >= from) && (!to || day(item.received_at) <= to))
    .sort((a, b) => b.received_at.localeCompare(a.received_at)), [list.data, reason, from, to]);
  const invalid = from && to && from > to;
  const shown = invalid ? [] : rows;
  const selected = shown.find(item => item.message_id === picked) ?? shown[0];
  const triage = useQuery({ queryKey: ['mail-triage', mode, selected?.message_id], queryFn: ({ signal }) => getMailTriage(selected!.message_id, signal), enabled: !!selected, retry: false, staleTime: 0 });

  const columns: KvColumn<ReplyReview>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_row, index) => index + 1 },
    { key: 'received', header: '받은 시각', width: 110, className: 'd', sort: 'descending', cell: row => formatStamp(row.received_at).slice(5) },
    { key: 'message', header: '메일', className: 't', headClass: '', cell: row => <button className="kb" aria-pressed={row.message_id === selected?.message_id} onClick={() => setPicked(row.message_id)}>{row.message_id.slice(0, 8)}</button> },
    { key: 'reason', header: '검토 사유', width: 150, className: 'c', cell: row => REASON_LABEL[row.reason] },
    { key: 'candidates', header: '연결 후보', width: 90, className: 'c', cell: row => `${row.candidates.length}건` },
  ];

  return <main>
    <KvTitle path="메일" title="받은 메일 검토" code="MB0101">
      <button className="kb primary" disabled={list.isFetching} onClick={fetchAgain}>조회<KvKey>F8</KvKey></button>
      <button className="kb" disabled title="서버 상태를 바꾸는 동작이라 아직 연결하지 않았습니다">연결</button>
      <button className="kb" disabled title="서버 상태를 바꾸는 동작이라 아직 연결하지 않았습니다">무시</button>
      <button className="kb" disabled title="서버 상태를 바꾸는 동작이라 아직 연결하지 않았습니다">다시 분류</button>
      <KvMore items={[{ label: '프로젝트 만들기', note: '메일에서 바로 만드는 경로 아직 없음' }, { label: '본문 열기', note: '본문은 메일 계정에서, 여기 저장하지 않음' }]} />
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 합성 예시이며 실제 메일이 아닙니다.</div>}
    <KvNext>검토 대기 <b>{(list.data ?? []).length}</b>건. 시스템은 연결 후보를 추천만 하고, 어디로 보낼지는 사람이 정합니다. 보낸 사람·제목·첨부는 이 목록 API가 주지 않아 표시하지 않습니다</KvNext>
    <form className="kv-cond" aria-label="조회조건" onSubmit={event => { event.preventDefault(); fetchAgain(); }}>
      <div className="row" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>
        <label><span>검토 사유</span><select aria-label="검토 사유" value={reason} onChange={event => setReason(event.target.value as ReplyReview['reason'] | '')}>
          <option value="">전체</option>
          {(Object.keys(REASON_LABEL) as ReplyReview['reason'][]).map(key => <option key={key} value={key}>{REASON_LABEL[key]}</option>)}
        </select></label>
        <label><span>받은 일자</span><div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="date" aria-label="시작일" value={from} onChange={event => setFrom(event.target.value)} />~<input type="date" aria-label="종료일" value={to} onChange={event => setTo(event.target.value)} />
        </div></label>
      </div>
    </form>
    {invalid && <div className="kv-sub" role="alert" style={{ margin: '6px 14px 0' }}>종료일은 시작일보다 빠를 수 없습니다.</div>}
    {list.isFetching && <Loading />}
    {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
    {!list.isFetching && list.isSuccess && <>
      <div className="kv-gridhead"><span>사람이 연결 대상을 정해야 하는 고객 답장 {shown.length}건, 받은 시각 내림차순</span><Link className="right" to="/mail/account">메일 계정</Link></div>
      <KvGrid label="검토 대기 메일" columns={columns} rows={shown} rowKey={row => row.message_id} selectedKey={selected?.message_id} fallback="검토할 메일이 없습니다." />
      {selected && <>
        <div style={{ marginTop: 8 }}>
          <KvSplit>
            <KvBox title="메일" note="메일 계정에서 받음">
              <KvField label="메일 ID">{selected.message_id}</KvField>
              <KvField label="받은 시각">{formatStamp(selected.received_at)}</KvField>
              <KvField label="검토 사유">{REASON_LABEL[selected.reason]}</KvField>
              <KvField label="본문"><span className="unc">본문은 여기 저장하지 않음</span></KvField>
            </KvBox>
            <KvBox title="자동 분류" note="AI 작성, 판본은 사람이 바꾸면 올라감">
              {triage.isFetching && <Loading />}
              {!triage.isFetching && triage.isError && <ErrorState error={triage.error} onRetry={() => { void triage.refetch(); }} />}
              {!triage.isFetching && triage.isSuccess && <TriageFields triage={triage.data} />}
            </KvBox>
          </KvSplit>
        </div>
        <div style={{ margin: '8px 14px 0' }}>
          <KvBox title="연결 후보" note="사람이 정함">
            <div className="kv-sub" style={{ margin: '0 0 6px' }}>{selected.candidates.length ? `후보 ${selected.candidates.length}건` : '시스템이 찾은 후보가 없습니다'}</div>
            {selected.candidates.length > 0 && <KvGrid label="연결 후보" columns={[
              { key: 'ref', header: '업무', width: 240, className: 'id', cell: c => c.owner_service === 'cs' && uuid.test(c.business_ref) ? <Link to={`/cs/tickets/${c.business_ref}`}>{c.business_ref.slice(0, 8)}</Link> : c.business_ref },
              { key: 'owner', header: '담당 서비스', width: 120, className: 'c', cell: c => c.owner_service === 'cs' ? 'C/S' : '플랫폼' },
              { key: 'method', header: '왜 후보인가', headClass: '', cell: c => METHOD_LABEL[c.method] },
            ] satisfies KvColumn<ReplyReview['candidates'][number]>[]} rows={selected.candidates} rowKey={c => `${c.owner_service}:${c.business_ref}`} fallback="" />}
          </KvBox>
        </div>
      </>}
    </>}
  </main>;
}

function TriageFields({ triage }: { triage: MailTriage }) {
  const evidence = triage.evidence.map(item => item.start != null && item.end != null ? `${SOURCE_LABEL[item.source]} ${item.start}~${item.end}자` : SOURCE_LABEL[item.source]).join(', ');
  return <>
    <KvField label="유형">{TYPE_LABEL[triage.mail_type]}</KvField>
    <KvField label="왜 그렇게 봤나">{evidence || '근거 위치 없음'}{triage.reason ? `, ${triage.reason}` : ''}</KvField>
    <KvField label="연결 추천">{triage.link_suggestion ? SUGGESTION_LABEL[triage.link_suggestion] : '추천 없음'}</KvField>
    <KvField label="확인">{triage.needs_review ? <span className="unc">확인 필요</span> : '확인 불필요로 분류'}</KvField>
    <KvField label="판본">{triage.version}{triage.is_latest ? '' : ` (최신 ${triage.latest_version})`}, {triage.provenance_kind === 'ai_draft' ? '자동 분류' : '사람이 입력'}</KvField>
    {triage.attachments_pending && <div className="kv-sub" style={{ margin: '8px 0 0' }}>첨부 확인이 끝나지 않았습니다</div>}
    <div className="kv-sub" style={{ margin: '8px 0 0' }}>추천일 뿐입니다. 어느 건에 붙일지는 사람이 정합니다</div>
  </>;
}
