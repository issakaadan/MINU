type DonutDatum = {
  label: string;
  value: number;
  color: string;
};

type DonutChartProps = {
  items: DonutDatum[];
  totalLabel: string;
  emptyMessage: string;
};

const RADIUS = 54;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function DonutChart({
  items,
  totalLabel,
  emptyMessage,
}: DonutChartProps) {
  const total = items.reduce((sum, item) => sum + item.value, 0);

  if (total === 0) {
    return <div className="empty-state">{emptyMessage}</div>;
  }

  let offset = 0;

  return (
    <div className="chart-card chart-card--donut">
      <div className="donut-chart">
        <svg viewBox="0 0 160 160" className="donut-chart__svg" aria-hidden="true">
          <circle
            className="donut-chart__track"
            cx="80"
            cy="80"
            r={RADIUS}
          />
          {items.map((item) => {
            const segmentLength = (item.value / total) * CIRCUMFERENCE;
            const dashOffset = -offset;
            offset += segmentLength;
            return (
              <circle
                key={item.label}
                className="donut-chart__segment"
                cx="80"
                cy="80"
                r={RADIUS}
                pathLength={CIRCUMFERENCE}
                stroke={item.color}
                strokeDasharray={`${segmentLength} ${CIRCUMFERENCE - segmentLength}`}
                strokeDashoffset={dashOffset}
              />
            );
          })}
        </svg>
        <div className="donut-chart__center">
          <strong>{total}</strong>
          <span>{totalLabel}</span>
        </div>
      </div>

      <div className="chart-legend">
        {items.map((item) => (
          <div className="chart-legend__row" key={item.label}>
            <div className="chart-legend__label">
              <span
                className="chart-legend__swatch"
                style={{ backgroundColor: item.color }}
              />
              <span>{item.label}</span>
            </div>
            <div className="chart-legend__meta">
              <strong>{item.value}</strong>
              <span>{Math.round((item.value / total) * 100)}%</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
