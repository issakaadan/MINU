import { NavLink } from "react-router-dom";

const navItems = [
  { to: "/", label: "Dashboard" },
  { to: "/new-assessment", label: "New Assessment" },
  { to: "/scope-management", label: "Scope Management" },
  { to: "/scan-configuration", label: "Scan Configuration" },
  { to: "/running-scan", label: "Running Scan" },
  { to: "/assets", label: "Assets" },
  { to: "/findings", label: "Findings" },
  { to: "/disruptive-tests", label: "Disruptive Tests" },
  { to: "/reports", label: "Reports" },
  { to: "/settings", label: "Settings" },
  { to: "/scan-history", label: "Scan History" },
];

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__eyebrow">Local-only MVP</span>
        <strong>Assessment Console</strong>
        <p>Private network inventory and reporting foundation.</p>
      </div>
      <nav className="sidebar__nav">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              isActive ? "sidebar__link sidebar__link--active" : "sidebar__link"
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
