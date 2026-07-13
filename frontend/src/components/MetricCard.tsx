type MetricCardProps = {
  label: string;
  value: string | number;
  caption: string;
};

export function MetricCard({ label, value, caption }: MetricCardProps) {
  return (
    <div className="metric-card">
      <span className="metric-card__label">{label}</span>
      <strong className="metric-card__value">{value}</strong>
      <span className="metric-card__caption">{caption}</span>
    </div>
  );
}

