import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getProject, type ProjectDetail as Project } from '../api/tft';
import { Empty, ErrorState, Loading, Preparing } from '../components/states';
import { Evidence, Origin, PROJECT_STATUS_LABEL, StatusBadge, formatDateTime } from '../components/tft';

// 프로젝트 현황. 프로젝트 조회(KAN-119) 응답만으로 그리며 문서 처리 상태는 RFP 처리 API 연결 전이라 준비 중으로 둔다.
// TFT 인력 구성 단계(역할·요청서·추천·구성안·책임자 결정)는 KAN-181에서 제거했다.
export default function ProjectDetail() {
  const { projectId = '' } = useParams();
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const project = useQuery({queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled: access.ready && projectId !== ''});
  if (!access.ready) return <BusinessGate access={access} title="TFT 프로젝트" />;
  const data = project.isSuccess ? project.data.data : undefined;
  return <main>
    <header><p className="eyebrow"><Link to="/tft/projects">TFT 프로젝트 목록</Link></p><h1>{data ? data.name : '프로젝트 현황'}</h1></header>
    <button disabled={project.isFetching} onClick={()=>project.refetch()}>최신 상태 조회</button> <Link to="table">자사 대응표</Link>
    {project.isPending && <Loading />}
    {project.isError && <ErrorState error={project.error} onRetry={() => project.refetch()} />}
    {data && <>
      <ProjectInfo project={data} />
      <DocumentStatus project={data} />
      <Requirements project={data} />
    </>}
  </main>;
}

function ProjectInfo({ project }: { project: Project }) {
  return <section><h2>프로젝트 정보</h2>
    <dl>
      <dt>외부 ID</dt><dd>{project.external_id ?? '-'}</dd>
      <dt>고객</dt><dd>{project.client_name ?? '-'}</dd>
      <dt>회사 코드</dt><dd>{project.company_code}</dd>
      <dt>상태</dt><dd><StatusBadge code={project.status} labels={PROJECT_STATUS_LABEL} /></dd>
      <dt>책임자</dt><dd>{project.responsible_user_id ?? '미지정'}</dd>
      <dt>담당 sales</dt><dd>{project.sales_user_id ?? '미지정'}</dd>
      <dt>등록</dt><dd>{formatDateTime(project.created_at)}</dd>
      <dt>출처</dt><dd><Origin dataOrigin={project.data_origin} derivationKind={project.derivation_kind} /></dd>
    </dl>
  </section>;
}

// 문서처리현황: RFP 업로드·문서 처리 API가 없어 처리 상태 필드가 응답에 없다. 요구사항이 인용한 문서명만 보이고 상태는 준비 중이다.
function DocumentStatus({ project }: { project: Project }) {
  const documents = [...new Set(project.rfp_requirements.map(req => req.document_name).filter((name): name is string => !!name))];
  if (documents.length === 0) return <Preparing inline title="문서 처리 현황" message="문서 처리 연결 준비 중 — RFP 문서 업로드·처리 상태 API 연결 전입니다." />;
  return <section><h2>문서 처리 현황</h2>
    <table><thead><tr><th>문서명</th><th>처리 상태</th></tr></thead>
      <tbody>{documents.map(name => <tr key={name}><td>{name}</td><td><span className="state-inline">문서 처리 연결 준비 중</span></td></tr>)}</tbody></table>
  </section>;
}

function Requirements({ project }: { project: Project }) {
  return <section><h2>RFP 요구사항</h2>
    {project.rfp_requirements.length === 0 ? <Empty message="정리된 요구사항이 없습니다." /> :
      <table><thead><tr><th>코드</th><th>요구</th><th>원문 근거</th><th>출처</th></tr></thead>
        <tbody>{project.rfp_requirements.map(req => <tr key={req.id}>
          <td>{req.requirement_code}</td>
          <td><strong>{req.title}</strong>{req.description && <><br /><small>{req.description}</small></>}{req.field_tag && <><br /><small>{req.field_tag}</small></>}</td>
          <td><Evidence requirement={req} /></td>
          <td><Origin dataOrigin={req.data_origin} derivationKind={req.derivation_kind} /></td>
        </tr>)}</tbody></table>}
  </section>;
}
