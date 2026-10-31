import { Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { NotFoundPage } from './components/states';
import DevCheck from './pages/DevCheck';
import AccountGate from './pages/AccountGate';
import Tickets from './pages/Tickets';
import CsCustomers from './pages/CsCustomers';
import CsAdmin from './pages/CsAdmin';
import CsImprovements from './pages/CsImprovements';
import TicketDetail from './pages/TicketDetail';
import Evaluations from './pages/Evaluations';
import LsShell from './ls-shell/LsShell';
import CsHome from './ls-shell/CsHome';
import RfpHome from './rfp/RfpHome';
import RfpIntake from './rfp/RfpIntake';
import RfpRequirement from './rfp/RfpRequirement';
import RfpSupplement from './rfp/RfpSupplement';
import RfpApprovals from './rfp/RfpApprovals';
import RfpTable from './rfp/RfpTable';
import RfpDraft from './rfp/RfpDraft';

export default function App() {
  return <Routes>
    <Route element={<Layout />}>
      {/* 운영에서 첫 화면은 계정(로그인) 화면이다. 계정 화면은 새 셸 안에 있다. */}
      <Route index element={import.meta.env.DEV ? <DevCheck /> : <Navigate to="/account" replace />} />
      <Route path="evaluations" element={<Evaluations />} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>
    <Route path="account" element={<AccountGate />} />
    <Route element={<LsShell />}>
      <Route path="cs" element={<CsHome />} />
      <Route path="cs/improvements" element={<div className="ls-legacy"><CsImprovements /></div>} />
      <Route path="cs/improvements/:requestId" element={<div className="ls-legacy"><CsImprovements /></div>} />
      <Route path="cs/customers" element={<CsCustomers />} />
      <Route path="cs/admin" element={<CsAdmin />} />
      <Route path="cs/tickets" element={<Tickets />} />
      <Route path="cs/tickets/:ticketId" element={<TicketDetail />} />
      <Route path="tft" element={<RfpHome />} />
      <Route path="tft/projects" element={<RfpIntake />} />
      <Route path="tft/projects/:projectId/supplement" element={<RfpSupplement />} />
      <Route path="tft/projects/:projectId/table" element={<RfpTable />} />
      <Route path="tft/projects/:projectId/table/approvals" element={<RfpApprovals />} />
      <Route path="tft/projects/:projectId/draft" element={<RfpDraft />} />
      <Route path="tft/projects/:projectId/requirements/:requirementId" element={<RfpRequirement />} />
      <Route path="tft/projects/:projectId" element={<RfpIntake />} />
    </Route>
  </Routes>;
}
