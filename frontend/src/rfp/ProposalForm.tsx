import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { createProposal, type Necessity, type RequirementDetail, type RequirementStatus } from '../api/rfp';
import { KvBox } from '../ls-shell/kv';
import { label, NECESSITY_LABEL, STATUS_LABEL } from './labels';
import { Failure, settled, useAttemptKey } from './proposal-shared';

const SAVE_CONFLICT: Record<string, string> = {
  TFT_VERSION_CONFLICT: '다른 사람이 먼저 바꿨습니다. 다시 불러와 최신 판단을 본 뒤 저장하세요. 입력한 내용은 남습니다.',
};
const parts = (text: string) => text.split(',').map(item => item.trim()).filter(Boolean);

// 변경 제안 입력(TF_K_Table_Propose). 바꿀 항목만 보낸다. 조건 칸은 이름·값 구조라 여기서 바꾸지 않고 보여 주기만 한다.
export default function ProposalForm({ projectId, requirement, onCancel, onSaved, onReload }: {
  projectId: string;
  requirement: RequirementDetail;
  onCancel: () => void;
  onSaved: () => void;
  // 저장 실패 뒤 최신 내용만 다시 읽는다. 폼을 닫지 않아 입력이 남는다.
  onReload: () => void;
}) {
  const { judgement, spec } = requirement;
  const unresolvedNow = judgement.unresolved.join(', ');
  const [status, setStatus] = useState('');
  const [necessity, setNecessity] = useState('');
  // 손대기 전엔 null이다. 입력칸에 값을 복사해 두면 다시 불러온 뒤 옛 값이 변경으로 잘못 제안된다.
  const [unresolved, setUnresolved] = useState<string | null>(null);
  const [rationale, setRationale] = useState('');
  const [evidence, setEvidence] = useState('');
  const proposed = {
    ...(status && status !== judgement.status ? { status: status as RequirementStatus } : {}),
    ...(necessity && necessity !== spec.necessity ? { necessity: necessity as Necessity } : {}),
    ...(unresolved !== null && parts(unresolved).join(', ') !== unresolvedNow ? { unresolved: parts(unresolved) } : {}),
  };
  const body = { requirement_id: requirement.requirement_id, proposed, rationale: rationale.trim(), evidence_refs: parts(evidence) };
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const create = useMutation({
    mutationFn: () => createProposal(projectId, body, key),
    onSuccess: () => { nextKey(); onSaved(); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  // 원문 확인 표시가 있는 요구는 값을 그대로 두고 '맞다'만 제안할 수 있다.
  const ready = (Object.keys(proposed).length > 0 || requirement.needs_review) && rationale.trim() !== '';
  return <div style={{ margin: '8px 14px 0' }}>
    <KvBox title={`변경 제안, ${requirement.code} ${spec.attribute ?? ''}`} note="바꿀 항목만 적습니다. 저장하면 승인 대기로 들어가고 승인 뒤 반영">
      <form onSubmit={event => { event.preventDefault(); if (ready) create.mutate(); }}>
        <div className="kv-field"><label htmlFor="proposal-status">상태</label>
          <div className="ro">{label(STATUS_LABEL, judgement.status)} → <select id="proposal-status" value={status} onChange={event => setStatus(event.target.value)}>
            <option value="">그대로</option>
            {Object.entries(STATUS_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
          </select></div></div>
        <div className="kv-field"><label htmlFor="proposal-necessity">성격</label>
          <div className="ro">{label(NECESSITY_LABEL, spec.necessity)} → <select id="proposal-necessity" value={necessity} onChange={event => setNecessity(event.target.value)}>
            <option value="">그대로</option>
            {Object.entries(NECESSITY_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
          </select></div></div>
        {spec.conditions.length > 0 && <div className="kv-field"><span>조건</span>
          <div className="ro">{spec.conditions.map(c => [c.name, c.value, c.unit].filter(Boolean).join(' ')).join(', ')}</div></div>}
        <div className="kv-field"><label htmlFor="proposal-unresolved">미확인</label>
          <input id="proposal-unresolved" type="text" value={unresolved ?? unresolvedNow} onChange={event => setUnresolved(event.target.value)} /></div>
        <div className="kv-field"><label htmlFor="proposal-rationale" className="req">제안 사유</label>
          <textarea id="proposal-rationale" rows={2} maxLength={2000} value={rationale} onChange={event => setRationale(event.target.value)} /></div>
        <div className="kv-field"><label htmlFor="proposal-evidence">근거</label>
          <input id="proposal-evidence" type="text" placeholder="쉼표로 구분" value={evidence} onChange={event => setEvidence(event.target.value)} /></div>
        <div className="kv-actions">
          <button type="button" className="kb" onClick={onCancel}>취소</button>
          <button type="submit" className="kb primary" disabled={!ready || create.isPending}>{requirement.needs_review && Object.keys(proposed).length === 0 ? '값 확인 제안' : '제안 저장'}</button>
        </div>
        {create.isError && <Failure error={create.error} onReload={onReload} messages={SAVE_CONFLICT} />}
      </form>
    </KvBox>
  </div>;
}
