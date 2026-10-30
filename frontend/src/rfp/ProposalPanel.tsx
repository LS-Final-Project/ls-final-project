import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ApiError, getApiMode } from '../api/client';
import {
  approveProposal, createProposal, listProposals, rejectProposal,
  type Necessity, type Proposal, type RequirementDetail, type RequirementStatus,
} from '../api/rfp';
import { ErrorState, Loading } from '../components/states';
import { KvBox, KvField } from '../ls-shell/kv';
import { label, NECESSITY_LABEL, STATUS_LABEL } from './labels';

// 같은 시도의 재전송은 같은 키로 보내 서버가 두 번 처리하지 않게 한다. 거절(4xx)이나 성공 뒤의 새 시도는 새 키다.
function useAttemptKey() {
  const [key, setKey] = useState(() => crypto.randomUUID());
  return [key, () => setKey(crypto.randomUUID())] as const;
}
const settled = (error: unknown) => error instanceof ApiError && error.status < 500;

// 재시도해도 소용없는 충돌은 다음 행동을 알려 준다. 입력한 내용은 지우지 않는다.
const CONFLICT: Record<string, string> = {
  TFT_VERSION_CONFLICT: '요구가 그사이 바뀌어 승인할 수 없습니다. 최신 내용을 보고 다시 제안하세요. 기각은 할 수 있습니다.',
  TFT_PROPOSAL_DECIDED: '이미 결정된 제안입니다. 다시 불러와 확인하세요.',
};
function Failure({ error, onReload }: { error: unknown; onReload: () => void }) {
  const code = error instanceof ApiError ? error.code : undefined;
  if (code && CONFLICT[code]) return <div role="alert" className="kv-next" style={{ margin: '6px 0 0' }}>
    <span>{CONFLICT[code]}</span>
    <button className="kb" onClick={onReload}>다시 불러오기</button>
  </div>;
  return <ErrorState error={error} />;
}

function Change({ title, proposal }: { title: string; proposal: Proposal }) {
  const { current, proposed } = proposal;
  return <>
    <KvField label={title}>{proposal.rationale}</KvField>
    {proposed.status !== current.status && <KvField label="상태">{label(STATUS_LABEL, current.status)} → {label(STATUS_LABEL, proposed.status)}</KvField>}
    {proposed.necessity !== current.necessity && <KvField label="필수 여부">{label(NECESSITY_LABEL, current.necessity)} → {label(NECESSITY_LABEL, proposed.necessity)}</KvField>}
    {proposed.reason !== current.reason && proposed.reason && <KvField label="사유">{proposed.reason}</KvField>}
  </>;
}

function Decision({ proposal, canDecide, onChanged }: { proposal: Proposal; canDecide: boolean; onChanged: () => void }) {
  const [reason, setReason] = useState('');
  const [approveKey, nextApprove] = useAttemptKey();
  const [rejectKey, nextReject] = useAttemptKey();
  const approve = useMutation({
    mutationFn: () => approveProposal(proposal.proposal_id, proposal.version, approveKey),
    onSuccess: () => { nextApprove(); onChanged(); },
    onError: error => { if (settled(error)) nextApprove(); },
  });
  const reject = useMutation({
    mutationFn: () => rejectProposal(proposal.proposal_id, proposal.version, reason.trim(), rejectKey),
    onSuccess: () => { nextReject(); onChanged(); },
    onError: error => { if (settled(error)) nextReject(); },
  });
  const busy = approve.isPending || reject.isPending;
  return <div style={{ padding: '6px 0', borderBottom: '1px solid var(--erp-border-soft)' }}>
    <Change title="제안" proposal={proposal} />
    {canDecide && <>
      <div className="kv-field">
        <label htmlFor={`reject-${proposal.proposal_id}`}>기각 사유</label>
        <input id={`reject-${proposal.proposal_id}`} type="text" maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} />
      </div>
      <div className="kv-btns" style={{ marginTop: 6 }}>
        <button className="kb primary" disabled={busy} onClick={() => approve.mutate()}>승인</button>
        <button className="kb" disabled={busy || !reason.trim()} onClick={() => reject.mutate()}>기각</button>
      </div>
    </>}
    {approve.isError && <Failure error={approve.error} onReload={onChanged} />}
    {reject.isError && <Failure error={reject.error} onReload={onChanged} />}
  </div>;
}

