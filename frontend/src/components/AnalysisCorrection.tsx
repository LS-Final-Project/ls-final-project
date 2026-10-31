import { useState } from 'react';
import { ApiError } from '../api/client';
import type { TicketAnalysisResponse } from '../api/cs';
import { isVersionConflict, type CorrectionBody } from '../api/cs-workflow';
import { CATEGORY_LABEL, categoryName } from '../ls-shell/cs-labels';

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
      setError(isVersionConflict(cause) || (cause instanceof ApiError && cause.status === 409) ? '다른 사용자가 먼저 정정했습니다. 최신 분류를 다시 읽었으니 확인한 뒤 다시 저장하세요.'
        : cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408 ? cause.message
          : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 분류를 읽었습니다. 반영 여부를 확인하세요.');
    } finally { setBusy(false); }
  }
  const select = (label: string, value: string, set: (next: string) => void, options: [string, string][], empty?: string) =>
    <div className="kv-field"><span>{label}</span><select aria-label={label} value={value} onChange={event => set(event.target.value)}>
      {empty !== undefined && <option value="">{empty}</option>}{options.map(([code, text]) => <option key={code} value={code}>{text}</option>)}</select></div>;
  const categories = Object.entries(CATEGORY_LABEL).map(([code, text]) => [code, `${text} (${code})`] as [string, string]);
  return <form aria-label="분류 정정" onSubmit={event => { event.preventDefault(); void submit(); }}>
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>AI 분류를 사람이 정정합니다. 바꾼 칸만 새 기록으로 남고, AI가 쓴 값과 업무 상태는 바뀌지 않습니다. 지금 주분류: {categoryName(current('category_code'))}</div>
    {select('주분류', category, setCategory, categories)}
    {select('보조분류', secondary, setSecondary, categories, '없음(바꾸지 않음)')}
    {select('긴급 여부', urgency, setUrgency, Object.entries(URGENCY_LABEL))}
    <div className="kv-field"><span className="req">정정 사유</span><textarea aria-label="정정 사유" rows={3} required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} /></div>
    {!changed && <div className="kv-sub" style={{ margin: '6px 0 0' }}>바꾼 칸이 없습니다.</div>}
    {error && <div className="kv-sub" role="alert" style={{ margin: '6px 0 0' }}>{error}</div>}
    <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={onDone}>취소</button><button type="submit" className="kb primary" disabled={busy || !changed || !reason.trim()}>{busy ? '저장 중' : '정정 저장'}</button></div>
  </form>;
}
