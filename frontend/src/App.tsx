import { Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import { NotFoundPage } from './components/states';
import DevCheck from './pages/DevCheck';
import AccountGate from './pages/AccountGate';
import DeptInbox from './pages/DeptInbox';
import DeptResult from './pages/DeptResult';
import MailAccount from './pages/MailAccount';
import MailInbox from './pages/MailInbox';
import MailSent from './pages/MailSent';
import Tickets from './pages/Tickets';
import CsCustomers from './pages/CsCustomers';
import CsAdmin from './pages/CsAdmin';
import CsImprovements from './pages/CsImprovements';
import CsRepeatCases from './pages/CsRepeatCases';
import TicketDetail from './pages/TicketDetail';
import Evaluations from './pages/Evaluations';
import LsShell from './ls-shell/LsShell';
import CsHome from './ls-shell/CsHome';
import RfpHome from './rfp/RfpHome';
import RfpIntake from './rfp/RfpIntake';
import RfpList from './rfp/RfpList';
import RfpRequirement from './rfp/RfpRequirement';
import RfpReviewRedirect from './rfp/RfpReviewRedirect';
import RfpApprovals from './rfp/RfpApprovals';
import RfpDraft from './rfp/RfpDraft';
import RfpWorkspace from './rfp/RfpWorkspace';

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
      <Route path="cs/improvements" element={<CsImprovements />} />
      <Route path="cs/improvements/:requestId" element={<CsImprovements />} />
      <Route path="cs/repeat-cases/:requestId?" element={<CsRepeatCases />} />
      <Route path="dept/inbox" element={<DeptInbox />} />
      <Route path="dept/result/:ticketId" element={<DeptResult />} />
      <Route path="mail/inbox" element={<MailInbox />} />
      <Route path="mail/account" element={<MailAccount />} />
      <Route path="mail/sent" element={<MailSent />} />
      <Route path="cs/customers" element={<CsCustomers />} />
      <Route path="cs/admin" element={<CsAdmin />} />
      <Route path="cs/tickets" element={<Tickets />} />
      <Route path="cs/tickets/:ticketId" element={<TicketDetail />} />
      <Route path="tft" element={<RfpHome />} />
      <Route path="tft/projects" element={<RfpList />} />
      {/* 예전 대응표 북마크는 통합 작업 공간으로 보낸다(KAN-222). 승인·초안·요구 상세는 아직 작업 공간에 없는 기능이 있어 그대로 둔다. */}
      <Route path="tft/projects/:projectId/supplement" element={<RfpReviewRedirect />} />
      <Route path="tft/projects/:projectId/table" element={<RfpReviewRedirect />} />
      <Route path="tft/projects/:projectId/table/approvals" element={<RfpApprovals />} />
      <Route path="tft/projects/:projectId/draft" element={<RfpDraft />} />
      <Route path="tft/projects/:projectId/requirements/:requirementId" element={<RfpRequirement />} />
      {/* 프로젝트 설정: 문서 관리, 공급 조건, 통합 책임, 검토 부서, 도면 검토는 기존 화면을 그대로 쓴다. */}
      <Route path="tft/projects/:projectId/settings" element={<RfpIntake />} />
      <Route path="tft/projects/:projectId" element={<RfpWorkspace />} />
    </Route>
  </Routes>;
}
