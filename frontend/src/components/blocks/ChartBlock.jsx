import { useMemo } from "react";
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts";

// Phase 20 — ChartBlock
// Props: block = { kind, title, x_key, y_keys:[...], data:[...], theme }
// Themed against SMIFS dark surface; no new color literals — uses palette below.

const PALETTE = ["#14a47a", "#C9A86A", "#5B7CC9", "#E07A6E", "#9C7CD2", "#6BC9C9", "#D2B48C", "#7CD2C9"];
const AXIS_COLOR = "#8FA4BD";
const GRID_COLOR = "rgba(255,255,255,0.06)";

function fmtTick(v) {
  if (typeof v !== "number") return v;
  if (Math.abs(v) >= 1e7) return `${(v / 1e7).toFixed(1)}Cr`;
  if (Math.abs(v) >= 1e5) return `${(v / 1e5).toFixed(1)}L`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return v;
}

export default function ChartBlock({ block, msgIdx }) {
  const kind = (block.kind || "bar").toLowerCase();
  const data = Array.isArray(block.data) ? block.data : [];
  const xKey = block.x_key || "x";
  const yKeys = block.y_keys && block.y_keys.length ? block.y_keys : ["y"];

  const collapsedData = useMemo(() => {
    if (kind !== "pie" && kind !== "donut") return data;
    const max = Number(block.max_slices || 7);
    if (data.length <= max) return data;
    const sorted = data.slice().sort((a, b) => (b[yKeys[0]] || 0) - (a[yKeys[0]] || 0));
    const top = sorted.slice(0, max - 1);
    const rest = sorted.slice(max - 1).reduce((s, r) => s + (Number(r[yKeys[0]]) || 0), 0);
    return [...top, { [xKey]: "Others", [yKeys[0]]: rest }];
  }, [data, kind, block.max_slices, xKey, yKeys]);

  if (!data.length) {
    return (
      <div className="smifs-block-chart" data-testid={`chart-block-${msgIdx}-empty`}>
        {block.title && <h4>{block.title}</h4>}
        <p className="smifs-block-chart-empty">No data to chart.</p>
      </div>
    );
  }

  const tooltipStyle = {
    contentStyle: { background: "#172a3f", border: "1px solid #2A3F58", color: "#F4E6CB" },
    labelStyle: { color: "#C9A86A" },
    itemStyle: { color: "#F4E6CB" },
  };

  return (
    <div className="smifs-block-chart" data-testid={`chart-block-${msgIdx}`} data-kind={kind}>
      {block.title && <h4>{block.title}</h4>}
      <div className="smifs-block-chart-canvas">
        <ResponsiveContainer width="100%" height={kind === "sparkline" ? 90 : 280}>
          {kind === "line" || kind === "sparkline" ? (
            <LineChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 8 }}>
              {kind !== "sparkline" && <CartesianGrid stroke={GRID_COLOR} vertical={false} />}
              {kind !== "sparkline" && <XAxis dataKey={xKey} stroke={AXIS_COLOR} fontSize={11} />}
              {kind !== "sparkline" && <YAxis stroke={AXIS_COLOR} fontSize={11} tickFormatter={fmtTick} />}
              {kind !== "sparkline" && <Tooltip {...tooltipStyle} />}
              {kind !== "sparkline" && yKeys.length > 1 && <Legend wrapperStyle={{ color: AXIS_COLOR }} />}
              {yKeys.map((yk, i) => (
                <Line key={yk} type="monotone" dataKey={yk}
                       stroke={PALETTE[i % PALETTE.length]} strokeWidth={2}
                       dot={kind === "sparkline" ? false : { r: 3 }} />
              ))}
            </LineChart>
          ) : kind === "area" ? (
            <AreaChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 8 }}>
              <CartesianGrid stroke={GRID_COLOR} vertical={false} />
              <XAxis dataKey={xKey} stroke={AXIS_COLOR} fontSize={11} />
              <YAxis stroke={AXIS_COLOR} fontSize={11} tickFormatter={fmtTick} />
              <Tooltip {...tooltipStyle} />
              {yKeys.length > 1 && <Legend wrapperStyle={{ color: AXIS_COLOR }} />}
              {yKeys.map((yk, i) => (
                <Area key={yk} type="monotone" dataKey={yk}
                        stroke={PALETTE[i % PALETTE.length]}
                        fill={PALETTE[i % PALETTE.length]} fillOpacity={0.25} />
              ))}
            </AreaChart>
          ) : kind === "pie" || kind === "donut" ? (
            <PieChart>
              <Pie data={collapsedData} dataKey={yKeys[0]} nameKey={xKey}
                    cx="50%" cy="50%"
                    innerRadius={kind === "donut" ? 60 : 0}
                    outerRadius={110}
                    paddingAngle={1}
                    label={(e) => e[xKey]}
                    labelLine={false}>
                {collapsedData.map((_, i) => (
                  <Cell key={i} fill={PALETTE[i % PALETTE.length]} stroke="#0B1B2B" strokeWidth={1.5} />
                ))}
              </Pie>
              <Tooltip {...tooltipStyle} />
            </PieChart>
          ) : (
            <BarChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 8 }}>
              <CartesianGrid stroke={GRID_COLOR} vertical={false} />
              <XAxis dataKey={xKey} stroke={AXIS_COLOR} fontSize={11} />
              <YAxis stroke={AXIS_COLOR} fontSize={11} tickFormatter={fmtTick} />
              <Tooltip {...tooltipStyle} />
              {yKeys.length > 1 && <Legend wrapperStyle={{ color: AXIS_COLOR }} />}
              {yKeys.map((yk, i) => (
                <Bar key={yk} dataKey={yk} fill={PALETTE[i % PALETTE.length]}
                      radius={[4, 4, 0, 0]} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
