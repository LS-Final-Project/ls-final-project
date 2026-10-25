import { NavLink, Outlet } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { me } from '../api/platform';

// 포털 전환: C/S와 TFT 업무 화면을 상단 메뉴로 오간다. 업무 메뉴와 계정 표시는 개발 모드에서만 보인다.
export default function Layout() {
  const user = useQuery({queryKey: ['me'], queryFn: me, enabled: import.meta.env.DEV});
  return <>
    <div className="topbar">
      <nav aria-label="포털"><NavLink to="/" end>개발 환경</NavLink>{import.meta.env.DEV && <><NavLink to="/cs/tickets">C/S</NavLink><NavLink to="/tft/projects">TFT</NavLink>{user.data?.data.roles.includes('dept_staff') && <NavLink to="/tft/requests">내 부서 요청</NavLink>}</>}</nav>
      {import.meta.env.DEV && <p className="account" data-testid="layout-user">{user.data ? `${user.data.data.display_name} (${user.data.data.roles.join(', ')})` : '계정 선택 전'}</p>}
    </div>
    <Outlet />
  </>;
}