function NewProposal({ projectId, requirement, onChanged }: { projectId: string; requirement: RequirementDetail; onChanged: () => void }) {
  const [status, setStatus] = useState('');
  const [necessity, setNecessity] = useState('');
  const [reason, setReason] = useState('');
  const [rationale, setRationale] = useState('');
  const [key, nextKey] = useAttemptKey();
  const proposed = {
    ...(status && status !== requirement.judgement.status ? { status: status as RequirementStatus } : {}),
    ...(necessity && necessity !== requirement.spec.necessity ? { necessity: necessity as Necessity } : {}),
    ...(reason.trim() ? { reason: reason.trim() } : {}),
  };
  const create = useMutation({
    mutationFn: () => createProposal(projectId, { requirement_id: requirement.requirement_id, proposed, rationale: rationale.trim() }, key),
    onSuccess: () => {
      nextKey(); setStatus(''); setNecessity(''); setReason(''); setRationale(''); onChanged();
    },
    onError: error => { if (settled(error)) nextKey(); },
  });
  const ready = Object.keys(proposed).length > 0 && rationale.trim() !== '';
  return <form style={{ marginTop: 8 }} onSubmit={event => { event.preventDefault(); if (ready) create.mutate(); }}>
    <div className="kv-field"><label htmlFor="proposal-status">바꿀 상태</label>
      <select id="proposal-status" value={status} onChange={event => setStatus(event.target.value)}>
        <option value="">그대로</option>
        {Object.entries(STATUS_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
      </select></div>
    <div className="kv-field"><label htmlFor="proposal-necessity">바꿀 필수 여부</label>
      <select id="proposal-necessity" value={necessity} onChange={event => setNecessity(event.target.value)}>
        <option value="">그대로</option>
        {Object.entries(NECESSITY_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
      </select></div>
    <div className="kv-field"><label htmlFor="proposal-reason">바꿀 사유</label>
      <input id="proposal-reason" type="text" value={reason} onChange={event => setReason(event.target.value)} /></div>
    <div className="kv-field"><label htmlFor="proposal-rationale">제안 근거</label>
      <textarea id="proposal-rationale" rows={2} maxLength={2000} value={rationale} onChange={event => setRationale(event.target.value)} /></div>
    <div className="kv-btns" style={{ marginTop: 6 }}><button className="kb" type="submit" disabled={!ready || create.isPending}>변경 제안</button></div>
    {create.isError && <Failure error={create.error} onReload={onChanged} />}
  </form>;
}

// 요구의 변경 제안. 대기 중인 제안을 결정하고 새 제안을 낸다. 권한은 서버가 다시 검사한다.
export default function ProposalPanel({ projectId, requirement, canDecide }: { projectId: string; requirement: RequirementDetail; canDecide: boolean }) {
  const mode = getApiMode();
  const client = useQueryClient();
  const id = requirement.requirement_id;
  const proposals = useQuery({ queryKey: ['rfp-proposals', mode, projectId, id], queryFn: () => listProposals(projectId, id) });
  const changed = () => {
    for (const key of ['rfp-proposals', 'rfp-requirement', 'rfp-comparison', 'rfp-requirements']) {
      client.invalidateQueries({ queryKey: [key, mode] });
    }
  };
  const items = proposals.data?.data.items ?? [];
  const pending = items.filter(p => p.status === 'PENDING');
  const decided = items.filter(p => p.status !== 'PENDING');
  return <KvBox title="변경 제안" note={proposals.isSuccess ? `대기 ${pending.length}건` : undefined}>
    {proposals.isPending && <Loading />}
    {proposals.isError && <ErrorState error={proposals.error} onRetry={() => proposals.refetch()} />}
    {proposals.isSuccess && pending.length === 0 && <p style={{ margin: 0 }}>대기 중인 제안이 없습니다.</p>}
    {pending.map(p => <Decision key={p.proposal_id} proposal={p} canDecide={canDecide} onChanged={changed} />)}
    {decided.map(p => <div key={p.proposal_id} style={{ padding: '6px 0' }}><Change title={p.status === 'APPROVED' ? '승인됨' : '기각됨'} proposal={p} /></div>)}
    <NewProposal key={id} projectId={projectId} requirement={requirement} onChanged={changed} />
  </KvBox>;
}
