import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { approveProposal, listProposals, listRequirements, rejectProposal, type Proposal } from '../api/rfp';
import { getProject } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime } from '../components/tft';
import { KvBox, KvCond, KvCondField, KvCondRow, KvField, KvGrid, KvNext, KvTitle, type KvColumn } from '../ls-shell/kv';
import { label, NECESSITY_LABEL, ORIGIN_LABEL, PROPOSAL_STATUS_LABEL, STATUS_LABEL } from './labels';
import { Failure, settled, useAttemptKey } from './proposal-shared';

type Change = { item: string; now: string; next: string };
function changes({ current, proposed }: Proposal): Change[] {
  const out: Change[] = [];
  if (proposed.status && proposed.status !== current.status) out.push({ item: '상태', now: label(STATUS_LABEL, current.status), next: label(STATUS_LABEL, proposed.status) });
  if (proposed.necessity && proposed.necessity !== current.necessity) out.push({ item: '성격', now: label(NECESSITY_LABEL, current.necessity), next: label(NECESSITY_LABEL, proposed.necessity) });
  if (proposed.unresolved && proposed.unresolved.join() !== (current.unresolved ?? []).join()) out.push({ item: '미확인', now: (current.unresolved ?? []).join(', ') || '없음', next: proposed.unresolved.join(', ') || '없음' });
  if (proposed.reason && proposed.reason !== current.reason) out.push({ item: '사유', now: current.reason ?? '없음', next: proposed.reason });
  return out;
}

function Decision({ proposal, code, canDecide, onChanged, onApproved }: {
  proposal: Proposal;
  code: string;
  canDecide: boolean;
  onChanged: () => void;
  onApproved: (text: string) => void;
}) {
  const [note, setNote] = useState('');
  const [reason, setReason] = useState('');
  const [approveKey, nextApprove] = useAttemptKey(note.trim());
  const [rejectKey, nextReject] = useAttemptKey(reason.trim());
  const approve = useMutation({
    mutationFn: () => approveProposal(proposal.proposal_id, proposal.version, note.trim(), approveKey),
    onSuccess: result => { nextApprove(); onApproved(`승인 완료, 요구 ${code} 판본 ${result.data.version}. 연결된 비교와 초안 문장에 재검토가 표시됩니다.`); onChanged(); },
    onError: error => { if (settled(error)) nextApprove(); },
  });
  const reject = useMutation({
    mutationFn: () => rejectProposal(proposal.proposal_id, proposal.version, reason.trim(), rejectKey),
    onSuccess: () => { nextReject(); onChanged(); },
    onError: error => { if (settled(error)) nextReject(); },
  });
  const busy = approve.isPending || reject.isPending;
  const pending = proposal.status === 'PENDING';
  return <div style={{ margin: '8px 14px 0' }}>
    <KvBox title={`제안, 요구 ${code}, ${label(ORIGIN_LABEL, proposal.origin)}`} note={label(PROPOSAL_STATUS_LABEL, proposal.status)}>
      {changes(proposal).map(c => <KvField key={c.item} label={c.item}>{c.now} → {c.next}</KvField>)}
      <KvField label="제안 사유">{proposal.rationale}</KvField>
      <KvField label="제안 일시">{formatDateTime(proposal.known_at)}, 판본 {proposal.version}</KvField>
      {!pending && <KvField label="처리">{label(PROPOSAL_STATUS_LABEL, proposal.status)} {formatDateTime(proposal.decided_at)}. 처리된 제안은 다시 바꿀 수 없습니다.</KvField>}
      {pending && canDecide && <>
        <div className="kv-field"><label htmlFor="approve-note">메모</label>
          <input id="approve-note" type="text" maxLength={1000} value={note} onChange={event => setNote(event.target.value)} /></div>
        <div className="kv-field"><label htmlFor="reject-reason">기각 사유</label>
          <input id="reject-reason" type="text" maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></div>
        <div className="kv-actions">
          <button className="kb" disabled={busy || !reason.trim()} onClick={() => reject.mutate()}>기각</button>
          <button className="kb primary" disabled={busy} onClick={() => approve.mutate()}>승인</button>
        </div>
      </>}
      {pending && !canDecide && <KvField label="승인">담당 또는 책임자만 승인합니다.</KvField>}
      {approve.isError && <Failure error={approve.error} onReload={onChanged} />}
      {reject.isError && <Failure error={reject.error} onReload={onChanged} />}
    </KvBox>
  </div>;
}

