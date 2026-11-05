import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode } from '../api/client';
import { getReplyAnalysis } from '../api/cs';
import { createReplyAnalysis } from '../api/cs-workflow';
import { formatStamp, infoName } from '../ls-shell/cs-labels';
import { ElloMark, ElloWorking } from './Ello';
import './reply-analysis.css';

type Record_ = NonNullable<Awaited<ReturnType<typeof getReplyAnalysis>>['data']['analysis']>;
type Applicability = Record_['interpretation']['applicability'];
type Reply = { message_id: string; received_at: string; body?: string | null };

const REQUEST_LABEL: Record<string, string> = { APPLIES: '같은 요청', DIFFERENT_REQUEST: '다른 요청', UNCLEAR: '불명확' };
const MATCH_LABEL: Record<string, string> = { SAME: '같음', DIFFERENT: '다름', NOT_MENTIONED: '언급 없음', UNCLEAR: '불명확' };
const NEXT: Record<string, { label: string; why: string; tone: 'calm' | 'warn' | 'danger' }> = {
  DRAFT_ANSWER: { label: '답변 작성', why: '요청한 정보를 모두 받았습니다.', tone: 'calm' },
  REQUEST_AGAIN: { label: '부족한 정보 다시 요청', why: '아직 받지 못한 정보가 있습니다.', tone: 'warn' },
  CONFIRM_WITH_CUSTOMER: { label: '고객에게 확인', why: '어느 요청·장비에 대한 답인지 분명하지 않습니다.', tone: 'warn' },
  ESCALATE_TO_AGENT: { label: '담당자 판단 필요', why: '다른 요청·장비·판본에 대한 회신으로 보입니다. 답한 정보를 합치지 않았습니다.', tone: 'danger' },
};
const ERROR_TEXT: Record<string, string> = {
  CS_REPLY_TEXT_MISSING: '본문이 기록된 고객 회신이 없어 해석할 수 없습니다.',
  CS_REPLY_INPUT_INVALID: '문의 정보로 해석 입력을 만들 수 없습니다.',
  CS_REPLY_ANALYSIS_FAILED: 'AI 해석이 실패했습니다. 카드에서 사유를 확인하세요.',
  CS_REPLY_ANALYSIS_CONFLICT: '다른 곳에서 먼저 저장했습니다. 잠시 뒤 다시 여세요.',
  CS_INVALID_OUTPUT: 'AI 결과가 형식 검사를 통과하지 못했습니다.',
  CS_FORBIDDEN_COMMITMENT: 'AI 결과에 약속 문구가 있어 버렸습니다.',
  CS_PROVIDER_TIMEOUT: 'AI 응답이 늦어 멈췄습니다.', CS_RATE_LIMITED: 'AI 호출 한도에 걸렸습니다.', CS_PROVIDER_ERROR: 'AI 호출이 실패했습니다.',
};
const toneOf = (value: string) => (value === 'APPLIES' || value === 'SAME' ? 'ok' : value === 'NOT_MENTIONED' ? 'idle' : value === 'UNCLEAR' ? 'warn' : 'bad');
const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && [...a].sort().every((code, index) => code === [...b].sort()[index]);

/**
 * 고객 회신 반영 분석(KAN-230). 정보를 요청한 뒤 고객이 회신하면 담당자가 눌러 AI 해석을 받는다.
 * 같은 요청·장비·판본인지, 답한 정보, 아직 부족한 정보, 고객이 새로 말한 내용, 다음 행동을 보인다.
 * 판정이 아니다. 답한 정보는 담당자가 확인하고 "제공 정보에 반영"을 눌러야 분류 정정으로 기록된다.
 */
