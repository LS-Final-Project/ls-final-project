import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { approveProposal, listProposals, listRequirements, rejectProposal, type Proposal } from '../api/rfp';
import { getProject } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime } from '../components/tft';
import { downloadCsv, KvBox, KvCond, KvCondField, KvCondRow, KvField, KvGrid, KvKey, KvNext, KvSplit, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { useNames } from './names';
import { label, NECESSITY_LABEL, ORIGIN_LABEL, PROPOSAL_STATUS_LABEL, requirementNo, shortDateTime, STATUS_LABEL } from './labels';
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

type DecisionHandle = { approve: () => void; reject: () => void };
type DecisionProps = {
  proposal: Proposal;
  code: string;
  canDecide: boolean;
  // 사용자가 행을 직접 눌렀는지. 승인은 되돌릴 수 없어 자동으로 고른 첫 행에는 결정 단추를 열지 않는다.
  chosenByUser: boolean;
  by: string;
  onChanged: () => void;
  onApproved: (text: string) => void;
};

const CHANGES: KvColumn<Change>[] = [
  { key: 'item', header: '항목', width: 96, cell: c => c.item },
  { key: 'now', header: '현재', cell: c => c.now },
  { key: 'next', header: '제안', cell: c => c.next },
];

// 제목 줄의 승인·기각 단추와 F10도 이 폼의 입력을 쓴다.
const Decision = forwardRef<DecisionHandle, DecisionProps>(function Decision({ proposal, code, canDecide, chosenByUser, by, onChanged, onApproved }, ref) {
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
  const able = pending && canDecide && chosenByUser && !busy;
  useImperativeHandle(ref, () => ({
    approve: () => { if (able) approve.mutate(); },
    reject: () => { if (able && reason.trim()) reject.mutate(); },
  }));
  return <KvBox title={`제안, 요구 ${code}, ${label(ORIGIN_LABEL, proposal.origin)}`} note={label(PROPOSAL_STATUS_LABEL, proposal.status)}>
      <KvGrid label="제안 내용" columns={CHANGES} rows={changes(proposal)} rowKey={c => c.item} fallback="바뀌는 항목이 없습니다." />
      <div className="kv-field" style={{ marginTop: 8 }}><span>사유</span><div className="kv-text" style={{ minHeight: 0 }}>{proposal.rationale}</div></div>
      <KvField label="제안자">{[proposal.proposer_kind === 'system' ? '자동' : by, shortDateTime(proposal.known_at), `판본 ${proposal.version}`].filter(Boolean).join(', ')}</KvField>
      {!pending && <KvField label="처리">{label(PROPOSAL_STATUS_LABEL, proposal.status)} {formatDateTime(proposal.decided_at)}. 처리된 제안은 다시 바꿀 수 없습니다.</KvField>}
      {pending && canDecide && <>
        <div className="kv-field"><label htmlFor="approve-note">메모</label>
          <input id="approve-note" type="text" maxLength={1000} value={note} onChange={event => setNote(event.target.value)} /></div>
        <div className="kv-field"><label htmlFor="reject-reason">기각 사유</label>
          <input id="reject-reason" type="text" maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></div>
        {!chosenByUser && <p className="kv-sub" style={{ margin: '6px 0 0' }}>표에서 이 제안 행을 누르면 승인과 기각을 할 수 있습니다</p>}
        <div className="kv-actions">
          <button className="kb" disabled={!able || !reason.trim()} onClick={() => reject.mutate()}>기각</button>
          <button className="kb primary" disabled={!able} onClick={() => approve.mutate()}>승인<KvKey>F10</KvKey></button>
        </div>
      </>}
      {pending && !canDecide && <KvField label="승인">담당 또는 책임자만 승인합니다.</KvField>}
      {approve.isError && <Failure error={approve.error} onReload={onChanged} />}
      {reject.isError && <Failure error={reject.error} onReload={onChanged} />}
    </KvBox>;
});

const canDecideRole = (roles: string[]) => roles.includes('sales') || roles.includes('manager');
const CSV_HEAD = ['제안', '들어온 곳', '요구', '항목', '현재', '제안 값', '일시', '상태'];

// 변경 제안 승인(TF_K_Table_Approve). 세 입구(화면 수정·고객 회신·메모 PDF)의 제안이 한 목록에 모인다.
export default function RfpApprovals() {
  const { projectId = '' } = useParams();
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const client = useQueryClient();
  const [status, setStatus] = useState('PENDING');
  const [origin, setOrigin] = useState('');
  const [picked, setPicked] = useState<string>();
  const [done, setDone] = useState<string>();
  const [search, setSearch] = useState('');
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const decision = useRef<DecisionHandle>(null);
  const approveNow = useCallback(() => decision.current?.approve(), []);
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
  // 세 입구 표는 조건과 상관없이 프로젝트 전체 제안을 센다.
  const everything = useQuery({ queryKey: ['rfp-proposals', mode, projectId, 'all'], queryFn: () => listProposals(projectId, {}), enabled });
  const codes = new Map(requirements.data?.data.items.map(r => [r.requirement_id, requirementNo(r.code)]));
  const codeOf = (p: Proposal) => p.requirement_id ? codes.get(p.requirement_id) ?? p.requirement_id.slice(0, 8) : '조항 메모';
  const listed = proposals.data?.data.items ?? [];
  const items = listed.filter(p => !search.trim() || codeOf(p).includes(search.trim()));
  // 보드처럼 첫 행을 고른 채로 시작한다.
  const chosen = items.find(p => p.proposal_id === picked) ?? items[0];
  const selected = chosen?.proposal_id;
  const chosenByUser = !!picked && picked === selected;
  const decidable = chosenByUser && chosen?.status === 'PENDING' && (!access.user.data || canDecideRole(access.user.data.data.roles));
  const nameOf = useNames(items.flatMap(p => [p.proposer_user_id, p.decided_by]));
  useHotkey('F10', approveNow, access.ready && decidable);
  if (!access.ready) return <BusinessGate access={access} title="자사 대응표" />;
  const canDecide = !access.user.data || canDecideRole(access.user.data.data.roles);
  const reload = () => {
    for (const key of ['rfp-proposals', 'rfp-requirements', 'rfp-requirement', 'rfp-comparison', 'projects']) client.invalidateQueries({ queryKey: [key, mode] });
  };
  const all = everything.data?.data.items ?? [];
  const tally = (origin: string, status: string) => all.filter(p => p.origin === origin && p.status === status).length;
  const DOORS: KvColumn<string>[] = [
    { key: 'door', header: '들어온 곳', cell: code => label(ORIGIN_LABEL, code) },
    { key: 'wait', header: '대기', width: 52, className: 'c', cell: code => tally(code, 'PENDING') },
    { key: 'ok', header: '승인', width: 52, className: 'c', cell: code => tally(code, 'APPROVED') },
    { key: 'no', header: '기각', width: 52, className: 'c', cell: code => tally(code, 'REJECTED') },
  ];
  const columns: KvColumn<Proposal>[] = [
    { key: 'check', header: <input type="checkbox" aria-label="전체 선택" checked={items.length > 0 && checked.size === items.length} onChange={event => setChecked(new Set(event.target.checked ? items.map(p => p.proposal_id) : []))} />, width: 30, className: 'c', cell: p => <input
      type="checkbox"
      aria-label={`${codeOf(p)} 제안 선택`}
      checked={checked.has(p.proposal_id)}
      onClick={event => event.stopPropagation()}
      onChange={event => {
        const next = new Set(checked);
        if (event.target.checked) next.add(p.proposal_id); else next.delete(p.proposal_id);
        setChecked(next);
      }}
    /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, index) => index + 1 },
    { key: 'id', header: '제안', width: 84, cell: p => <button className="lnk" aria-pressed={p.proposal_id === selected}>{p.proposal_id.slice(0, 8)}</button> },
    { key: 'origin', header: '들어온 곳', width: 84, className: 'c', cell: p => label(ORIGIN_LABEL, p.origin) },
    { key: 'requirement', header: '요구', width: 80, cell: codeOf },
    { key: 'item', header: '항목', width: 72, className: 'c', cell: p => changes(p).map(c => c.item).join(', ') },
    { key: 'now', header: '현재', cell: p => changes(p).map(c => c.now).join(', ') },
    { key: 'next', header: '제안', cell: p => changes(p).map(c => c.next).join(', ') },
    { key: 'by', header: '제안자', width: 96, cell: p => p.proposer_kind === 'system' ? '자동' : nameOf(p.proposer_user_id) || '사용자' },
    { key: 'at', header: '일시', width: 120, className: 'd', cell: p => shortDateTime(p.known_at) },
    { key: 'status', header: '상태', width: 76, className: 'c', cell: p => label(PROPOSAL_STATUS_LABEL, p.status) },
  ];
  return <main>
    <KvTitle path="제안 지원" title="자사 대응표" code="TF0102">
      <button className="kb primary" disabled={!decidable} title={chosenByUser ? undefined : '표에서 제안 행을 먼저 누르세요'} onClick={approveNow}>승인<KvKey>F10</KvKey></button>
      <button className="kb" disabled={!decidable} title={chosenByUser ? '기각 사유를 적으면 기각됩니다' : '표에서 제안 행을 먼저 누르세요'} onClick={() => decision.current?.reject()}>기각</button>
      <Link className="kb" to={`/tft/projects/${projectId}/table`}>대응표</Link>
      <Link className="kb" to={`/tft/projects/${projectId}/draft`}>초안</Link>
      <button className="kb" disabled={items.length === 0} title={checked.size ? `고른 ${checked.size}건` : '표시한 행 전체'} onClick={() => downloadCsv('변경 제안', CSV_HEAD, items.filter(p => !checked.size || checked.has(p.proposal_id)).map(p => [p.proposal_id.slice(0, 8), label(ORIGIN_LABEL, p.origin), codeOf(p), changes(p).map(c => c.item).join(', '), changes(p).map(c => c.now).join(', '), changes(p).map(c => c.next).join(', '), shortDateTime(p.known_at), label(PROPOSAL_STATUS_LABEL, p.status)]))}>엑셀</button>
    </KvTitle>
    <KvNext>{done ?? (project.data?.data.name ? `${project.data.data.name}, 변경 제안 ${items.length}건.` : '변경 제안.')}{!done && ' 기각은 사유 필수입니다.'}</KvNext>
    <KvCond onSearch={() => proposals.refetch()}>
      <KvCondRow columns={3}>
        <KvCondField label="상태"><select aria-label="상태" value={status} onChange={event => { setStatus(event.target.value); setPicked(undefined); }}>
          <option value="">전체</option>
          {Object.entries(PROPOSAL_STATUS_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select></KvCondField>
        <KvCondField label="들어온 곳"><select aria-label="들어온 곳" value={origin} onChange={event => { setOrigin(event.target.value); setPicked(undefined); }}>
          <option value="">전체</option>
          {Object.entries(ORIGIN_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select></KvCondField>
        <KvCondField label="요구"><input aria-label="요구" type="text" placeholder="요구 번호" value={search} onChange={event => setSearch(event.target.value)} /></KvCondField>
      </KvCondRow>
    </KvCond>
    <div className="kv-gridhead"><span>변경 제안 {items.length}건</span></div>
    {proposals.isError
      ? <ErrorState error={proposals.error} onRetry={() => proposals.refetch()} />
      : <KvGrid label="변경 제안" columns={columns} rows={items} rowKey={p => p.proposal_id} selectedKey={selected} onRow={p => { setPicked(p.proposal_id); setDone(undefined); }} fallback={proposals.isPending ? <Loading /> : '조건에 맞는 제안이 없습니다.'} />}
    <KvSplit columns="1.6fr 1fr">
      {chosen
        ? <Decision ref={decision} key={chosen.proposal_id} proposal={chosen} code={codeOf(chosen)} canDecide={canDecide} chosenByUser={chosenByUser} by={nameOf(chosen.proposer_user_id)} onChanged={reload} onApproved={setDone} />
        : <KvBox title="제안">제안을 고르면 내용이 보입니다.</KvBox>}
      <div>
        <p className="kv-label">세 입구가 같은 목록</p>
        <KvGrid label="세 입구" columns={DOORS} rows={Object.keys(ORIGIN_LABEL)} rowKey={code => code} fallback="" />
      </div>
    </KvSplit>
  </main>;
}
