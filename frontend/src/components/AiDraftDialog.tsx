import { useMemo, useState } from 'react';
import type { TicketAnalysisResponse } from '../api/cs';
import type { SendBody } from '../api/cs-workflow';
import { formatStamp } from '../ls-shell/cs-labels';

type Analysis = TicketAnalysisResponse['data'];
type DraftRun = NonNullable<Analysis['drafts']>[number];
type Option = { label: string; sentences: { text: string; kind: string; evidence_chunk_ids: string[] }[]; info_requests: { info_code: string; question: string }[] };
type Verdict = { label: string; sentence_index: number; verdict: string; reason: string };

const OPTION_LABEL: Record<string, string> = { CONCISE: '간결하게', DETAILED: '자세하게', INFO_FIRST: '부족한 정보 먼저' };
const KIND_LABEL: Record<string, string> = { EVIDENCE: '근거 있음', CONDITION: '조건', UNCONFIRMED: '확인 안 됨' };
const VERDICT_LABEL: Record<string, string> = { SUPPORTED: '근거 확인', UNSUPPORTED: '근거 부족', UNVERIFIABLE: '검증 불가' };
const OWN = 'OWN';
const KIND_NAME = { ANSWER: '답변', INFO_REQUEST: '추가 정보 요청' } as const;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// 보내기에 필요한 것. 보낼 수 없는 이유(disabled)가 있으면 단추를 막고 이유를 보인다.
export type SendOptions = {
  originalSubject: string | null;
  defaultTo: string | null;
  // undefined는 읽는 중, 빈 배열은 연결된 사서함 없음.
  mailboxes: { id: string; label: string }[] | undefined;
  // 사서함 연결 목록을 읽지 못했다.
  mailboxesError?: boolean;
  defaultMailboxId: string | null;
  disabled?: string;
  busy: boolean;
  onSend: (body: Omit<SendBody, 'version'>) => void;
};

// 최신 유효 초안을 고르고, 없으면 내용이 있는 가장 최근 초안, 그것도 없으면 가장 최근 시도(실패 안내용)를 고른다. 초안은 사람이 읽고 고치는 제안이다.
export function pickDraft(analysis: Analysis | undefined): DraftRun | undefined {
  const drafts = analysis?.drafts ?? [];
  return drafts.find(item => item.id === analysis?.latest_valid_draft_id) ?? [...drafts].reverse().find(item => item.draft) ?? drafts.at(-1);
}
const optionsOf = (run: DraftRun | undefined): Option[] => Array.isArray((run?.draft as { options?: unknown } | null)?.options) ? (run!.draft as { options: Option[] }).options : [];
const textOf = (option: Option | undefined) => option ? option.sentences.map(item => item.text).join(' ') : '';

// 안전 문의인데 쓸 초안이 없으면 담당자가 직접 응답해야 한다. 일반 안내 대신 강조 경고를 보인다.
// 기한은 분석 규칙의 첫 응답 기한이고, 정정이 있으면 더 이른 쪽(effective)을 쓴다.
export function safetyWithoutDraft(analysis: Analysis | undefined): { due: string | null } | null {
  if (!analysis || analysis.latest_valid_draft_id) return null;
  const urgency = (analysis.fields as Record<string, { effective_value?: unknown } | undefined> | undefined)?.urgency_flag?.effective_value;
  if (urgency !== 'SAFETY') return null;
  return { due: analysis.rules?.first_response_due?.effective ?? analysis.rules?.ai?.first_response_due ?? null };
}

/**
 * AI 초안 3개를 보고 하나를 고르거나 직접 쓰는 대화상자. 문장마다 근거 표시와 검증 결과를 함께 보인다.
 * 고른 문안은 고쳐 쓸 수 있고 복사할 수 있다. send가 있으면 사람이 확인해 승인한 뒤 고객에게 보낸다(자동 발송 없음).
 */
