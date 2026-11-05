import { Navigate, useLocation, useParams } from 'react-router-dom';

// Preserve bookmarks while keeping a single review workspace.
export default function RfpReviewRedirect() {
  const { projectId } = useParams();
  const { search, hash } = useLocation();
  return <Navigate replace to={projectId ? `/tft/projects/${encodeURIComponent(projectId)}${search}${hash || '#rfp-document-review'}` : '/tft/projects'} />;
}
