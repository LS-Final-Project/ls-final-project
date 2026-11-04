import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type ReactNode, type UIEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import {
  addRequirement, approveProposal, createProject, listClauses, listProjectHistory, type Clause, type ManualRequirementRequest, listRequirementHistory, type HistoryItem, type HistoryKind, type HistoryPage, createProposal, createQuestion, decideRequirement, getComparison, getRequirement, listDocuments, listQuestions, listRequirements, recompareRequirement, startProcess, uploadDocument,
  type BidVerdict, type DecisionRequest, type DocumentVersion, type Proposal, type ProposalCreate, type Question, type QuestionCreate, type RequirementDetail, type RequirementSummary,
} from '../api/rfp';
import { activeRfpJob, type RfpWatch } from '../api/rfp-job-events';
import { listSupplementCandidates } from '../api/rfp-supplement';
import { getDraft, sendCommand } from '../api/rfp-draft';
import { getProject, listProjects, type ProjectDetail } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvTitle } from '../ls-shell/kv';
import { MOCK_WORKSPACE_REQUIREMENTS } from '../mocks/rfp-workspace';
import { fileProblem, latestVersions } from './IntakeForms';
import { OriginalDocument } from './OriginalDocument';
import { ISSUE_LABEL, label, NECESSITY_LABEL, OPERATOR_LABEL, PATH_LABEL, STATUS_LABEL } from './labels';
import { useNames } from './names';
import { RfpPersona } from './RfpPersona';
import { RfpReasoning, rfpStageDescription } from './RfpReasoning';
import { useRfpJobEvents } from './useRfpJobEvents';
import { DEPARTMENT_LABEL } from './labels';
import { constraintsBody, emptyConstraints } from './ProjectConstraintsForm';
import './RfpWorkspace.css';

// 통합 작업 공간(KAN-222, 사본 캔버스 "RFP 검토 화면 개편안" 1번 보드): 올리기 → 분석 시작 → 분석 중 → 결정 + 출력.
export const VERDICT: Record<BidVerdict, string> = { DEVIATION: '편차', QUERY: '질의', SUPPLEMENT: '보완', ASSIGN: '배정', CONFIRM: '충족 확인', MET: '충족' };
// 결정할 것 묶음 순서(사용자 확정): 질의 → 편차 → 보완 → 배정 → 충족 확인. 도면 확인은 서버 판정이 아직 없다.
export const DECIDE_ORDER: BidVerdict[] = ['QUERY', 'DEVIATION', 'SUPPLEMENT', 'ASSIGN', 'CONFIRM'];
const ROW_ACTION: Partial<Record<BidVerdict, string>> = { DEVIATION: '편차 기재', QUERY: '질의서에 넣기', SUPPLEMENT: '협력사 선정', ASSIGN: '배정', CONFIRM: '충족 확정' };
const STEPS = ['parse', 'extract', 'search', 'compare', 'supplement', 'candidates', 'draft'];
const STEP_LABEL: Record<string, string> = { parse: '원문 읽기', extract: '요구 뽑기', search: '자사 자료 찾기', compare: '자사 제품 비교', supplement: '보완 경로', candidates: '협력사 후보', draft: '검토본 초안' };
const NEW = 'new';

export type Phase = 'empty' | 'ready' | 'running' | 'failed' | 'none' | 'done';
// 서버 상태에서 화면 단계를 정한다. 작업이 돌면 분석 중이 먼저다(요구가 일부 저장돼도 아직 결정 단계가 아니다).
export function workspacePhase(documents: number, requirements: number, jobStatus?: string): Phase {
  if (jobStatus && activeRfpJob(jobStatus)) return 'running';
  if (requirements > 0) return 'done';
  if (jobStatus === 'FAILED' || jobStatus === 'CANCELLED') return 'failed';
  if (jobStatus === 'SUCCEEDED' || jobStatus === 'PARTIAL') return 'none';
  return documents > 0 ? 'ready' : 'empty';
}

// 책임자는 결정하지 않는다(사용자 확정). 담당 sales만 결정 칸을 본다. 개발 모의 모드는 담당으로 본다.
export function viewerRole(actorId: string | undefined, project?: Pick<ProjectDetail, 'sales_user_id' | 'responsible_user_id'>) {
  if (!project || !actorId) return getApiMode() === 'mock' ? 'sales' : 'viewer';
  if (actorId === project.sales_user_id) return 'sales';
  if (actorId === project.responsible_user_id) return 'responsible';
  return 'viewer';
}

const key = () => crypto.randomUUID();
const failureText = (error: unknown) => error instanceof ApiError
  ? error.code === 'TFT_JOB_IN_PROGRESS' ? '이미 분석 중입니다. 진행 화면으로 이어집니다.' : error.message
  : '처리하지 못했습니다.';

export default function RfpWorkspace() {
  const { projectId = NEW } = useParams();
  const access = useBusinessAccess('tft');
  const mode = getApiMode();
  const actorId = access.user.data?.data.id;
  const isNew = projectId === NEW;
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled: access.ready && !isNew });
  const documents = useQuery({ queryKey: ['documents', mode, projectId], queryFn: () => listDocuments(projectId), enabled: access.ready && !isNew });
  const requirements = useQuery({
    queryKey: ['workspace-requirements', mode, projectId],
    queryFn: async () => mode === 'mock' ? MOCK_WORKSPACE_REQUIREMENTS : (await listRequirements(projectId, {}, 1000, undefined, { useMockFallback: false })).data.items,
    enabled: access.ready && !isNew,
  });
  const jobId = project.data?.data.latest_job?.job_id;
  const live = useRfpJobEvents(jobId, projectId, actorId, mode);
  const client = useQueryClient();
  const liveStatus = live?.job?.status;
  // 작업이 끝나면 요구와 프로젝트를 다시 읽는다(결정 단계로 넘어가거나 실패를 보여 준다).
  useEffect(() => {
    if (liveStatus && !activeRfpJob(liveStatus)) { client.invalidateQueries({ queryKey: ['workspace-requirements', mode, projectId] }); client.invalidateQueries({ queryKey: ['projects', mode, projectId] }); }
  }, [liveStatus, client, mode, projectId]);
  if (!access.ready) return <BusinessGate access={access} title="RFP 검토" />;
  if (isNew) return <NewProject />;
  if (project.isError) return <main><KvTitle path="제안 지원" title="RFP 검토" /><ErrorState error={project.error} onRetry={() => project.refetch()} /></main>;
  if (!project.data || !documents.data || !requirements.data) return <main><KvTitle path="제안 지원" title="RFP 검토" /><Loading /></main>;
  const p = project.data.data;
  const docs = latestVersions(documents.data.data.items);
  const status = live?.job?.status ?? p.latest_job?.status;
  const phase = workspacePhase(docs.length, requirements.data.length, status ?? undefined);
  return <main className="ws">
    <KvTitle path="제안 지원 › RFP 검토" title={p.name} code={p.external_id ?? undefined}><Link className="kb" to={`/tft/projects/${projectId}/settings`}>프로젝트 설정</Link></KvTitle>
    {phase === 'running' || phase === 'failed'
      ? <AnalysisStrip projectId={projectId} documents={docs} live={live} failed={phase === 'failed'} />
      : null}
    {phase === 'done'
      ? <Decide project={p} documents={docs} rows={requirements.data} role={viewerRole(actorId, p)} actorId={actorId} log={live} />
      : <div className="ws-grid">
        <section className="ws-sec ws-left">
          {phase === 'empty' && <DropZone onFile={() => undefined} projectId={projectId} />}
          {phase === 'ready' && <StartAnalysis projectId={projectId} documents={docs} />}
          {(phase === 'running' || phase === 'failed') && <Skeleton count={live?.progress?.counts?.requirements} />}
          {phase === 'none' && <div className="ws-center"><p className="ws-strong">요구를 찾지 못했습니다</p><p className="muted">목차만 있는 문서이거나 스캔 이미지일 수 있습니다.</p></div>}
        </section>
        <section className="ws-sec ws-right">
          {docs[0] ? <OriginalDocument document={docs[0]} onQuoteClick={() => undefined} /> : <div className="ws-center muted">올린 원문이 여기에 보입니다</div>}
        </section>
      </div>}
  </main>;
}

