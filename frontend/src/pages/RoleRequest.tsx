import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { me } from '../api/platform';
import { getProject, issueStaffingRequest, listStaffingRequests, updateStaffingRequest, type Participation, type ProjectDetail, type RequiredRole, type StaffingRequest, type StaffingRequestPatch } from '../api/tft';
import { Empty, ErrorState, Loading, Preparing } from '../components/states';
import { COMPETENCY_KIND_LABEL, Evidence, Origin, PARTICIPATION_LABEL, REQUEST_STATUS_LABEL, ROLE_STATUS_LABEL, StatusBadge, formatDate, formatDateTime } from '../components/tft';

type OrgOption = { id: string; name: string; deptCode: string | null };

// S2 역할·인력 요청서 (docs/tft-screens.md). 역할 요건은 프로젝트 조회(KAN-119), 요청서 목록·편집·발행은 인력요청 API(KAN-122) 계약을 쓴다.
export default function RoleRequest() {
  const { projectId = '', roleId = '' } = useParams();
  const mode = getApiMode();
  const enabled = import.meta.env.DEV && projectId !== '' && roleId !== '';
  const project = useQuery({queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled});
  const requests = useQuery({queryKey: ['staffing-requests', mode, projectId], queryFn: () => listStaffingRequests(projectId), enabled});
  const user = useQuery({queryKey: ['me'], queryFn: me, enabled: import.meta.env.DEV});
  if (!import.meta.env.DEV) return <Preparing title="TFT 역할·인력 요청서" />;
  const data = project.data?.data;
  const role = data?.required_roles.find(item => item.id === roleId);
  const items = requests.data?.data.items.filter(item => item.required_role_id === roleId);
  // 조직 목록 API가 없으므로 프로젝트 상세와 요청서에 들어 있는 organization 정보만 선택지로 쓴다.
  const orgOptions = uniqueOrgs([
    ...(data?.staffing_requests ?? []).map(req => ({ id: req.organization_id, name: req.organization_name, deptCode: null })),
    ...(requests.data?.data.items ?? []).map(req => ({ id: req.organization_id, name: req.organization_name, deptCode: req.organization_dept_code })),
  ]);
  const roles = user.data?.data.roles ?? null;
  return <main>
    <header><p className="eyebrow"><Link to="/tft/projects">TFT 프로젝트 목록</Link> / <Link to={`/tft/projects/${projectId}`}>{data?.name ?? '프로젝트 현황'}</Link></p>
      <h1>{role ? role.title : '역할·인력 요청서'}</h1>{role && <StatusBadge code={role.status} labels={ROLE_STATUS_LABEL} />}</header>
    {project.isPending && <Loading />}
    {project.isError && <ErrorState error={project.error} onRetry={() => project.refetch()} />}
    {data && !role && <ErrorState error={new ApiError(404, '이 프로젝트에 없는 역할입니다.')} />}
    {data && role && <>
      <RoleInfo role={role} />
      <Competencies role={role} />
      <LinkedRequirements role={role} project={data} />
      <section><h2>인력 요청서</h2>
        {requests.isPending && <Loading label="요청서 불러오는 중" />}
        {requests.isError && <ErrorState error={requests.error} onRetry={() => requests.refetch()} />}
        {items && items.length === 0 && <Empty message="요청서 없음" />}
        {items && items.length > 0 && <RequestList items={items} orgOptions={orgOptions} userRoles={roles} />}
        <ReferenceRequests project={data} roleId={roleId} known={new Set(items?.map(item => item.id))} />
      </section>
    </>}
  </main>;
}

function uniqueOrgs(options: OrgOption[]) {
  const map = new Map<string, OrgOption>();
  for (const option of options) { const prev = map.get(option.id); map.set(option.id, { ...option, deptCode: option.deptCode ?? prev?.deptCode ?? null }); }
  return [...map.values()];
}

