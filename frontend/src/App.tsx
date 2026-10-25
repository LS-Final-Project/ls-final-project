import { Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { NotFoundPage } from './components/states';
import DevCheck from './pages/DevCheck';
import Tickets from './pages/Tickets';
import TicketDetail from './pages/TicketDetail';
import Projects from './pages/Projects';
import ProjectDetail from './pages/ProjectDetail';
import RoleRequest from './pages/RoleRequest';
import DepartmentRequests from './pages/DepartmentRequests';
import DepartmentRequest from './pages/DepartmentRequest';

export default function App() {
  return <Routes>
    <Route element={<Layout />}>
      <Route index element={<DevCheck />} />
      <Route path="cs/tickets" element={<Tickets />} />
      <Route path="cs/tickets/:ticketId" element={<TicketDetail />} />
      <Route path="tft/requests" element={<DepartmentRequests />} />
      <Route path="tft/projects/:projectId/requests/:requestId" element={<DepartmentRequest />} />
      <Route path="tft/projects" element={<Projects />} />
      <Route path="tft/projects/:projectId" element={<ProjectDetail />} />
      <Route path="tft/projects/:projectId/roles/:roleId" element={<RoleRequest />} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>
  </Routes>;
}
