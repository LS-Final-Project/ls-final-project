import { useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { businessUiEnabled } from '../components/BusinessAccess';
import { me } from '../api/platform';
import logo from './ls-logo.svg';
import './ls-shell.css';

// inProject: 프로젝트를 연 뒤에만 가는 화면. 경로 뒷부분을 적는다.
type Leaf = { label: string; to?: string; end?: boolean; devOnly?: boolean; inProject?: string };
type Group = { key: string; label: string; leaves: Leaf[] };

// 경로가 아직 없는 항목은 링크 없이 자리만 둔다. 화면이 생기면 to만 채운다.
const MENU: Group[] = [
  {
    key: 'cs', label: 'C/S 관리', leaves: [
      { label: '업무 홈', to: '/cs', end: true },
      { label: '문의 조회', to: '/cs/tickets' },
      { label: '문의 처리' },
      { label: '고객 이력' },
      { label: '개선요청' },
      { label: '반복 사례 모음' },
    ],
  },
  {
    key: 'rfp', label: '제안 지원', leaves: [
      { label: '업무 홈', to: '/tft', end: true },
      { label: '접수 및 요구 검토', to: '/tft/projects', end: true },
      { label: '자사 대응표', inProject: 'table' },
      { label: '보완 및 협력사 비교', inProject: 'supplement' },
      { label: '초안 및 검토 이력', inProject: 'draft' },
    ],
  },
  { key: 'mail', label: '메일', leaves: [] },
  { key: 'stats', label: '통계/보고서', leaves: [] },
  { key: 'system', label: '시스템 관리', leaves: [{ label: '평가', to: '/evaluations', devOnly: true }] },
];

// 열린 화면 탭 이름.
function tabName(pathname: string) {
  if (pathname === '/cs' || pathname === '/tft') return '업무 홈';
  if (pathname === '/cs/tickets') return '문의 조회';
  if (pathname.startsWith('/cs/tickets/')) return '문의 처리';
  if (/^\/tft\/projects\/[^/]+\/draft$/.test(pathname)) return '초안 및 검토 이력';
  if (pathname.endsWith('/table') || pathname.endsWith('/table/approvals')) return '자사 대응표';
  if (pathname.startsWith('/tft/projects')) return '접수 및 요구 검토';
  return 'C/S 관리';
}

const Chevron = ({ open }: { open: boolean }) => <svg viewBox="0 0 12 12" aria-hidden="true">
  <path d={open ? 'M3 4.5l3 3 3-3' : 'M4.5 3l3 3-3 3'} fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
</svg>;

// 캔버스 셸(사이드바, 상단 바, 열린 화면 탭). 화면별 제목 줄과 본문은 각 화면이 그린다.
export default function LsShell() {
  const { pathname } = useLocation();
  const tab = pathname.endsWith('/supplement') ? '보완 및 협력사 비교' : tabName(pathname);
  const user = useQuery({ queryKey: ['me'], queryFn: me, retry: false });
  const [open, setOpen] = useState<Record<string, boolean>>({ [pathname.startsWith('/tft') ? 'rfp' : 'cs']: true });
  // 넓은 화면은 메뉴 접기, 800px 이하는 메뉴 열기다(ls-shell.css). 좁은 화면은 화면을 옮기면 닫는다.
  const [toggled, setToggled] = useState(false);
  useEffect(() => {
    if (window.matchMedia?.('(max-width:800px)').matches) setToggled(false);
  }, [pathname]);
  const identity = user.data?.data;
  // 기존 Layout과 같은 조건이다. 업무 UI가 꺼졌거나 미로그인이면 경로를 열지 않는다.
  const linked = import.meta.env.DEV || (businessUiEnabled && user.isSuccess && !!user.data);
  const project = /^\/tft\/projects\/([^/]+)/.exec(pathname)?.[1];
  const menu = MENU.map(group => ({
    ...group,
    leaves: group.leaves
      .filter(leaf => !leaf.devOnly || import.meta.env.DEV)
      .map(leaf => leaf.inProject && project ? { ...leaf, to: `/tft/projects/${project}/${leaf.inProject}` } : leaf),
  }));
  const name = identity?.display_name ?? '';
  const affiliation = identity ? [identity.dept, identity.roles.join(', ')].filter(Boolean).join(', ') : '';
  return <div className="ls-root" data-cs="1">
    <input type="checkbox" id="nav-collapse" className="nav-collapse-input" checked={toggled} onChange={event => setToggled(event.target.checked)} />
    <div className={toggled ? 'shell menu-open' : 'shell'} id="shell">
      <aside className="sidebar" id="sidebar" aria-label="주 메뉴">
        <div className="brand"><span className="brand-mark"><Link to="/" aria-label="포털 홈"><img src={logo} width="94.7" height="42.619" alt="LS 로고" /></Link></span>업무지원</div>
        <div className="km-find"><input type="search" placeholder="메뉴 검색" aria-label="메뉴 검색" autoComplete="off" spellCheck={false} /></div>
        <nav className="km" aria-label="메뉴"><ul>
          {menu.map(group => {
            const expanded = !!open[group.key] && group.leaves.length > 0;
            return <li key={group.key} className={expanded ? 'open' : undefined}>
              <button className="km-g" aria-expanded={expanded} title={group.leaves.length ? undefined : '준비 중'}
                onClick={() => group.leaves.length && setOpen(prev => ({ ...prev, [group.key]: !prev[group.key] }))}>
                <Chevron open={expanded} />{group.label}
              </button>
              {expanded && <ul>{group.leaves.map(leaf => <li key={leaf.label}>
                {leaf.to && linked
                  ? <NavLink to={leaf.to} end={leaf.end} className={() => undefined}>{leaf.label}</NavLink>
                  : <a aria-disabled="true" title={leaf.to ? '로그인 후 사용할 수 있습니다' : leaf.inProject ? '프로젝트를 먼저 여세요' : '준비 중'} tabIndex={-1}>{leaf.label}</a>}
              </li>)}</ul>}
            </li>;
          })}
        </ul></nav>
        <div className="km-user">
          <span className="km-av" aria-hidden="true">{name.slice(0, 1) || '?'}</span>
          <div className="km-who"><b data-testid="layout-user">{name || (user.isPending ? '세션 확인 중' : user.isError ? '세션 확인 실패' : '로그인 필요')}</b><small>{affiliation}</small></div>
          <NavLink to="/account">내 계정</NavLink>
        </div>
      </aside>
      <div className="main">
        <header className="ls-topbar kt">
          <div className="kt-left"><label htmlFor="nav-collapse" className="nav-toggle" title="메뉴 접기/펼치기" aria-label="메뉴 접기/펼치기">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4h11M2.5 8h11M2.5 12h11" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
          </label></div>
          <div className="kt-right">{name && <span>{name} <small>{identity?.dept}</small></span>}{name && <span className="kt-sep" />}<NavLink to="/account">계정·로그아웃</NavLink></div>
        </header>
        <div className="kt-tabs" role="tablist" aria-label="열린 화면"><button role="tab" aria-selected="true">{tab}</button></div>
        <div className="content kv" id="main-content" data-view="cs"><Outlet /></div>
      </div>
    </div>
  </div>;
}
