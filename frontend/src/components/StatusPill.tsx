type StatusPillProps = {
  label: string;
};

export function StatusPill({ label }: StatusPillProps) {
  return <span className={`status-pill status-pill--${label}`}>{label}</span>;
}