// 새 RFP: 파일을 고르고 프로젝트 이름·고객 코드만 받은 뒤 분석 시작을 누르면 접수, 올리기, 처리 실행을 차례로 한다.
function NewProject() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const recent = useQuery({ queryKey: ['projects', getApiMode(), 'resume'], queryFn: () => listProjects({}, undefined, 1) });
  const resume = (recent.data?.data.items ?? []).filter(p => p.unresolved_mandatory_count > 0).slice(0, 3);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const problem = fileProblem(file);
  const start = useMutation({
    mutationFn: async () => {
      const made = await createProject({ ...constraintsBody(emptyConstraints), name: name.trim(), company_code: code.trim(), data_origin: 'synthetic', purpose: 'INITIAL_PROPOSAL' }, key());
      const id = made.data.id;
      const doc = await uploadDocument(id, { file: file as File, kind: 'RFP_SPEC', title: name.trim(), note: '', data_origin: 'synthetic' }, key());
      await startProcess(id, { document_version_ids: [doc.document_version_id], reason: null }, key());
      return id;
    },
    onSuccess: id => { client.invalidateQueries({ queryKey: ['projects'] }); navigate(`/tft/projects/${id}`); },
  });
  const ready = !!file && !problem && name.trim() !== '' && code.trim() !== '';
  return <main className="ws">
    <KvTitle path="제안 지원 › RFP 검토" title="새 RFP 검토" />
    <div className="ws-grid">
      <section className="ws-sec ws-left">
        {!file ? <DropZone onFile={next => { setFile(next); if (next && !name) setName(next.name.replace(/\.[^.]+$/, '')); }} /> : <div className="ws-ready">
          <div className="ws-file"><span className="ws-strong">{file.name}</span><span className="muted">{(file.size / 1024).toFixed(1)} KB</span><span className="sp" /><button className="ws-link" onClick={() => setFile(null)}>빼기</button></div>
          {problem && <p role="alert">{problem}</p>}
          <label className="ws-field">프로젝트 이름<input value={name} onChange={event => setName(event.target.value)} /></label>
          <label className="ws-field">고객 코드<input value={code} maxLength={40} onChange={event => setCode(event.target.value)} /></label>
          <p className="muted">도면도 함께 분석합니다</p>
          <button className="kb primary ws-big" disabled={!ready || start.isPending} title="본문과 도면 해석에 모델 비용이 듭니다" onClick={() => start.mutate()}>분석 시작</button>
          {start.isError && <p role="alert">{failureText(start.error)}</p>}
        </div>}
      </section>
      <section className="ws-sec ws-right"><div className="ws-center muted">올린 원문이 여기에 보입니다</div>
        {resume.length > 0 && <div className="ws-resume"><p className="muted">이어서 할 프로젝트</p>
          {resume.map(p => <p key={p.id}><Link to={`/tft/projects/${p.id}`}>{p.external_id} {p.name}</Link><span className="muted">미해결 필수 {p.unresolved_mandatory_count}</span></p>)}</div>}
      </section>
    </div>
  </main>;
}

function DropZone({ onFile, projectId }: { onFile: (file: File | null) => void; projectId?: string }) {
  const client = useQueryClient();
  const mode = getApiMode();
  const upload = useMutation({
    mutationFn: (file: File) => uploadDocument(projectId as string, { file, kind: 'RFP_SPEC', title: file.name.replace(/\.[^.]+$/, ''), note: '', data_origin: 'synthetic' }, key()),
    onSuccess: () => client.invalidateQueries({ queryKey: ['documents', mode, projectId] }),
  });
  const pick = (file: File | null) => {
    if (!file) return;
    const problem = fileProblem(file);
    if (projectId && !problem) upload.mutate(file); else onFile(file);
  };
  return <label className="ws-drop" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); pick(event.dataTransfer.files[0] ?? null); }}>
    <span className="ws-strong">제안요청서를 끌어 놓으세요</span>
    <span className="muted">DOCX, PDF</span>
    <span className="kb primary">파일 선택</span>
    <input type="file" accept=".docx,.pdf" hidden onChange={event => pick(event.target.files?.[0] ?? null)} />
    {upload.isError && <span role="alert">이 파일은 열 수 없습니다. {failureText(upload.error)}</span>}
  </label>;
}

function StartAnalysis({ projectId, documents }: { projectId: string; documents: DocumentVersion[] }) {
  const client = useQueryClient();
  const run = useMutation({
    mutationFn: () => startProcess(projectId, { document_version_ids: documents.map(d => d.document_version_id), reason: null }, key()),
    onSettled: () => client.invalidateQueries({ queryKey: ['projects'] }),
  });
  return <div className="ws-ready">
    {documents.map(d => <div key={d.document_version_id} className="ws-file"><span className="ws-strong">{d.title}</span><span className="muted">판본 {d.version}</span></div>)}
    <p className="muted">도면도 함께 분석합니다</p>
    <button className="kb primary ws-big" disabled={run.isPending} title="본문과 도면 해석에 모델 비용이 듭니다" onClick={() => run.mutate()}>분석 시작</button>
    {run.isError && <p role="alert">{failureText(run.error)}</p>}
  </div>;
}

// 분석 중 상태 띠. 서버 이벤트는 단계·요구 건수만 준다. 한 줄 문장은 단계 설명으로 대신한다(서버에 문장 필드가 생기면 바꾼다).
function AnalysisStrip({ projectId, documents, live, failed }: { projectId: string; documents: DocumentVersion[]; live: ReturnType<typeof useRfpJobEvents>; failed: boolean }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const activity = live && 'activity' in live ? live.activity ?? [] : [];
  const step = live && 'progress' in live ? live.progress?.step ?? activity.at(-1)?.step : undefined;
  const at = Math.max(0, STEPS.indexOf(step ?? 'parse'));
  const disconnected = live?.connection === 'reconnecting' || live?.connection === 'fallback';
  const retry = useMutation({
    mutationFn: () => startProcess(projectId, { document_version_ids: documents.map(d => d.document_version_id), reason: '분석 중단 뒤 다시 시도' }, key()),
    onSettled: () => client.invalidateQueries({ queryKey: ['projects'] }),
  });
  const line = failed ? `${STEPS.length}단계 중 ${at + 1}단계에서 멈췄습니다` : disconnected ? '연결이 끊겼습니다. 다시 연결하는 중입니다. 분석은 서버에서 계속됩니다.' : rfpStageDescription(step);
  return <>
    <div className="ws-strip" aria-label="AI 분석 상태">
      <span className="ws-persona"><RfpPersona state={failed ? 'idle' : 'thinking'} paused={failed} label={failed ? '분석 중단' : 'AI 분석 중'} /></span>
      <span className={failed ? 'ws-strong' : 'ws-shim'}>{failed ? '분석이 멈췄습니다' : '분석 중'}</span>
      <span className="muted">{STEP_LABEL[STEPS[at]]} ({at + 1}/{STEPS.length})</span>
      <span className="ws-seg" role="img" aria-label={`${STEPS.length}단계 중 ${at + 1}단계`}>{STEPS.map((s, i) => <i key={s} className={i < at ? 'done' : i === at ? (failed ? 'stop' : 'now') : ''} />)}</span>
      <span className="ws-line" aria-live="polite">{line}</span>
      {failed && <button className="kb primary" title="다시 분석하면 모델 비용이 듭니다" disabled={retry.isPending} onClick={() => retry.mutate()}>다시 시도</button>}
      <button className="ws-link" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? '접기' : '자세히'}</button>
    </div>
    {retry.isError && <p role="alert" className="ws-alert">{failureText(retry.error)}</p>}
    {open && <div className="ws-log"><RfpReasoning activity={activity} active={!failed} completed={false} disconnected={disconnected} currentStep={step} documents={documents} labels={STEP_LABEL} /></div>}
  </>;
}

function Skeleton({ count }: { count?: number }) {
  return <div className="ws-skel" aria-busy="true">
    <p className="muted">{count ? `요구 ${count}건을 찾았습니다. 비교 중입니다.` : '요구를 찾는 중입니다.'}</p>
    {[70, 110, 90, 130, 80, 100].map((w, i) => <span key={i} className="sk" style={{ width: `${w * 2}px` }} />)}
  </div>;
}

type Row = RequirementSummary & { bid_verdict: BidVerdict };
const needed = (r: RequirementSummary) => !!r.bid_verdict && r.bid_verdict !== 'MET';

type Line = { kind: 'group'; title: string; verdict?: BidVerdict; rows: Row[] } | { kind: 'row'; row: Row } | { kind: 'fold'; n: number };
const ROW_H = 34;
// 수백 건이어도 보이는 행만 그린다. 묶음 제목·접힘 줄도 같은 높이의 한 줄로 센다.
function useWindow(total: number, height = 680) {
  const [top, setTop] = useState(0);
  const first = Math.max(0, Math.floor(top / ROW_H) - 10);
  const last = Math.min(total, Math.ceil((top + height) / ROW_H) + 10);
  return { first, last, onScroll: (event: UIEvent<HTMLDivElement>) => setTop(event.currentTarget.scrollTop) };
}

const BULK_ACTION: Partial<Record<BidVerdict, { label: string; body: Omit<DecisionRequest, 'version'> }>> = {
  CONFIRM: { label: '모두 충족 확정', body: { action: 'confirm_met' } },
};

