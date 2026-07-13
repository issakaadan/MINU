type LineChartDatum = {
  label: string;
  value: number;
  detail?: string;
};

type LineChartProps = {
  items: LineChartDatum[];
  emptyMessage: string;
};

export function LineChart({ items, emptyMessage }: LineChartProps) {
  if (items.length === 0) {
    return <div className="empty-state">{emptyMessage}</div>;
  }

  const maxValue = Math.max(...items.map((item) => item.value), 1);
  const width = 520;
  const height = 220;
  const padding = 28;
  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;

  const points = items.map((item, index) => {
    const x =
      items.length === 1
        ? width / 2
        : padding + (usableWidth * index) / (items.length - 1);
    const y = padding + usableHeight - (item.value / maxValue) * usableHeight;
    return { ...item, x, y };
  });

  const polylinePoints = points.map((point) => `${point.x},${point.y}`).join(" ");
  const guideValues = Array.from({ length: 4 }, (_, index) =>
    Math.round((maxValue / 4) * (4 - index)),
  );

  return (
    <div className="line-chart">
      <svg viewBox={`0 0 ${width} ${height}`} className="line-chart__svg">
        {guideValues.map((value) => {
          const y = padding + usableHeight - (value / maxValue) * usableHeight;
          return (
            <g key={value}>
              <line
                className="line-chart__grid"
                x1={padding}
                y1={y}
                x2={width - padding}
                y2={y}
              />
              <text className="line-chart__axis" x={6} y={y + 4}>
                {value}
              </text>
            </g>
          );
        })}
        <polyline className="line-chart__line" points={polylinePoints} />
        {points.map((point) => (
          <g key={point.label}>
            <circle className="line-chart__dot" cx={point.x} cy={point.y} r="5" />
          </g>
        ))}
      </svg>

      <div className="line-chart__labels">
        {points.map((point) => (
          <div className="line-chart__label" key={point.label}>
            <strong>{point.label}</strong>
            <span>{point.value}</span>
            {point.detail ? <small>{point.detail}</small> : null}
          </div>
        ))}
      </div>
    </div>
  );
}
