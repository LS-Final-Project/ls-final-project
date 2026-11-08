import { useMemo, useRef, useState, type MouseEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { dismissReply, getMailContent, getReplyCandidates, linkReply, listMailMessages, listReplyReviews, type InboxMessage, type ReplyReview, type ReviewCandidate } from '../api/mail';
import { listTicketsBy, type TicketItem } from '../api/cs';
import { ErrorState, Loading } from '../components/states';
import { KvModal, useHotkey } from '../ls-shell/kv';
import LogTable, { type LogColumn } from './LogTable';
import { STATE_LABEL, formatStamp } from '../ls-shell/cs-labels';
import MailGate from './MailGate';
import './ticket-detail-v3.css';
import './mailbox-v3.css';

// 메일함 MB0100. 위: 내 메일함의 받은 메일 전체와 연결 상태(KAN-238, 행을 누르면 원문에서 꺼낸 본문을 본다).
// 아래: 기획서 4.1 ⑦ 회신 검토. 새 문의·RFP 접수는 업무 홈의 후보 표(KAN-237·KAN-73)에서 한다.
// 시스템이 어느 문의의 답장인지 정하지 못한 메일(플랫폼 회신 검토 API)만 사람이 연결하거나 업무 아님으로 닫는다.
// 후보는 답장 헤더·대화 묶음·주고받은 주소·고객 연락처에서 오고 후보마다 근거가 붙는다(KAN-235). 후보 밖 문의도 번호로 찾아
// 연결할 수 있으며 서버가 누가 무엇을 근거로 골랐는지 남긴다. 후보와 근거로 자동 연결하지 않는다.
// 읽음 상태는 보이지 않는다(본문은 플랫폼에 저장하지 않는다).

type Review = ReplyReview;
type Evidence = NonNullable<ReviewCandidate['evidence']>[number];
type Cand = Pick<ReviewCandidate, 'owner_service' | 'business_ref' | 'ticket'> & { evidence: Evidence[]; method: ReviewCandidate['method'] | 'SEARCH' };
type Reason = Review['reason'];
type Filter = 'ALL' | Reason;

const REASON_LABEL: Record<Reason, string> = {
  AMBIGUOUS: '후보가 여럿', UNMATCHED_REPLY: '답장 대상 못 찾음', CONFLICT: '근거가 서로 다름', TRIAGE_DISAGREES: '자동 분류와 다름', ADDRESS_MATCH: '주고받은 주소에서 옴',
};
const METHOD_LABEL: Record<Cand['method'], string> = {
  HEADER: '답장 헤더가 가리킴', THREAD: '같은 대화로 묶임', ADDRESS: '이 주소와 주고받은 문의', CONTACT: '같은 고객의 진행 중 문의', SEARCH: '문의 번호로 찾음',
};
const EVIDENCE_LABEL: Record<Evidence, string> = {
  ORIGIN_ADDRESS: '이 주소로 접수된 문의', SENT_TO_ADDRESS: '이 주소로 보낸 문의', CUSTOMER_CONTACT: '등록된 고객 연락처',
  WAITING_CUSTOMER: '고객 회신 대기 중', PRODUCT_MATCH: '본문에 같은 제품', ERROR_CODE_MATCH: '본문에 같은 오류 코드',
};
const ticketIdOf = (ref: string) => /^cs_ticket:([0-9a-fA-F-]{36})$/.exec(ref)?.[1];
const FILTERS: [Filter, string][] = [['ALL', '전체'], ...(Object.keys(REASON_LABEL) as Reason[]).map(key => [key, REASON_LABEL[key]] as [Filter, string])];
const keyOf = (c: Pick<Cand, 'owner_service' | 'business_ref'>) => `${c.owner_service}:${c.business_ref}`;
const stamp = (iso: string) => formatStamp(iso).slice(5);
const initials = (r: Review) => (r.from_address ?? '?').replace(/^[^A-Za-z0-9가-힣]+/, '').slice(0, 2).toUpperCase() || '?';
const numberOf = (t: { external_id: string | null; id: string }) => t.external_id ?? t.id.slice(0, 8);

const TYPE_LABEL: Record<NonNullable<InboxMessage['mail_type']>, string> = {
  NEW_RFP: 'RFP로 보임', CS: '문의로 보임', CANDIDATE_REPLY: '회신으로 보임', EXISTING_CHANGE: '변경 요청으로 보임', OTHER: '업무 아님',
};
const INBOX_ICON = <svg viewBox="0 0 20 20"><path d="M3 11l2-6h10l2 6v4H3v-4zm0 0h4l1 2h4l1-2h4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;
const LINK_ICON = <svg viewBox="0 0 20 20"><path d="M8.5 11.5a3 3 0 0 0 4.2 0l2.6-2.6a3 3 0 0 0-4.2-4.2l-1 1m-.6 4.6a3 3 0 0 0-4.2 0l-2.6 2.6a3 3 0 0 0 4.2 4.2l1-1" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;
const stop = (event: MouseEvent) => event.stopPropagation();
// 연결 > 열린 회신 검토 > 최신 분류 > 미연결 순으로 하나만 보인다.
function linkState(m: InboxMessage) {
  const hit = m.link && /^(cs_ticket|rfp_project):([0-9a-fA-F-]{36})$/.exec(m.link.business_ref);
  if (hit?.[1] === 'cs_ticket') return <Link to={`/cs/tickets/${hit[2]}`} onClick={stop}>문의 {hit[2].slice(0, 8)}</Link>;
  if (hit?.[1] === 'rfp_project') return <Link to={`/tft/projects/${hit[2]}`} onClick={stop}>RFP 프로젝트</Link>;
  if (m.link) return '업무 연결됨';
  if (m.reply_review_open) return '회신 검토 중';
  return m.mail_type ? TYPE_LABEL[m.mail_type] : '미연결';
}

function Inbox() {
  const mode = getApiMode();
  const list = useQuery({ queryKey: ['mail-messages', mode], queryFn: ({ signal }) => listMailMessages(signal), retry: false, staleTime: 0 });
  const [open, setOpen] = useState<InboxMessage | null>(null);
  const items = list.data ?? [];
  const columns: LogColumn<InboxMessage>[] = [
    { key: 'received', header: '받은 시각', width: 130, className: 'c', cell: row => formatStamp(row.received_at) },
    { key: 'from', header: '보낸 사람', width: 220, className: 'who', cell: row => row.from_address ?? '보낸 사람 미확인' },
    { key: 'subject', header: '제목', className: 't', cell: row => <>{row.subject || '(제목 없음)'}{row.has_attachments && <span className="chip">첨부</span>}</> },
    { key: 'link', header: '연결 상태', width: 150, cell: linkState },
    { key: 'act', header: '', width: 70, className: 'c', cell: row => <button className="kb3 sm" onClick={event => { stop(event); setOpen(row); }}>본문</button> },
  ];
  return <section className="pane bx-inbox" aria-label="받은 메일 전체">
    <div className="ph"><h2>받은 메일</h2><span className="cnt">{items.length}</span><div className="r"><span className="meta">최근 100통 · 행을 누르면 본문</span></div></div>
    {list.isPending ? <Loading /> : list.isError ? <div className="ml-empty sm" role="status"><b>받은 메일 목록을 읽지 못했습니다.</b><small>조회로 다시 읽으세요.</small></div>
      : <LogTable label="받은 메일 전체" columns={columns} rows={items} rowKey={row => row.message_id} onRow={setOpen}
        fallback={<div className="ml-empty"><span className="ic" aria-hidden="true">{INBOX_ICON}</span><b>받은 메일이 없습니다.</b></div>} />}
    {open && <Body item={open} onClose={() => setOpen(null)} />}
  </section>;
}

function Body({ item, onClose }: { item: InboxMessage; onClose: () => void }) {
  const content = useQuery({ queryKey: ['mail-content', item.message_id], queryFn: ({ signal }) => getMailContent(item.message_id, signal), retry: false });
  return <KvModal title={content.data?.subject ?? item.subject ?? '(제목 없음)'} onClose={onClose}>
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>{item.from_address ?? '보낸 사람 미확인'} · {formatStamp(item.received_at)} · {linkState(item)}</div>
    {content.isPending ? <div>본문을 읽는 중</div> : content.isError ? <div role="alert">{content.error instanceof ApiError ? content.error.message : '본문을 읽지 못했습니다.'}</div>
      : <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto', margin: 0, font: 'inherit' }}>{content.data.body || '(본문 없음)'}</pre>}
    {content.data?.truncated && <p className="meta">본문이 길어 앞부분 50,000자만 보입니다.</p>}
    <div className="kv-actions"><button type="button" className="kb" onClick={onClose}>닫기</button></div>
  </KvModal>;
}

export default function MailBox() {
  return <MailGate title="메일함"><BoxView /></MailGate>;
}

type Found = { kind: 'candidate'; key: string } | { kind: 'outside'; ticket: TicketItem; key: string } | { kind: 'none'; code: string } | { kind: 'error'; text: string };

function BoxView() {
  const mode = getApiMode();
  const [filter, setFilter] = useState<Filter>('ALL');
  const [search, setSearch] = useState('');
  const [picked, setPicked] = useState('');
  const [pick, setPick] = useState('');
  const [manual, setManual] = useState('');
  const [found, setFound] = useState<Found | null>(null);
  const [finding, setFinding] = useState(false);
  const [memo, setMemo] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [formFor, setFormFor] = useState<string | undefined>(undefined);

  const list = useQuery({ queryKey: ['reply-reviews', mode], queryFn: ({ signal }) => listReplyReviews(signal), retry: false, staleTime: 0 });
  const client = useQueryClient();
  const fetchAgain = () => { void list.refetch(); void client.invalidateQueries({ queryKey: ['mail-messages'] }); };
  useHotkey('F8', fetchAgain, !list.isFetching);

  const all = useMemo(() => [...(list.data ?? [])].sort((a, b) => b.received_at.localeCompare(a.received_at)), [list.data]);
  const needle = search.trim().toLocaleLowerCase();
  const match = (r: Review, f: Filter) => f === 'ALL' || r.reason === f;
  const rows = all.filter(r => match(r, filter) && (!needle || `${r.subject ?? ''} ${r.from_address ?? ''}`.toLocaleLowerCase().includes(needle)));
  const selected = rows.find(r => r.message_id === picked) ?? rows[0];
  // 고른 메일이 바뀌면(처리돼 목록에서 빠진 경우 포함) 이전 메일에 적던 메모·후보·찾기 값을 비운다.
  const shown = useRef<string | undefined>(undefined);
  shown.current = selected?.message_id;
  if (selected?.message_id !== formFor) { setFormFor(selected?.message_id); setPick(''); setManual(''); setFound(null); setMemo(''); }
  const others = selected?.from_address ? all.filter(r => r.from_address === selected.from_address && r.message_id !== selected.message_id) : [];

  // 후보와 근거는 서버가 C/S에서 받아 붙인다. 읽지 못하면 저장된 후보만 근거 없이 보인다.
  const cands = useQuery({ queryKey: ['reply-candidates', mode, selected?.message_id], queryFn: ({ signal }) => getReplyCandidates(selected!.message_id, signal), enabled: !!selected, retry: false, staleTime: 30_000 });
  const items: Cand[] = cands.data?.items.map(c => ({ ...c, evidence: c.evidence ?? [] })) ?? (selected?.candidates ?? []).map(c => ({ ...c, method: c.method as Cand['method'], ticket: null, evidence: [] }));
  const enriched = cands.data?.enriched === true;
  const outside: Cand | undefined = found?.kind === 'outside'
    ? { owner_service: 'cs', business_ref: `cs_ticket:${found.ticket.id}`, method: 'SEARCH', evidence: [],
      ticket: { external_id: found.ticket.external_id ?? null, subject: found.ticket.subject ?? null, state: found.ticket.workflow_state, customer_external_id: '-', product_code: found.ticket.product_code } }
    : undefined;

  const open = (id: string) => { setPicked(id); setPick(''); setManual(''); setFound(null); setMemo(''); setNotice(null); };
  const chosen = [...items, ...(outside ? [outside] : [])].find(c => keyOf(c) === pick);
  const chosenTicket = chosen?.ticket ?? undefined;
  const chosenId = chosen ? ticketIdOf(chosen.business_ref) : undefined;

  async function find() {
    const code = manual.trim();
    if (!code || !selected || finding) return;
    setFinding(true); setFound(null);
    const forId = selected.message_id;
    try {
      const body = await listTicketsBy({ q: code, limit: '20' });
      // 찾는 동안 다른 메일을 골랐으면 늦게 온 결과를 그 메일에 붙이지 않는다.
      if (shown.current !== forId) return;
      const lower = code.toLocaleLowerCase();
      const hit = body.data.items.find(t => (t.external_id ?? '').toLocaleLowerCase() === lower || t.id.toLocaleLowerCase() === lower);
      if (!hit) { setFound({ kind: 'none', code }); return; }
      const ref = `cs_ticket:${hit.id}`;
      const candidate = items.find(c => c.owner_service === 'cs' && c.business_ref === ref);
      if (candidate) { setPick(keyOf(candidate)); setFound({ kind: 'candidate', key: keyOf(candidate) }); }
      else { const key = keyOf({ owner_service: 'cs', business_ref: ref }); setPick(key); setFound({ kind: 'outside', ticket: hit, key }); }
    } catch (error) {
      if (shown.current !== forId) return;
      setFound({ kind: 'error', text: error instanceof ApiError ? error.message : '문의를 찾지 못했습니다. 잠시 뒤 다시 찾으세요.' });
    } finally { setFinding(false); }
  }

  const failure = (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) return '이미 처리된 메일입니다. 다른 곳에서 먼저 연결하거나 닫았을 수 있어 목록을 다시 읽었습니다.';
    if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) return error.message;
    return '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 목록을 다시 읽었습니다. 반영 여부를 확인하세요.';
  };
  async function act(done: string, call: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true); setNotice(null);
    try { await call(); setNotice({ kind: 'ok', text: done }); setPick(''); setMemo(''); setFound(null); setManual(''); }
    catch (error) { setNotice({ kind: 'error', text: failure(error) }); }
    finally { setBusy(false); await list.refetch(); }
  }
  const link = () => {
    if (!selected || !chosen) return;
    const how = chosen.method === 'SEARCH' || (found?.kind === 'candidate' && found.key === keyOf(chosen)) ? '문의 번호로 찾아 연결' : `후보 선택(${METHOD_LABEL[chosen.method]})`;
    const label = chosenTicket?.external_id ?? (chosenId ?? chosen.business_ref).slice(0, 8);
    void act(`${label}에 연결했습니다. 그 문의에 고객 답장으로 기록됩니다.`, () => linkReply(selected.message_id, { owner_service: chosen.owner_service, business_ref: chosen.business_ref, note: memo.trim() || how }));
  };
  const dismiss = () => {
    if (!selected || !memo.trim()) return;
    void act('업무 아님으로 닫았습니다.', () => dismissReply(selected.message_id, memo.trim()));
  };

  const candLabel = (c: Cand) => {
    const id = ticketIdOf(c.business_ref);
    if (c.ticket) return `${c.ticket.external_id ?? id?.slice(0, 8) ?? ''} · ${c.ticket.subject ?? '(제목 없음)'}`;
    return `${c.owner_service === 'cs' ? 'C/S 문의' : '플랫폼 업무'} ${(id ?? c.business_ref).slice(0, 8)}`;
  };

  return <main className="td3 bx3 ml3">
    <div className="title">
      <span className="path">메일 ›</span><h1>메일함</h1><code className="kv-code">MB0100</code>
      <span className="bx-lead">받은 메일과 연결 상태를 보고, 시스템이 문의에 붙이지 못한 고객 답장을 어느 문의에 넣을지 정합니다.</span>
      <label className="bx-search">
        <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
        <input type="text" aria-label="메일 검색" placeholder="제목 · 보낸 사람 검색" value={search} onChange={event => setSearch(event.target.value)} />
      </label>
      <div className="btns">
        <Link className="kb3" to="/mail/account">메일 계정</Link>
        <button className="kb3 pri" disabled={list.isFetching} onClick={fetchAgain}>조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      {mode === 'mock' && <div className="tier-note" role="status">모의 응답 모드 — 합성 예시이며 실제 메일이 아닙니다.</div>}
      <Inbox />
      {notice && <div className={notice.kind === 'error' ? 'note err' : 'note'} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</div>}
      <section className="pane bx-review" aria-label="연결 필요">
        <div className="ph"><h2>연결이 필요한 고객 답장</h2><span className={all.length ? 'cnt hot' : 'cnt'}>{all.length}</span>
          <div className="r"><span className="meta bx-scope" role="note">연결이 필요한 고객 답장만 보입니다. 새 RFP 메일은 RFP 업무 홈에서 확인 후 등록하고, 새 문의는 문의 조회로 들어옵니다.</span></div>
        </div>
      {list.isFetching && !list.data && <Loading />}
      {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
      {list.isSuccess && all.length === 0 && <div className="ml-empty"><span className="ic ok" aria-hidden="true">{LINK_ICON}</span><b>연결할 고객 답장이 없습니다.</b></div>}
      {list.isSuccess && all.length > 0 && <div className="bx-wrap">
        <section className="bx-list" aria-label="받은 메일">
          <div className="bx-pills" role="tablist" aria-label="메일 거르기">
            {FILTERS.map(([key, label]) => <button key={key} role="tab" aria-selected={filter === key} className={filter === key ? 'on' : undefined} onClick={() => setFilter(key)}>
              {label} <b>{all.filter(r => match(r, key)).length}</b></button>)}
          </div>
          <ul>
            {rows.length === 0 && <li className="none">{all.length === 0 ? '연결할 고객 답장이 없습니다.' : '조건에 맞는 메일이 없습니다.'}</li>}
            {rows.map(r => <li key={r.message_id}><button className={r.message_id === selected?.message_id ? 'row on' : 'row'} onClick={() => open(r.message_id)}>
              <span className={r.from_address ? 'av' : 'av gray'} aria-hidden="true">{initials(r)}</span>
              <span className="b">
                <span className="top"><span className="who">{r.from_address ?? '보낸 사람 미확인'}</span><span className="when">{stamp(r.received_at)}</span></span>
                <span className="subj">{r.subject || `(제목 없음) ${r.message_id.slice(0, 8)}`}</span>
                <span className="tags">
                  <span className="chip amber">연결 필요</span>
                  <span className="chip">{REASON_LABEL[r.reason]}</span>
                  <span className="chip">후보 {r.candidates.length}건</span>
                  {r.has_attachments && <span className="chip">첨부</span>}
                </span>
              </span>
            </button></li>)}
          </ul>
        </section>

        {selected ? <section className="bx-detail" aria-label="메일 내용">
          <header className="bx-dh">
            <div className="who">
              <span className="tags"><span className="chip amber">연결 필요</span><span className="chip">{REASON_LABEL[selected.reason]}</span></span>
              <h2>{selected.subject || '(제목 없음)'}</h2>
              <p><b>{selected.from_address ?? '보낸 사람 미확인'}</b> · {formatStamp(selected.received_at)} · 메일 {selected.message_id.slice(0, 8)}</p>
            </div>
            {chosenId && <Link className="kb3 pri big" to={`/cs/tickets/${chosenId}`}>고른 문의 열기</Link>}
          </header>
          <div className="bx-cols">
            <div className="bx-main">
              <div className="bx-link">
                <div className="h"><b>어느 문의의 답장인지 정해 주세요</b><small>시스템이 하나로 정하지 못했습니다({REASON_LABEL[selected.reason]}). 후보의 근거를 보고 고르거나, 문의 번호로 찾아 연결하세요. 연결하면 고른 근거가 기록에 남습니다.</small></div>
                {cands.isError && <p className="meta" role="status">근거를 읽지 못해 저장된 후보만 보입니다.</p>}
                {cands.isSuccess && !enriched && <p className="meta" role="status">업무 서버에 연결되지 않아 후보를 근거 없이 보입니다.</p>}
                {items.length > 0 ? <ul aria-label="연결 후보">
                  {items.map(c => { const key = keyOf(c); const t = c.ticket; return <li key={key}><label className={pick === key ? 'cand on' : 'cand'}>
                    <input type="radio" name="cand" aria-label={`${candLabel(c)} 선택`} checked={pick === key} onChange={() => setPick(key)} />
                    <span className="b"><b>{candLabel(c)}</b>
                      {t && <span className="st">{STATE_LABEL[t.state as keyof typeof STATE_LABEL] ?? t.state} · {t.customer_external_id} · {t.product_code}</span>}
                      <span className="why"><span className="chip">{METHOD_LABEL[c.method]}</span>
                        {c.evidence.filter(e => !(c.method === 'CONTACT' && e === 'CUSTOMER_CONTACT')).map(e => <span key={e} className={e === 'WAITING_CUSTOMER' ? 'chip amber' : 'chip blue'}>{EVIDENCE_LABEL[e]}</span>)}
                        {found?.kind === 'candidate' && found.key === key && <span className="chip blue">문의 번호로 찾음</span>}</span></span>
                  </label></li>; })}
                </ul> : cands.isPending ? <p className="meta">후보를 찾는 중</p>
                  : <p className="meta" role="status">이 주소로 주고받은 문의나 등록된 고객 연락처가 없어 후보를 찾지 못했습니다. 문의 번호로 찾아 연결하거나, 업무 메일이 아니면 업무 아님으로 닫으세요.</p>}
                <form className="manual" onSubmit={event => { event.preventDefault(); void find(); }}>
                  <input aria-label="문의 번호로 찾기" placeholder="문의 번호로 찾기 예: CS-2026-0001" value={manual} onChange={event => { setManual(event.target.value); setFound(null); }} />
                  <button type="submit" className="kb3" disabled={!manual.trim() || finding}>{finding ? '찾는 중' : '찾기'}</button>
                </form>
                {found?.kind === 'none' && <p className="meta" role="status">{found.code} 문의를 찾지 못했습니다.</p>}
                {found?.kind === 'error' && <p className="meta" role="alert">{found.text}</p>}
                {found?.kind === 'outside' && <label className={pick === found.key ? 'cand on found' : 'cand found'}>
                  <input type="radio" name="cand" aria-label={`${numberOf(found.ticket)} 선택`} checked={pick === found.key} onChange={() => setPick(found.key)} />
                  <span className="b"><b><Link to={`/cs/tickets/${found.ticket.id}`}>{numberOf(found.ticket)} · {found.ticket.subject ?? '(제목 없음)'}</Link></b>
                    <span className="st">{STATE_LABEL[found.ticket.workflow_state]} · {found.ticket.product_code}</span>
                    <span className="why"><span className="chip blue">문의 번호로 찾음</span><span className="chip">후보 밖</span></span></span>
                </label>}
                <label className="memo"><small>처리 메모 · 기록에 남음 (업무 아님은 필수)</small>
                  <input aria-label="처리 메모" maxLength={500} value={memo} onChange={event => setMemo(event.target.value)} placeholder="예: 답장 헤더가 가리키는 문의, 광고 메일" />
                </label>
                <div className="acts">
                  <button className="kb3" disabled={busy || !memo.trim()} title={memo.trim() ? undefined : '처리 메모에 업무가 아닌 이유를 적으세요'} onClick={dismiss}>업무 아님</button>
                  <button className="kb3 pri" disabled={busy || !chosen} title={chosen ? undefined : '후보를 고르거나 문의 번호로 찾은 뒤 누르세요'} onClick={link}>이 문의에 연결</button>
                </div>
              </div>
              <div className="bx-orig"><small>본문</small><p className="meta">본문은 위 받은 메일 표의 [본문]으로 봅니다(플랫폼에 저장하지 않음). 여기서는 어느 문의에 붙일지만 정합니다.</p></div>
            </div>
            <aside className="bx-ctx" aria-label="메일 맥락">
              <h3>메일</h3>
              <dl>
                <dt>보낸 사람</dt><dd>{selected.from_address ?? '-'}</dd>
                <dt>받은 시각</dt><dd>{formatStamp(selected.received_at)}</dd>
                <dt>첨부</dt><dd>{selected.has_attachments == null ? '-' : selected.has_attachments ? '있음' : '없음'}</dd>
                <dt>검토 사유</dt><dd>{REASON_LABEL[selected.reason]}</dd>
              </dl>
              <h3>고른 문의</h3>
              <dl>
                <dt>고객</dt><dd>{chosenTicket?.customer_external_id ?? '-'}</dd>
                <dt>제품</dt><dd>{chosenTicket?.product_code ?? '-'}</dd>
                <dt>상태</dt><dd>{chosenTicket ? STATE_LABEL[chosenTicket.state as keyof typeof STATE_LABEL] ?? chosenTicket.state : '-'}</dd>
                <dt>근거</dt><dd>{chosen ? METHOD_LABEL[chosen.method] : '-'}</dd>
              </dl>
              <h3>같은 주소의 다른 연결 필요 메일 <b>{others.length}</b></h3>
              {others.length === 0 ? <p className="meta">없음</p> : <ul>{others.map(r => <li key={r.message_id}><button onClick={() => open(r.message_id)}><b>{r.subject || '(제목 없음)'}</b><small>{stamp(r.received_at)}</small></button></li>)}</ul>}
            </aside>
          </div>
        </section> : <section className="bx-detail empty"><p>{all.length === 0 ? '연결할 고객 답장이 없습니다.' : '왼쪽에서 메일을 고르세요.'}</p></section>}
      </div>}
      </section>
    </div>
  </main>;
}