function RoleInfo({ role }: { role: RequiredRole }) {
  return <section><h2>역할 요건</h2>
    <dl>
      <dt>역할 코드</dt><dd>{role.role_code} {role.is_core ? <small>핵심</small> : <small>보강</small>}</dd>
      <dt>수행 과업</dt><dd>{role.tasks ?? '미정'}</dd>
      <dt>유사 경험 조건</dt><dd>{role.experience_condition ?? '미정'}</dd>
      <dt>요청 인원</dt><dd>{role.headcount ?? '미정'}</dd>
      <dt>투입 기간</dt><dd>{formatDate(role.period_start)} ~ {formatDate(role.period_end)}</dd>
      <dt>투입률</dt><dd>{role.allocation_rate != null ? `${role.allocation_rate}%` : '미정'}</dd>
      <dt>분야</dt><dd>{role.field_tag ?? '미정'}</dd>
      <dt>담당 검토</dt><dd>{role.reviewed_at ? <>{formatDateTime(role.reviewed_at)} <small>사용자 {role.reviewed_by_user_id}</small></> : '검토 전'}</dd>
      <dt>다음 행동</dt><dd>{role.next_action ?? '-'}{role.reply_due_at && <> <small>회신 기한 {formatDateTime(role.reply_due_at)}</small></>}</dd>
      <dt>출처</dt><dd><Origin dataOrigin={role.data_origin} derivationKind={role.derivation_kind} /></dd>
    </dl>
  </section>;
}

function Competencies({ role }: { role: RequiredRole }) {
  const required = role.competencies.filter(c => c.kind === 'REQUIRED');
  const preferred = role.competencies.filter(c => c.kind === 'PREFERRED');
  const list = (items: typeof required, kind: string) => items.length === 0 ? <p className="state">{COMPETENCY_KIND_LABEL[kind]} 역량 미정</p> : <ul>{items.map(c => <li key={c.id}>{c.description}</li>)}</ul>;
  return <section><h2>역량</h2>
    <div className="columns"><article><h3>필수 역량</h3>{list(required, 'REQUIRED')}</article><article><h3>우대 역량</h3>{list(preferred, 'PREFERRED')}</article></div>
  </section>;
}

function LinkedRequirements({ role, project }: { role: RequiredRole; project: ProjectDetail }) {
  const links = role.requirements.map(link => ({ link, requirement: project.rfp_requirements.find(req => req.id === link.requirement_id) }));
  return <section><h2>연결 요구사항과 원문 근거</h2>
    {links.length === 0 ? <p className="state">근거 없음 — 추가 확인 (사람이 추가한 역할이거나 RFP 연결 전)</p> :
      <table><thead><tr><th>코드</th><th>요구</th><th>도출 이유</th><th>원문 근거</th></tr></thead>
        <tbody>{links.map(({ link, requirement }) => <tr key={link.requirement_id}>
          <td>{requirement?.requirement_code ?? link.requirement_id}</td>
          <td>{requirement ? requirement.title : <span>프로젝트 요구사항 목록에 없음</span>}</td>
          <td>{link.rationale ?? '-'}</td>
          <td>{requirement ? <Evidence requirement={requirement} /> : '근거 없음'}</td>
        </tr>)}</tbody></table>}
  </section>;
}

// 프로젝트 조회 응답에도 요청서 요약이 들어 있다(KAN-119). 인력요청 API 목록에 없는 것만 읽기 전용으로 보여 중복을 피한다.
function ReferenceRequests({ project, roleId, known }: { project: ProjectDetail; roleId: string; known: Set<string> }) {
  const refs = project.staffing_requests.filter(req => req.required_role_id === roleId && !known.has(req.id));
  if (refs.length === 0) return null;
  return <details><summary>프로젝트 조회 응답의 요청서 {refs.length}건 (읽기 전용 — 편집·발행은 인력요청 API 연결 후)</summary>
    <table><thead><tr><th>부서</th><th>참여</th><th>상태</th><th>기한</th><th>메시지</th></tr></thead>
      <tbody>{refs.map(req => <tr key={req.id}><td>{req.organization_name}</td><td>{PARTICIPATION_LABEL[req.participation]}</td><td>{REQUEST_STATUS_LABEL[req.status] ?? req.status}</td><td>{formatDateTime(req.due_at)}</td><td>{req.message ?? '-'}</td></tr>)}</tbody></table>
  </details>;
}

