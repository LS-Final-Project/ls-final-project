import { useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { me } from '../api/platform';
import logo from './ls-logo.svg';
import './ls-shell.css';

type Leaf = { label: string; to?: string; end?: boolean };
type Group = { key: string; label: string; leaves: Leaf[] };

// 경로가 아직 없는 항목은 링크 없이 자리만 둔다. 화면이 생기면 to만 채운다.
const MENU: Group[] = [
  { key: 'mail', label: '메일', leaves: [] },
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
  { key: 'stats', label: '통계/보고서', leaves: [] },
  { key: 'system', label: '시스템 관리', leaves: [] },
];

const Chevron = ({ open }: { open: boolean }) => <svg viewBox="0 0 12 12" aria-hidden="true">
  <path d={open ? 'M3 4.5l3 3 3-3' : 'M4.5 3l3 3-3 3'} fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
</svg>;

// 캔버스 셸(사이드바, 상단 바, 열린 화면 탭). 화면별 제목 줄과 본문은 각 화면이 그린다.
export default function LsShell() {
  const { pathname } = useLocation();
  const tab = pathname === '/cs' ? '업무 홈' : pathname === '/cs/tickets' ? '문의 조회' : pathname.startsWith('/cs/tickets/') ? '문의 처리' : 'C/S 관리';
  const user = useQuery({ queryKey: ['me'], queryFn: me, retry: false });
  const [open, setOpen] = useState<Record<string, boolean>>({ cs: true });
  const identity = user.data?.data;
  const name = identity?.display_name ?? '';
  const affiliation = identity ? [identity.dept, identity.roles.join(', ')].filter(Boolean).join(', ') : '';
  return <div className="ls-root" data-cs="1">
    <input type="checkbox" id="nav-collapse" className="nav-collapse-input" />
    <div className="shell" id="shell">
      <aside className="sidebar" id="sidebar" aria-label="주 메뉴">
        <div className="brand"><span className="brand-mark"><img src={logo} width="94.7" height="42.619" alt="LS 로고" /></span>업무지원</div>
        <div className="km-find"><input type="search" placeholder="메뉴 검색" aria-label="메뉴 검색" autoComplete="off" spellCheck={false} /></div>
        <nav className="km" aria-label="메뉴"><ul>
          {MENU.map(group => {
            const expanded = !!open[group.key] && group.leaves.length > 0;
            return <li key={group.key} className={expanded ? 'open' : undefined}>
              <button className="km-g" aria-expanded={expanded} title={group.leaves.length ? undefined : '준비 중'}
                onClick={() => group.leaves.length && setOpen(prev => ({ ...prev, [group.key]: !prev[group.key] }))}>
                <Chevron open={expanded} />{group.label}
              </button>
              {expanded && <ul>{group.leaves.map(leaf => <li key={leaf.label}>
                {leaf.to
                  ? <NavLink to={leaf.to} end={leaf.end} className={() => undefined}>{leaf.label}</NavLink>
                  : <a aria-disabled="true" title="준비 중" tabIndex={-1}>{leaf.label}</a>}
              </li>)}</ul>}
            </li>;
          })}
        </ul></nav>
        <div className="km-user">
          <span className="km-av" aria-hidden="true">{name.slice(0, 1) || '?'}</span>
          <div className="km-who"><b data-testid="layout-user">{name || (user.isPending ? '세션 확인 중' : '로그인 필요')}</b><small>{affiliation}</small></div>
          <NavLink to="/account">내 계정</NavLink>
        </div>
      </aside>
      <div className="main">
        <header className="topbar kt">
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
