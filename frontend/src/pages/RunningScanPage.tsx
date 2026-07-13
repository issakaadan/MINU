import { useEffect, useState } from "react";

import { LoadingState } from "../components/LoadingState";
import { PageHeader } from "../components/PageHeader";
import { ProgressPanel } from "../components/ProgressPanel";
import { api } from "../lib/api";
import type { ScanJob } from "../types";

export function RunningScanPage() {
  const [scans, setScans] = useState<ScanJob[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  async function load() {
    try {
      const data = await api.getScans();
      setScans(data);
      setError("");
    } catch (loadError) {
      setError((loadError as Error).message);
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void load();

    const intervalId = window.setInterval(() => {
      void load();
    }, 3000);

    return () => window.clearInterval(intervalId);
  }, []);

  async function handleStart(scanId: number) {
    try {
      const updated = await api.startScan(scanId);
      setScans((current) =>
        current.map((scan) => (scan.id === updated.id ? updated : scan)),
      );
    } catch (startError) {
      setError((startError as Error).message);
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Running Scan"
        subtitle="Monitor authorized safe discovery, port enumeration, and performance-impacting jobs in real time while each module writes to its own local result set."
      />
      {error ? <div className="error-banner">{error}</div> : null}
      {isLoading ? (
        <LoadingState
          title="Loading Scan Jobs"
          message="Checking the current job queue and recent progress updates."
        />
      ) : null}
      <ProgressPanel scans={scans} onStart={handleStart} />
    </div>
  );
}
