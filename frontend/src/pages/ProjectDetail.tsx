import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getProject, type ProjectDetail as Project, type RequiredRole } from '../api/tft';
import { Empty, ErrorState, Loading, Preparing } from '../components/states';
import { COMPETENCY_KIND_LABEL, Evidence, Origin, PROJECT_STATUS_LABEL, REQUEST_STATUS_LABEL, ROLE_STATUS_LABEL, StatusBadge, formatDate, formatDateTime } from '../components/tft';

const countBy = <T,>(items: T[], key: (item: T) => string) => items.reduce<Record<string, number>>((acc, item) => { const k = key(item); acc[k] = (acc[k] ?? 0) + 1; return acc; }, {});
const summarize = (counts: Record<string, number>, labels: Record<string, string>) => Object.entries(counts).map(([code, n]) => `${labels[code] ?? code} ${n}`).join(' · ') || '없음';

// S1 프로젝트·역할 현황 (docs/tft-screens.md). 프로젝트 조회(KAN-119) 응답만으로 그리며 문서 처리 상태는 KAN-120 연결 전이라 준비 중으로 둔다.
export default function ProjectDetail() {
  const { projectId = '' } = useParams();
  const mode = getApiMode();
  const project = useQuery({queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled: import.meta.env.DEV && projectId !== ''});
  if (!import.meta.env.DEV) return <Preparing title="TFT 프로젝트" />;
  const data = project.data?.data;
  return <main>
    <header><p className="eyebrow"><Link to="/tft/projects">TFT 프로젝트 목록</Link></p><h1>{data ? data.name : '프로젝트 현황'}</h1></header>
    {project.isPending && <Loading />}
    {project.isError && <ErrorState error={project.error} onRetry={() => project.refetch()} />}
    {data && <>
      <p><Link to="composition">구성안 비교와 책임자 결정</Link></p>
      <ProjectInfo project={data} />
      <DocumentStatus project={data} />
      <InitialReview project={data} />
      <Requirements project={data} />
      <Roles project={data} />
    </>}
  </main>;
}

function ProjectInfo({ project }: { project: Project }) {
  const r = project.responsible;
  return <section><h2>프로젝트 정보</h2>
    <dl>
      <dt>외부 ID</dt><dd>{project.external_id ?? '-'}</dd>
      <dt>고객</dt><dd>{project.client_name ?? '-'}</dd>
      <dt>회사 코드</dt><dd>{project.company_code}</dd>
      <dt>상태</dt><dd><StatusBadge code={project.status} labels={PROJECT_STATUS_LABEL} /></dd>
      <dt>책임자</dt><dd>{r ? <>지정됨 — 사용자 {r.user_id} <br /><small>지정 {formatDateTime(r.assigned_at)} · 사유: {r.reason}</small></> : <span className="warn">책임자 미지정 — 구성안 확정 불가. 관리 manager가 지정합니다.</span>}</dd>
      <dt>담당 sales</dt><dd>정보 없음 <small>프로젝트 조회 계약(KAN-119)에 담당 sales 필드가 없음</small></dd>
      <dt>구성안 판본</dt><dd>{project.current_composition ? `판본 ${project.current_composition.version}` : '없음'}</dd>
      <dt>최종 확정</dt><dd>{project.final_confirmation ? `판본 ${project.final_confirmation.composition_version} · ${formatDateTime(project.final_confirmation.confirmed_at)}` : '미확정'}</dd>
      <dt>등록</dt><dd>{formatDateTime(project.created_at)}</dd>
      <dt>출처</dt><dd><Origin dataOrigin={project.data_origin} derivationKind={project.derivation_kind} /></dd>
    </dl>
  </section>;
}

// 문서처리현황: RFP 업로드·문서 처리 API(KAN-120)가 없어 처리 상태 필드가 응답에 없다. 요구사항이 인용한 문서명만 보이고 상태는 준비 중이다.
function DocumentStatus({ project }: { project: Project }) {
  const documents = [...new Set(project.rfp_requirements.map(req => req.document_name).filter((name): name is string => !!name))];
  if (documents.length === 0) return <Preparing inline title="문서 처리 현황" message="문서 처리 연결 준비 중 — RFP 문서 업로드·처리 상태 API(KAN-120) 연결 전입니다." />;
  return <section><h2>문서 처리 현황</h2>
    <table><thead><tr><th>문서명</th><th>처리 상태</th></tr></thead>
      <tbody>{documents.map(name => <tr key={name}><td>{name}</td><td><span className="state-inline">문서 처리 연결 준비 중</span> <small>KAN-120</small></td></tr>)}</tbody></table>
  </section>;
}

