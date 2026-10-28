import { Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { NotFoundPage } from './components/states';
import DevCheck from './pages/DevCheck';
import Account from './pages/Account';
import Tickets from './pages/Tickets';
import CsStats from './pages/CsStats';
import CsImprovements from './pages/CsImprovements';
import TicketDetail from './pages/TicketDetail';
import Projects from './pages/Projects';
import ProjectDetail from './pages/ProjectDetail';
import Evaluations from './pages/Evaluations';

export default function App() {
  return <Routes>
    <Route element={<Layout />}>
      <Route index element={import.meta.env.DEV ? <DevCheck /> : <Account />} />
      <Route path="account" element={<Account />} />
      <Route path="cs/improvements" element={<CsImprovements />} />
      <Route path="cs/improvements/:requestId" element={<CsImprovements />} />
      <Route path="cs/stats" element={<CsStats />} />
      <Route path="cs/tickets" element={<Tickets />} />
      <Route path="cs/tickets/:ticketId" element={<TicketDetail />} />
      <Route path="tft/projects" element={<Projects />} />
      <Route path="tft/projects/:projectId" element={<ProjectDetail />} />
      <Route path="evaluations" element={<Evaluations />} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>
  </Routes>;
}
