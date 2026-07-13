type SeverityBadgeProps = {
  severity: string;
};

export function SeverityBadge({ severity }: SeverityBadgeProps) {
  return (
    <span className={`badge badge--${severity.toLowerCase()}`}>{severity}</span>
  );
}

