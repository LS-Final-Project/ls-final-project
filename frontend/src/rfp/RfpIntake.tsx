import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getJob, listDocuments, listProposals, listQuestions, type DocumentVersion } from '../api/rfp';
import { getProject } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime } from '../components/tft';
import { KvBox, KvField, KvGrid, KvNext, KvSplit, KvTitle, type KvColumn } from '../ls-shell/kv';
import { ProcessForm, UploadForm } from './IntakeForms';
import { DATA_ORIGIN_LABEL, DOCUMENT_KIND_LABEL, JUDGEMENT_LABEL, label, NECESSITY_LABEL, OUTPUT_LABEL, PURPOSE_LABEL, STATUS_LABEL } from './labels';

const POLL_MS = 30_000;
const ACTIVE = new Set(['QUEUED', 'RUNNING']);
const format = (mime: string) => mime.includes('pdf') ? 'PDF' : mime.includes('word') ? 'DOCX' : mime;
const DOCUMENTS: KvColumn<DocumentVersion>[] = [
  { key: 'title', header: '문서', className: 't', cell: d => d.title },
  { key: 'kind', header: '종류', width: 96, className: 'c', cell: d => label(DOCUMENT_KIND_LABEL, d.kind) },
  { key: 'version', header: '판본', width: 48, className: 'n', cell: d => d.version },
  { key: 'mime', header: '형식', width: 56, className: 'c', cell: d => format(d.mime) },
  { key: 'trace', header: '편집 흔적', width: 76, className: 'c', cell: d => d.edit_trace_support ? '확인 가능' : '확인 불가' },
  { key: 'at', header: '올린 일시', width: 168, className: 'd', cell: d => formatDateTime(d.uploaded_at) },
];
const counted = (counts: Record<string, number>, labels: Record<string, string>) =>
  Object.entries(labels).map(([code, text]) => `${text} ${counts[code] ?? 0}`).join(', ');

// 실행 때 받은 작업 ID. 프로젝트 응답에 작업이 없어(latest_job은 null) 새로고침해도 이어 보도록 탭에 둔다.
// 다른 탭이나 다른 사람의 실행은 여기 없으므로 서버의 TFT_JOB_IN_PROGRESS가 막는다.
const jobKey = (projectId: string) => `rfp-job:${projectId}`;
function rememberedJob(projectId: string) {
  try { return sessionStorage.getItem(jobKey(projectId)) ?? undefined; } catch { return undefined; }
}
function rememberJob(projectId: string, jobId: string | undefined) {
  try {
    if (jobId) sessionStorage.setItem(jobKey(projectId), jobId);
    else sessionStorage.removeItem(jobKey(projectId));
  } catch { /* 저장소를 못 쓰면 이 화면에 있는 동안만 본다 */ }
}

// 접수 및 요구 검토(TF0101) 프로젝트 화면. 진행 칸(output_status)은 산출물 단계이지 작업 상태가 아니다.
// 작업이 도는지는 작업 ID로만 판단한다. 진행 칸으로 막으면 끝난 프로젝트가 영영 잠긴다.
export default function RfpIntake() {
  const { projectId = '' } = useParams();
  // 프로젝트를 옮기면 작업·알림 상태를 새로 시작한다(같은 경로 요소라 그대로 두면 앞 프로젝트 상태가 남는다).
  return <Intake key={projectId} projectId={projectId} />;
}