export default function AiDraftDialog({ analysis, ticketLabel, send }: { analysis: Analysis | undefined; ticketLabel: string; send?: SendOptions }) {
  const run = pickDraft(analysis);
  const options = useMemo(() => optionsOf(run), [run]);
  const verdicts = useMemo<Verdict[]>(() => {
    const latest = [...(run?.verifications ?? [])].sort((a, b) => b.attempt_number - a.attempt_number)[0];
    return (latest?.sentences ?? []) as Verdict[];
  }, [run]);
  const recommended = options.find(item => item.label === 'INFO_FIRST') ?? options[0];
  const [picked, setPicked] = useState<string>(recommended?.label ?? OWN);
  const [text, setText] = useState(textOf(recommended));
  const [copied, setCopied] = useState('');
  const current = options.find(item => item.label === picked);
  const safety = safetyWithoutDraft(analysis);
  const [to, setTo] = useState(send?.defaultTo ?? '');
  const [subject, setSubject] = useState(`Re: ${send?.originalSubject || '문의'}`);
  const [mailboxPick, setMailboxPick] = useState('');
  const [kindPick, setKindPick] = useState<'' | keyof typeof KIND_NAME>('');
  const [dropped, setDropped] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const kind = kindPick || (current && current.info_requests.length > 0 ? 'INFO_REQUEST' : 'ANSWER');
  const mailboxId = mailboxPick || (send?.mailboxes?.some(item => item.id === send.defaultMailboxId) ? send.defaultMailboxId ?? '' : send?.mailboxes?.length === 1 ? send.mailboxes[0].id : '');
  const recipients = to.split(/[\s,;]+/).filter(Boolean);
  const problem = !send ? '' : send.disabled ? send.disabled
    : send.mailboxesError ? '사서함 연결을 읽지 못했습니다. 대화상자를 닫고 다시 여세요'
    : !send.mailboxes ? '사서함 연결을 읽는 중입니다'
    : send.mailboxes.length === 0 ? '연결된 사서함이 없습니다. 메일 계정에서 먼저 연결하세요'
    : !mailboxId ? '보낼 사서함을 고르세요'
    : recipients.length === 0 ? '받는 사람을 입력하세요'
    : !recipients.every(item => EMAIL.test(item)) ? '받는 사람 주소 형식을 확인하세요'
    : !subject.trim() ? '제목을 입력하세요'
    : !text.trim() ? '보낼 문안이 비어 있습니다'
    : !confirmed ? '내용과 받는 사람을 확인했다고 표시하세요' : '';
  function submit() {
    if (!send || problem) return;
    // 고른 문안이 최신 유효 초안의 선택지일 때만 초안 연결을 남긴다(둘 중 하나만 보내면 서버가 거부한다).
    const linked = current && run && run.id === analysis?.latest_valid_draft_id;
    send.onSend({
      kind, mailbox_id: mailboxId, subject: subject.trim(), body: text.trim(), body_type: 'text', to: recipients,
      requested_info_codes: kind === 'INFO_REQUEST' && current ? current.info_requests.map(item => item.info_code).filter(code => !dropped.includes(code)) : [],
      ...(linked ? { draft_id: run.id, draft_option: current.label as 'CONCISE' | 'DETAILED' | 'INFO_FIRST' } : {}),
    });
  }
  const choose = (label: string) => { setPicked(label); setText(label === OWN ? '' : textOf(options.find(item => item.label === label))); setCopied(''); setKindPick(''); setDropped([]); setConfirmed(false); };
  const verdictOf = (label: string, index: number) => verdicts.find(item => item.label === label && item.sentence_index === index);
  async function copy() {
    try { await navigator.clipboard.writeText(text); setCopied('복사했습니다.'); }
    catch { setCopied('복사하지 못했습니다. 미리보기 글을 직접 선택해 복사하세요.'); }
  }
  return <div>
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>{ticketLabel}{run ? <> · <span className="unc">AI 작성 {formatStamp(run.created_at)}, 검증 {run.verification_status}</span></> : null}</div>
    {safety && <div className="kv-err" role="alert" style={{ margin: '0 0 8px', padding: '8px 10px' }}><b>안전 문의입니다.</b> AI 초안이 없어 담당자가 직접 응답해야 합니다.{safety.due ? ` 첫 응답 기한 ${formatStamp(safety.due)}` : ''}</div>}
    {options.length === 0 && !safety && <div className="kv-sub" role="note" style={{ margin: '0 0 8px' }}>{run ? 'AI 초안을 쓸 수 없습니다(검증을 통과하지 못했거나 비어 있음). 직접 쓸 수 있습니다.' : '아직 AI 초안이 없습니다. 분석을 실행하면 만들어집니다. 직접 쓸 수 있습니다.'}</div>}
    <div className="kv-field"><span>문안</span>
      <div className="kv-check" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 6 }} role="radiogroup" aria-label="문안">
        {options.map(item => <label key={item.label}><input type="radio" name="draft-option" checked={picked === item.label} onChange={() => choose(item.label)} />{OPTION_LABEL[item.label] ?? item.label}{item.label === recommended?.label ? ' (추천)' : ''}</label>)}
        <label><input type="radio" name="draft-option" checked={picked === OWN} onChange={() => choose(OWN)} />직접 쓰기</label>
      </div></div>
    <div className="kv-field"><span>미리보기</span><textarea aria-label="미리보기" rows={5} value={text} onChange={event => { setText(event.target.value); setCopied(''); setConfirmed(false); }} /></div>
    {current && <>
      <div className="kv-sub" style={{ margin: '8px 0 4px' }}>문장별 근거와 검증</div>
      <ul aria-label="문장별 근거와 검증" style={{ margin: 0, padding: '0 0 0 16px' }}>
        {current.sentences.map((sentence, index) => {
          const verdict = verdictOf(current.label, index);
          return <li key={index}>{sentence.text}
            <small style={{ display: 'block', color: 'var(--erp-muted)' }}>{KIND_LABEL[sentence.kind] ?? sentence.kind}, 근거 {sentence.evidence_chunk_ids.length}건{verdict ? `, ${VERDICT_LABEL[verdict.verdict] ?? verdict.verdict}: ${verdict.reason}` : ', 검증 결과 없음'}</small></li>;
        })}
      </ul>
      {current.info_requests.length > 0 && <>
        <div className="kv-sub" style={{ margin: '8px 0 4px' }}>이 문안이 고객에게 묻는 정보</div>
        <ul aria-label="요청 정보" style={{ margin: 0, padding: '0 0 0 16px' }}>{current.info_requests.map(item => <li key={item.info_code}>{item.question}<small style={{ color: 'var(--erp-muted)' }}> ({item.info_code})</small></li>)}</ul>
      </>}
    </>}
    <div className="kv-sub" style={{ margin: '8px 0 0' }}>AI 초안은 제안입니다. 사람이 읽고 고친 뒤에만 쓰세요. RMA·방문·보증 승인은 초안이 정하지 않습니다.</div>
    {copied && <div className="kv-sub" role="status" style={{ margin: '6px 0 0' }}>{copied}</div>}
    <div className="kv-actions">
      <button type="button" className="kb" disabled={!text.trim()} onClick={() => void copy()}>문안 복사</button>
    </div>
    {send && <fieldset style={{ margin: '10px 0 0', padding: '8px 10px', border: '1px solid var(--erp-line)' }} disabled={send.busy}>
      <legend>고객에게 보내기</legend>
      <div className="kv-field"><span className="req">종류</span>
        <div className="kv-check" role="radiogroup" aria-label="발송 종류">
          {(Object.keys(KIND_NAME) as (keyof typeof KIND_NAME)[]).map(item => <label key={item}><input type="radio" name="send-kind" checked={kind === item} onChange={() => { setKindPick(item); setConfirmed(false); }} />{KIND_NAME[item]}</label>)}
        </div></div>
      <div className="kv-field"><span className="req">사서함</span>
        <select aria-label="보낼 사서함" value={mailboxId} onChange={event => { setMailboxPick(event.target.value); setConfirmed(false); }}>
          <option value="" disabled>선택</option>{(send.mailboxes ?? []).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></div>
      <div className="kv-field"><span className="req">받는 사람</span><input aria-label="받는 사람" value={to} onChange={event => { setTo(event.target.value); setConfirmed(false); }} placeholder="여러 명이면 쉼표로 구분" /></div>
      <div className="kv-field"><span className="req">제목</span><input aria-label="제목" value={subject} onChange={event => { setSubject(event.target.value); setConfirmed(false); }} /></div>
      {kind === 'INFO_REQUEST' && current && current.info_requests.length > 0 && <div className="kv-field"><span>요청 정보</span>
        <div className="kv-check" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
          {current.info_requests.map(item => <label key={item.info_code}><input type="checkbox" checked={!dropped.includes(item.info_code)} onChange={event => { setDropped(event.target.checked ? dropped.filter(code => code !== item.info_code) : [...dropped, item.info_code]); setConfirmed(false); }} />{item.question}</label>)}
        </div></div>}
      {kind === 'INFO_REQUEST' && current && current.info_requests.length > 0 && <div className="kv-sub" style={{ margin: '0 0 4px' }}>문안을 고쳐 더는 묻지 않는 정보는 체크를 풀어 주세요. 체크한 정보는 보낸 뒤 같은 정보를 다시 묻지 않는 데 쓰입니다.</div>}
      <label className="kv-check" style={{ display: 'flex', gap: 6, margin: '6px 0' }}><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />내용과 받는 사람을 확인했고, 고객에게 메일이 나가는 것을 승인합니다.</label>
      <div className="kv-sub" style={{ margin: '0 0 4px' }}>승인하면 판본이 오르고 플랫폼에 넘어갑니다. 문의 상태는 플랫폼이 접수를 알린 뒤에 바뀝니다.</div>
      <div className="kv-actions">
        {problem && <span className="kv-sub" role="note" style={{ marginRight: 'auto' }}>{problem}</span>}
        <button type="button" className="kb primary" disabled={Boolean(problem) || send.busy} onClick={submit}>승인하고 보내기</button>
      </div>
    </fieldset>}
  </div>;
}