export function Decide({ project, documents, rows, role, actorId, log }: { project: ProjectDetail; documents: DocumentVersion[]; rows: RequirementSummary[]; role: string; actorId?: string; log?: RfpWatch }) {
  const [byEq, setByEq] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const client = useQueryClient();
  const responsible = role === 'responsible';
  const [tab, setTab] = useState<'review' | 'output'>(responsible ? 'output' : 'review');
  const [filter, setFilter] = useState<'todo' | 'all' | BidVerdict>('todo');
  const [showMet, setShowMet] = useState(false);
  const [q, setQ] = useState('');
  const [eq, setEq] = useState('');
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [bulkOwner, setBulkOwner] = useState('기술');
  const [asking, setAsking] = useState<BidVerdict>();
  const [lastBulk, setLastBulk] = useState<{ ids: string[] }>();
  const [similar, setSimilar] = useState<{ body: Omit<DecisionRequest, 'version'>; rows: Row[] }>();
  const verdictRows = rows.filter((r): r is Row => !!r.bid_verdict);
  const open = (r: Row) => needed(r) && !r.decision?.decided;
  const [sel, setSel] = useState<string | undefined>(() => verdictRows.find(open)?.requirement_id ?? rows[0]?.requirement_id);
  const todo = verdictRows.filter(open).length;
  const total = verdictRows.filter(needed).length;
  const gate = project.decision_gate ?? { undecided: todo, deviation_missing: 0, open_queries: 0, review_pending: 0 };
  const equipments = [...new Set(verdictRows.map(r => r.equipment ?? '공통'))];
  const text = q.trim().toLowerCase();
  const visible = verdictRows.filter(r => (!eq || (r.equipment ?? '공통') === eq)
    && (!text || [r.code, r.attribute, r.value_text, r.equipment].some(v => v?.toLowerCase().includes(text))));
  const count = (v: BidVerdict) => visible.filter(r => r.bid_verdict === v).length;
  const groups = filter === 'todo' && !byEq
    ? [...DECIDE_ORDER, ...(showMet ? ['MET' as const] : [])].map(v => ({ title: VERDICT[v], verdict: v, rows: visible.filter(r => r.bid_verdict === v) })).filter(g => g.rows.length)
    : equipments.map(e => ({ title: e, verdict: undefined, rows: visible.filter(r => (r.equipment ?? '공통') === e && (filter === 'all' || (filter === 'todo' ? open(r) : r.bid_verdict === filter))) })).filter(g => g.rows.length);
  const lines: Line[] = groups.flatMap(g => [{ kind: 'group' as const, ...g }, ...g.rows.map(row => ({ kind: 'row' as const, row }))]);
  if (filter === 'todo' && !byEq && !showMet && count('MET') > 0) lines.push({ kind: 'fold', n: count('MET') });
  const order = groups.flatMap(g => g.rows);
  const current = order.find(r => r.requirement_id === sel) ?? order[0];
  const win = useWindow(lines.length);
  const refresh = () => { client.invalidateQueries({ queryKey: ['workspace-requirements'] }); client.invalidateQueries({ queryKey: ['projects'] }); };
  // 일괄 결정은 서버 일괄 경로가 없어 결정 경로를 건별로 부른다. 중간에 실패하면 거기서 멈추고 앞의 결정은 남는다.
  const bulk = useMutation({
    mutationFn: async ({ targets, body }: { targets: Row[]; body: Omit<DecisionRequest, 'version'> }) => {
      const done: string[] = [];
      for (const r of targets) { await decideRequirement(r.requirement_id, { version: r.version, ...body } as DecisionRequest, key()); done.push(r.requirement_id); }
      return done;
    },
    onSuccess: ids => { setLastBulk({ ids }); setChecked(new Set()); setAsking(undefined); setSimilar(undefined); },
    onSettled: refresh,
  });
  const undoBulk = useMutation({
    mutationFn: async (ids: string[]) => {
      for (const id of ids) {
        const r = rows.find(x => x.requirement_id === id);
        if (r?.decision?.decision_id) await decideRequirement(id, { version: r.version, action: 'undo', decision_id: r.decision.decision_id } as DecisionRequest, key());
      }
    },
    onSuccess: () => setLastBulk(undefined),
    onSettled: refresh,
  });
  const next = (from: string) => { const rest = order.filter(r => open(r) && r.requirement_id !== from); setSel(rest[0]?.requirement_id ?? from); };
  // 배정·보완 결정 뒤 같은 설비의 같은 판정 미결이 있으면 같은 결정을 권한다(편차·질의는 문장이 달라 제외).
  const onDecided = (row: Row, body: Omit<DecisionRequest, 'version'>) => {
    const same = row.bid_verdict === 'ASSIGN' || row.bid_verdict === 'SUPPLEMENT'
      ? verdictRows.filter(r => r.requirement_id !== row.requirement_id && open(r) && r.bid_verdict === row.bid_verdict && r.equipment === row.equipment) : [];
    setSimilar(same.length ? { body, rows: same } : undefined);
    next(row.requirement_id);
  };
  const assignChecked = verdictRows.filter(r => checked.has(r.requirement_id));
  const [showExcluded, setShowExcluded] = useState(false);
  const excludedQuery = useQuery({ queryKey: ['workspace-requirements', getApiMode(), project.id, 'excluded'], queryFn: () => listRequirements(project.id, { excluded: true }, 1000, undefined, { useMockFallback: false }), enabled: getApiMode() === 'live' });
  const excluded = (excludedQuery.data?.data.items ?? []) as Row[];
  const tqs = useQuery({ queryKey: ['questions', getApiMode(), project.id], queryFn: () => listQuestions(project.id, undefined, { useMockFallback: false }), enabled: getApiMode() === 'live' });
  const customerTq = (tqs.data?.data.items ?? []).filter(q => q.target === 'CUSTOMER');
  // 회신 알림은 새 알림 종류 없이 질의 행의 "회신 도착" 배지로만 보인다.
  const arrived = new Set(customerTq.filter(q => q.status === 'REPLIED' && q.requirement_id).map(q => q.requirement_id as string));
  // 질의 행의 다음 행동 자리에는 질의 상태(질의서에 넣음, 회신 기다림 n일째, 회신 받음)를 보인다.
  const tqOf = new Map(customerTq.filter(q => q.requirement_id).map(q => [q.requirement_id as string, queryState(q)]));
  const remainByEq = equipments.map(e => [e, verdictRows.filter(r => (r.equipment ?? '공통') === e && open(r)).length] as const).filter(([, n]) => n);
  return <>
    <div className="ws-strip">
      <span className="ws-strong">결정 {total}건 중 {total - todo}건 완료</span>
      <span className="ws-track" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={total - todo}><span style={{ width: `${total ? Math.round((total - todo) / total * 100) : 0}%` }} /></span>
      <span className="muted ws-remain">{remainByEq.map(([e, n]) => `${e} ${n}`).join(', ')}</span>
      <span className="sp" />
      <button className="ws-link" aria-expanded={logOpen} onClick={() => setLogOpen(!logOpen)}>분석 기록</button>
    </div>
    {logOpen && <div className="ws-log">{log?.activity?.length ? <RfpReasoning activity={log.activity} active={false} completed disconnected={false} documents={documents} labels={STEP_LABEL} /> : <p className="muted">이 화면을 연 뒤 받은 분석 기록이 없습니다. 지난 분석의 단계별 기록은 서버가 아직 주지 않습니다.</p>}</div>}
    <div className="ws-tabs" role="tablist">
      <button role="tab" aria-selected={tab === 'review'} className={tab === 'review' ? 'on' : ''} onClick={() => setTab('review')}>검토</button>
      <button role="tab" aria-selected={tab === 'output'} className={tab === 'output' ? 'on' : ''} onClick={() => setTab('output')}>출력 {[gate.undecided, gate.deviation_missing, gate.open_queries, gate.review_pending].some(Boolean) && <span className="muted">남은 조건 {[gate.undecided, gate.deviation_missing, gate.open_queries, gate.review_pending].filter(Boolean).length}</span>}</button>
    </div>
    {tab === 'output'
      ? <Output project={project} rows={verdictRows} excluded={excluded} responsible={responsible} actorId={actorId} onJump={f => { setFilter(f); setTab('review'); }} onPick={id => { setFilter('all'); setSel(id); setTab('review'); }} />
      : <>
        <div className="ws-filters" role="toolbar" aria-label="판정 필터">
          <Chip on={filter === 'todo'} onClick={() => setFilter('todo')}>결정할 것 {todo}</Chip>
          <Chip on={filter === 'all'} onClick={() => setFilter('all')}>전체 {verdictRows.length}</Chip>
          <Chip on={showExcluded} onClick={() => setShowExcluded(!showExcluded)}>뺀 요구 {excluded.length}</Chip>
          <span className="ws-vsep" />
          {[...DECIDE_ORDER, 'MET' as const].map(v => <Chip key={v} on={filter === v} onClick={() => setFilter(v)}>{VERDICT[v]} {count(v)}</Chip>)}
          <span className="sp" />
          <label className="ws-field inline">설비<select aria-label="설비" value={eq} onChange={event => setEq(event.target.value)}><option value="">전체</option>{equipments.map(e => <option key={e}>{e}</option>)}</select></label>
          <input className="ws-search" aria-label="요구 검색" placeholder="요구, 조항, 값 검색" value={q} onChange={event => setQ(event.target.value)} />
          {filter === 'todo' && <button className={byEq ? 'ws-chip on' : 'ws-chip'} aria-pressed={byEq} onClick={() => setByEq(!byEq)}>설비별로 보기</button>}
          <button className="kb" onClick={() => downloadCsv(`${project.external_id ?? 'RFP'} 요구.csv`, order)}>엑셀</button>
        </div>
        {lastBulk && <p className="ws-bulkline">{lastBulk.ids.length}건을 한 번에 결정했습니다. <button className="ws-link" disabled={undoBulk.isPending} onClick={() => undoBulk.mutate(lastBulk.ids)}>한 번에 되돌리기</button></p>}
        {(bulk.isError || undoBulk.isError) && <p role="alert" className="ws-alert">{failureText(bulk.error ?? undoBulk.error)} 앞에서 처리한 결정은 남아 있습니다.</p>}
        {showExcluded && <ExcludedTable rows={excluded} responsible={responsible} />}
        <div className="ws-grid">
          <section className="ws-sec ws-left">
            {!responsible && assignChecked.length > 0 && <div className="ws-bulkbar">
              <span>선택 {assignChecked.length}건</span>
              <label className="ws-field inline">담당<select value={bulkOwner} onChange={event => setBulkOwner(event.target.value)}>{Object.keys(OWNER).map(k => <option key={k}>{k}</option>)}</select></label>
              <button className="kb primary" disabled={bulk.isPending} onClick={() => bulk.mutate({ targets: assignChecked, body: { action: 'assign', holder: 'OWN', owner: OWNER[bulkOwner] } })}>자사 수행으로 일괄 배정</button>
            </div>}
            <div className="ws-tbox" onScroll={win.onScroll}><table className="ws-table">
              <thead><tr><th style={{ width: 28 }} aria-label="선택" /><th>설비</th><th>요구</th><th>요구값</th><th title="요구 목록 응답에 자사 보증값이 아직 없어 비워 둡니다">자사 보증값</th><th className="r">다음 행동</th></tr></thead>
              <tbody>
                {win.first > 0 && <tr style={{ height: win.first * ROW_H }} aria-hidden="true" />}
                {lines.slice(win.first, win.last).map((line, i) => line.kind === 'group'
                  ? <tr key={`g:${line.title}`} className="g"><td colSpan={6}>
                    {line.title} <span className="muted">{line.rows.length}</span>
                    {!responsible && line.verdict && BULK_ACTION[line.verdict] && line.rows.some(open) && (asking === line.verdict
                      ? <span className="ws-confirm">{line.rows.filter(open).length}건을 {BULK_ACTION[line.verdict]!.label.replace('모두 ', '')}합니다. <button className="kb primary" disabled={bulk.isPending} onClick={() => bulk.mutate({ targets: line.rows.filter(open), body: BULK_ACTION[line.verdict!]!.body })}>확인</button> <button className="ws-link" onClick={() => setAsking(undefined)}>취소</button></span>
                      : <button className="ws-link ws-bulk" onClick={() => setAsking(line.verdict)}>{BULK_ACTION[line.verdict]?.label}</button>)}
                  </td></tr>
                  : line.kind === 'fold'
                    ? <tr key="fold" className="fold"><td colSpan={6}><button className="ws-link" onClick={() => setShowMet(true)}>충족 {line.n}건 펼치기</button></td></tr>
                    : <RowLine key={`${line.row.requirement_id}:${i}`} r={line.row} sel={current?.requirement_id} onPick={setSel} responsible={responsible} replied={arrived.has(line.row.requirement_id)} queryText={tqOf.get(line.row.requirement_id)}
                      checkable={!responsible && line.row.bid_verdict === 'ASSIGN' && open(line.row)} checked={checked.has(line.row.requirement_id)}
                      onCheck={on => { const nextSet = new Set(checked); if (on) nextSet.add(line.row.requirement_id); else nextSet.delete(line.row.requirement_id); setChecked(nextSet); }} />)}
                {win.last < lines.length && <tr style={{ height: (lines.length - win.last) * ROW_H }} aria-hidden="true" />}
              </tbody>
            </table></div>
          </section>
          <section className="ws-sec ws-right" aria-label="선택한 요구와 원문">
            {similar && !responsible && <p className="ws-similar">비슷한 {similar.rows.length}건에도 같은 결정 적용 <button className="kb" disabled={bulk.isPending} onClick={() => bulk.mutate({ targets: similar.rows, body: similar.body })}>적용</button> <button className="ws-link" onClick={() => setSimilar(undefined)}>닫기</button></p>}
            {current && <Panel key={current.requirement_id} row={current} projectId={project.id} responsible={responsible} documents={documents} onDecided={body => onDecided(current, body)} order={order} onMove={setSel} />}
          </section>
        </div>
      </>}
  </>;
}

