import { keepPreviousData, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getDocument, getJob, listDocuments, listProposals, listQuestions, listRequirements, type DocumentVersion, type Proposal, type Question, type RequirementSummary } from '../api/rfp';
import { getProject, listProjects, type ProjectFilters, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDate } from '../components/tft';
import { downloadCsv, KvCond, KvCondField, KvCondRow, KvFoot, KvGrid, KvKey, KvNext, KvRibbon, KvSplit, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { latestVersions, ProcessForm, ProjectForm, UploadForm } from './IntakeForms';
import { useNames } from './names';
import { attributeName, DATA_ORIGIN_LABEL, DEPARTMENT_LABEL, DOCUMENT_KIND_LABEL, ISSUE_LABEL, JUDGEMENT_LABEL, label, NECESSITY_LABEL, ORIGIN_LABEL, OUTPUT_LABEL, PURPOSE_LABEL, requirementNo, shortDateTime, STATUS_LABEL } from './labels';

const POLL_MS = 30_000;
const ACTIVE = new Set(['QUEUED', 'RUNNING']);
const format = (mime: string) => mime.includes('pdf') ? 'PDF' : mime.includes('word') ? 'DOCX' : mime;
// 작업 상태 경로는 요청한 사용자 본인만 열린다. 404·403이면 다시 읽어도 소용없다.
const notMine = (error: unknown) => error instanceof ApiError && (error.status === 404 || error.status === 403);

const count = (value: number, ready: boolean) => ready ? value : '';
const PROJECT_HEAD = ['프로젝트명', '고객', '진행', '판단', '요구', '미해결 필수', '승인 대기', '열린 질문', '접수일'];
const projectRow = (p: ProjectSummary) => [p.name, p.client_name, label(OUTPUT_LABEL, p.output_status), label(JUDGEMENT_LABEL, p.judgement_status), p.requirements_count, p.unresolved_mandatory_count, p.pending_proposals_count, p.open_questions_count, p.created_at.slice(0, 10)];
const days = (at: string) => Math.max(0, Math.floor((Date.now() - new Date(at).getTime()) / 86_400_000));

// 접수 및 요구 검토(TF0101, TF_K_Intake): 프로젝트 목록과 고른 프로젝트를 한 화면에 둔다.
// 경로에 프로젝트가 없으면 목록의 첫 프로젝트를 고른다.
export default function RfpIntake() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const [filters, setFilters] = useState<ProjectFilters>({});
  const [text, setText] = useState('');
  const [creating, setCreating] = useState(false);
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const cursor = cursors.at(-1);
  const projects = useQuery({ queryKey: ['projects', mode, filters, cursor], queryFn: () => listProjects(filters, cursor), enabled: access.ready, placeholderData: keepPreviousData });
  const refetch = projects.refetch;
  const reload = useCallback(() => { refetch(); }, [refetch]);
  useHotkey('F8', reload, access.ready);
  const items = projects.data?.data.items ?? [];
  const nameOf = useNames(items.flatMap(p => [p.sales_user_id, p.responsible_user_id]));
  if (!access.ready) return <BusinessGate access={access} title="접수 및 요구 검토" />;
  const filter = (change: ProjectFilters) => { setFilters({ ...filters, ...change }); setCursors([undefined]); setChecked(new Set()); };
  // 서버 검색어는 2~100자다. 한 글자는 보내지 않고 알린다.
  const search = text.trim();
  const shortSearch = search.length === 1;
  const next = projects.data?.data.next_cursor;
  const selected = projectId ?? items[0]?.id;
  const columns: KvColumn<ProjectSummary>[] = [
    { key: 'check', header: <input type="checkbox" aria-label="전체 선택" checked={items.length > 0 && checked.size === items.length} onChange={event => setChecked(new Set(event.target.checked ? items.map(p => p.id) : []))} />, width: 30, className: 'c', cell: p => <input
      type="checkbox"
      aria-label={`${p.name} 선택`}
      checked={checked.has(p.id)}
      onClick={event => event.stopPropagation()}
      onChange={event => {
        const nextChecked = new Set(checked);
        if (event.target.checked) nextChecked.add(p.id); else nextChecked.delete(p.id);
        setChecked(nextChecked);
      }}
    /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, index) => index + 1 },
    { key: 'code', header: '프로젝트', width: 88, cell: p => p.external_id ? <Link to={`/tft/projects/${p.id}`}>{p.external_id}</Link> : '' },
    { key: 'name', header: '프로젝트명', className: 't', cell: p => <Link to={`/tft/projects/${p.id}`}>{p.name}</Link> },
    { key: 'client', header: '고객', width: 130, cell: p => p.client_name ?? '' },
    { key: 'output', header: '진행', width: 96, className: 'c', cell: p => label(OUTPUT_LABEL, p.output_status) },
    { key: 'judgement', header: '판단', width: 104, className: 'c', cell: p => label(JUDGEMENT_LABEL, p.judgement_status) },
    // 요구가 없는 접수 단계는 0이 아니라 빈칸이다(아직 셀 것이 없다).
    { key: 'requirements', header: '요구', width: 48, className: 'c', cell: p => p.requirements_count || '없음' },
    { key: 'unresolved', header: '미해결 필수', width: 76, className: 'c', cell: p => count(p.unresolved_mandatory_count, p.requirements_count > 0) },
    { key: 'pending', header: '승인 대기', width: 64, className: 'c', cell: p => count(p.pending_proposals_count, p.requirements_count > 0) },
    { key: 'questions', header: '열린 질문', width: 64, className: 'c', cell: p => count(p.open_questions_count, p.requirements_count > 0) },
    { key: 'sales', header: '담당', width: 104, className: 'c', cell: p => nameOf(p.sales_user_id) || (p.sales_user_id ? '지정' : '미지정') },
    { key: 'owner', header: '책임자', width: 104, className: 'c', cell: p => nameOf(p.responsible_user_id) || (p.responsible_user_id ? '지정' : '미지정') },
    { key: 'created', header: '접수일', width: 96, className: 'd', sort: 'descending', cell: p => formatDate(p.created_at.slice(0, 10)) },
  ];
  const list = <>
    <KvCond onSearch={() => { if (!shortSearch) filter({ q: search || undefined }); }}>
      <KvCondRow columns={3}>
        <KvCondField label="진행"><select aria-label="진행" value={filters.output_status ?? ''} onChange={event => filter({ output_status: event.target.value || undefined })}>
          <option value="">전체</option>
          {Object.entries(OUTPUT_LABEL).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select></KvCondField>
        <KvCondField label="판단"><select aria-label="판단" value={filters.judgement_status ?? ''} onChange={event => filter({ judgement_status: event.target.value || undefined })}>
          <option value="">전체</option>
          {Object.entries(JUDGEMENT_LABEL).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select></KvCondField>
        <KvCondField label="검색어"><input aria-label="검색어" type="text" maxLength={100} placeholder="프로젝트명, 고객명, 2자 이상" value={text} onChange={event => setText(event.target.value)} /></KvCondField>
      </KvCondRow>
      <button type="submit" hidden>검색</button>
    </KvCond>
    <div className="kv-gridhead"><span>{shortSearch ? '검색어는 두 글자 이상 입력하세요.' : `내 프로젝트 ${items.length}건, 접수일 내림차순. 요구 N건 중 미해결 필수 M건`}</span></div>
    {projects.isError
      ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
      : <KvGrid
        label="접수 및 요구 검토"
        columns={columns}
        rows={items}
        rowKey={p => p.id}
        selectedKey={selected}
        onRow={p => navigate(`/tft/projects/${p.id}`)}
        fallback={projects.isPending ? <Loading /> : '프로젝트가 없습니다.'}
      />}
    {(cursors.length > 1 || next) && <KvFoot
      count={`${cursors.length}쪽, ${items.length}건`}
      more={<>
        <button className="kb" disabled={projects.isFetching || cursors.length === 1} onClick={() => { setCursors(cursors.slice(0, -1)); setChecked(new Set()); }}>이전</button>
        <button className="kb" disabled={projects.isFetching || !next} onClick={() => { if (next) { setCursors([...cursors, next]); setChecked(new Set()); } }}>다음</button>
      </>}
    />}
    {creating && <ProjectForm onCancel={() => setCreating(false)} />}
  </>;
  const common = <>
    <button className="kb primary" disabled={projects.isFetching || shortSearch} onClick={() => { if (search !== (filters.q ?? '')) filter({ q: search || undefined }); else projects.refetch(); }}>조회<KvKey>F8</KvKey></button>
    <button className="kb" onClick={() => setCreating(true)}>접수</button>
  </>;
  const excel = <button className="kb" disabled={items.length === 0} title={checked.size ? `고른 ${checked.size}건` : '표시한 행 전체'} onClick={() => downloadCsv('프로젝트 목록', PROJECT_HEAD, items.filter(p => !checked.size || checked.has(p.id)).map(projectRow))}>엑셀</button>;
  if (!selected) return <main><KvTitle path="제안 지원" title="접수 및 요구 검토" code="TF0101">{common}{excel}</KvTitle>{list}</main>;
  // 프로젝트를 옮기면 작업·알림 상태를 새로 시작한다(같은 경로 요소라 그대로 두면 앞 프로젝트 상태가 남는다).
  return <Intake key={selected} projectId={selected} list={list} before={common} after={excel} />;
}

// 고른 프로젝트 부분. 진행 칸(output_status)은 산출물 단계이지 작업 상태가 아니다.
// 작업이 도는지는 작업 ID로만 판단한다. 진행 칸으로 막으면 끝난 프로젝트가 영영 잠긴다.
function Intake({ projectId, list, before, after }: { projectId: string; list: ReactNode; before: ReactNode; after: ReactNode }) {
  const mode = getApiMode();
  const client = useQueryClient();
  const [panel, setPanel] = useState<'upload' | 'process'>();
  // 방금 실행한 작업. 프로젝트 응답이 latest_job을 돌려주기 전까지 이 ID로 본다.
  const [started, setStarted] = useState<string>();
  const [finished, setFinished] = useState<string>();
  // 끝남은 돌던 모습을 본 작업만 알린다. latest_job은 끝난 뒤에도 남아 안 그러면 들어올 때마다 완료가 뜬다.
  const watched = useRef<string>(undefined);
  const enabled = projectId !== '';
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled });
  const jobId = started ?? project.data?.data.latest_job?.job_id;
  const job = useQuery({
    queryKey: ['jobs', mode, jobId],
    queryFn: () => getJob(jobId as string),
    enabled: enabled && !!jobId,
    refetchInterval: query => {
      if (notMine(query.state.error)) return false;
      const status = query.state.data?.data.status;
      return !status || ACTIVE.has(status) ? POLL_MS : false;
    },
  });
  const jobStatus = job.data?.data.status;
  // 상태를 읽지 못하면(다른 사용자의 작업 404, 장애) 돈다고 단정하지 않는다. 화면을 잠그지 않고 서버 409에 맡긴다.
  const running = !!jobId && !job.isError && (!jobStatus || ACTIVE.has(jobStatus));
  const documents = useQuery({ queryKey: ['rfp-documents', mode, projectId], queryFn: () => listDocuments(projectId), enabled });
  const pending = useQuery({ queryKey: ['rfp-proposals', mode, projectId, 'PENDING'], queryFn: () => listProposals(projectId, { status: 'PENDING' }), enabled });
  const open = useQuery({ queryKey: ['rfp-questions', mode, projectId, 'OPEN'], queryFn: () => listQuestions(projectId, 'OPEN'), enabled });
  const mandatory = useQuery({ queryKey: ['rfp-requirements', mode, projectId, 'unresolved'], queryFn: () => listRequirements(projectId, { unresolved_mandatory: true }, 5), enabled });
  const docs = documents.data?.data.items ?? [];
  const details = useQueries({ queries: docs.map(d => ({ queryKey: ['rfp-document', mode, d.document_id], queryFn: () => getDocument(d.document_id) })) });
  const proposals = pending.data?.data.items ?? [];
  const nameOf = useNames([
    project.data?.data.sales_user_id, project.data?.data.responsible_user_id,
    ...docs.map(d => d.uploaded_by), ...proposals.map(p => p.proposer_user_id),
  ]);
  useEffect(() => {
    if (!jobId || !jobStatus) return;
    if (ACTIVE.has(jobStatus)) { watched.current = jobId; return; }
    if (watched.current !== jobId) return;
    watched.current = undefined;
    setStarted(undefined);
    setFinished(jobStatus);
    for (const key of ['projects', 'rfp-documents', 'rfp-requirements', 'rfp-proposals', 'rfp-questions']) client.invalidateQueries({ queryKey: [key, mode] });
  }, [jobId, jobStatus, client, mode]);
  const title = (buttons?: ReactNode) => <KvTitle path="제안 지원" title="접수 및 요구 검토" code="TF0101">{before}{buttons}{after}</KvTitle>;
  if (project.isPending) return <main>{title()}{list}<Loading /></main>;
  if (project.isError) return <main>{title()}{list}<ErrorState error={project.error} onRetry={() => project.refetch()} /></main>;
  const data = project.data.data;
  const analyzing = running;
  const counts = data.requirement_counts;
  const questions = open.data?.data.items ?? [];
  const unresolved = mandatory.data?.data.items ?? [];
  const latest = new Set(latestVersions(docs).map(d => d.document_version_id));
  const original = latestVersions(docs).find(d => d.kind === 'RFP_SPEC') ?? latestVersions(docs)[0];
  const next = (() => {
    if (finished === 'FAILED' || finished === 'CANCELLED') return '처리가 끝나지 못했습니다. 입력을 확인하고 다시 실행하세요.';
    if (finished === 'PARTIAL') return `일부만 처리됐습니다. 요구 ${data.requirements_count}건, 빠진 절이 있으니 자사 대응표를 확인하세요.`;
    if (finished && data.requirements_count === 0) return '분석 완료, 요구 0건. 원문을 확인하세요. 평가 절이 비었거나 뽑은 요구가 인용 검사에서 모두 버려졌을 수 있습니다.';
    if (finished) return `분석 완료, 요구 ${data.requirements_count}건. 자사 대응표에서 확인하세요.`;
    if (analyzing) return '분석 중입니다. 30초마다 다시 읽습니다. 처리 실행과 문서 올리기는 이 작업이 끝나야 합니다.';
    if (data.documents_count === 0) return `새 프로젝트, 문서 0, 요구 0. 다음 문서 올리기, 그 다음 처리 실행. 접수만으로는 분석하지 않습니다.`;
    if (data.requirements_count === 0) return `문서 ${data.documents_count}건, 요구 0. 다음 처리 실행. 올리기만으로는 분석하지 않습니다.`;
    return `요구 ${data.requirements_count}건, 미해결 필수 ${data.unresolved_mandatory_count}, 승인 대기 ${data.pending_proposals_count}, 열린 질문 ${data.open_questions_count}`;
  })();
  // 못 읽은 표를 '없습니다'로 보이면 위 건수와 어긋나고 할 일을 숨긴다.
  const todoFallback = (query: { isPending: boolean; isError: boolean }, empty: string) =>
    query.isError ? '목록을 읽지 못했습니다. 조회를 다시 눌러 주세요.' : query.isPending ? '읽는 중입니다.' : empty;
  const documentColumns: KvColumn<DocumentVersion>[] = [
    { key: 'title', header: '문서', className: 't', cell: d => d.title },
    { key: 'kind', header: '종류', width: 84, className: 'c', cell: d => label(DOCUMENT_KIND_LABEL, d.kind) },
    { key: 'version', header: '판본', width: 44, className: 'c', cell: d => d.version },
    { key: 'mime', header: '형식', width: 50, className: 'c', cell: d => format(d.mime) },
    { key: 'trace', header: '편집 흔적', width: 66, className: 'c', cell: d => d.edit_trace_support ? '확인 가능' : '확인 불가' },
    { key: 'clauses', header: '조항', width: 44, className: 'c', cell: (d, index) => latest.has(d.document_version_id) ? details[index]?.data?.data.clauses_count ?? '' : '' },
    { key: 'deleted', header: '삭제 값', width: 52, className: 'c', cell: (d, index) => latest.has(d.document_version_id) ? details[index]?.data?.data.deleted_values_count || '' : '' },
    { key: 'by', header: '올린 사람', width: 104, className: 'c', cell: d => nameOf(d.uploaded_by) },
    { key: 'at', header: '올린 일시', width: 120, className: 'd', cell: d => shortDateTime(d.uploaded_at) },
  ];
  const proposalColumns: KvColumn<Proposal>[] = [
    { key: 'origin', header: '들어온 곳', width: 72, className: 'c', cell: p => label(ORIGIN_LABEL, p.origin) },
    { key: 'text', header: '내용', className: 't', cell: p => <Link to={`/tft/projects/${projectId}/table/approvals`}>{p.rationale}</Link> },
    { key: 'by', header: '제안자', width: 56, className: 'c', cell: p => p.proposer_kind === 'system' ? '자동' : nameOf(p.proposer_user_id) },
    { key: 'at', header: '일시', width: 56, className: 'd', cell: p => p.known_at.slice(5, 10) },
  ];
  const questionColumns: KvColumn<Question>[] = [
    { key: 'issue', header: '유형', width: 72, className: 'c', cell: q => label(ISSUE_LABEL, q.issue_type) },
    { key: 'text', header: '내용', className: 't', cell: q => q.requirement_id ? <Link to={`/tft/projects/${projectId}/requirements/${q.requirement_id}`}>{q.question_text}</Link> : q.question_text },
    { key: 'days', header: '경과', width: 44, className: 'c', cell: q => `${days(q.created_at)}일` },
  ];
  const unresolvedColumns: KvColumn<RequirementSummary>[] = [
    { key: 'no', header: '요구', width: 84, cell: r => <Link to={`/tft/projects/${projectId}/requirements/${r.requirement_id}`}>{requirementNo(r.code)}</Link> },
    { key: 'text', header: '내용', className: 't', cell: r => [attributeName(r.attribute_key, r.attribute), label(STATUS_LABEL, r.status)].filter(Boolean).join(', ') },
    { key: 'block', header: '막힌 곳', width: 64, className: 'c', cell: r => r.open_questions ? `질문 ${r.open_questions}` : '판단' },
  ];
  const summary: [string, ReactNode, boolean?][] = [
    [`요구 ${data.requirements_count}건`, Object.entries(STATUS_LABEL).map(([code, name]) => `${name} ${counts?.by_status[code] ?? 0}`).join(', ')],
    ['성격', Object.entries(NECESSITY_LABEL).map(([code, name]) => `${name} ${counts?.by_necessity[code] ?? 0}`).join(', ')],
    ['미해결 필수', data.unresolved_mandatory_count, data.unresolved_mandatory_count > 0],
    ['재검토 필요', counts?.needs_rereview ?? 0],
    ['열린 질문', data.open_questions_count],
    ['승인 대기', data.pending_proposals_count],
    ['초안', [data.draft_version ? `판본 ${data.draft_version}` : '없음', data.review_version ? `검토본 v${data.review_version}` : ''].filter(Boolean).join(', ')],
  ];
  return <main>
    {title(<>
      <button className="kb" disabled={analyzing} title={analyzing ? '작업이 끝나야 합니다' : undefined} onClick={() => setPanel('upload')}>문서 올리기</button>
      <button className="kb" disabled={analyzing || docs.length === 0} title={analyzing ? '작업이 끝나야 합니다' : docs.length === 0 ? '문서를 먼저 올리세요' : undefined} onClick={() => setPanel('process')}>처리 실행</button>
      {original
        ? <a className="kb" href={`/api/v1/tft/documents/${encodeURIComponent(original.document_id)}/file`}>원문 내려받기</a>
        : <button className="kb" disabled title="문서를 먼저 올리세요">원문 내려받기</button>}
    </>)}
    <KvNext>{next}</KvNext>
    {list}
    {panel === 'upload' && <UploadForm projectId={projectId} documents={docs} onCancel={() => setPanel(undefined)} onDone={() => {
      setPanel(undefined);
      client.invalidateQueries({ queryKey: ['rfp-documents', mode] });
      client.invalidateQueries({ queryKey: ['projects', mode] });
    }} />}
    {panel === 'process' && <ProcessForm projectId={projectId} documents={docs} rerun={data.requirements_count > 0} onCancel={() => setPanel(undefined)} onStarted={({ job_id }) => { setPanel(undefined); setFinished(undefined); watched.current = job_id; setStarted(job_id); }} />}
    <div className="kv-gridhead"><span>선택 {data.external_id ?? data.name}</span><Link className="right" to={`/tft/projects/${projectId}/table`}>자사 대응표 (TF0102)</Link></div>
    <KvRibbon cells={[
      ['프로젝트', data.external_id ?? ''],
      ['프로젝트명', data.name],
      ['고객', data.client_name ?? '미기재'],
      ['제출 목적', label(PURPOSE_LABEL, data.purpose)],
      ['진행', label(OUTPUT_LABEL, data.output_status)],
      ['판단', label(JUDGEMENT_LABEL, data.judgement_status)],
      ['담당', nameOf(data.sales_user_id) || (data.sales_user_id ? '지정' : '미지정')],
      ['책임자', nameOf(data.responsible_user_id) || (data.responsible_user_id ? '지정' : '미지정')],
      ['검토 부서', (data.review_departments ?? []).map(code => label(DEPARTMENT_LABEL, code)).join(', ') || '없음'],
      ['최근 작업', jobStatus ? label({ QUEUED: '대기', RUNNING: '실행 중', SUCCEEDED: '완료', PARTIAL: '일부 완료', FAILED: '실패', CANCELLED: '취소' }, jobStatus) : '없음'],
      ['검토본', data.review_version ? `v${data.review_version}` : '없음'],
      ['자료 구분', label(DATA_ORIGIN_LABEL, data.data_origin)],
    ]} />
    <KvSplit columns="1.25fr 1fr">
      <div>
        <p className="kv-label">문서와 판본 {docs.length}건, 이전 판본은 흐리게</p>
        {documents.isError
          ? <ErrorState error={documents.error} onRetry={() => documents.refetch()} />
          : <KvGrid label="문서와 판본" columns={documentColumns} rows={docs} rowKey={d => d.document_version_id} rowClass={d => latest.has(d.document_version_id) ? undefined : 'old'} fallback={documents.isPending ? <Loading /> : '문서가 없습니다. 문서 올리기로 시작하세요.'} />}
      </div>
      <div>
        <p className="kv-label">요구 요약</p>
        <table className="kv-summary" aria-label="요구 요약"><tbody>
          {summary.map(([name, value, hot]) => <tr key={name}><th scope="row">{name}</th><td className={hot ? 'hot' : undefined}>{value}</td></tr>)}
        </tbody></table>
      </div>
    </KvSplit>
    <div className="kv-gridhead sub"><span>이 프로젝트에서 내가 할 일, 매일 여기서 출발. 건수는 프로젝트 상세의 집계 값</span></div>
    <KvSplit columns="1fr 1fr 1fr">
      <div>
        <div className="kv-headlink"><span>승인 대기 {data.pending_proposals_count}</span><Link to={`/tft/projects/${projectId}/table/approvals`}>승인 대기 화면</Link></div>
        <KvGrid label="승인 대기" columns={proposalColumns} rows={proposals.slice(0, 5)} rowKey={p => p.proposal_id} fallback={todoFallback(pending, '승인 대기 제안이 없습니다.')} />
      </div>
      <div>
        <div className="kv-headlink"><span>회신 대기 질문 {data.open_questions_count}</span></div>
        <KvGrid label="회신 대기 질문" columns={questionColumns} rows={questions.slice(0, 5)} rowKey={q => q.question_id} fallback={todoFallback(open, '회신 대기 질문이 없습니다.')} />
      </div>
      <div>
        <div className="kv-headlink"><span>미해결 필수 {data.unresolved_mandatory_count}</span><Link to={`/tft/projects/${projectId}/table`}>대응표에서 보기</Link></div>
        <KvGrid label="미해결 필수" columns={unresolvedColumns} rows={unresolved} rowKey={r => r.requirement_id} fallback={todoFallback(mandatory, '미해결 필수가 없습니다.')} />
      </div>
    </KvSplit>
  </main>;
}