function InitialReview({ project }: { project: Project }) {
  const roles = project.required_roles;
  const requests = project.staffing_requests;
  const rolesWithoutRequest = roles.filter(role => !requests.some(req => req.required_role_id === role.id)).length;
  const rolesWithoutCompetency = roles.filter(role => role.competencies.length === 0).length;
  const reviewed = roles.filter(role => role.reviewed_at).length;
  return <section><h2>초기검토</h2>
    <dl>
      <dt>프로젝트 단계</dt><dd>{project.status === 'INITIAL_REVIEW' ? '초기검토 진행 중' : <>초기검토 이후 단계 <small>{PROJECT_STATUS_LABEL[project.status] ?? project.status}</small></>}</dd>
      <dt>요구사항</dt><dd>{project.rfp_requirements.length}건</dd>
      <dt>역할 준비</dt><dd>{roles.length === 0 ? '정리된 역할 없음' : <>{roles.length}개 — {summarize(countBy(roles, role => role.status), ROLE_STATUS_LABEL)}<br /><small>담당 검토 완료 {reviewed} · 역량 미정 {rolesWithoutCompetency}</small></>}</dd>
      <dt>요청서 준비</dt><dd>{requests.length === 0 ? '요청서 없음' : summarize(countBy(requests, req => req.status), REQUEST_STATUS_LABEL)}{roles.length > 0 && <><br /><small>요청서 없는 역할 {rolesWithoutRequest}</small></>}</dd>
    </dl>
  </section>;
}

// 요구사항 계약(RfpRequirement)에는 필수/우대 필드가 없다. 연결된 역할의 역량(kind REQUIRED/PREFERRED)으로 구분하고 역량이 없으면 "미정"이다.
function Requirements({ project }: { project: Project }) {
  const roles = project.required_roles;
  const classify = (requirementId: string) => {
    const linked = roles.filter(role => role.requirements.some(link => link.requirement_id === requirementId));
    const kinds = countBy(linked.flatMap(role => role.competencies), c => c.kind);
    return { linked, kinds, label: kinds.REQUIRED ? '필수' : kinds.PREFERRED ? '우대' : '미정' };
  };
  const groups = countBy(project.rfp_requirements, req => classify(req.id).label);
  return <section><h2>RFP 요구사항</h2>
    {project.rfp_requirements.length === 0 ? <Empty message="정리된 요구사항이 없습니다." /> : <>
      <p className="summary">필수 {groups['필수'] ?? 0} · 우대 {groups['우대'] ?? 0} · 미정 {groups['미정'] ?? 0} <small>구분은 연결 역할의 역량(필수/우대)에서 도출. 역량이 없으면 미정</small></p>
      <table><thead><tr><th>코드</th><th>요구</th><th>구분</th><th>연결 역할</th><th>원문 근거</th><th>출처</th></tr></thead>
        <tbody>{project.rfp_requirements.map(req => { const c = classify(req.id); return <tr key={req.id}>
          <td>{req.requirement_code}</td>
          <td><strong>{req.title}</strong>{req.description && <><br /><small>{req.description}</small></>}{req.field_tag && <><br /><small>{req.field_tag}</small></>}</td>
          <td><span className="badge" data-kind={c.label}>{c.label}</span>{(c.kinds.REQUIRED || c.kinds.PREFERRED) ? <><br /><small>필수 {c.kinds.REQUIRED ?? 0} · 우대 {c.kinds.PREFERRED ?? 0}</small></> : null}</td>
          <td>{c.linked.length === 0 ? '없음' : c.linked.map(role => <div key={role.id}><Link to={`roles/${role.id}`}>{role.title}</Link></div>)}</td>
          <td><Evidence requirement={req} /></td>
          <td><Origin dataOrigin={req.data_origin} derivationKind={req.derivation_kind} /></td>
        </tr>; })}</tbody></table>
    </>}
  </section>;
}

function Roles({ project }: { project: Project }) {
  const roles = project.required_roles;
  const requestsOf = (role: RequiredRole) => project.staffing_requests.filter(req => req.required_role_id === role.id);
  return <section><h2>역할 현황</h2>
    {roles.length === 0 ? <Empty message="아직 정리된 역할이 없습니다." /> :
      <table><thead><tr><th>역할</th><th>과업</th><th>인원</th><th>상태</th><th>역량</th><th>요청서</th><th>회신 기한</th><th>다음 행동</th><th>출처</th></tr></thead>
        <tbody>{roles.map(role => { const reqs = requestsOf(role); const kinds = countBy(role.competencies, c => c.kind); return <tr key={role.id}>
          <td><Link to={`roles/${role.id}`}>{role.title}</Link><br /><small>{role.role_code}</small> {role.is_core && <small>핵심</small>}</td>
          <td>{role.tasks ?? '-'}</td>
          <td>{role.headcount ?? '미정'}</td>
          <td><StatusBadge code={role.status} labels={ROLE_STATUS_LABEL} /></td>
          <td>{role.competencies.length === 0 ? '미정' : Object.entries(kinds).map(([kind, n]) => `${COMPETENCY_KIND_LABEL[kind] ?? kind} ${n}`).join(' · ')}</td>
          <td>{reqs.length === 0 ? '없음' : reqs.map(req => <div key={req.id}>{req.organization_name} <small>{REQUEST_STATUS_LABEL[req.status] ?? req.status}</small></div>)}</td>
          <td>{role.reply_due_at ? formatDateTime(role.reply_due_at) : formatDate(null)}</td>
          <td>{role.next_action ?? '-'}</td>
          <td><Origin dataOrigin={role.data_origin} derivationKind={role.derivation_kind} /></td>
        </tr>; })}</tbody></table>}
  </section>;
}
