import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { watchRfpJob, type RfpWatch } from '../api/rfp-job-events';

export function useRfpJobEvents(jobId: string | undefined, projectId: string, actorId: string | undefined, mode: string) {
  const client = useQueryClient();
  const identity = `${mode}:${actorId}:${projectId}:${jobId}`;
  const [stored, setStored] = useState<{ identity: string; view: RfpWatch }>();
  useEffect(() => {
    setStored(undefined);
    if (!jobId || !actorId || mode !== 'live') return;
    const controller = new AbortController();
    void watchRfpJob({ jobId, projectId, actorId }, controller.signal, view => {
      if (controller.signal.aborted) return;
      if (view.job) client.setQueryData(['jobs', mode, actorId, projectId, jobId], { data: view.job });
      setStored({ identity, view });
    });
    return () => controller.abort();
  }, [jobId, projectId, actorId, mode, identity, client]);
  return stored?.identity === identity ? stored.view : jobId && actorId && mode === 'live' ? { connection: 'connecting' as const } : undefined;
}
