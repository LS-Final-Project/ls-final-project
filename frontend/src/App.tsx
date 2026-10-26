import { Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { NotFoundPage } from './components/states';
import DevCheck from './pages/DevCheck';
import Tickets from './pages/Tickets';
import TicketDetail from './pages/TicketDetail';
import Projects from './pages/Projects';
import Evaluations from './pages/Evaluations';

export default function App() {
  return <Routes>
    <Route element={<Layout />}>
      <Route index element={<DevCheck />} />
      <Route path="cs/tickets" element={<Tickets />} />
      <Route path="cs/tickets/:ticketId" element={<TicketDetail />} />
      <Route path="tft/projects" element={<Projects />} />
      <Route path="evaluations" element={<Evaluations />} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>
  </Routes>;
}
