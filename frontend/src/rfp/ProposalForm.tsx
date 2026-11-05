import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { createProposal, type Necessity, type Question, type RequirementDetail, type RequirementStatus } from '../api/rfp';
import { KvBox } from '../ls-shell/kv';
import { label, NECESSITY_LABEL, OPERATOR_LABEL, STATUS_LABEL } from './labels';
import { Failure, settled, useAttemptKey } from './proposal-shared';
import './ProposalForm.css';

const SAVE_CONFLICT: Record<string, string> = {
  TFT_VERSION_CONFLICT: '다른 사람이 먼저 바꿨습니다. 다시 불러와 최신 판단을 본 뒤 저장하세요. 입력한 내용은 남습니다.',
};
const parts = (text: string) => text.split(',').map(item => item.trim()).filter(Boolean);

// 변경 제안 입력(TF_K_Table_Propose). 바꿀 항목만 보낸다. 조건 칸은 이름·값 구조라 여기서 바꾸지 않고 보여 주기만 한다.
export default function ProposalForm({ projectId, requirement, onCancel, onSaved, onReload, sourceQuestion }: {
  sourceQuestion?: Question;
  projectId: string;
  requirement: RequirementDetail;
  onCancel: () => void;
  onSaved: () => void;
  // 저장 실패 뒤 최신 내용만 다시 읽는다. 폼을 닫지 않아 입력이 남는다.
  onReload: () => void;
}) {
  const { judgement, spec } = requirement;
  const [baseVersion, setBaseVersion] = useState(requirement.version);
  const [conditions, setConditions] = useState<typeof spec.conditions | null>(null);
  const unresolvedNow = judgement.unresolved.join(', ');
  const [status, setStatus] = useState('');
  const [necessity, setNecessity] = useState('');
  // 손대기 전엔 null이다. 입력칸에 값을 복사해 두면 다시 불러온 뒤 옛 값이 변경으로 잘못 제안된다.
  const [unresolved, setUnresolved] = useState<string | null>(null);
  const [resolvedItems, setResolvedItems] = useState<string[]>([]);
  const remainingItems = judgement.unresolved.filter(item => !resolvedItems.includes(item));
  const [rationale, setRationale] = useState('');
  const [evidence, setEvidence] = useState(sourceQuestion?.reply?.source_ref ?? '');
  const [changeSpec, setChangeSpec] = useState(false);
  const [value, setValue] = useState(spec.value ?? '');
  const [unit, setUnit] = useState(spec.unit ?? '');
  const [operator, setOperator] = useState(spec.operator);
  const [tolerance, setTolerance] = useState(spec.tolerance ?? '');
  const [confirmed, setConfirmed] = useState(false);
  const proposed = {
    ...(conditions !== null ? { conditions } : {}),
    ...(changeSpec ? { spec: { value: value.trim() || null, unit: unit.trim() || null, operator, tolerance: tolerance.trim() || null } } : {}),
    ...(status && status !== judgement.status ? { status: status as RequirementStatus } : {}),
    ...(necessity && necessity !== spec.necessity ? { necessity: necessity as Necessity } : {}),
    ...(unresolved !== null && parts(unresolved).join(', ') !== unresolvedNow ? { unresolved: parts(unresolved) } : unresolved === null && resolvedItems.length > 0 ? { unresolved: remainingItems } : {}),
  };
  const body = { requirement_version: baseVersion, ...(sourceQuestion ? { source_question_id: sourceQuestion.question_id } : {}), requirement_id: requirement.requirement_id, proposed, rationale: rationale.trim(), evidence_refs: parts(evidence) };
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const create = useMutation({
    mutationFn: () => createProposal(projectId, body, key),
    onSuccess: () => { nextKey(); onSaved(); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  // 원문 확인 표시가 있는 요구는 값을 그대로 두고 '맞다'만 제안할 수 있다.
  const ready = (Object.keys(proposed).length > 0 || requirement.needs_review) && rationale.trim() !== '' && (!sourceQuestion || confirmed) && (conditions ?? []).every(c => c.name.trim()) && baseVersion === requirement.version;
  return <div className="rfp-proposal-form" style={{ margin: '8px 14px 0' }}>
    <KvBox title={`변경 제안, ${requirement.code} ${spec.attribute ?? ''}`} note="바꿀 항목만 적습니다. 저장하면 승인 대기로 들어가고 승인 뒤 반영">
      {baseVersion !== requirement.version && <p role="alert">요구가 바뀌었습니다. 현재 값과 입력한 제안을 대조한 뒤 계속하세요. <button type="button" className="kb" onClick={() => { setBaseVersion(requirement.version); setConfirmed(false); }}>최신 요구를 확인했습니다</button></p>}
      <form onSubmit={event => { event.preventDefault(); if (ready) create.mutate(); }}>
        {sourceQuestion?.reply && <div className="kv-text" style={{ whiteSpace: 'pre-wrap', marginBottom: 12 }}><strong>반영 근거인 회신</strong><p>{sourceQuestion.reply.reply_text}</p><small>출처: {sourceQuestion.reply.source_ref || '기록 없음'}</small></div>}
        <label className="rfp-proposal-check"><input type="checkbox" checked={changeSpec} onChange={e => setChangeSpec(e.target.checked)} /> 고객 요구값·단위 변경</label>
        {changeSpec && <>
          <div className="kv-field"><label htmlFor="proposal-value">요구값</label><input type="text" id="proposal-value" value={value} onChange={e => setValue(e.target.value)} /></div>
          <div className="kv-field"><label htmlFor="proposal-unit">단위</label><input type="text" id="proposal-unit" value={unit} onChange={e => setUnit(e.target.value)} /></div>
          <div className="kv-field"><label htmlFor="proposal-operator">비교 조건</label><select id="proposal-operator" value={operator} onChange={e => setOperator(e.target.value as typeof operator)}>{Object.entries(OPERATOR_LABEL).map(([code, title]) => <option key={code} value={code}>{title}</option>)}</select></div>
          <div className="kv-field"><label htmlFor="proposal-tolerance">허용차</label><input type="text" id="proposal-tolerance" value={tolerance} onChange={e => setTolerance(e.target.value)} /></div>
        </>}
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
        <fieldset style={{ border: '1px solid var(--border, #ddd)', margin: '12px 0' }}><legend>적용 조건</legend>
          {(conditions ?? spec.conditions).map((condition, index) => <div key={index} className="kv-field">
            <label htmlFor={`condition-name-${index}`}>조건 {index + 1}</label>
            <div><input type="text" id={`condition-name-${index}`} aria-label={`조건 ${index + 1} 이름`} value={condition.name} onChange={e => setConditions((conditions ?? spec.conditions).map((row, i) => i === index ? { ...row, name: e.target.value } : row))} />
              <input type="text" aria-label={`조건 ${index + 1} 값`} value={condition.value ?? ''} onChange={e => setConditions((conditions ?? spec.conditions).map((row, i) => i === index ? { ...row, value: e.target.value } : row))} />
              <input type="text" aria-label={`조건 ${index + 1} 단위`} value={condition.unit ?? ''} onChange={e => setConditions((conditions ?? spec.conditions).map((row, i) => i === index ? { ...row, unit: e.target.value || null } : row))} />
              <button type="button" className="kb" onClick={() => setConditions((conditions ?? spec.conditions).filter((_, i) => i !== index))}>조건 {index + 1} 삭제</button></div>
          </div>)}
          <button type="button" className="kb" onClick={() => setConditions([...(conditions ?? spec.conditions), { name: '', value: '', unit: null }])}>조건 추가</button>
        </fieldset>
        {sourceQuestion && judgement.unresolved.length > 0 && <fieldset style={{ border: '1px solid var(--border, #ddd)', margin: '12px 0' }}><legend>이 회신으로 확인된 항목</legend>
          <p className="kv-sub">근거를 확인한 항목만 선택하세요. 선택하지 않은 항목은 미확인으로 남습니다.</p>
          {judgement.unresolved.map(item => <label key={item} className="rfp-proposal-check"><input type="checkbox" checked={resolvedItems.includes(item)} onChange={e => {
            setResolvedItems(e.target.checked ? [...resolvedItems, item] : resolvedItems.filter(row => row !== item));
            setUnresolved(null);
          }} />회신으로 확인: {item}</label>)}
        </fieldset>}
        <div className="kv-field"><label htmlFor="proposal-unresolved">미확인</label>
          <input id="proposal-unresolved" type="text" value={unresolved ?? remainingItems.join(', ')} onChange={event => setUnresolved(event.target.value)} /></div>
        <div className="kv-field"><label htmlFor="proposal-rationale" className="req">제안 사유</label>
          <textarea id="proposal-rationale" rows={2} maxLength={2000} value={rationale} onChange={event => setRationale(event.target.value)} /></div>
        <div className="kv-field"><label htmlFor="proposal-evidence">근거</label>
          <input id="proposal-evidence" type="text" placeholder="쉼표로 구분" value={evidence} onChange={event => setEvidence(event.target.value)} /></div>
        {sourceQuestion && <label className="rfp-proposal-check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> 회신과 근거를 확인하고 바꿀 항목을 직접 선택했습니다.</label>}
        <p className="kv-sub">요구값을 바꿔도 자사 대응 가능 여부는 별도로 확인해야 합니다. 승인 후 비교와 대응 방안을 검토하세요.</p>
        <div className="kv-actions">
          <button type="button" className="kb" onClick={onCancel}>취소</button>
          <button type="submit" className="kb primary" disabled={!ready || create.isPending}>{requirement.needs_review && Object.keys(proposed).length === 0 ? '값 확인 제안' : '제안 저장'}</button>
        </div>
        {create.isError && <Failure error={create.error} onReload={onReload} messages={SAVE_CONFLICT} />}
      </form>
    </KvBox>
  </div>;
}