type Notice = { kind: 'info' | 'error'; text: string; requestId?: string };

// 안내(notice)·치명 오류(fatal)는 요청서 ID별로 여기에 둔다. 저장·발행 성공이나 412/409 뒤 재조회로 version이 바뀌어도
// 폼은 재마운트되지 않고(key는 id만) 안내와 입력값이 남는다.
function RequestList({ items, orgOptions, userRoles }: { items: StaffingRequest[]; orgOptions: OrgOption[]; userRoles: string[] | null }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notices, setNotices] = useState<Record<string, { notice: Notice | null; fatal: unknown }>>({});
  const selected = items.find(item => item.id === selectedId) ?? items[0];
  const feedback = notices[selected.id] ?? { notice: null, fatal: null };
  const setFeedback = (id: string, value: { notice: Notice | null; fatal: unknown }) => setNotices(prev => ({ ...prev, [id]: value }));
  return <>
    <table><thead><tr><th>부서</th><th>참여</th><th>상태</th><th>기한</th><th>판본</th><th></th></tr></thead>
      <tbody>{items.map(item => <tr key={item.id} aria-selected={item.id === selected.id}>
        <td>{item.organization_name} {item.organization_dept_code && <small>{item.organization_dept_code}</small>}</td>
        <td>{PARTICIPATION_LABEL[item.participation]}</td>
        <td><StatusBadge code={item.status} labels={REQUEST_STATUS_LABEL} /></td>
        <td>{item.due_undecided ? '기한 미정' : formatDateTime(item.due_at)}</td>
        <td>{item.version}</td>
        <td><button type="button" onClick={() => setSelectedId(item.id)} aria-pressed={item.id === selected.id}>{item.id === selected.id ? '편집 중' : '편집'}</button></td>
      </tr>)}</tbody></table>
    <RequestForm key={selected.id} request={selected} orgOptions={orgOptions} userRoles={userRoles} feedback={feedback} setFeedback={value => setFeedback(selected.id, value)} />
  </>;
}

