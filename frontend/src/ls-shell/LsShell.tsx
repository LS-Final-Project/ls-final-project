import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { businessUiEnabled } from '../components/BusinessAccess';
import { logoutSession, me } from '../api/platform';
import { getApiMode } from '../api/client';
import { getRequirement } from '../api/rfp';
import { getProject } from '../api/tft';
import { DEPARTMENT_LABEL, label, requirementNo } from '../rfp/labels';
import logo from './ls-logo.svg';
import NotificationBell from '../components/NotificationBell';
import './ls-shell.css';

// inProject: 프로젝트를 열면 그 프로젝트 안으로 가는 화면. 경로 뒷부분을 적는다(빈 값은 프로젝트 화면).
type Leaf = { label: string; to?: string; end?: boolean; devOnly?: boolean; inProject?: string; also?: RegExp };
type Group = { key: string; label: string; leaves: Leaf[] };

// 경로가 아직 없는 항목은 링크 없이 자리만 둔다. 화면이 생기면 to만 채운다.
const MENU: Group[] = [
  { key: 'mail', label: '메일', leaves: [] },
  {
    key: 'cs', label: 'C/S 관리', leaves: [
      { label: '업무 홈', to: '/cs', end: true },
      { label: '문의 조회', to: '/cs/tickets' },
      { label: '문의 처리' },
      { label: '고객 이력', to: '/cs/customers' },
      { label: '개선요청' },
      { label: '반복 사례 모음' },
    ],
  },
  {
    key: 'rfp', label: '제안 지원', leaves: [
      { label: '업무 홈', to: '/tft', end: true },
      { label: '접수 및 요구 검토', to: '/tft/projects', end: true, inProject: '', also: /\/requirements\// },
      { label: '자사 대응표', inProject: 'table' },
      { label: '보완 및 협력사 비교', inProject: 'supplement' },
      { label: '초안 및 검토 이력', inProject: 'draft' },
    ],
  },
  { key: 'stats', label: '통계/보고서', leaves: [{ label: '운영 현황', to: '/cs/admin' }] },
  { key: 'system', label: '시스템 관리', leaves: [{ label: '평가', to: '/evaluations', devOnly: true }] },
];

// 열린 화면 탭. 같은 종류의 화면은 탭 하나를 쓴다(프로젝트를 옮기면 그 탭이 따라온다).
function tabOf(pathname: string) {
  if (pathname === '/cs' || pathname === '/tft') return { kind: 'home', name: '업무 홈' };
  if (pathname === '/cs/tickets') return { kind: 'tickets', name: '문의 조회' };
  if (pathname.startsWith('/cs/tickets/')) return { kind: 'ticket', name: '문의 처리' };
  if (pathname === '/cs/customers') return { kind: 'customers', name: '고객 이력' };
  if (pathname === '/cs/admin') return { kind: 'admin', name: '운영 현황' };
  if (pathname.endsWith('/draft')) return { kind: 'draft', name: '초안 및 검토 이력' };
  if (pathname.endsWith('/supplement')) return { kind: 'supplement', name: '보완 및 협력사 비교' };
  if (pathname.endsWith('/table/approvals')) return { kind: 'approvals', name: '승인 대기' };
  if (pathname.endsWith('/table')) return { kind: 'table', name: '자사 대응표' };
  if (pathname.includes('/requirements/')) return { kind: 'requirement', name: '요구' };
  if (pathname.startsWith('/tft/projects')) return { kind: 'intake', name: '접수 및 요구 검토' };
  return { kind: 'other', name: 'C/S 관리' };
}
type OpenTab = { kind: string; to: string; name: string };
const TABS_KEY = 'ls-open-tabs';
function savedTabs(): OpenTab[] {
  try { return JSON.parse(sessionStorage.getItem(TABS_KEY) ?? '[]'); } catch { return []; }
}
function saveTabs(tabs: OpenTab[]) {
  try { sessionStorage.setItem(TABS_KEY, JSON.stringify(tabs)); } catch { /* 저장소를 못 쓰면 이 화면에 있는 동안만 쓴다 */ }
}

const Chevron = ({ open }: { open: boolean }) => <svg viewBox="0 0 12 12" aria-hidden="true">
  <path d={open ? 'M3 4.5l3 3 3-3' : 'M4.5 3l3 3-3 3'} fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
</svg>;

// 캔버스 셸(사이드바, 상단 바, 열린 화면 탭). 화면별 제목 줄과 본문은 각 화면이 그린다.
export default function LsShell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const mode = getApiMode();
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
  const requirement = /\/requirements\/([^/]+)/.exec(pathname)?.[1];
  // 탭 이름에 붙일 번호. 같은 키라 화면이 부른 응답을 그대로 쓴다.
  const projectInfo = useQuery({ queryKey: ['projects', mode, project], queryFn: () => getProject(project as string), enabled: !!project && !!identity, retry: false });
  const requirementInfo = useQuery({ queryKey: ['rfp-requirement', mode, requirement], queryFn: () => getRequirement(requirement as string), enabled: !!requirement && !!identity, retry: false });
  const current = tabOf(pathname);
  const suffix = current.kind === 'requirement'
    ? requirementInfo.data && requirementNo(requirementInfo.data.data.code)
    : ['table', 'approvals', 'draft'].includes(current.kind) ? projectInfo.data?.data.external_id : undefined;
  const title = [current.name, suffix].filter(Boolean).join(' ');
  const [tabs, setTabs] = useState<OpenTab[]>(savedTabs);
  const closing = useRef(false);
  useEffect(() => {
    if (closing.current) { closing.current = false; return; }
    setTabs(prev => {
      const next = prev.some(t => t.kind === current.kind)
        ? prev.map(t => t.kind === current.kind ? { ...t, to: pathname, name: title } : t)
        : [...prev, { kind: current.kind, to: pathname, name: title }];
      saveTabs(next);
      return next;
    });
  }, [current.kind, pathname, title]);
  const closeTab = (kind: string) => {
    const left = tabs.filter(t => t.kind !== kind);
    saveTabs(left);
    setTabs(left);
    if (kind === current.kind) {
      closing.current = left.length > 0;
      navigate(left.at(-1)?.to ?? '/tft');
    }
  };
  const menu = MENU.map(group => ({
    ...group,
    leaves: group.leaves
      .filter(leaf => !leaf.devOnly || import.meta.env.DEV)
      .map(leaf => leaf.inProject !== undefined && project ? { ...leaf, to: `/tft/projects/${project}${leaf.inProject && `/${leaf.inProject}`}` } : leaf),
  }));
  const name = identity?.display_name ?? '';
  const dept = identity?.dept ? label(DEPARTMENT_LABEL, identity.dept) : '';
  const affiliation = identity ? [dept, identity.roles.join(', ')].filter(Boolean).join(', ') : '';
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
                  ? leaf.also?.test(pathname)
                    ? <Link to={leaf.to} aria-current="page">{leaf.label}</Link>
                    : <NavLink to={leaf.to} end={leaf.end} className={() => undefined}>{leaf.label}</NavLink>
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
          <div className="kt-right"><NotificationBell />{name && <span>{name} <small>{dept}</small></span>}{name && <span className="kt-sep" />}{name
            ? <button className="kt-out" onClick={() => { logoutSession().finally(() => { window.location.href = '/'; }); }}>로그아웃</button>
            : <NavLink to="/account">계정·로그아웃</NavLink>}</div>
        </header>
        <div className="kt-tabs" role="tablist" aria-label="열린 화면">
          {tabs.map(t => <span key={t.kind} className={t.kind === current.kind ? 'kt-tab on' : 'kt-tab'} role="presentation">
            <button role="tab" aria-selected={t.kind === current.kind} onClick={() => navigate(t.to)}>{t.name}</button>
            {t.kind !== 'home' && <button className="kt-x" aria-label={`${t.name} 닫기`} onClick={() => closeTab(t.kind)}>×</button>}
          </span>)}
        </div>
        <div className="content kv" id="main-content" data-view="cs"><Outlet /></div>
      </div>
    </div>
  </div>;
}
