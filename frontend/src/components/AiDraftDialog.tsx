import { useMemo, useState } from 'react';
import type { TicketAnalysisResponse } from '../api/cs';
import { formatStamp } from '../ls-shell/cs-labels';

type Analysis = TicketAnalysisResponse['data'];
type DraftRun = NonNullable<Analysis['drafts']>[number];
type Option = { label: string; sentences: { text: string; kind: string; evidence_chunk_ids: string[] }[]; info_requests: { info_code: string; question: string }[] };
type Verdict = { label: string; sentence_index: number; verdict: string; reason: string };

const OPTION_LABEL: Record<string, string> = { CONCISE: '간결하게', DETAILED: '자세하게', INFO_FIRST: '부족한 정보 먼저' };
const KIND_LABEL: Record<string, string> = { EVIDENCE: '근거 있음', CONDITION: '조건', UNCONFIRMED: '확인 안 됨' };
const VERDICT_LABEL: Record<string, string> = { SUPPORTED: '근거 확인', UNSUPPORTED: '근거 부족', UNVERIFIABLE: '검증 불가' };
const OWN = 'OWN';

// 최신 유효 초안을 고르고, 없으면 내용이 있는 가장 최근 초안, 그것도 없으면 가장 최근 시도(실패 안내용)를 고른다. 초안은 사람이 읽고 고치는 제안이다. 보내기는 이 대화상자에서 하지 않는다.
export function pickDraft(analysis: Analysis | undefined): DraftRun | undefined {
  const drafts = analysis?.drafts ?? [];
  return drafts.find(item => item.id === analysis?.latest_valid_draft_id) ?? [...drafts].reverse().find(item => item.draft) ?? drafts.at(-1);
}
const optionsOf = (run: DraftRun | undefined): Option[] => Array.isArray((run?.draft as { options?: unknown } | null)?.options) ? (run!.draft as { options: Option[] }).options : [];
const textOf = (option: Option | undefined) => option ? option.sentences.map(item => item.text).join(' ') : '';

/**
 * AI 초안 3개를 보고 하나를 고르거나 직접 쓰는 대화상자. 문장마다 근거 표시와 검증 결과를 함께 보인다.
 * 고른 문안은 고쳐 쓸 수 있고 복사할 수 있다. 고객에게 보내는 동작은 연결하지 않았다.
 */
export default function AiDraftDialog({ analysis, ticketLabel }: { analysis: Analysis | undefined; ticketLabel: string }) {
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
  const choose = (label: string) => { setPicked(label); setText(label === OWN ? '' : textOf(options.find(item => item.label === label))); setCopied(''); };
  const verdictOf = (label: string, index: number) => verdicts.find(item => item.label === label && item.sentence_index === index);
  async function copy() {
    try { await navigator.clipboard.writeText(text); setCopied('복사했습니다.'); }
    catch { setCopied('복사하지 못했습니다. 미리보기 글을 직접 선택해 복사하세요.'); }
  }
  return <div>
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>{ticketLabel}{run ? <> · <span className="unc">AI 작성 {formatStamp(run.created_at)}, 검증 {run.verification_status}</span></> : null}</div>
    {options.length === 0 && <div className="kv-sub" role="note" style={{ margin: '0 0 8px' }}>{run ? 'AI 초안을 쓸 수 없습니다(검증을 통과하지 못했거나 비어 있음). 직접 쓸 수 있습니다.' : '아직 AI 초안이 없습니다. 분석을 실행하면 만들어집니다. 직접 쓸 수 있습니다.'}</div>}
    <div className="kv-field"><span>문안</span>
      <div className="kv-check" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 6 }} role="radiogroup" aria-label="문안">
        {options.map(item => <label key={item.label}><input type="radio" name="draft-option" checked={picked === item.label} onChange={() => choose(item.label)} />{OPTION_LABEL[item.label] ?? item.label}{item.label === recommended?.label ? ' (추천)' : ''}</label>)}
        <label><input type="radio" name="draft-option" checked={picked === OWN} onChange={() => choose(OWN)} />직접 쓰기</label>
      </div></div>
    <div className="kv-field"><span>미리보기</span><textarea aria-label="미리보기" rows={5} value={text} onChange={event => { setText(event.target.value); setCopied(''); }} /></div>
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
      <button type="button" className="kb primary" disabled title="최초 발송은 팀 확인 전까지 연결하지 않았습니다. 문안을 복사해 메일 계정에서 보내거나 발송 허락 뒤 연결됩니다.">보내기</button>
    </div>
  </div>;
}
