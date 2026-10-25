import { NavLink, Outlet } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { businessUiEnabled } from './BusinessAccess';
import { me } from '../api/platform';

// 업무 메뉴 활성화는 통합 범위에서 별도로 검증한다. 계정 상태는 운영에서도 표시한다.
export default function Layout() {
  const user = useQuery({queryKey: ['me'], queryFn: me, retry: false});
  return <>
    <div className="topbar">
      <nav aria-label="포털"><NavLink to="/" end>{import.meta.env.DEV ? '개발 환경' : '홈'}</NavLink><NavLink to="/account">계정</NavLink>{(import.meta.env.DEV || businessUiEnabled && user.isSuccess && user.data) && <><NavLink to="/cs/tickets">C/S</NavLink><NavLink to="/tft/projects">TFT</NavLink>{import.meta.env.DEV && user.data?.data.roles.includes('dept_staff') && <NavLink to="/tft/requests">내 부서 요청</NavLink>}</>}</nav>
      {<p className="account" data-testid="layout-user">{user.data ? `${user.data.data.display_name} (${user.data.data.roles.join(', ')})` : user.isPending ? '세션 확인 중' : user.isError ? '세션 확인 실패' : '로그인 필요'}</p>}
    </div>
    <Outlet />
  </>;
}