const Chip = ({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) => <button className={on ? 'ws-chip on' : 'ws-chip'} onClick={onClick}>{children}</button>;

function RowLine({ r, sel, onPick, responsible, checkable, checked, onCheck, replied, queryText }: { r: Row; sel?: string; onPick: (id: string) => void; responsible: boolean; checkable: boolean; checked: boolean; onCheck: (on: boolean) => void; replied?: boolean; queryText?: string }) {
  const decided = r.decision?.decided;
  return <tr className={`row${r.requirement_id === sel ? ' sel' : ''}${decided ? ' decided' : ''}`} onClick={() => onPick(r.requirement_id)}>
    <td>{checkable && <input type="checkbox" aria-label={`${r.attribute ?? r.code} 선택`} checked={checked} onClick={event => event.stopPropagation()} onChange={event => onCheck(event.target.checked)} />}</td>
    <td className="tag">{r.equipment}</td>
    <td title={r.code}><button className="ws-rowbtn" onClick={() => onPick(r.requirement_id)}>{r.attribute ?? r.code}</button>{replied && !decided && <span className="ws-badge">회신 도착</span>}{r.decision?.manually_added && <span className="ws-badge gray">직접 추가</span>}</td>
    <td className={r.value_text ? '' : 'empty'}>{r.value_text ?? '빈 칸'}</td>
    <td className="muted" />
    <td className="r">{r.bid_verdict === 'QUERY' && queryText ? <span className="tag">{queryText}</span>
      : decided ? <span className="tag">{r.decision?.summary ?? '결정함'}</span>
      : !responsible && ROW_ACTION[r.bid_verdict] ? <button className="kb ws-act" onClick={event => { event.stopPropagation(); onPick(r.requirement_id); }}>{ROW_ACTION[r.bid_verdict]}</button> : null}</td>
  </tr>;
}

const OWNER: Record<string, NonNullable<DecisionRequest['owner']>> = { 영업: 'SALES', 기술: 'TECH', 구매: 'PURCHASE' };

function Panel({ row, projectId, responsible, documents, onDecided, order, onMove }: {
  row: Row; projectId: string; responsible: boolean; documents: DocumentVersion[]; onDecided: (body: Omit<DecisionRequest, 'version'>) => void; order: Row[]; onMove: (id: string) => void;
}) {
  const client = useQueryClient();
  const mode = getApiMode();
  const live = mode === 'live';
  const detail = useQuery({ queryKey: ['requirement', mode, row.requirement_id], queryFn: () => getRequirement(row.requirement_id), enabled: live });
  const comparison = useQuery({ queryKey: ['comparison', mode, row.requirement_id], queryFn: () => getComparison(row.requirement_id, { useMockFallback: false }), enabled: live });
  const questions = useQuery({ queryKey: ['questions', mode, projectId], queryFn: () => listQuestions(projectId, undefined, { useMockFallback: false }), enabled: live && row.bid_verdict === 'QUERY' });
  const partners = useQuery({ queryKey: ['partner-candidates', mode, row.requirement_id], queryFn: () => listSupplementCandidates(projectId, row.requirement_id), enabled: live && row.bid_verdict === 'SUPPLEMENT' });
  const d = detail.data?.data;
  const reason = d?.judgement?.reason ?? '';
  const draftQuestion = questions.data?.data.items.find(q => q.requirement_id === row.requirement_id && q.status === 'DRAFT')?.question_text;
  const replied = questions.data?.data.items.filter(q => q.requirement_id === row.requirement_id && q.status === 'REPLIED' && q.reply).at(-1);
  const [text, setText] = useState<string>();
  const [choice, setChoice] = useState<'deviate' | 'check'>('deviate');
  const [dept, setDept] = useState('TS_TRF');
  const [holder, setHolder] = useState<'OWN' | 'PARTNER'>('OWN');
  const [owner, setOwner] = useState('기술');
  const [partner, setPartner] = useState<string>();
  const [changing, setChanging] = useState(false);
  const [mode2, setMode2] = useState<'doc' | 'quote'>('doc');
  const value = text ?? (row.bid_verdict === 'QUERY' ? draftQuestion ?? '' : reason);
  const decide = useMutation({
    mutationFn: (body: Omit<DecisionRequest, 'version'>) => decideRequirement(row.requirement_id, { version: d?.version ?? row.version, ...body } as DecisionRequest, key()),
    onSuccess: (_result, body) => { client.invalidateQueries({ queryKey: ['workspace-requirements'] }); client.invalidateQueries({ queryKey: ['projects'] }); if (body.action !== 'undo') onDecided(body); },
  });
  const own = comparison.data?.data.candidates.find(c => c.attribute_match?.our_value);
  const ownText = own ? `${own.attribute_match?.our_value ?? ''} ${own.attribute_match?.our_unit ?? ''}`.trim() : '─';
  const doc = documents.find(x => x.document_version_id === d?.origin?.document_version_id) ?? documents[0];
  const decided = row.decision?.decided;
  const idx = order.findIndex(r => r.requirement_id === row.requirement_id);
  const candidates = partners.data?.data.items ?? [];
  const clauses = useClauses(doc?.document_version_id);
  const [picked, setPicked] = useState('');
  const [adding, setAdding] = useState<{ quote: string; clause: Pick<Clause, 'clause_id' | 'clause_no'> }>();
  const pickedClause = responsible ? undefined : clauseOf(clauses.data ?? [], picked);
  return <div className="ws-panel">
    {adding ? <AddRequirement projectId={projectId} quote={adding.quote} clause={adding.clause} equipment={row.equipment} onCancel={() => setAdding(undefined)} onDone={id => { setAdding(undefined); setPicked(''); onMove(id); }} />
    : <div className="ws-pick">
      <div className="hd"><span className="name">{row.attribute ?? row.code}</span><span className="muted">{row.code} {row.equipment}</span><span className="sp" /><span>{decided ? '결정함' : VERDICT[row.bid_verdict]}</span>{!decided && <span className="dot" title="AI가 정한 판정입니다" aria-label="AI 판정" />}</div>
      <div className="ws-cmp"><span className="k">요구</span><span className={row.value_text ? 'v' : 'v empty'}>{row.value_text ?? '빈 칸'}</span><span className="k">자사</span><span className="v">{ownText}{own && <small className="muted"> {own.product_code}</small>}</span></div>
      {reason && <p className="ws-why">{reason}</p>}
      {replied && d && !responsible && row.bid_verdict === 'QUERY' ? <>
        <div className="ws-decided"><span>회신 받음</span><span className="sp" />{row.decision?.decision_id && <button className="ws-link" disabled={decide.isPending} onClick={() => decide.mutate({ action: 'undo', decision_id: row.decision?.decision_id })}>되돌리기</button>}</div>
        <ReplyFlow projectId={projectId} detail={d} question={replied} onDone={() => onDecided({ action: 'confirm_met' })} /></>
        : decided ? <div className="ws-decided"><span>{row.decision?.summary ?? '결정함'}</span><span className="sp" />
        {!responsible && row.decision?.decision_id && <button className="ws-link" disabled={decide.isPending} onClick={() => decide.mutate({ action: 'undo', decision_id: row.decision?.decision_id })}>되돌리기</button>}</div>
        : responsible ? <p className="muted ws-note">결정은 담당자가 합니다.</p>
          : <div className="ws-form">
            {row.bid_verdict === 'DEVIATION' && <>
              <div className="ws-opts" role="radiogroup" aria-label="편차 처리">
                <label><input type="radio" checked={choice === 'deviate'} onChange={() => setChoice('deviate')} /> 편차로 기재</label>
                <label><input type="radio" checked={choice === 'check'} onChange={() => setChoice('check')} /> 충족 근거 확인</label>
              </div>
              {choice === 'check' && <label className="ws-field">요청 부서<select value={dept} onChange={event => setDept(event.target.value)}>{Object.entries(DEPARTMENT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
              <label className="ws-field">{choice === 'deviate' ? '편차 내용' : '요청 내용'}<input value={value} onChange={event => setText(event.target.value)} /></label>
              <div className="ws-go"><button className="kb primary" disabled={!value.trim() || decide.isPending} onClick={() => decide.mutate(choice === 'deviate' ? { action: 'record_deviation', text: value.trim() } : { action: 'request_check', text: value.trim(), target_dept: dept })}>{choice === 'deviate' ? '편차표에 넣기' : '확인 요청 보내기'}</button></div>
            </>}
            {row.bid_verdict === 'QUERY' && <>
              <label className="ws-field">고객 질의<input value={value} onChange={event => setText(event.target.value)} /></label>
              <div className="ws-go"><button className="kb primary" disabled={!value.trim() || decide.isPending} onClick={() => decide.mutate({ action: 'add_query', text: value.trim() })}>질의서에 넣기</button></div>
            </>}
            {row.bid_verdict === 'SUPPLEMENT' && <>
              <span className="ws-label">협력사</span>
              <div className="ws-plist" role="radiogroup" aria-label="협력사">
                {candidates.length === 0 && <span className="muted">{live ? '후보가 없습니다' : '후보 목록은 서버에서 읽습니다'}</span>}
                {candidates.map(c => <label key={c.candidate_code} className={c.overall_grade === 'EXCLUDED' ? 'off' : ''}>
                  <input type="radio" name="partner" disabled={c.overall_grade === 'EXCLUDED'} checked={partner === c.candidate_code} onChange={() => setPartner(c.candidate_code)} /> {c.display_name}
                </label>)}
              </div>
              <label className="ws-field">역할<input value={text ?? ''} onChange={event => setText(event.target.value)} /></label>
              <div className="ws-go"><button className="kb primary" disabled={!partner || decide.isPending} onClick={() => decide.mutate({ action: 'select_partner', candidate_codes: [partner as string], text: (text ?? '').trim() || null })}>{partner ? `${partner} 선정` : '협력사 선정'}</button>
                <button className="ws-link" disabled={decide.isPending} onClick={() => decide.mutate({ action: 'ask_rnd' })}>R&amp;D에 개발 문의</button></div>
            </>}
            {row.bid_verdict === 'ASSIGN' && <>
              <span className="ws-label">수행</span>
              <div className="ws-opts" role="radiogroup" aria-label="수행 주체">
                <label><input type="radio" checked={holder === 'OWN'} onChange={() => setHolder('OWN')} /> 자사</label>
                <label><input type="radio" checked={holder === 'PARTNER'} onChange={() => setHolder('PARTNER')} /> 협력사</label>
              </div>
              <div className="ws-go"><label className="ws-field inline">담당<select value={owner} onChange={event => setOwner(event.target.value)}>{Object.keys(OWNER).map(k => <option key={k}>{k}</option>)}</select></label>
                <button className="kb primary" disabled={decide.isPending} onClick={() => decide.mutate({ action: 'assign', holder, owner: OWNER[owner] })}>배정</button></div>
            </>}
            {row.bid_verdict === 'CONFIRM' && <div className="ws-go">
              <button className="kb primary" disabled={decide.isPending} onClick={() => decide.mutate({ action: 'confirm_met' })}>충족 확정</button>
              <button className="ws-link" disabled={decide.isPending} onClick={() => decide.mutate({ action: 'change_verdict', verdict: 'DEVIATION' })}>편차로 바꾸기</button>
            </div>}
            <div className="ws-change">{changing
              ? <label className="ws-field inline">판정<select defaultValue={row.bid_verdict} onChange={event => decide.mutate({ action: 'change_verdict', verdict: event.target.value as BidVerdict })}>{(Object.keys(VERDICT) as BidVerdict[]).map(v => <option key={v} value={v}>{VERDICT[v]}</option>)}</select></label>
              : <button className="ws-link muted" onClick={() => setChanging(true)}>판정 바꾸기</button>}</div>
            {decide.isError && <p role="alert">{failureText(decide.error)}</p>}
          </div>}
      {!responsible && <ExcludeLine row={row} version={d?.version ?? row.version} />}
    </div>}
    <RequirementHistory id={row.requirement_id} />
    <div className="ws-srcbar">
      <span className="ws-seg2" role="group" aria-label="원문 보기 방식">
        <button className={mode2 === 'doc' ? 'on' : ''} onClick={() => setMode2('doc')}>원문</button>
        <button className={mode2 === 'quote' ? 'on' : ''} onClick={() => setMode2('quote')}>조항만</button>
      </span>
      <span className="sp" />
      {pickedClause && !adding && <button className="kb primary" onClick={() => setAdding({ quote: picked, clause: pickedClause })}>요구로 추가</button>}
      <button className="kb" disabled={idx <= 0} onClick={() => onMove(order[idx - 1].requirement_id)}>이전</button>
      <button className="kb" disabled={idx < 0 || idx >= order.length - 1} onClick={() => onMove(order[idx + 1].requirement_id)}>다음</button>
    </div>
    <div className="ws-source">
      {mode2 === 'quote' || !doc ? <p className="ws-quote"><span className="muted">{d?.origin?.clause_no ?? row.code}</span> {d?.origin?.quote ?? '조항 원문은 서버에서 읽습니다.'}</p>
        : <OriginalDocument document={doc} origin={d?.origin} onQuoteClick={() => undefined} onSelectText={setPicked} />}
    </div>
  </div>;
}

function Output({ project, rows, excluded = [], responsible, actorId, onJump, onPick }: { project: ProjectDetail; rows: Row[]; excluded?: Row[]; responsible: boolean; actorId?: string; onJump: (filter: 'todo' | BidVerdict) => void; onPick: (id: string) => void }) {
  const client = useQueryClient();
  const mode = getApiMode();
  const [doc, setDoc] = useState<'dev' | 'tq' | 'eq' | 'sum' | 'hist'>('dev');
  const name = useNames([project.responsible_user_id]);
  const questions = useQuery({ queryKey: ['questions', mode, project.id], queryFn: () => listQuestions(project.id, undefined, { useMockFallback: false }), enabled: mode === 'live' });
  const gate = project.decision_gate ?? { undecided: rows.filter(r => needed(r) && !r.decision?.decided).length, deviation_missing: 0, open_queries: 0, review_pending: 0 };
  const pending = pendingReviews([...rows, ...excluded]);
  const review = useMutation({
    // 한 요구에 확인할 것이 둘이면 앞 호출로 판본이 오른다. 응답의 판본으로 다음 호출을 보낸다.
    mutationFn: async ({ ids, action }: { ids: PendingReview[]; action: 'review_confirm' | 'review_return' }) => {
      const versions = new Map<string, number>();
      for (const p of ids) {
        const result = await decideRequirement(p.row.requirement_id, { version: versions.get(p.row.requirement_id) ?? p.row.version, action, decision_id: p.decisionId } as DecisionRequest, key());
        if (result.data.requirement_version) versions.set(p.row.requirement_id, result.data.requirement_version);
      }
    },
    onSettled: () => { client.invalidateQueries({ queryKey: ['workspace-requirements'] }); client.invalidateQueries({ queryKey: ['projects'] }); },
  });
  const save = useMutation({
    mutationFn: async () => {
      const draft = await getDraft(project.id);
      return sendCommand(project.id, { actorId: actorId as string, kind: 'review', key: key(), body: { version: draft.version } });
    },
  });
  const ready = !gate.undecided && !gate.deviation_missing && !gate.open_queries && !gate.review_pending;
  const deviations = rows.filter(r => r.decision?.action === 'record_deviation');
  const byEq = useMemo(() => [...new Set(rows.map(r => r.equipment ?? '공통'))].map(eq => {
    const items = rows.filter(r => (r.equipment ?? '공통') === eq);
    return { eq, items, open: items.filter(r => needed(r) && !r.decision?.decided).length };
  }), [rows]);
  const tq = questions.data?.data.items.filter(q => q.target === 'CUSTOMER') ?? [];
  const reviewBlock = <section className="ws-sec ws-review" aria-label="책임자 확인">
    <h2>책임자 확인 <span className="muted">{name(project.responsible_user_id)} {pending.length}건</span>{responsible && pending.length > 0 && <button className="kb primary" disabled={review.isPending} onClick={() => review.mutate({ ids: pending, action: 'review_confirm' })}>모두 확인</button>}</h2>
    <table className="ws-table"><tbody>{pending.map(p => <tr key={p.decisionId}><td className="tag">{p.row.equipment}</td><td>{p.row.attribute ?? p.row.code}</td><td>{p.label}</td>
      <td className="r">{responsible && <button className="ws-link" disabled={review.isPending} onClick={() => review.mutate({ ids: [p], action: 'review_return' })}>되돌려 보내기</button>}</td></tr>)}
      {pending.length === 0 && <tr><td className="muted">확인할 결정이 없습니다.</td></tr>}</tbody></table>
  </section>;
  return <div className="ws-out">
    <div className="ws-gate" aria-label="저장 조건">
      <GateItem n={gate.undecided} label="판정 안 된 요구" onClick={() => onJump('todo')} />
      <GateItem n={gate.deviation_missing} label="편차표 누락" onClick={() => onJump('DEVIATION')} />
      <GateItem n={gate.open_queries} label="회신 대기 질의" onClick={() => onJump('QUERY')} />
      <GateItem n={gate.review_pending} label="책임자 확인 대기" />
      <span className="sp" />
      {!responsible && <button className="kb primary" disabled={!ready || save.isPending} onClick={() => save.mutate()}>검토본 저장</button>}
    </div>
    {save.isError && <p role="alert" className="ws-alert">{failureText(save.error)}</p>}
    {save.isSuccess && <p className="ws-alert">검토본을 저장했습니다.</p>}
    {reviewBlock}
    <div className="ws-dtabs" role="tablist" aria-label="출력 문서">
      {([['dev', '편차표', deviations.length], ['tq', '질의서', tq.length], ['eq', '현황표', byEq.length], ['sum', '요약서', ''], ['hist', '변경 이력', '']] as const).map(([k, l, n]) =>
        <button key={k} role="tab" aria-selected={doc === k} className={doc === k ? 'on' : ''} onClick={() => setDoc(k)}>{l} <span className="muted">{n}</span></button>)}
    </div>
    <section className="ws-sec ws-doc">
      {doc === 'dev' && <table className="ws-table"><thead><tr><th>조항</th><th>항목</th><th>요구</th><th>편차 내용</th></tr></thead>
        <tbody>{deviations.map(r => <tr key={r.requirement_id}><td>{r.code}</td><td>{r.equipment} {r.attribute}</td><td>{r.value_text}</td><td>{r.decision?.summary}</td></tr>)}
          {deviations.length === 0 && <tr><td colSpan={4} className="muted">편차로 결정한 요구가 없습니다.</td></tr>}</tbody></table>}
      {doc === 'tq' && <table className="ws-table"><thead><tr><th>No</th><th>질의</th><th>상태</th></tr></thead>
        <tbody>{tqNumbers(tq).map(({ q, no }) => <tr key={q.question_id}><td>{no}</td><td>{q.question_text}</td><td>{queryState(q)}</td></tr>)}
          {tq.length === 0 && <tr><td colSpan={3} className="muted">질의가 없습니다.</td></tr>}</tbody></table>}
      {doc === 'eq' && <table className="ws-table"><thead><tr><th>장비</th><th>요구</th><th>충족</th><th>남은 결정</th></tr></thead>
        <tbody>{byEq.map(e => <tr key={e.eq}><td>{e.eq}</td><td>{e.items.length}</td><td>{e.items.filter(r => r.bid_verdict === 'MET').length}</td><td>{e.open}</td></tr>)}</tbody></table>}
      {doc === 'sum' && <div className="ws-sum"><p>요구 {rows.length}건. {(['MET', ...DECIDE_ORDER] as BidVerdict[]).map(v => `${VERDICT[v]} ${rows.filter(r => r.bid_verdict === v).length}`).join(', ')}.</p>
        <p>남은 결정 {gate.undecided}건, 회신 대기 질의 {gate.open_queries}건.</p></div>}
      {doc === 'hist' && <ProjectHistory projectId={project.id} codeOf={id => [...rows, ...excluded].find(r => r.requirement_id === id)?.code ?? '─'} onPick={onPick} />}
    </section>
  </div>;
}

const GateItem = ({ n, label, onClick }: { n: number; label: string; onClick?: () => void }) => onClick && n > 0
  ? <button className="ws-gateitem warn" onClick={onClick}>{label} {n} ›</button>
  : <span className={n > 0 ? 'ws-gateitem warn' : 'ws-gateitem'}>{label} {n}</span>;

// 질의 상태: 질의서에 넣음(초안) → 회신 기다림 n일째 → 회신 받음. 질문에 발송 기록이 없어 "보냄"을 따로 보이지 못한다.
export function queryState(q: Pick<Question, 'status' | 'created_at'>, now = Date.now()) {
  if (q.status === 'REPLIED') return '회신 받음';
  if (q.status === 'OPEN') return `회신 기다림 ${Math.max(1, Math.ceil((now - Date.parse(q.created_at)) / 86_400_000))}일째`;
  return '질의서에 넣음';
}

const RECOMPARED: Record<string, string> = { EVIDENCE_FOUND: '충족', CONDITIONAL: '충족 확인', SPEC_GAP: '편차', INSUFFICIENT_EVIDENCE: '확인 필요' };

// 회신에서 읽은 값 후보(KAN-224). 회신 전이나 요구에 이어진 고객 질의가 아니면 null이고, 그때는 직접 입력으로 시작한다.
const REASON: Record<string, string> = {
  NO_VALUE: '회신에 값이 없습니다', MULTIPLE_VALUES: '회신에 값이 여러 개 있습니다', CORRECTION: '회신이 앞의 값을 고쳐 말합니다',
  CONDITIONAL: '조건에 따라 값이 다릅니다', MULTIPLE_QUESTIONS: '여러 질의에 한꺼번에 답했습니다', AI_UNGROUNDED: '읽은 값이 회신 문장에서 확인되지 않습니다', AI_FAILED: '회신에서 값을 읽지 못했습니다',
};

// 질의 번호: 첫 질의는 TQ-n, 후속 질의는 원래 질의 아래 TQ-n-2, TQ-n-3.
export function tqNumbers(items: Question[]) {
  const followsOf = (q: Question) => q.follows_question_id ?? null;
  const roots = items.filter(q => !followsOf(q) || !items.some(x => x.question_id === followsOf(q)));
  return roots.flatMap((root, i) => [{ q: root, no: `TQ-${i + 1}` }, ...items.filter(q => followsOf(q) === root.question_id).map((q, j) => ({ q, no: `TQ-${i + 1}-${j + 2}` }))]);
}

// 고객 회신 반영: 읽은 값 확인 → 값 반영하고 다시 비교 → 결과대로 확정(한 번 더 누름). 자동 반영은 하지 않는다.
function ReplyFlow({ projectId, detail, question, onDone }: { projectId: string; detail: RequirementDetail; question: Question; onDone: () => void }) {
  const client = useQueryClient();
  const found = question.value_candidate ?? undefined;
  const read = found && found.method !== 'none' ? found : undefined;
  const [value, setValue] = useState(read?.value ?? '');
  const [unit, setUnit] = useState(read?.unit ?? detail.spec?.unit ?? '');
  const [cond, setCond] = useState(read?.conditions?.map(c => [c.value, c.unit].filter(Boolean).join(' ')).join(', ') ?? '');
  const refresh = () => client.invalidateQueries({ queryKey: ['workspace-requirements'] });
  const apply = useMutation({
    mutationFn: async () => {
      const conditions = read?.conditions?.length ? read.conditions.map(c => ({ name: c.name, value: c.value, unit: c.unit ?? null }))
        : cond.trim() ? [{ name: 'duration', value: cond.trim().split(/\s+/)[0], unit: cond.trim().split(/\s+/)[1] ?? null }] : [];
      const made = await createProposal(projectId, {
        requirement_id: detail.requirement_id, requirement_version: detail.version, source_question_id: question.question_id,
        proposed: { spec: { value: value.trim(), unit: unit.trim() || null, operator: detail.spec?.operator ?? 'GTE', tolerance: null }, conditions, unresolved: [] },
        rationale: '고객 회신 반영', evidence_refs: [],
      } as ProposalCreate, key());
      const approved = await approveProposal(made.data.proposal_id, made.data.version, '', key());
      return (await recompareRequirement(detail.requirement_id, approved.data.version, key())).data;
    },
    onSettled: refresh,
  });
  // 결과대로 확정: 다시 비교한 제안을 승인하고, 결과가 충족이면 충족 확정까지 기록한다. 다른 판정이면 그 판정 묶음에서 이어서 결정한다.
  const confirm = useMutation({
    mutationFn: async (p: Proposal) => {
      const approved = await approveProposal(p.proposal_id, p.version, '', key());
      if (p.proposed?.status === 'EVIDENCE_FOUND') await decideRequirement(detail.requirement_id, { version: approved.data.version, action: 'confirm_met' } as DecisionRequest, key());
    },
    onSuccess: onDone, onSettled: refresh,
  });
  const again = useMutation({
    mutationFn: () => createQuestion(projectId, { requirement_id: detail.requirement_id, origin: question.origin, issue_type: question.issue_type, question_text: question.question_text, target: 'CUSTOMER', follows_question_id: question.question_id } as QuestionCreate, key(), { useMockFallback: false }),
    onSettled: () => client.invalidateQueries({ queryKey: ['questions'] }),
  });
  const result = apply.data;
  return <div className="ws-form">
    <p className="ws-reply"><span className="muted">회신 </span>{marked(question.reply?.reply_text ?? '', read?.quote)}</p>
    {question.reply && <p className="muted ws-small">{[question.reply.source_ref, question.reply.received_at && `${question.reply.received_at.slice(0, 10)} 수신`].filter(Boolean).join(', ')}</p>}
    {read ? <p className="ws-note">회신에서 읽은 값: {[read.value, read.unit].filter(Boolean).join(' ')}{read.conditions?.length ? `, ${read.conditions.map(c => [c.value, c.unit].filter(Boolean).join(' ')).join(', ')}` : ''}
      {read.method === 'ai' && <span className="muted ws-small"> AI가 읽음</span>}
</p>
      : <p className="muted ws-note">{found?.reason ? `${REASON[found.reason] ?? '회신에서 값을 찾지 못했습니다'}. ` : '회신에서 값을 찾지 못했습니다. '}값을 직접 입력하거나 다시 질의하세요.</p>}
    <div className="ws-go">
      <label className="ws-field inline">새 요구값<input value={value} placeholder="값을 직접 입력" onChange={event => setValue(event.target.value)} /></label>
      <label className="ws-field inline">단위<input value={unit} size={5} onChange={event => setUnit(event.target.value)} /></label>
      <label className="ws-field inline">조건<input value={cond} placeholder="1 min" size={8} onChange={event => setCond(event.target.value)} /></label>
    </div>
    {!result ? <div className="ws-go">
      <button className="kb primary" disabled={!value.trim() || apply.isPending} onClick={() => apply.mutate()}>값 반영하고 다시 비교</button>
      <button className="ws-link" disabled={again.isPending || again.isSuccess} onClick={() => again.mutate()}>다시 질의</button>
    </div> : <div className="ws-decided">
      <span>다시 비교: {result.proposal ? RECOMPARED[result.proposal.proposed?.status ?? ''] ?? '확인 필요' : '판정이 바뀌지 않습니다'}</span><span className="sp" />
      {result.proposal && <button className="kb primary" disabled={confirm.isPending} onClick={() => confirm.mutate(result.proposal!)}>결과대로 확정</button>}
    </div>}
    {again.isSuccess && <p className="muted ws-note">후속 질의를 질의서에 넣었습니다.</p>}
    {(apply.isError || confirm.isError || again.isError) && <p role="alert">{failureText(apply.error ?? confirm.error ?? again.error)}</p>}
  </div>;
}


// 변경 이력(KAN-226): 결정 패널 아래 요구별, 출력 탭 프로젝트 전체. 최신이 위, 더 보기는 next_cursor로 잇는다.
const KIND_GROUP: Record<string, HistoryKind[]> = {
  전체: [], 결정: ['decision', 'decision_undone', 'review_confirmed', 'review_returned', 'human_change'],
  회신: ['reply_recorded', 'reply_applied'], 재비교: ['recompared'], AI: ['ai_verdict'],
};
const when = (at: string) => new Date(at).toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

function useHistory(key: unknown[], load: (cursor?: string | null) => Promise<{ data: HistoryPage }>, enabled = true) {
  const pages = useInfiniteQuery({
    queryKey: ['history', getApiMode(), ...key], enabled,
    queryFn: ({ pageParam }) => load(pageParam).then(r => r.data),
    initialPageParam: null as string | null, getNextPageParam: last => last.next_cursor,
  });
  return { pages, items: pages.data?.pages.flatMap(p => p.items) ?? [] };
}

function RequirementHistory({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const { pages, items } = useHistory(['req', id], cursor => listRequirementHistory(id, cursor));
  const name = useNames(items.map(h => h.actor_user_id));
  const count = pages.isSuccess ? `${items.length}${pages.hasNextPage ? '+' : ''}건` : '';
  if (!open) return <button className="ws-link muted ws-histlink" onClick={() => setOpen(true)}>이력 {count}</button>;
  return <div className="ws-hist" aria-label="이 요구 이력">
    {pages.isError ? <p role="alert">{failureText(pages.error)}</p> : pages.isPending ? <p className="muted">이력 읽는 중</p>
      : items.length === 0 ? <p className="muted">기록이 없습니다.</p>
      : <ol>{items.map(h => <li key={h.id}><span className="muted">{when(h.at)} {name(h.actor_user_id) || (h.kind === 'ai_verdict' ? 'AI' : '시스템')}</span> {historyText(h).head} <span className="muted">{historyText(h).change}</span></li>)}</ol>}
    {pages.hasNextPage && <button className="ws-link" disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>더 보기</button>}
  </div>;
}

function ProjectHistory({ projectId, codeOf, onPick }: { projectId: string; codeOf: (id: string | null) => string; onPick: (id: string) => void }) {
  const [group, setGroup] = useState('전체');
  const [actor, setActor] = useState('');
  const { pages, items } = useHistory(['project', projectId, group, actor], cursor => listProjectHistory(projectId, KIND_GROUP[group], actor || undefined, cursor));
  const name = useNames(items.map(h => h.actor_user_id));
  const actors = [...new Set(items.map(h => h.actor_user_id).filter((a): a is string => !!a))];
  return <section className="ws-sec ws-review" aria-label="변경 이력">
    <h2>변경 이력</h2>
    <div className="ws-filters ws-histfilter">
      {Object.keys(KIND_GROUP).map(k => <Chip key={k} on={group === k} onClick={() => setGroup(k)}>{k}</Chip>)}
      <span className="sp" />
      <label className="ws-field inline">사람<select value={actor} onChange={event => setActor(event.target.value)}><option value="">전체</option>{actors.map(a => <option key={a} value={a}>{name(a) || a.slice(0, 8)}</option>)}</select></label>
    </div>
    {pages.isError ? <p role="alert">{failureText(pages.error)}</p> : <table className="ws-table ws-histtable">
      <thead><tr><th>시각</th><th>사람</th><th>요구</th><th>무엇을</th><th>전</th><th>후</th></tr></thead>
      <tbody>{items.map(h => <tr key={h.id} className="row" onClick={() => h.requirement_id && onPick(h.requirement_id)}>
        <td className="muted">{when(h.at)}</td><td>{name(h.actor_user_id) || (h.kind === 'ai_verdict' ? 'AI' : '시스템')}</td>
        <td>{codeOf(h.requirement_id)}</td><td>{historyText(h).head}</td><td className="muted">{historyText(h).before}</td><td>{historyText(h).after}</td></tr>)}
        {!pages.isPending && items.length === 0 && <tr><td colSpan={6} className="muted">기록이 없습니다.</td></tr>}</tbody>
    </table>}
    {pages.hasNextPage && <button className="ws-link" disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>더 보기</button>}
  </section>;
}

// 직접 추가·빼기·되살리기(KAN-225). 결정 기록의 action으로 오고 화면에서는 아래 말로 보인다.
const CHANGE_LABEL: Record<string, string> = { add_requirement: '직접 추가', exclude_requirement: '뺌', restore_requirement: '되살림' };
const KIND_LABEL = { SPEC: '사양', TASK: '과업', DOCUMENT: '제출물' } as const;
// 서버 근거 검사와 같은 정규화: 공백 하나, 곧은 따옴표, 대소문자 무시.
export const normQuote = (s: string) => s.replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/\s+/g, ' ').trim().toLowerCase();

function useClauses(documentVersionId: string | undefined) {
  return useQuery({
    queryKey: ['clauses', getApiMode(), documentVersionId], enabled: getApiMode() === 'live' && !!documentVersionId, staleTime: 300_000,
    queryFn: async () => {
      const all: Clause[] = [];
      let cursor: string | undefined;
      do { const page = await listClauses(documentVersionId as string, cursor); all.push(...page.data.items); cursor = page.data.next_cursor ?? undefined; } while (cursor);
      return all;
    },
  });
}

// 고른 글이 들어 있는 조항. 없으면(표 글, 여러 조항에 걸친 글) 요구로 추가할 수 없다.
export const clauseOf = <C extends Pick<Clause, 'clause_id' | 'clause_no' | 'text'>>(clauses: C[], quote: string) =>
  quote ? clauses.find(c => normQuote(c.text ?? '').includes(normQuote(quote))) : undefined;

function AddRequirement({ projectId, quote, clause, equipment, onDone, onCancel }: { projectId: string; quote: string; clause: Pick<Clause, 'clause_id' | 'clause_no'>; equipment?: string | null; onDone: (id: string) => void; onCancel: () => void }) {
  const client = useQueryClient();
  const [kind, setKind] = useState<keyof typeof KIND_LABEL>('SPEC');
  const [eq, setEq] = useState(equipment ?? '');
  const [attr, setAttr] = useState('');
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState('');
  const [cond, setCond] = useState('');
  const [dupe, setDupe] = useState(false);
  const valueOk = !value.trim() || normQuote(quote).includes(normQuote(value));
  const add = useMutation({
    mutationFn: () => {
      const [cv, cu] = cond.trim().split(/\s+/);
      return addRequirement(projectId, {
        clause_id: clause.clause_id, quote, kind, equipment: eq.trim() || null, task: kind === 'SPEC' ? null : attr.trim(), attribute: attr.trim(),
        value: value.trim() || null, unit: unit.trim() || null, conditions: cv ? [{ name: 'duration', value: cv, unit: cu ?? null }] : [], allow_duplicate: dupe,
      } as ManualRequirementRequest, key());
    },
    onSuccess: result => { client.invalidateQueries({ queryKey: ['workspace-requirements'] }); client.invalidateQueries({ queryKey: ['projects'] }); onDone(result.data.requirement_id); },
    onError: error => { if (error instanceof ApiError && error.status === 409) setDupe(true); },
  });
  const failed = add.error instanceof ApiError && add.error.status !== 409 ? add.error : add.isError && !(add.error instanceof ApiError) ? add.error : undefined;
  return <div className="ws-pick ws-form" aria-label="새 요구(직접 추가)">
    <div className="hd"><span className="name">새 요구(직접 추가)</span><span className="sp" /><button className="ws-link" onClick={onCancel}>취소</button></div>
    <div className="ws-cmp"><span className="k">조항</span><span className="v">{clause.clause_no}</span><span className="k">인용</span><span className="v ws-quoteval">{quote}</span></div>
    <div className="ws-go">
      <label className="ws-field inline">설비<input value={eq} size={10} onChange={event => setEq(event.target.value)} /></label>
      <label className="ws-field inline">종류<select value={kind} onChange={event => setKind(event.target.value as keyof typeof KIND_LABEL)}>{Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
    </div>
    <label className="ws-field">속성<input value={attr} onChange={event => setAttr(event.target.value)} /></label>
    <div className="ws-go">
      <label className="ws-field inline">값<input value={value} size={10} onChange={event => { setValue(event.target.value); setDupe(false); }} /></label>
      <label className="ws-field inline">단위<input value={unit} size={5} onChange={event => setUnit(event.target.value)} /></label>
      <label className="ws-field inline">조건<input value={cond} size={8} placeholder="1 min" onChange={event => setCond(event.target.value)} /></label>
    </div>
    {!valueOk && <p role="alert" className="ws-alert">값이 고른 글에 없습니다. 고른 글에 있는 값만 넣을 수 있습니다.</p>}
    {dupe && <p role="status" className="ws-note">같은 설비·속성·값의 요구가 이미 있습니다. 그래도 추가하려면 다시 누르세요.</p>}
    <div className="ws-go"><button className="kb primary" disabled={!attr.trim() || !valueOk || add.isPending} onClick={() => add.mutate()}>{dupe ? '그래도 추가' : '추가하고 비교'}</button></div>
    {failed && <p role="alert">{failureText(failed)}</p>}
  </div>;
}

function ExcludeLine({ row, version }: { row: Row; version: number }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [why, setWhy] = useState('');
  const exclude = useMutation({
    mutationFn: () => decideRequirement(row.requirement_id, { version, action: 'exclude_requirement', text: why.trim() } as DecisionRequest, key()),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['workspace-requirements'] }); client.invalidateQueries({ queryKey: ['projects'] }); },
  });
  if (!open) return <button className="ws-link muted ws-histlink" onClick={() => setOpen(true)}>요구에서 빼기</button>;
  return <div className="ws-form ws-exclude">
    <div className="ws-go"><label className="ws-field inline">뺀 사유<input value={why} size={28} onChange={event => setWhy(event.target.value)} /></label>
      <button className="kb" disabled={!why.trim() || exclude.isPending} onClick={() => exclude.mutate()}>요구에서 빼기</button><button className="ws-link" onClick={() => setOpen(false)}>취소</button></div>
    {exclude.isError && <p role="alert">{failureText(exclude.error)}</p>}
  </div>;
}

function ExcludedTable({ rows, responsible }: { rows: Row[]; responsible: boolean }) {
  const client = useQueryClient();
  const restore = useMutation({
    mutationFn: (r: Row) => decideRequirement(r.requirement_id, { version: r.version, action: 'restore_requirement' } as DecisionRequest, key()),
    onSettled: () => { client.invalidateQueries({ queryKey: ['workspace-requirements'] }); client.invalidateQueries({ queryKey: ['projects'] }); },
  });
  return <section className="ws-sec ws-review" aria-label="뺀 요구">
    <h2>뺀 요구 <span className="muted">{rows.length}건</span></h2>
    <table className="ws-table"><thead><tr><th>설비</th><th>요구</th><th>요구값</th><th>뺀 사유</th><th className="r" /></tr></thead>
      <tbody>{rows.map(r => <tr key={r.requirement_id}><td className="tag">{r.equipment}</td><td>{r.attribute ?? r.code}</td><td>{r.value_text ?? '빈 칸'}</td><td>{r.decision?.excluded_reason}</td>
        <td className="r">{!responsible && <button className="kb" disabled={restore.isPending} onClick={() => restore.mutate(r)}>되살리기</button>}</td></tr>)}
        {rows.length === 0 && <tr><td colSpan={5} className="muted">뺀 요구가 없습니다.</td></tr>}</tbody></table>
    {restore.isError && <p role="alert">{failureText(restore.error)}</p>}
  </section>;
}

// 책임자 확인 대상: 확인 안 된 결정(뺀 요구 제외)과 확인 안 된 직접 추가·빼기·되살리기. 확인·되돌려 보내기는 그 decision_id로 한다.
export type PendingReview = { row: Row; decisionId: string; label: string };
export const pendingReviews = (rows: Row[]): PendingReview[] => rows.flatMap(r => {
  const d = r.decision;
  if (!d) return [];
  return [
    ...(d.decided && !d.reviewed && !d.excluded && d.decision_id ? [{ row: r, decisionId: d.decision_id, label: d.summary ?? '결정' }] : []),
    ...(d.change_id && !d.change_reviewed ? [{ row: r, decisionId: d.change_id, label: CHANGE_LABEL[d.change_action ?? ''] ?? '변경' }] : []),
  ];
});

const FIELD_LABEL: Record<string, string> = { status: '판단', value: '값', unit: '단위', operator: '비교', necessity: '필수 여부', path: '경로' };
const fieldValue = (k: string, v: unknown) => v == null ? '없음' : k === 'status' ? label(STATUS_LABEL, String(v)) : k === 'necessity' ? label(NECESSITY_LABEL, String(v)) : k === 'path' ? label(PATH_LABEL, String(v)) : k === 'operator' ? label(OPERATOR_LABEL, String(v)) || '같음' : String(v);
// 이력 한 줄의 내용: 직접 추가·빼기·되살리기는 그 말로, 판본 변화는 바뀐 칸마다 전 → 후.
export function historyText(h: HistoryItem) {
  const action = String(h.after?.action ?? '');
  // 서버 요약의 괄호 안 질의 종류 코드(예: 회신 기록 (EMPTY_VALUE))는 한국어로 바꾼다.
  const summary = h.summary.replace(/\(([A-Z_]+)\)/g, (_m, code: string) => `(${label(ISSUE_LABEL, code)})`);
  const head = h.kind === 'decision' && CHANGE_LABEL[action] ? `${CHANGE_LABEL[action]}: ${summary}` : summary;
  const keys = [...new Set([...Object.keys(h.before ?? {}), ...Object.keys(h.after ?? {})])].filter(k => FIELD_LABEL[k]);
  return {
    head,
    change: keys.map(k => `${FIELD_LABEL[k]} ${h.before ? fieldValue(k, h.before[k]) + ' → ' : ''}${fieldValue(k, h.after?.[k])}`).join(', '),
    before: h.before ? keys.map(k => `${FIELD_LABEL[k]} ${fieldValue(k, h.before?.[k])}`).join(', ') : '',
    after: keys.map(k => `${FIELD_LABEL[k]} ${fieldValue(k, h.after?.[k])}`).join(', '),
  };
}

// 회신 문장 안의 근거 문장을 강조한다. 근거가 문장에 그대로 없으면 강조하지 않는다.
const marked = (text: string, quote?: string | null) => {
  const at = quote ? text.indexOf(quote) : -1;
  return at < 0 || !quote ? text : <>{text.slice(0, at)}<mark className="ws-mark">{quote}</mark>{text.slice(at + quote.length)}</>;
};

// 엑셀: 지금 보이는 순서의 요구를 CSV로 내려받는다(엑셀이 한글을 읽도록 BOM을 붙인다).
function downloadCsv(name: string, rows: Row[]) {
  const cell = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const lines = [['설비', '조항', '요구', '요구값', '판정', '결정'], ...rows.map(r => [r.equipment, r.code, r.attribute, r.value_text, VERDICT[r.bid_verdict], r.decision?.summary])];
  const url = URL.createObjectURL(new Blob(['\uFEFF' + lines.map(l => l.map(cell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
