import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { me } from '../api/platform';
import { departmentRequests } from '../api/recommendations';
import { ApiError, getApiMode } from '../api/client';
import { Empty, ErrorState, Loading, Preparing } from '../components/states';
import { REQUEST_STATUS_LABEL } from '../components/tft';

export default function DepartmentRequests() {
  const [cursor, setCursor] = useState<string>();
  const user = useQuery({queryKey:['me'], queryFn:me, enabled:import.meta.env.DEV});
  const allowed = user.isSuccess && user.data?.data.roles.includes('dept_staff') === true && Boolean(user.data?.data.dept);
  const data = useQuery({queryKey:['department-requests',getApiMode(),cursor],queryFn:()=>departmentRequests(cursor),enabled:import.meta.env.DEV && allowed});
  if (!import.meta.env.DEV) return <Preparing title="내 부서 인력 요청" />;
  return <main><h1>내 부서 인력 요청</h1>
    {user.isPending && <Loading label="접근 권한 확인 중" />}
    {user.isError && <ErrorState error={user.error} onRetry={()=>user.refetch()} />}
    {user.isSuccess && !allowed && <ErrorState error={new ApiError(403,'소속 부서 담당자만 요청에 응답할 수 있습니다.')} />}
    {allowed && <>{data.isPending && <Loading />}{data.isError && <ErrorState error={data.error} onRetry={()=>data.refetch()} />}
      {data.data && (data.data.data.items.length ? <ul>{data.data.data.items.map(item=><li key={item.id}><Link to={`/tft/projects/${item.project_id}/requests/${item.id}`}>{item.role_name} · {item.organization_name}</Link> — {REQUEST_STATUS_LABEL[item.status]} · 판본 {item.version}</li>)}</ul> : <Empty message="배정된 요청서가 없습니다" />)}
      {cursor && <button onClick={()=>setCursor(undefined)}>처음 목록</button>}{data.data?.data.next_cursor && <button onClick={()=>setCursor(data.data!.data.next_cursor!)}>다음 요청</button>}
    </>}
  </main>;
}
