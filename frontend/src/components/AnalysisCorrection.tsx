import { useState } from 'react';
import { ApiError } from '../api/client';
import type { TicketAnalysisResponse } from '../api/cs';
import { isVersionConflict, type CorrectionBody } from '../api/cs-workflow';
import { CATEGORY_LABEL } from '../ls-shell/cs-labels';
import { ElloMark, ElloWorking } from './Ello';

type Analysis = TicketAnalysisResponse['data'];
export const URGENCY_LABEL: Record<string, string> = { SAFETY: '안전', SITE_DOWN: '현장 정지', PROD_IMPACT: '생산 영향', COMMISSION: '시운전', DEADLINE: '기한', NONE: '해당 없음' };

/**
 * AI 분류 정정 양식. 바뀐 칸(주분류·보조분류·긴급 여부)만 사유(필수)와 함께 보낸다.
 * 정정은 사람 입력으로 따로 기록되고 AI 원본 값은 바뀌지 않는다. 고객이 보낸 정보 정정은 이 양식에 없다.
 */
export default function AnalysisCorrection({ analysis, onSubmit, onDone }: { analysis: Analysis; onSubmit: (body: CorrectionBody) => Promise<void>; onDone: () => void }) {
  const fields = (analysis.fields ?? {}) as Record<string, { effective_value?: unknown } | undefined>;
  const current = (key: string) => { const value = fields[key]?.effective_value; return typeof value === 'string' ? value : ''; };
  const [category, setCategory] = useState(current('category_code'));
  const [secondary, setSecondary] = useState(current('secondary_category'));
  const [urgency, setUrgency] = useState(current('urgency_flag'));
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const changes: Partial<CorrectionBody> = {};
  if (category && category !== current('category_code')) changes.category_code = category as CorrectionBody['category_code'];
  if (secondary && secondary !== current('secondary_category')) changes.secondary_category = secondary as CorrectionBody['secondary_category'];
  if (urgency && urgency !== current('urgency_flag')) changes.urgency_flag = urgency as CorrectionBody['urgency_flag'];
  const changed = Object.keys(changes).length > 0;
  const runId = analysis.latest_valid_run_id ?? null;

  async function submit() {
    if (busy || !changed || !reason.trim()) return;
    setBusy(true); setError('');
    try {
      await onSubmit({ version: analysis.version, analysis_id: runId, reason: reason.trim(), ...changes });
      onDone();
    } catch (cause) {
      // 409는 이유가 셋이다. 코드로 나눠 보여 준다(상태가 맞지 않음 / 다른 정정 / 새 AI 분석).
      const code = cause instanceof ApiError && cause.status === 409 ? cause.code : undefined;
      setError(code === 'CS_INVALID_TRANSITION' ? '지금 문의 상태에서는 정정할 수 없습니다. 접수 상태면 먼저 "검토 시작"을 누르세요. 종결된 문의는 정정할 수 없습니다.'
        : code === 'CS_ANALYSIS_STALE' ? '그 사이 새 AI 분석이 나왔습니다. 최신 분류를 다시 읽었으니 확인한 뒤 다시 저장하세요.'
        : code === 'CS_ANALYSIS_VERSION_CONFLICT' || isVersionConflict(cause) ? '다른 사용자가 먼저 정정했습니다. 최신 분류를 다시 읽었으니 확인한 뒤 다시 저장하세요.'
        : cause instanceof ApiError && cause.status === 409 ? `저장하지 못했습니다(${cause.code ?? '409'}). 최신 분류를 다시 읽었으니 확인한 뒤 다시 저장하세요.`
        : cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408 ? cause.message
          : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 분류를 읽었습니다. 반영 여부를 확인하세요.');
    } finally { setBusy(false); }
  }
  const aiOf = (key: string) => { const value = (fields[key] as { ai_value?: unknown } | undefined)?.ai_value; return typeof value === 'string' ? value : ''; };
  const categories = Object.entries(CATEGORY_LABEL).map(([code, text]) => [code, text] as [string, string]);
  const nameIn = (options: [string, string][], code: string) => options.find(([value]) => value === code)?.[1] ?? (code || '없음');
  const row = (label: string, key: string, value: string, set: (next: string) => void, options: [string, string][], empty?: string) => {
    const was = current(key);
    const moved = value !== was && !(empty !== undefined && value === '');
    return <div className="fr">
      <span className="k">{label}</span>
      <div>
        <div className={moved ? 'box edit moved' : 'box edit'}>
          <select aria-label={label} value={value} onChange={event => set(event.target.value)}>
            {empty !== undefined && <option value="">{empty}</option>}{options.map(([code, text]) => <option key={code} value={code}>{text}</option>)}</select>
          {moved ? <span className="chg">바뀜</span> : <span className="ai2">{(fields[key] as { corrected?: boolean } | undefined)?.corrected ? '✓ 사람 정정' : <><ElloMark size={14} />AI</>}</span>}
        </div>
        <div className="was">{moved ? `지금 값 ${nameIn(options, was)} → ${nameIn(options, value)}` : `AI 값 ${nameIn(options, aiOf(key))}`}</div>
      </div>
    </div>;
  };
  return <form aria-label="분류 정정" className="corr" onSubmit={event => { event.preventDefault(); void submit(); }}>
    <div className="sum"><span className="ai"><ElloMark />AI 분류를 사람이 정정합니다</span><p className="meta">바꾼 칸만 새 기록으로 남습니다. AI가 쓴 값과 업무 상태는 바뀌지 않습니다.</p></div>
    {row('주분류', 'category_code', category, setCategory, categories)}
    {row('보조분류', 'secondary_category', secondary, setSecondary, categories, '없음(바꾸지 않음)')}
    {row('긴급 여부', 'urgency_flag', urgency, setUrgency, Object.entries(URGENCY_LABEL))}
    <div className="fr top"><span className="k">정정 사유 <em>*</em></span>
      <textarea aria-label="정정 사유" rows={3} required maxLength={2000} placeholder="예: 원문에 라인 정지가 적혀 있어 긴급으로 바꿈" value={reason} onChange={event => setReason(event.target.value)} /></div>
    {error && <div className="note err" role="alert">{error}</div>}
    <div className="foot">
      <span className="meta">{!changed ? '바꾼 칸이 없습니다.' : !reason.trim() ? '정정 사유를 적어 주세요.' : `${Object.keys(changes).length}개 칸을 정정합니다`}</span>
      <button type="button" className="kb3" disabled={busy} onClick={onDone}>취소</button>
      <button type="submit" className="kb3 pri" disabled={busy || !changed || !reason.trim()}>{busy ? '저장 중' : '정정 저장'}</button>
    </div>
  </form>;
}
