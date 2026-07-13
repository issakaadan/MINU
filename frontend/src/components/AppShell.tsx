import { Outlet } from "react-router-dom";

import { LegalNotice } from "./LegalNotice";
import { Sidebar } from "./Sidebar";

type AppShellProps = {
  legalNotice: string;
};

export function AppShell({ legalNotice }: AppShellProps) {
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-content">
        <LegalNotice notice={legalNotice} />
        <Outlet />
      </div>
    </div>
  );
}

