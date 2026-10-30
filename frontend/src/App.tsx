import { Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { NotFoundPage } from './components/states';
import DevCheck from './pages/DevCheck';
import Account from './pages/Account';
import Tickets from './pages/Tickets';
import TicketDetail from './pages/TicketDetail';
import ProjectDetail from './pages/ProjectDetail';
import Evaluations from './pages/Evaluations';
import LsShell from './ls-shell/LsShell';
import CsHome from './ls-shell/CsHome';
import RfpHome from './rfp/RfpHome';
import RfpProjects from './rfp/RfpProjects';
import RfpTable from './rfp/RfpTable';

export default function App() {
  return <Routes>
    <Route element={<Layout />}>
      <Route index element={import.meta.env.DEV ? <DevCheck /> : <Account />} />
      <Route path="account" element={<Account />} />
      <Route path="evaluations" element={<Evaluations />} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>
    <Route element={<LsShell />}>
      <Route path="cs" element={<CsHome />} />
      <Route path="cs/tickets" element={<div className="ls-legacy"><Tickets /></div>} />
      <Route path="cs/tickets/:ticketId" element={<div className="ls-legacy"><TicketDetail /></div>} />
      <Route path="tft" element={<RfpHome />} />
      <Route path="tft/projects" element={<RfpProjects />} />
      <Route path="tft/projects/:projectId/table" element={<RfpTable />} />
      <Route path="tft/projects/:projectId" element={<div className="ls-legacy"><ProjectDetail /></div>} />
    </Route>
  </Routes>;
}
