import { Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { NotFoundPage } from './components/states';
import DevCheck from './pages/DevCheck';
import Account from './pages/Account';
import Tickets from './pages/Tickets';
import TicketDetail from './pages/TicketDetail';
import Projects from './pages/Projects';
import ProjectDetail from './pages/ProjectDetail';
import Evaluations from './pages/Evaluations';
import LsShell from './ls-shell/LsShell';
import CsHome from './ls-shell/CsHome';

export default function App() {
  return <Routes>
    <Route element={<Layout />}>
      <Route index element={import.meta.env.DEV ? <DevCheck /> : <Account />} />
      <Route path="account" element={<Account />} />
      <Route path="tft/projects" element={<Projects />} />
      <Route path="tft/projects/:projectId" element={<ProjectDetail />} />
      <Route path="evaluations" element={<Evaluations />} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>
    <Route element={<LsShell />}>
      <Route path="cs" element={<CsHome />} />
      <Route path="cs/tickets" element={<Tickets />} />
      <Route path="cs/tickets/:ticketId" element={<TicketDetail />} />
    </Route>
  </Routes>;
}
