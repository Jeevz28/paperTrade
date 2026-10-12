import { useEffect, useRef, type ReactNode } from "react";
import * as echarts from "echarts/core";
import { CandlestickChart, LineChart, BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { Database, X } from "lucide-react";
import { rupees } from "./api";
echarts.use([
  CandlestickChart,
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <Database size={28} />
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Table({
  rows,
  columns,
  empty = "No records yet",
}: {
  rows: any[];
  columns: { key: string; label: string; render?: (r: any) => ReactNode }[];
  empty?: string;
}) {
  if (!rows.length) return <Empty title={empty} />;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? i}>
              {columns.map((c) => (
                <td key={c.key}>
                  {c.render
                    ? c.render(r)
                    : r[c.key] == null
                      ? "--"
                      : typeof r[c.key] === "object"
                        ? JSON.stringify(r[c.key])
                        : String(r[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} onCancel={close}>
      <div className="modal-title">
        <h2>{title}</h2>
        <button className="icon" title="Close" onClick={close}>
          <X size={19} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function CandleChart({ rows }: { rows: any[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !rows.length) return;
    const chart = echarts.init(ref.current);
    const names = ["ema_20", "vwap", "bb_upper", "bb_lower"];
    chart.setOption({
      animation: false,
      backgroundColor: "#fff",
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      legend: { data: ["OHLC", ...names], top: 8, textStyle: { fontSize: 11 } },
      grid: [
        { left: 62, right: 20, top: 48, bottom: 110 },
        { left: 62, right: 20, height: 42, bottom: 55 },
      ],
      xAxis: [
        {
          type: "category",
          data: rows.map((r) =>
            new Date(r.timestamp).toLocaleString("en-IN", {
              timeZone: "Asia/Kolkata",
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              hour12: false,
            }),
          ),
          boundaryGap: true,
          axisLine: { lineStyle: { color: "#d5ded9" } },
          axisLabel: { fontSize: 10 },
        },
        {
          type: "category",
          gridIndex: 1,
          data: rows.map((r) => r.timestamp),
          axisLabel: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          splitLine: { lineStyle: { color: "#edf0ee" } },
          axisLabel: { fontSize: 10 },
        },
        {
          scale: true,
          gridIndex: 1,
          axisLabel: { show: false },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: [0, 1],
          start: Math.max(0, 100 - 10000 / rows.length),
          end: 100,
        },
        { type: "slider", xAxisIndex: [0, 1], bottom: 8, height: 18 },
      ],
      series: [
        {
          name: "OHLC",
          type: "candlestick",
          data: rows.map((r) => [r.open, r.close, r.low, r.high]),
          itemStyle: {
            color: "#187c62",
            color0: "#cf5262",
            borderColor: "#187c62",
            borderColor0: "#cf5262",
          },
        },
        ...names.map((name, i) => ({
          name,
          type: "line",
          data: rows.map((r) => r[name]),
          symbol: "none",
          lineStyle: {
            width: 1,
            color: ["#3d75bb", "#a17d15", "#9aa9b0", "#9aa9b0"][i],
          },
        })),
        {
          name: "Volume",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: rows.map((r) => r.volume),
          itemStyle: { color: "#c9dcd3" },
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [rows]);
  return rows.length ? (
    <div
      ref={ref}
      className="candle-chart"
      role="img"
      aria-label="Imported OHLC candles, indicators and volume"
    />
  ) : (
    <Empty title="No market data loaded">
      Import a sourced CSV or Parquet dataset to begin. No sample prices are
      preloaded.
    </Empty>
  );
}

export function EquityChart({ rows }: { rows: any[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !rows.length) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      tooltip: { trigger: "axis" },
      grid: { left: 65, right: 25, top: 25, bottom: 35 },
      xAxis: {
        type: "category",
        data: rows.map((r) => r.timestamp.slice(0, 16)),
      },
      yAxis: { type: "value", scale: true },
      series: [
        {
          name: "Simulated equity INR",
          type: "line",
          symbol: "none",
          data: rows.map((r) => r.equity_paise / 100),
          lineStyle: { color: "#187c62", width: 2 },
          areaStyle: { color: "#e5f1eb" },
        },
      ],
    });
    const obs = new ResizeObserver(() => chart.resize());
    obs.observe(ref.current);
    return () => {
      obs.disconnect();
      chart.dispose();
    };
  }, [rows]);
  return (
    <div
      className="equity-chart"
      ref={ref}
      role="img"
      aria-label="Simulated equity curve"
    />
  );
}

export function Metrics({ metrics }: { metrics: any }) {
  const fields = [
    ["Closed trades", metrics.total_trades],
    [
      "Win rate",
      metrics.win_rate_pct == null
        ? "--"
        : metrics.win_rate_pct.toFixed(1) + "%",
    ],
    ["Net P&L", rupees(metrics.net_pnl_paise)],
    ["Max drawdown", (metrics.max_drawdown_pct ?? 0).toFixed(2) + "%"],
    ["Profit factor", metrics.profit_factor?.toFixed(2) ?? "--"],
    ["Sharpe", metrics.sharpe?.toFixed(2) ?? "--"],
  ];
  return (
    <div className="metrics">
      {fields.map(([label, value]) => (
        <div key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}
