import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode } from '../api/client';
import { getTicketSummary, getTicketTranslation, type TicketDetailResponse } from '../api/cs';
import { createTicketSummary, createTicketTranslation } from '../api/cs-workflow';
import { CHANNEL_LABEL, formatStamp } from '../ls-shell/cs-labels';
import '../pages/ticket-detail-v3.css';
import { ElloMark, ElloWorking } from './Ello';

type Ticket = TicketDetailResponse['data'];
const hm = (iso: string) => formatStamp(iso).slice(11, 16);
// 요약은 줄마다 한 항목(요약 프롬프트 v2). 줄이 없으면 문장 끝에서 나눈다. 앞 번호·글머리표는 화면이 붙인다.
const lines = (text: string) => (text.includes('\n') ? text.split('\n') : text.split(/(?<=[.!?。])\s+/))
  .map(line => line.replace(/^\s*(?:\d{1,2}[.)]|[-•·*])\s+/, '').trim()).filter(Boolean);

/**
 * 고객 원문 상자: AI 요약, 메일 카드. 원문은 항상 보이고 한국어 번역은 원문 위에 덧붙인다(전환 아님). 문의 처리(CS0102)와 같은 모양이다.
 * 검토 답변(CS0203, 부서 창)에서 쓴다. 저장된 요약·번역은 누구나(문의 조회 범위) 읽고,
 * 새로 만들기(모델 호출, 비용)는 canCreate일 때만 단추가 보인다(서버도 cs_agent·이관받은 dept_staff만).
 */
export default function TicketSourcePane({ ticket, canCreate, onNote }: { ticket: Ticket; canCreate: boolean; onNote?: (kind: 'ok' | 'error', text: string) => void }) {
  const mode = getApiMode();
  const summary = useQuery({ queryKey: ['ticket-summary', mode, ticket.id], queryFn: ({ signal }) => getTicketSummary(ticket.id, signal), retry: false, staleTime: 0 });
  const translation = useQuery({ queryKey: ['ticket-translation', mode, ticket.id], queryFn: ({ signal }) => getTicketTranslation(ticket.id, signal), retry: false, staleTime: 0 });
  const [showKo, setShowKo] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [translating, setTranslating] = useState(false);
  const summaryRecord = summary.data?.data.summary;
  const summaryOff = summary.error instanceof ApiError && summary.error.status === 404;
  const translationRecord = translation.data?.data.translation;
  const translationOff = translation.error instanceof ApiError && translation.error.status === 404;
  const koSource = (ticket.body_language ?? '').split('-')[0] === 'ko';

  async function make(done: string, call: (signal: AbortSignal) => Promise<unknown>, after: () => unknown, set: (on: boolean) => void) {
    set(true);
    try {
      await call(new AbortController().signal);
      onNote?.('ok', done);
    } catch (error) {
      onNote?.('error', error instanceof ApiError ? error.message : '만들지 못했습니다. 잠시 뒤 다시 시도하세요.');
    } finally {
      set(false);
      await after();
    }
  }
  const translateButton = canCreate && <button className="kb3 sm" disabled={translating || translation.isPending} title="모델을 한 번 호출합니다(비용)"
    onClick={() => void make('번역을 만들었습니다.', signal => createTicketTranslation(ticket.id, signal), () => translation.refetch(), setTranslating)}><ElloMark />한국어 번역</button>;
  const card = (subject: string, body: string, when?: ReactNode) => <div className="mail-card">
    <div className="mail-head">
      <span className="avatar" aria-hidden="true">{ticket.customer_external_id.slice(0, 1).toUpperCase()}</span>
      <div className="who"><b>{ticket.customer_external_id}</b><small>{CHANNEL_LABEL[ticket.channel as keyof typeof CHANNEL_LABEL] ?? ticket.channel}</small></div>
      <span className="when">{when ?? formatStamp(ticket.received_at)}</span>
    </div>
    <div className="mail-subj">{subject}</div>
    <div className="mail">{body}</div>
    {(ticket.error_code_reported || ticket.fw_version_reported) && <div className="mail-tags">
      {ticket.error_code_reported && <span className="tag"><small>보고된 오류 코드</small>{ticket.error_code_reported}</span>}
      {ticket.fw_version_reported && <span className="tag"><small>FW</small>{ticket.fw_version_reported}</span>}
    </div>}
  </div>;

  return <>
    <div className="ph">고객 원문 <div className="r">
      {!summaryRecord && !summaryOff && canCreate && <button className="kb3 sm" disabled={summarizing || summary.isPending} title="모델을 한 번 호출합니다(비용)"
        onClick={() => void make('요약을 만들었습니다.', signal => createTicketSummary(ticket.id, signal), () => summary.refetch(), setSummarizing)}><ElloMark />{summarizing ? '요약 중…' : 'AI 요약'}</button>}
      {!koSource && <button className={showKo ? 'kb3 sm on' : 'kb3 sm'} aria-pressed={showKo} onClick={() => setShowKo(value => !value)}>{showKo ? '한국어 번역 숨기기' : '한국어 번역 보기'}</button>}</div></div>
    <div className="pb">
      <div className="sum">
        <div className="h"><span className="ai"><ElloMark />AI 요약</span><span className="meta">{summaryRecord ? hm(summaryRecord.created_at) : ''}</span></div>
        {summarizing && !summaryRecord ? <div className="sk-lines" aria-busy="true"><span><ElloWorking />AI가 원문을 읽고 요약하는 중…</span><i /><i /><i /></div>
          : summaryRecord ? lines(summaryRecord.summary_ko).map((line, index) => <p key={index}><span className="n">{index + 1}</span>{line}</p>)
          : summaryOff ? <p className="meta">요약 기능이 이 배포에서 꺼져 있습니다</p>
          : summary.isPending ? <p className="meta">불러오는 중</p>
          : <p className="meta">아직 요약이 없습니다{canCreate ? '. 위의 "AI 요약"을 누르면 만듭니다.' : ''}</p>}
      </div>
      {showKo && !koSource && <div className="ko-add">
        {translating ? <div className="sk-lines" aria-busy="true"><span><ElloWorking />AI가 원문을 한국어로 옮기는 중…</span><i /><i /><i /></div>
          : translationRecord ? <>
            {translationRecord.stale && <div className="meta" style={{ marginBottom: 6 }}>원문이 바뀌어 다시 번역할 수 있습니다 {translateButton}</div>}
            {card(translationRecord.subject_ko || '(제목 없음)', translationRecord.body_ko, <><span className="ai"><ElloMark />AI 번역</span> {hm(translationRecord.created_at)}</>)}
            <div className="meta" style={{ marginTop: 6 }}>AI 번역은 참고용입니다. 숫자·코드·판단은 아래 원문이 기준입니다.</div>
          </>
          : translationOff ? <div className="meta">번역 기능이 이 배포에서 꺼져 있습니다</div>
          : translation.isPending ? <div className="meta">불러오는 중</div>
          : <div className="meta">아직 번역이 없습니다 {translateButton}</div>}
      </div>}
      <div className="orig-always"><div className="meta">원문</div>{card(ticket.subject || '(제목 없음)', ticket.body)}</div>
    </div>
  </>;
}
