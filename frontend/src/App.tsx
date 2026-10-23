import { NavLink, Outlet, Route, Routes } from 'react-router-dom';
import DevCheck from './pages/DevCheck';
import Tickets from './pages/Tickets';
import Projects from './pages/Projects';

function Layout() {
  return <>
    <nav aria-label="주 메뉴"><NavLink to="/" end>개발 환경</NavLink>{import.meta.env.DEV && <><NavLink to="/cs/tickets">C/S 문의</NavLink><NavLink to="/tft/projects">TFT 프로젝트</NavLink></>}</nav>
    <Outlet />
  </>;
}

export default function App() {
  return <Routes>
    <Route element={<Layout />}>
      <Route index element={<DevCheck />} />
      <Route path="cs/tickets" element={<Tickets />} />
      <Route path="tft/projects" element={<Projects />} />
      <Route path="*" element={<main><h1>페이지를 찾을 수 없습니다</h1><p>주소를 확인하거나 위 메뉴에서 이동하세요.</p></main>} />
    </Route>
  </Routes>;
}