export default function ReplyAnalysisCard({ ticketId, replies, canCreate, provided, apply, onNote }: {
  ticketId: string;
  replies: readonly Reply[];
  canCreate: boolean;
  provided: readonly string[];
  /** 반영할 수 없으면(분석 전·종결 등) 사유 문자열. 반영할 수 있으면 함수. */
  apply: string | ((codes: string[], reason: string) => Promise<void>);
  onNote: (kind: 'ok' | 'error', text: string) => void;
}) {
  const mode = getApiMode();
  const view = useQuery({ queryKey: ['reply-analysis', mode, ticketId], queryFn: ({ signal }) => getReplyAnalysis(ticketId, signal), retry: false, staleTime: 0 });
  const [running, setRunning] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  if (view.error instanceof ApiError && view.error.status === 404) return null;
  const data = view.data?.data;
  const record = data?.analysis ?? null;
  const attempt = data?.latest_attempt ?? null;
  const failed = attempt && attempt.outcome !== 'VALID' && (!record || attempt.created_at > record.created_at) ? attempt : null;
  const target = replies.find(item => item.message_id === (record?.message_id ?? data?.message_id)) ?? [...replies].filter(item => item.body).sort((a, b) => b.received_at.localeCompare(a.received_at))[0];
  const newer = !!record && replies.some(item => item.body && item.received_at > (target?.received_at ?? ''));
  if (!target) return null;

  async function run() {
    setRunning(true);
    try {
      await createReplyAnalysis(ticketId, newer ? null : target!.message_id, new AbortController().signal);
      onNote('ok', '회신을 해석했습니다. 반영 전에 내용을 확인하세요.');
    } catch (error) {
      // 502(CS_REPLY_ANALYSIS_FAILED)의 세부 사유는 다시 읽은 latest_attempt로 카드에 보인다.
      const code = error instanceof ApiError ? error.code : undefined;
      onNote('error', (code && ERROR_TEXT[code]) || (error instanceof ApiError ? error.message : '해석하지 못했습니다. 잠시 뒤 다시 시도하세요.'));
    } finally {
      setRunning(false);
      await view.refetch();
    }
  }

  const update = record?.update;
  const answered = record?.interpretation.answered_codes ?? [];
  const added = update ? update.provided_codes.filter(code => !provided.includes(code)) : [];
  const canApply = !!update && typeof apply === 'function' && !record?.stale && added.length > 0 && !same(update.provided_codes, provided);
  const applyWhy = !update ? '' : record?.stale ? '해석 뒤 문의 정보가 바뀌었습니다. 다시 해석하세요' : typeof apply === 'string' ? apply
    : added.length === 0 ? '새로 반영할 정보가 없습니다' : '';
  const reasonDefault = `고객 회신(${formatStamp(target.received_at).slice(5, 16)}) 반영: ${added.map(infoName).join(', ')}`;
  const button = canCreate && <button type="button" className="kb3 sm" disabled={running || view.isPending} title="모델을 한 번 호출합니다(비용). 같은 회신·입력이면 저장본을 다시 씁니다"
    onClick={() => void run()}><ElloMark size={14} />{record ? (record.stale || newer ? '다시 해석' : '다시 보기') : '회신 반영 분석'}</button>;

  return <section className="ra3" aria-label="회신 반영 분석">
    <div className="ra-head">
      <div><b>회신 반영 분석</b><span className="meta">고객 회신 {formatStamp(target.received_at)} 기준{record ? ` · 해석 ${formatStamp(record.created_at).slice(5, 16)}` : ''}</span></div>
      {button}
    </div>
    {running ? <div className="sk-lines" aria-busy="true"><span><ElloWorking />AI가 회신이 요청한 정보에 답했는지 읽는 중…</span><i /><i /></div>
      : !record ? <p className="meta ra-empty">{failed ? `마지막 시도가 실패했습니다: ${ERROR_TEXT[failed.error_code ?? ''] ?? failed.error_code ?? '알 수 없음'}`
        : view.isPending ? '불러오는 중' : `고객 회신이 도착했습니다. 요청한 정보에 답했는지 AI로 확인할 수 있습니다${canCreate ? '' : '(C/S 담당자만 실행)'}.`}</p>
      : <>
        {(record.stale || newer) && <p className="ra-stale">{newer ? '이 해석 뒤에 새 회신이 왔습니다.' : '해석 뒤 문의 정보가 바뀌었습니다.'} 다시 해석하면 새로 만듭니다.</p>}
        {failed && <p className="ra-stale">마지막 시도는 실패했습니다({ERROR_TEXT[failed.error_code ?? ''] ?? failed.error_code}). 아래는 이전 해석입니다.</p>}
        <div className={`ra-next ${NEXT[update!.next_action]?.tone ?? 'warn'}`}>
          <small>다음 행동</small><b>{NEXT[update!.next_action]?.label ?? update!.next_action}</b><span>{NEXT[update!.next_action]?.why}</span>
        </div>
        <Fit value={record.interpretation.applicability} />
        <div className="ra-codes">
          <div><small>답한 정보 {answered.length}</small>{answered.length ? answered.map(code => <span key={code} className="chip ok">✓ {infoName(code)}</span>) : <span className="meta">없음</span>}</div>
          <div><small>아직 부족 {update!.still_missing.length}</small>{update!.still_missing.length ? update!.still_missing.map(code => <span key={code} className="chip warn">{infoName(code)}</span>) : <span className="meta">없음</span>}</div>
        </div>
        {record.interpretation.new_facts.length > 0 && <div className="ra-list"><small>고객이 새로 말한 내용</small><ul>
          {record.interpretation.new_facts.map((fact, index) => <li key={index}><span className={`kind ${fact.kind === 'STATED' ? 'stated' : 'unc'}`}>{fact.kind === 'STATED' ? '고객 진술' : '불확실'}</span>{fact.text}</li>)}
        </ul></div>}
        {record.interpretation.remaining_questions.length > 0 && <div className="ra-list"><small>아직 답하지 않은 요청</small><ul>
          {record.interpretation.remaining_questions.map((item, index) => <li key={index}><span className="kind ask">{infoName(item.info_code)}</span>{item.question}</li>)}
        </ul></div>}
        <div className="ra-foot">
          {confirming !== null ? <div className="ra-confirm">
            <label>정정 사유<input value={confirming} onChange={event => setConfirming(event.target.value)} /></label>
            <span className="meta">제공 정보에 {added.map(infoName).join(', ')}을(를) 더합니다. 사람 정정으로 기록됩니다.</span>
            <div className="btns">
              <button type="button" className="kb3 sm" disabled={applying} onClick={() => setConfirming(null)}>취소</button>
              <button type="button" className="kb3 sm pri" disabled={applying || !confirming.trim()} onClick={async () => {
                if (typeof apply !== 'function' || !update) return;
                setApplying(true);
                try { await apply(update.provided_codes, confirming.trim()); setConfirming(null); } catch (error) { onNote('error', error instanceof ApiError ? error.message : '반영하지 못했습니다.'); } finally { setApplying(false); }
              }}>반영</button>
            </div>
          </div> : <>
            <span className="meta"><ElloMark size={13} />AI 해석은 참고용입니다. 회신 원문과 함께 확인하세요.</span>
            {update!.next_action !== 'ESCALATE_TO_AGENT' && update!.next_action !== 'CONFIRM_WITH_CUSTOMER' && <button type="button" className="kb3 sm pri" disabled={!canApply} title={applyWhy || '답한 정보를 분류 정정(제공 정보)으로 기록합니다'}
              onClick={() => setConfirming(reasonDefault)}>답한 정보 반영{added.length ? ` ${added.length}` : ''}</button>}
          </>}
        </div>
      </>}
  </section>;
}

function Fit({ value }: { value: Applicability }) {
  const rows = [
    ['요청', REQUEST_LABEL[value.same_request] ?? value.same_request, value.same_request, value.same_request_reason],
    ['장비', MATCH_LABEL[value.same_unit] ?? value.same_unit, value.same_unit, value.same_unit_reason],
    ['판본', MATCH_LABEL[value.same_version] ?? value.same_version, value.same_version, value.same_version_reason],
  ] as const;
  return <div className="ra-fit">{rows.map(([name, label, raw, reason]) => <div key={name} className={`fit ${toneOf(raw)}`}>
    <small>{name}</small><b>{label}</b><span>{reason}</span>
  </div>)}</div>;
}