function Intake({ projectId }: { projectId: string }) {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const client = useQueryClient();
  const [panel, setPanel] = useState<'upload' | 'process'>();
  const [jobId, setJobId] = useState(() => rememberedJob(projectId));
  const [finished, setFinished] = useState<string>();
  const enabled = access.ready && projectId !== '';
  const job = useQuery({
    queryKey: ['jobs', mode, jobId],
    queryFn: () => getJob(jobId as string),
    enabled: enabled && !!jobId,
    refetchInterval: query => { const status = query.state.data?.data.status; return !status || ACTIVE.has(status) ? POLL_MS : false; },
  });
  const jobStatus = job.data?.data.status;
  // 상태를 읽지 못하면(다른 사용자의 작업 404, 장애) 돈다고 단정하지 않는다. 화면을 잠그지 않고 서버 409에 맡긴다.
  const running = !!jobId && !job.isError && (!jobStatus || ACTIVE.has(jobStatus));
  const project = useQuery({
    queryKey: ['projects', mode, projectId],
    queryFn: () => getProject(projectId),
    enabled,
  });
  const documents = useQuery({ queryKey: ['rfp-documents', mode, projectId], queryFn: () => listDocuments(projectId), enabled });
  const pending = useQuery({ queryKey: ['rfp-proposals', mode, projectId, 'PENDING'], queryFn: () => listProposals(projectId, { status: 'PENDING' }), enabled });
  const open = useQuery({ queryKey: ['rfp-questions', mode, projectId, 'OPEN'], queryFn: () => listQuestions(projectId, 'OPEN'), enabled });
  const jobGone = job.error instanceof ApiError && (job.error.status === 404 || job.error.status === 403);
  useEffect(() => {
    if (!jobId || !jobGone) return;
    setJobId(undefined);
    rememberJob(projectId, undefined);
  }, [jobId, jobGone, projectId]);
  useEffect(() => {
    if (!jobId || !jobStatus || ACTIVE.has(jobStatus)) return;
    setFinished(jobStatus);
    setJobId(undefined);
    rememberJob(projectId, undefined);
    for (const key of ['projects', 'rfp-documents', 'rfp-requirements', 'rfp-proposals', 'rfp-questions']) client.invalidateQueries({ queryKey: [key, mode] });
  }, [jobId, jobStatus, client, mode, projectId]);
  if (!access.ready) return <BusinessGate access={access} title="접수 및 요구 검토" />;
  if (project.isPending) return <main><Loading /></main>;
  if (project.isError) return <main><ErrorState error={project.error} onRetry={() => project.refetch()} /></main>;
  const data = project.data.data;
  const docs = documents.data?.data.items ?? [];
  const analyzing = running;
  const counts = data.requirement_counts;
  const proposals = pending.data?.data.items ?? [];
  const questions = open.data?.data.items ?? [];
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
  return <main>
    <KvTitle path="제안 지원" title="접수 및 요구 검토" code="TF0101">
      <button className="kb primary" disabled={analyzing} title={analyzing ? '작업이 끝나야 합니다' : undefined} onClick={() => setPanel('upload')}>문서 올리기</button>
      <button className="kb" disabled={analyzing || docs.length === 0} title={analyzing ? '작업이 끝나야 합니다' : docs.length === 0 ? '문서를 먼저 올리세요' : undefined} onClick={() => setPanel('process')}>처리 실행</button>
      <Link className="kb" to={`/tft/projects/${projectId}/table`}>자사 대응표</Link>
      <Link className="kb" to="/tft/projects">목록</Link>
    </KvTitle>
    <KvNext>{next}</KvNext>
    {panel === 'upload' && <UploadForm projectId={projectId} documents={docs} onCancel={() => setPanel(undefined)} onDone={() => {
      setPanel(undefined);
      client.invalidateQueries({ queryKey: ['rfp-documents', mode] });
      client.invalidateQueries({ queryKey: ['projects', mode] });
    }} />}
    {panel === 'process' && <ProcessForm projectId={projectId} documents={docs} rerun={data.requirements_count > 0} onCancel={() => setPanel(undefined)} onStarted={started => { setPanel(undefined); setFinished(undefined); setJobId(started.job_id); rememberJob(projectId, started.job_id); }} />}
    <KvSplit columns="1fr 1fr">
      <KvBox title={`프로젝트 ${data.external_id ?? ''}`.trim()}>
        <KvField label="프로젝트명">{data.name}</KvField>
        <KvField label="고객">{data.client_name ?? '미기재'}</KvField>
        <KvField label="제출 목적">{label(PURPOSE_LABEL, data.purpose)}</KvField>
        <KvField label="진행">{label(OUTPUT_LABEL, data.output_status)}</KvField>
        <KvField label="판단">{label(JUDGEMENT_LABEL, data.judgement_status)}</KvField>
        <KvField label="담당">{data.sales_user_id ? '지정' : '미지정'}</KvField>
        <KvField label="책임자">{data.responsible_user_id ? '지정' : '미지정'}</KvField>
        <KvField label="검토 부서">{data.review_departments.join(', ') || '없음'}</KvField>
        <KvField label="검토본">{data.review_version ? `v${data.review_version}` : '없음'}</KvField>
        <KvField label="자료 구분">{label(DATA_ORIGIN_LABEL, data.data_origin)}</KvField>
      </KvBox>
      <KvBox title="요구 요약" note={`요구 ${data.requirements_count}건`}>
        <KvField label="상태">{counted(counts.by_status, STATUS_LABEL)}</KvField>
        <KvField label="성격">{counted(counts.by_necessity, NECESSITY_LABEL)}</KvField>
        <KvField label="미해결 필수">{data.unresolved_mandatory_count}</KvField>
        <KvField label="재검토 필요">{counts.needs_rereview}</KvField>
        <KvField label="열린 질문">{data.open_questions_count}</KvField>
        <KvField label="승인 대기">{data.pending_proposals_count}</KvField>
        <KvField label="초안">{data.draft_version ? `판본 ${data.draft_version}` : '없음'}</KvField>
      </KvBox>
    </KvSplit>
    <div className="kv-gridhead"><span>문서와 판본 {docs.length}건</span></div>
    {documents.isError
      ? <ErrorState error={documents.error} onRetry={() => documents.refetch()} />
      : <KvGrid label="문서와 판본" columns={DOCUMENTS} rows={docs} rowKey={d => d.document_version_id} fallback={documents.isPending ? <Loading /> : '문서가 없습니다. 문서 올리기로 시작하세요.'} />}
    <div className="kv-sub">이 프로젝트에서 내가 할 일</div>
    <KvSplit columns="1fr 1fr 1fr">
      <KvBox title={`승인 대기 ${proposals.length}`}>
        <p style={{ margin: 0 }}>{proposals.length ? <Link to={`/tft/projects/${projectId}/table/approvals`}>승인 화면으로</Link> : '승인 대기 제안이 없습니다.'}</p>
      </KvBox>
      <KvBox title={`회신 대기 질문 ${questions.length}`}>
        {questions.length ? <ul style={{ margin: 0, paddingLeft: 16 }}>{questions.slice(0, 5).map(q => <li key={q.question_id}>{q.requirement_id ? <Link to={`/tft/projects/${projectId}/requirements/${q.requirement_id}`}>{q.question_text}</Link> : q.question_text}</li>)}</ul> : <p style={{ margin: 0 }}>회신 대기 질문이 없습니다.</p>}
      </KvBox>
      <KvBox title={`미해결 필수 ${data.unresolved_mandatory_count}`}>
        <p style={{ margin: 0 }}>{data.unresolved_mandatory_count ? <Link to={`/tft/projects/${projectId}/table`}>대응표에서 보기</Link> : '미해결 필수가 없습니다.'}</p>
      </KvBox>
    </KvSplit>
  </main>;
}