const toLocalInput = (iso: string | null) => {
  if (!iso) return '';
  const d = new Date(iso); const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

type Feedback = { notice: Notice | null; fatal: unknown };
type FormValues = { organizationId: string; participation: Participation; dueAt: string; dueUndecided: boolean; message: string };
const fromRequest = (request: StaffingRequest): FormValues => ({ organizationId: request.organization_id, participation: request.participation, dueAt: toLocalInput(request.due_at), dueUndecided: request.due_undecided, message: request.message ?? '' });

// 바뀐 필드만 PATCH 본문에 넣는다. message null→'' 같은 표시용 변환이나 분 단위로 잘린 due_at을 바꾸지 않았는데 보내지 않기 위해서다.
function diffPatch(values: FormValues, request: StaffingRequest): StaffingRequestPatch {
  const base = fromRequest(request);
  const patch: StaffingRequestPatch = {};
  if (values.organizationId !== base.organizationId) patch.organization_id = values.organizationId;
  if (values.participation !== base.participation) patch.participation = values.participation;
  if (values.dueUndecided !== base.dueUndecided) patch.due_undecided = values.dueUndecided;
  const dueChanged = values.dueUndecided ? request.due_at !== null : values.dueAt !== base.dueAt;
  if (dueChanged) patch.due_at = values.dueUndecided || values.dueAt === '' ? null : new Date(values.dueAt).toISOString();
  if (values.message !== base.message) patch.message = values.message;
  return patch;
}

function RequestForm({ request, orgOptions, userRoles, feedback, setFeedback }: { request: StaffingRequest; orgOptions: OrgOption[]; userRoles: string[] | null; feedback: Feedback; setFeedback: (value: Feedback) => void }) {
  const cache = useQueryClient();
  const [values, setValues] = useState<FormValues>(() => fromRequest(request));
  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) => setValues(prev => ({ ...prev, [key]: value }));
  const { notice, fatal } = feedback;

  // 잠금 이유: DRAFT가 아닌 요청서, 또는 로그인 역할에 sales가 없음. 서버 검사는 UI 잠금과 무관하게 수행된다.
  const lockReason = request.status !== 'DRAFT' ? `작성 중(DRAFT) 요청서만 편집·발행할 수 있습니다. 현재 상태: ${REQUEST_STATUS_LABEL[request.status] ?? request.status}${request.issued_at ? ` (발행 ${formatDateTime(request.issued_at)})` : ''}`
    : userRoles && !userRoles.includes('sales') ? '담당 sales만 요청서를 편집·발행할 수 있습니다.' : null;
  const locked = lockReason !== null;
  const patch = diffPatch(values, request);
  const dirty = Object.keys(patch).length > 0;
  const issueBlock = locked ? lockReason : dirty ? '저장하지 않은 변경이 있습니다. 먼저 저장하세요.' : (request.due_undecided ? false : !request.due_at) ? '기한을 입력하거나 "기한 미정"을 표시해야 발행할 수 있습니다.' : null;

  const refresh = () => { cache.invalidateQueries({queryKey: ['staffing-requests']}); cache.invalidateQueries({queryKey: ['projects']}); };
  const onError = (error: unknown) => {
    const api = error instanceof ApiError ? error : null;
    if (api?.status === 412) { setFeedback({ notice: { kind: 'error', text: '다른 사람이 먼저 수정했습니다. 새로고침 후 다시 시도하세요. 서버의 최신 판본을 다시 불러왔으며 입력한 값은 그대로 두었습니다. 아래 서버 값과 비교해 확인한 뒤 다시 저장하거나 "서버 값으로 되돌리기"를 누르세요.', requestId: api.requestId }, fatal: null }); refresh(); return; }
    if (api?.status === 428) { setFeedback({ notice: { kind: 'error', text: '판본 정보(If-Match)가 없어 처리하지 못했습니다. 화면 오류이므로 요청 ID와 함께 알려 주세요.', requestId: api.requestId }, fatal: null }); return; }
    if (api?.status === 409) { setFeedback({ notice: { kind: 'error', text: '상태가 바뀌어 처리할 수 없습니다. 최신 상태를 다시 불러왔습니다. 입력한 값은 그대로 두었습니다.', requestId: api.requestId }, fatal: null }); refresh(); return; }
    if (api?.status === 422) { setFeedback({ notice: { kind: 'error', text: api.code === 'EVIDENCE_REQUIRED' ? '기한 또는 "기한 미정" 표시가 필요합니다.' : api.message, requestId: api.requestId }, fatal: null }); return; }
    setFeedback({ notice: null, fatal: error });
  };
  const save = useMutation({
    mutationFn: (body: StaffingRequestPatch) => updateStaffingRequest(request.id, request.version, body),
    onMutate: () => setFeedback({ notice: null, fatal: null }),
    onSuccess: result => { setFeedback({ notice: { kind: 'info', text: `저장했습니다. 판본 ${result.data.version}` }, fatal: null }); refresh(); },
    onError,
  });
  const issue = useMutation({
    mutationFn: () => issueStaffingRequest(request.id, request.version),
    onMutate: () => setFeedback({ notice: null, fatal: null }),
    onSuccess: result => { setFeedback({ notice: { kind: 'info', text: `요청서를 발행했습니다. 요청 부서의 목록에 나타납니다. 판본 ${result.data.version}` }, fatal: null }); refresh(); },
    onError,
  });
  // 기한 없는 작성 중 요청서도 저장은 된다(계약상 EVIDENCE_REQUIRED는 발행에서만). 기한 조건은 발행 버튼이 막는다.
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate(patch); };
  const busy = save.isPending || issue.isPending;
  const orgKnown = orgOptions.some(option => option.id === values.organizationId);
  const serverValues = fromRequest(request);
  return <form onSubmit={submit} aria-label="인력 요청서 편집" className="request-form">
    <h3>요청서 편집 <small>{request.role_name}</small> <small data-testid="request-version">판본 {request.version}</small> <Origin dataOrigin={request.data_origin} derivationKind={request.derivation_kind} /></h3>
    {locked && <p className="notice" role="note">읽기 전용: {lockReason}</p>}
    <fieldset disabled={locked || busy}>
      <div className="field">
        <label htmlFor="sr-organization">요청 부서</label>
        {orgOptions.length > 0 ? <select id="sr-organization" value={values.organizationId} onChange={e => set('organizationId', e.target.value)}>
          {!orgKnown && <option value={values.organizationId}>{values.organizationId}</option>}
          {orgOptions.map(option => <option key={option.id} value={option.id}>{option.name}{option.deptCode ? ` (${option.deptCode})` : ''}</option>)}
        </select> : <input id="sr-organization" value={values.organizationId} onChange={e => set('organizationId', e.target.value)} placeholder="organization_id (UUID)" />}
        <small>조직 목록 API 미구현 — {orgOptions.length > 0 ? '프로젝트와 요청서에 포함된 부서만 선택할 수 있습니다.' : 'organization_id를 직접 입력합니다.'}</small>
      </div>
      <div className="field">
        <label htmlFor="sr-participation">참여 형태</label>
        <select id="sr-participation" value={values.participation} onChange={e => set('participation', e.target.value as Participation)}>
          <option value="LEAD">주관 (LEAD)</option><option value="SUPPORT">협업 (SUPPORT)</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="sr-due-at">회신 기한</label>
        <input id="sr-due-at" type="datetime-local" value={values.dueAt} disabled={values.dueUndecided} onChange={e => set('dueAt', e.target.value)} aria-describedby="sr-due-hint" />
        <label className="check"><input type="checkbox" checked={values.dueUndecided} onChange={e => set('dueUndecided', e.target.checked)} /> 기한 미정</label>
        <small id="sr-due-hint">기한과 "기한 미정" 중 하나가 있어야 발행할 수 있습니다. 빈칸으로 두지 않습니다.</small>
      </div>
      <div className="field">
        <label htmlFor="sr-message">메시지</label>
        <textarea id="sr-message" rows={4} value={values.message} onChange={e => set('message', e.target.value)} />
      </div>
    </fieldset>
    <div className="actions">
      <button type="submit" disabled={locked || busy || !dirty}>{save.isPending ? '저장 중' : '저장'}</button>
      <button type="button" disabled={issueBlock !== null || busy} onClick={() => issue.mutate()} aria-describedby="sr-issue-hint">{issue.isPending ? '발행 중' : '발행'}</button>
      <button type="button" disabled={locked || busy || !dirty} onClick={() => setValues(serverValues)}>서버 값으로 되돌리기</button>
      <small id="sr-issue-hint">{issueBlock ?? '발행하면 요청 부서의 목록에 공지됩니다. 메일은 보내지 않습니다.'}</small>
    </div>
    {notice && <p role={notice.kind === 'error' ? 'alert' : 'status'} className={notice.kind === 'error' ? 'state error' : 'state'}>{notice.text}{notice.requestId && <> <small>요청 ID {notice.requestId}</small></>}</p>}
    {notice?.kind === 'error' && dirty && <dl className="server-values"><dt>서버 값 (판본 {request.version})</dt><dd>부서 {request.organization_name} · {PARTICIPATION_LABEL[request.participation]} · {request.due_undecided ? '기한 미정' : formatDateTime(request.due_at)} · 메시지 {request.message ?? '-'}</dd></dl>}
    {fatal != null && <ErrorState error={fatal} />}
  </form>;
}
