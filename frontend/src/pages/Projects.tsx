import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects } from '../api/tft';
import { Empty, ErrorState, Loading } from '../components/states';
import { PROJECT_STATUS_LABEL, StatusBadge } from '../components/tft';

export default function Projects() {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const projects = useQuery({queryKey: ['projects', mode], queryFn: listProjects, enabled: access.ready});
  if (!access.ready) return <BusinessGate access={access} title="TFT 프로젝트" />;
  const items = projects.isSuccess ? projects.data.data.items : undefined;
  return <main>
    <header><p className="eyebrow">TFT</p><h1>프로젝트 목록</h1></header>
    <button disabled={projects.isFetching} onClick={()=>projects.refetch()}>최신 상태 조회</button>
    <section>
      {projects.isPending && <Loading />}
      {projects.isError && <ErrorState error={projects.error} onRetry={() => projects.refetch()} />}
      {items && items.length === 0 && <Empty message="프로젝트가 없습니다." />}
      {items && items.length > 0 && <table><thead><tr><th>이름</th><th>상태</th><th>책임자</th><th>출처</th></tr></thead>
        <tbody>{items.map(item => <tr key={item.id}><td><Link to={item.id}>{item.name}</Link></td><td><StatusBadge code={item.status} labels={PROJECT_STATUS_LABEL} /></td><td>{item.responsible_user_id ? '지정됨' : '미지정'}</td><td><small>{item.data_origin}</small></td></tr>)}</tbody></table>}
    </section>
  </main>;
}
