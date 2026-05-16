/*
  SmallBarChart.jsx

  A small reusable Recharts bar chart.

  This is intentionally simple. The goal is not to build a full dashboard yet.
  In Chat 10, charts should summarize the currently filtered table, while the
  table remains the main analysis tool.
*/

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export default function SmallBarChart({
  data,
  xKey = "name",
  yKey = "value",
  title = "Chart",
  description = "",
}) {
  if (!data || data.length === 0) {
    return (
      <section className="chart-card">
        <h2>{title}</h2>
        {description && <p>{description}</p>}
        <p className="muted">No chart data available for the current filters.</p>
      </section>
    );
  }

  return (
    <section className="chart-card">
      <div className="chart-card-header">
        <div>
          <p className="eyebrow">Visualization</p>
          <h2>{title}</h2>
        </div>

        {description && <p>{description}</p>}
      </div>

      <div className="chart-frame">
        <ResponsiveContainer width="100%" height={300}>
          <BarChart
            data={data}
            margin={{
              top: 12,
              right: 16,
              bottom: 60,
              left: 12,
            }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey={xKey}
              angle={-35}
              textAnchor="end"
              interval={0}
              height={80}
            />
            <YAxis />
            <Tooltip />
            <Bar dataKey={yKey} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}