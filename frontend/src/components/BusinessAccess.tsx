import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { me } from '../api/platform';
import { ErrorState, Loading, Preparing } from './states';

export const businessUiEnabled = import.meta.env.VITE_BUSINESS_UI_ENABLED === 'true';
export type Business = 'cs' | 'tft';
// Only the already allowlisted read paths opt in. Backend scope checks remain authoritative.
export function useBusinessAccess(business:Business, operational= !import.meta.env.DEV, enabled=businessUiEnabled){
  const user=useQuery({queryKey:['me'],queryFn:me,enabled:operational&&enabled,retry:false});
  const permitted=user.data?.data.roles.some(role=>business==='cs'?['cs_agent','dept_staff','manager'].includes(role):['sales','manager','dept_staff'].includes(role))===true;
  return {ready:!operational || enabled&&user.isSuccess&&permitted,operational,enabled,user};
}
export function BusinessGate({access,title}:{access:ReturnType<typeof useBusinessAccess>;title:string}){
  if(!access.enabled)return <Preparing title={title}/>;
  if(access.user.isPending)return <main><h1>{title}</h1><Loading label="접근 권한 확인 중"/></main>;
  if(access.user.isError)return <main><h1>{title}</h1><ErrorState error={access.user.error} onRetry={()=>access.user.refetch()}/></main>;
  if(!access.user.data)return <main><h1>{title}</h1><p>로그인이 필요합니다.</p><Link to="/account">계정 확인</Link></main>;
  return <main><h1>{title}</h1><ErrorState error={new ApiError(403,'이 업무를 조회할 역할이 없습니다.')}/></main>;
}
