export default function IntentBars({ intents = [], emptyLabel = "No data yet" }) {
  if (!intents.length) return <div className="smifs-empty">{emptyLabel}</div>;
  const max = Math.max(...intents.map((i) => i.count), 1);
  return (
    <ul className="smifs-bars" data-testid="intent-bars">
      {intents.map((i) => (
        <li key={i.intent} className="smifs-bar">
          <span className="smifs-bar-label">{(i.intent || "").replace(/_/g, " ").toLowerCase()}</span>
          <div className="smifs-bar-track">
            <span className="smifs-bar-fill" style={{ width: `${(i.count / max) * 100}%` }} />
          </div>
          <span className="smifs-bar-value">{i.count}</span>
        </li>
      ))}
    </ul>
  );
}