// 변경 제안 승인(TF_K_Table_Approve). 세 입구(화면 수정·고객 회신·메모 PDF)의 제안이 한 목록에 모인다.
export default function RfpApprovals() {
  const { projectId = '' } = useParams();
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const client = useQueryClient();
  const [status, setStatus] = useState('PENDING');
  const [origin, setOrigin] = useState('');
  const [selected, setSelected] = useState<string>();
  const [done, setDone] = useState<string>();
  const enabled = access.ready && projectId !== '';
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled });
  const proposals = useQuery({
    queryKey: ['rfp-proposals', mode, projectId, status, origin],
    queryFn: () => listProposals(projectId, { status, origin }),
    enabled,
  });
  // 제안에는 요구 ID만 있어 번호는 대응표 첫 묶음에서 찾는다. 없으면 ID 앞자리를 보인다.
  const requirements = useQuery({
    queryKey: ['rfp-requirements', mode, projectId, 'codes'],
    queryFn: () => listRequirements(projectId, {}, 100),
    enabled,
  });
  if (!access.ready) return <BusinessGate access={access} title="자사 대응표" />;
  const codes = new Map(requirements.data?.data.items.map(r => [r.requirement_id, r.code]));
  const codeOf = (p: Proposal) => p.requirement_id ? codes.get(p.requirement_id) ?? p.requirement_id.slice(0, 8) : '조항 메모';
  const items = proposals.data?.data.items ?? [];
  const chosen = items.find(p => p.proposal_id === selected);
  const roles = access.user.data?.data.roles;
  const canDecide = !roles || roles.includes('sales') || roles.includes('manager');
  const reload = () => {
    for (const key of ['rfp-proposals', 'rfp-requirements', 'rfp-requirement', 'rfp-comparison', 'projects']) client.invalidateQueries({ queryKey: [key, mode] });
  };
  const columns: KvColumn<Proposal>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, index) => index + 1 },
    { key: 'id', header: '제안', width: 92, cell: p => <button className="kb sm" style={{ padding: '0 6px' }} aria-pressed={p.proposal_id === selected} onClick={() => { setSelected(p.proposal_id); setDone(undefined); }}>{p.proposal_id.slice(0, 8)}</button> },
    { key: 'origin', header: '들어온 곳', width: 84, className: 'c', cell: p => label(ORIGIN_LABEL, p.origin) },
    { key: 'requirement', header: '요구', width: 80, cell: codeOf },
    { key: 'item', header: '항목', width: 72, className: 'c', cell: p => changes(p).map(c => c.item).join(', ') },
    { key: 'now', header: '현재', cell: p => changes(p).map(c => c.now).join(', ') },
    { key: 'next', header: '제안', cell: p => changes(p).map(c => c.next).join(', ') },
    { key: 'by', header: '제안자', width: 72, className: 'c', cell: p => p.proposer_kind === 'system' ? '자동' : '사용자' },
    { key: 'at', header: '일시', width: 160, className: 'd', cell: p => formatDateTime(p.known_at) },
    { key: 'status', header: '상태', width: 76, className: 'c', cell: p => label(PROPOSAL_STATUS_LABEL, p.status) },
  ];
  return <main>
    <KvTitle path="제안 지원" title="자사 대응표" code="TF0102">
      <button className="kb primary" disabled={proposals.isFetching} onClick={() => proposals.refetch()}>조회</button>
      <Link className="kb" to={`/tft/projects/${projectId}/table`}>대응표</Link>
    </KvTitle>
    <KvNext>{done ?? (project.data?.data.name ? `${project.data.data.name}, 변경 제안 ${items.length}건.` : '변경 제안.')}{!done && ' 기각은 사유 필수입니다.'}</KvNext>
    <KvCond onSearch={() => proposals.refetch()}>
      <KvCondRow columns={2}>
        <KvCondField label="상태"><select aria-label="상태" value={status} onChange={event => { setStatus(event.target.value); setSelected(undefined); }}>
          <option value="">전체</option>
          {Object.entries(PROPOSAL_STATUS_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select></KvCondField>
        <KvCondField label="들어온 곳"><select aria-label="들어온 곳" value={origin} onChange={event => { setOrigin(event.target.value); setSelected(undefined); }}>
          <option value="">전체</option>
          {Object.entries(ORIGIN_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select></KvCondField>
      </KvCondRow>
    </KvCond>
    <div className="kv-gridhead"><span>변경 제안 {items.length}건</span></div>
    {proposals.isError
      ? <ErrorState error={proposals.error} onRetry={() => proposals.refetch()} />
      : <KvGrid label="변경 제안" columns={columns} rows={items} rowKey={p => p.proposal_id} selectedKey={selected} fallback={proposals.isPending ? <Loading /> : '조건에 맞는 제안이 없습니다.'} />}
    {chosen && <Decision key={chosen.proposal_id} proposal={chosen} code={codeOf(chosen)} canDecide={canDecide} onChanged={reload} onApproved={setDone} />}
  </main>;
}
