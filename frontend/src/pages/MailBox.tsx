import { useMemo, useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { dismissReply, linkReply, listReplyReviews, type ReplyReview } from '../api/mail';
import { getTicket, listTicketsBy, type TicketDetailResponse, type TicketItem } from '../api/cs';
import { ErrorState, Loading } from '../components/states';
import { useHotkey } from '../ls-shell/kv';
import { CHANNEL_LABEL, STATE_LABEL, formatStamp } from '../ls-shell/cs-labels';
import MailGate from './MailGate';
import './ticket-detail-v3.css';
import './mailbox-v3.css';

// 메일함 MB0100. 기획서 4.1 ⑦: 메일은 고객에게 회신하고 답장을 같은 문의에 다시 넣는 데 쓴다. 새 문의 접수는 이 화면의 범위가 아니다.
// 시스템이 어느 문의의 답장인지 정하지 못한 메일(플랫폼 회신 검토 API)만 사람이 후보 중에서 연결하거나 업무 아님으로 닫는다.
// 받은 메일 전체 목록·본문·읽음 상태는 아직 API가 없어 보이지 않는다(본문은 플랫폼에 저장하지 않는다).
// 연결은 서버가 후보 중 하나만 받는다(후보 밖은 422). 번호로 찾은 문의가 후보가 아니면 연결 버튼을 열지 않는다.

type Review = ReplyReview;
type Cand = Review['candidates'][number];
type Ticket = TicketDetailResponse['data'];
type Reason = Review['reason'];
type Filter = 'ALL' | Reason;

const REASON_LABEL: Record<Reason, string> = {
  AMBIGUOUS: '후보가 여럿', UNMATCHED_REPLY: '맞는 후보 없음', CONFLICT: '근거가 서로 다름', TRIAGE_DISAGREES: '자동 분류와 다름',
};
const METHOD_LABEL: Record<Cand['method'], string> = {
  HEADER: '답장 헤더가 가리킴', THREAD: '같은 대화로 묶임', HUMAN: '사람이 지정', BUSINESS: '업무 기록과 일치',
};
const FILTERS: [Filter, string][] = [['ALL', '전체'], ...(Object.keys(REASON_LABEL) as Reason[]).map(key => [key, REASON_LABEL[key]] as [Filter, string])];
const uuid = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const keyOf = (c: Pick<Cand, 'owner_service' | 'business_ref'>) => `${c.owner_service}:${c.business_ref}`;
const stamp = (iso: string) => formatStamp(iso).slice(5);
const initials = (r: Review) => (r.from_address ?? '?').replace(/^[^A-Za-z0-9가-힣]+/, '').slice(0, 2).toUpperCase() || '?';
const numberOf = (t: { external_id: string | null; id: string }) => t.external_id ?? t.id.slice(0, 8);

export default function MailBox() {
  return <MailGate title="메일함"><BoxView /></MailGate>;
}

type Found = { kind: 'candidate'; key: string } | { kind: 'outside'; ticket: TicketItem } | { kind: 'none'; code: string } | { kind: 'error'; text: string };

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
  const fetchAgain = () => { void list.refetch(); };
  useHotkey('F8', fetchAgain, !list.isFetching);

  const all = useMemo(() => [...(list.data ?? [])].sort((a, b) => b.received_at.localeCompare(a.received_at)), [list.data]);
  const needle = search.trim().toLocaleLowerCase();
  const match = (r: Review, f: Filter) => f === 'ALL' || r.reason === f;
  const rows = all.filter(r => match(r, filter) && (!needle || `${r.subject ?? ''} ${r.from_address ?? ''}`.toLocaleLowerCase().includes(needle)));
  const selected = rows.find(r => r.message_id === picked) ?? rows[0];
  // 고른 메일이 바뀌면(처리돼 목록에서 빠진 경우 포함) 이전 메일에 적던 메모·후보·찾기 값을 비운다.
  if (selected?.message_id !== formFor) { setFormFor(selected?.message_id); setPick(''); setManual(''); setFound(null); setMemo(''); }
  const others = selected?.from_address ? all.filter(r => r.from_address === selected.from_address && r.message_id !== selected.message_id) : [];

  // 후보가 C/S 문의(UUID)면 문의 상세를 읽어 번호·제목·상태를 보인다. 읽지 못하면 참조 앞자리만 보인다.
  const csRefs = (selected?.candidates ?? []).filter(c => c.owner_service === 'cs' && uuid.test(c.business_ref)).map(c => c.business_ref);
  const details = useQueries({ queries: csRefs.map(id => ({ queryKey: ['cs-ticket', mode, id], queryFn: ({ signal }: { signal: AbortSignal }) => getTicket(id, signal), retry: false, staleTime: 30_000 })) });
  const ticketOf = (c: Cand): Ticket | undefined => {
    const index = csRefs.indexOf(c.business_ref);
    return c.owner_service === 'cs' && index >= 0 ? details[index]?.data?.data : undefined;
  };

  const open = (id: string) => { setPicked(id); setPick(''); setManual(''); setFound(null); setMemo(''); setNotice(null); };
  const chosen = selected?.candidates.find(c => keyOf(c) === pick);
  const chosenTicket = chosen ? ticketOf(chosen) : undefined;

  async function find() {
    const code = manual.trim();
    if (!code || !selected || finding) return;
    setFinding(true); setFound(null);
    try {
      const body = await listTicketsBy({ q: code, limit: '20' });
      const lower = code.toLocaleLowerCase();
      const hit = body.data.items.find(t => (t.external_id ?? '').toLocaleLowerCase() === lower || t.id.toLocaleLowerCase() === lower);
      if (!hit) { setFound({ kind: 'none', code }); return; }
      const candidate = selected.candidates.find(c => c.owner_service === 'cs' && c.business_ref === hit.id);
      if (candidate) { setPick(keyOf(candidate)); setFound({ kind: 'candidate', key: keyOf(candidate) }); }
      else setFound({ kind: 'outside', ticket: hit });
    } catch (error) {
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
    const how = found?.kind === 'candidate' && found.key === keyOf(chosen) ? '문의 번호로 찾아 연결' : `후보 선택(${METHOD_LABEL[chosen.method]})`;
    const label = chosenTicket ? numberOf(chosenTicket) : chosen.business_ref.slice(0, 8);
    void act(`${label}에 연결했습니다. 그 문의에 고객 답장으로 기록됩니다.`, () => linkReply(selected.message_id, { owner_service: chosen.owner_service, business_ref: chosen.business_ref, note: memo.trim() || how }));
  };
  const dismiss = () => {
    if (!selected || !memo.trim()) return;
    void act('업무 아님으로 닫았습니다.', () => dismissReply(selected.message_id, memo.trim()));
  };

  const candLabel = (c: Cand) => {
    const t = ticketOf(c);
    if (t) return `${numberOf(t)} · ${t.subject ?? '(제목 없음)'}`;
    return `${c.owner_service === 'cs' ? 'C/S 문의' : '플랫폼 업무'} ${c.business_ref.slice(0, 8)}`;
  };

  return <main className="td3 bx3">
    <div className="title">
      <span className="path">메일 ›</span><h1>메일함</h1><code className="kv-code">MB0100</code>
      <span className="bx-lead">시스템이 문의에 붙이지 못한 고객 답장을 어느 문의에 넣을지 정합니다.</span>
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
      <p className="meta bx-scope" role="note">연결이 필요한 고객 답장만 보입니다. 받은 메일 전체 목록과 본문은 아직 API가 없어 메일 계정(Outlook)에서 읽습니다.</p>
      {notice && <div className={notice.kind === 'error' ? 'note err' : 'note'} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</div>}
      {list.isFetching && !list.data && <Loading />}
      {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
      {list.isSuccess && <div className="bx-wrap">
        <section className="bx-list" aria-label="받은 메일">
          <div className="bx-pills" role="tablist" aria-label="메일 거르기">
            {FILTERS.map(([key, label]) => <button key={key} role="tab" aria-selected={filter === key} className={filter === key ? 'on' : undefined} onClick={() => setFilter(key)}>
              {label} <b>{all.filter(r => match(r, key)).length}</b></button>)}
          </div>
          <div className="bx-sub"><span>연결 필요</span><b>{all.length}</b></div>
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
            {chosenTicket && <Link className="kb3 pri big" to={`/cs/tickets/${chosenTicket.id}`}>고른 문의 열기</Link>}
          </header>
          <div className="bx-cols">
            <div className="bx-main">
              <div className="bx-link">
                <div className="h"><b>어느 문의의 답장인지 정해 주세요</b><small>시스템이 하나로 정하지 못했습니다({REASON_LABEL[selected.reason]}). 근거를 보고 고르면 그 문의로 연결됩니다.</small></div>
                {selected.candidates.length > 0 ? <ul aria-label="연결 후보">
                  {selected.candidates.map(c => { const key = keyOf(c); const t = ticketOf(c); return <li key={key}><label className={pick === key ? 'cand on' : 'cand'}>
                    <input type="radio" name="cand" aria-label={`${candLabel(c)} 선택`} checked={pick === key} onChange={() => setPick(key)} />
                    <span className="b"><b>{candLabel(c)}</b>
                      {t && <span className="st">{STATE_LABEL[t.workflow_state]} · {CHANNEL_LABEL[t.channel]} · {t.product_code}</span>}
                      <span className="why"><span className="chip">{METHOD_LABEL[c.method]}</span>{found?.kind === 'candidate' && found.key === key && <span className="chip blue">문의 번호로 찾음</span>}</span></span>
                  </label></li>; })}
                </ul> : <p className="meta">시스템이 찾은 후보가 없습니다. 지금 API는 후보 중에서만 연결할 수 있어, 이 메일은 업무 아님으로 닫거나 메일 계정에서 확인하세요.</p>}
                <form className="manual" onSubmit={event => { event.preventDefault(); void find(); }}>
                  <input aria-label="문의 번호로 찾기" placeholder="문의 번호로 찾기 예: CS-2026-0001" value={manual} onChange={event => { setManual(event.target.value); setFound(null); }} />
                  <button type="submit" className="kb3" disabled={!manual.trim() || finding}>{finding ? '찾는 중' : '찾기'}</button>
                </form>
                {found?.kind === 'none' && <p className="meta" role="status">{found.code} 문의를 찾지 못했습니다.</p>}
                {found?.kind === 'error' && <p className="meta" role="alert">{found.text}</p>}
                {found?.kind === 'outside' && <p className="meta" role="status">
                  <Link to={`/cs/tickets/${found.ticket.id}`}>{numberOf(found.ticket)} · {found.ticket.subject ?? '(제목 없음)'}</Link> ({STATE_LABEL[found.ticket.workflow_state]})는 이 메일의 후보가 아닙니다. 지금 API는 후보 밖 문의에 연결하지 않습니다.
                </p>}
                <label className="memo"><small>처리 메모 · 기록에 남음 (업무 아님은 필수)</small>
                  <input aria-label="처리 메모" maxLength={500} value={memo} onChange={event => setMemo(event.target.value)} placeholder="예: 답장 헤더가 가리키는 문의, 광고 메일" />
                </label>
                <div className="acts">
                  <button className="kb3" disabled={busy || !memo.trim()} title={memo.trim() ? undefined : '처리 메모에 업무가 아닌 이유를 적으세요'} onClick={dismiss}>업무 아님</button>
                  <button className="kb3 pri" disabled={busy || !chosen} title={chosen ? undefined : '후보를 고르거나 문의 번호로 찾은 뒤 누르세요'} onClick={link}>이 문의에 연결</button>
                </div>
              </div>
              <div className="bx-orig"><small>본문</small><p className="meta">본문은 플랫폼에 저장하지 않습니다. 메일 계정(Outlook)에서 읽고, 여기서는 어느 문의에 붙일지만 정합니다.</p></div>
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
                <dt>채널</dt><dd>{chosenTicket ? CHANNEL_LABEL[chosenTicket.channel] : '-'}</dd>
                <dt>제품</dt><dd>{chosenTicket?.product_code ?? '-'}</dd>
                <dt>상태</dt><dd>{chosenTicket ? STATE_LABEL[chosenTicket.workflow_state] : '-'}</dd>
              </dl>
              <h3>같은 주소의 다른 연결 필요 메일 <b>{others.length}</b></h3>
              {others.length === 0 ? <p className="meta">없음</p> : <ul>{others.map(r => <li key={r.message_id}><button onClick={() => open(r.message_id)}><b>{r.subject || '(제목 없음)'}</b><small>{stamp(r.received_at)}</small></button></li>)}</ul>}
            </aside>
          </div>
        </section> : <section className="bx-detail empty"><p>{all.length === 0 ? '연결할 고객 답장이 없습니다.' : '왼쪽에서 메일을 고르세요.'}</p></section>}
      </div>}
    </div>
  </main>;
}
