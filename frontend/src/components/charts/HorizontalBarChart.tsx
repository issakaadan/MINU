type HorizontalBarDatum = {
  label: string;
  value: number;
  color?: string;
  meta?: string;
};

type HorizontalBarChartProps = {
  items: HorizontalBarDatum[];
  emptyMessage: string;
  valueLabel?: string;
};

export function HorizontalBarChart({
  items,
  emptyMessage,
  valueLabel = "items",
}: HorizontalBarChartProps) {
  const maxValue = Math.max(...items.map((item) => item.value), 0);

  if (maxValue === 0) {
    return <div className="empty-state">{emptyMessage}</div>;
  }

  return (
    <div className="bar-chart">
      {items.map((item) => {
        const width = maxValue > 0 ? (item.value / maxValue) * 100 : 0;
        return (
          <div className="bar-chart__row" key={item.label}>
            <div className="bar-chart__header">
              <div>
                <strong>{item.label}</strong>
                {item.meta ? <span>{item.meta}</span> : null}
              </div>
              <span>
                {item.value} {valueLabel}
              </span>
            </div>
            <div className="bar-chart__track">
              <div
                className="bar-chart__fill"
                style={{
                  width: `${width}%`,
                  background: item.color
                    ? `linear-gradient(90deg, ${item.color}, ${item.color}cc)`
                    : undefined,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
