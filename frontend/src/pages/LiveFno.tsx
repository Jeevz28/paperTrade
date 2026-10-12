import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { CandlestickChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { api, rupees, when } from "../api";
echarts.use([
  CandlestickChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
]);
type Row = Record<string, any>;
type Bar = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};
type Chart = {
  instrument_id: number;
  label: string;
  bars: Bar[];
  forming?: Bar | null;
  levels?: Record<string, number | null>;
  source?: string;
};
const text = (v: unknown): string =>
  v == null
    ? "Not reported"
    : typeof v === "string"
      ? v.replaceAll("_", " ")
      : JSON.stringify(v);
const err = (v: unknown) => (v instanceof Error ? v.message : String(v));
function MarketChart({
  data,
  contract,
  strategy,
}: {
  data: Chart;
  contract: boolean;
  strategy: string;
}) {
  const node = useRef<HTMLDivElement>(null),
    instance = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!node.current) return;
    const chart = echarts.init(node.current);
    instance.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(node.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      instance.current = null;
    };
  }, []);
  useEffect(() => {
    const rows = [
      ...data.bars.filter((b) => b.timestamp !== data.forming?.timestamp),
      ...(data.forming ? [data.forming] : []),
    ].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
    const ema = (period: number) => {
      let previous = rows[0]?.close ?? 0;
      return rows.map((b, i) => {
        previous = i
          ? (b.close * 2) / (period + 1) + previous * (1 - 2 / (period + 1))
          : b.close;
        return i >= period - 1 ? previous / 100 : null;
      });
    };
    const levels = contract
      ? Object.entries(data.levels ?? {})
          .filter(([, v]) => v != null)
          .map(([k, v]) => ({
            name:
              k === "entry_paise"
                ? "Entry"
                : k === "stop_paise"
                  ? "SL"
                  : "Target",
            yAxis: Number(v) / 100,
            lineStyle: {
              color:
                k === "stop_paise"
                  ? "#cb5969"
                  : k === "target_paise"
                    ? "#168677"
                    : "#8075b4",
            },
          }))
      : [];
    const prices = [
      ...rows.flatMap((b) => [b.low / 100, b.high / 100]),
      ...levels.map((level) => level.yAxis),
    ];
    const low = Math.min(...prices),
      high = Math.max(...prices);
    const padding = Math.max((high - low) * 0.08, 0.01);
    instance.current?.setOption(
      {
        animation: false,
        tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
        legend: {
          top: 6,
          data: ["Candles", "Live close", "EMA 20", "EMA 50"],
          textStyle: { fontSize: 11, color: "#63796e" },
        },
        grid: { top: 45, left: 64, right: contract ? 64 : 18, bottom: 40 },
        xAxis: {
          type: "category",
          data: rows.map((b) => when(b.timestamp) + " IST"),
          axisLabel: {
            color: "#6b8074",
            fontSize: 10,
            formatter: (v: string) =>
              v.split(", ").pop()?.replace(" IST", "") ?? v,
          },
          axisLine: { lineStyle: { color: "#cbdad1" } },
        },
        yAxis: {
          scale: true,
          min: levels.length ? Math.max(0, low - padding) : undefined,
          max: levels.length ? high + padding : undefined,
          axisLabel: { fontSize: 10 },
          splitLine: { lineStyle: { color: "#edf2ef" } },
        },
        series: [
          {
            name: "Candles",
            type: "candlestick",
            data: rows.map((b) => [
              b.open / 100,
              b.close / 100,
              b.low / 100,
              b.high / 100,
            ]),
            itemStyle: {
              color: "#168677",
              color0: "#cb5969",
              borderColor: "#168677",
              borderColor0: "#cb5969",
            },
            markLine: {
              symbol: "none",
              label: { formatter: "{b}" },
              data: levels,
            },
          },
          {
            name: "Live close",
            type: "line",
            symbol: "none",
            data: rows.map((b) => b.close / 100),
            itemStyle: { color: "#8ba297" },
            lineStyle: { color: "#8ba297", width: 1 },
          },
          ...[20, 50].map((p, i) => ({
            name: `EMA ${p}`,
            type: "line",
            symbol: "none",
            data: ema(p),
            itemStyle: { color: ["#b49337", "#8475b7"][i] },
            lineStyle: { color: ["#b49337", "#8475b7"][i], width: 1.5 },
          })),
        ],
      },
      true,
    );
  }, [data, contract]);
  return (
    <div
      className="lf-chart"
      ref={node}
      role="img"
      aria-label={`${data.label}: candlesticks, live close, EMA 20 and EMA 50. Strategy: ${strategy}. INR prices, IST timestamps.${contract ? " Entry, SL and target levels when available." : ""}`}
    />
  );
}
export function LiveFno() {
  const [data, setData] = useState<Row | null>(null),
    [pollError, setPollError] = useState(""),
    [actionError, setActionError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [updated, setUpdated] = useState<string>();
  const [selection, setSelection] = useState("underlying"),
    [chart, setChart] = useState<Chart | null>(null),
    [chartError, setChartError] = useState("");
  const acting = useRef(false),
    revision = useRef(0);
  const account = data?.selected_account ?? data?.runtime?.account_id;
  const session = data?.sessions?.find(
    (s: Row) => String(s.account_id) === String(account),
  );
  const state = session?.state ?? {},
    balances = data?.balances ?? {};
  const positions: Row[] =
      data?.snapshot?.basket_positions ?? data?.snapshot?.positions ?? [],
    orders: Row[] = data?.orders ?? [];
  const options = positions.filter(
    (p, i) =>
      p.instrument_id != null &&
      positions.findIndex((q) => q.instrument_id === p.instrument_id) === i,
  );
  const contract =
    selection !== "underlying" &&
    options.some((p) => String(p.instrument_id) === selection);
  const instrument = contract
    ? selection
    : (session?.underlying_id ?? session?.underlying_instrument_id);
  const strategy = text(state.strategy ?? "Waiting for strategy"),
    enabled = !!session?.enabled;
  const blocked =
    /BLOCK|CONFIG|ERROR|INVALID/i.test(data?.runtime?.status ?? "") ||
    !!data?.runtime?.capital_mismatch;
  useEffect(() => {
    let active = true,
      pending = false;
    const poll = async () => {
      if (pending || acting.current) return;
      pending = true;
      const generation = revision.current;
      try {
        const next = await api("/fno-auto");
        if (active && generation === revision.current) {
          setData(next);
          setPollError("");
          setUpdated(new Date().toISOString());
        }
      } catch (e) {
        if (active && generation === revision.current) setPollError(err(e));
      } finally {
        pending = false;
      }
    };
    void poll();
    const timer = setInterval(poll, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => setSelection("underlying"), [account]);
  useEffect(() => {
    let active = true,
      pending = false;
    setChart(null);
    setChartError("");
    if (!account || !instrument) return;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const next = await api<Chart>(
          `/fno-auto/chart?account_id=${encodeURIComponent(account)}&instrument_id=${encodeURIComponent(instrument)}`,
        );
        if (active) {
          setChart(next);
          setChartError("");
        }
      } catch (e) {
        if (active) setChartError(err(e));
      } finally {
        pending = false;
      }
    };
    void poll();
    const timer = setInterval(poll, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [account, instrument]);
  async function act(action: "start" | "pause" | "close") {
    if (!account || acting.current) return;
    acting.current = true;
    revision.current++;
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      await api(`/fno-auto/${account}/${action}`, "POST");
      setNotice(
        action === "close"
          ? "Exit requested and entries paused. Watch the position for fill confirmation."
          : action === "pause"
            ? "Pause requested."
            : "Resume requested.",
      );
      const next = await api("/fno-auto");
      setData(next);
      setPollError("");
      setUpdated(new Date().toISOString());
    } catch (e) {
      setActionError(err(e));
    } finally {
      acting.current = false;
      setBusy(false);
    }
  }
  const metrics = [
    ["Cash after premium & fees", "cash_paise"],
    ["Available cash", "available_cash_paise"],
    ["Deployed premium", "premium_in_positions_paise"],
    ["Reserved margin", "margin_reserved_paise"],
    ["Unrealized P&L", "unrealized_pnl_paise"],
    ["Realized net P&L", "realized_net_pnl_paise"],
  ];
  return (
    <div className="lf-app">
      <header className="lf-header">
        <div className="lf-brand">
          <b className="lf-logo">P</b>
          <strong>PaperLab</strong>
          <span> / Live F&O</span>
        </div>
        <span className="lf-pill">PAPER ONLY · NO REAL ORDERS</span>
      </header>
      <main className="lf-main">
        <div className="lf-heading">
          <div>
            <p className="lf-eyebrow">AUTOMATIC PAPER TRADING</p>
            <h1>Live F&O workspace</h1>
            <p>
              {session?.label ?? "Managed account"}
              {account ? ` · #${account}` : " · Awaiting backend"} · Updates
              every 2 seconds
            </p>
          </div>
          <div className="lf-actions">
            <button
              disabled={!account || !session || busy || (!enabled && blocked)}
              onClick={() => void act(enabled ? "pause" : "start")}
            >
              {enabled ? "Pause" : "Resume"}
            </button>
            <button
              className="lf-danger"
              disabled={!account || !positions.length || busy}
              onClick={() => void act("close")}
            >
              Close paper position &amp; pause
            </button>
          </div>
        </div>
        {pollError && (
          <div className="lf-alert" role="alert">
            Status unavailable; displayed values may be stale. {pollError}
          </div>
        )}
        {actionError && (
          <div className="lf-alert" role="alert">
            {actionError}
          </div>
        )}
        {notice && (
          <div className="lf-notice" role="status">
            {notice}
          </div>
        )}
        <section
          className={`lf-runtime ${blocked ? "lf-blocked" : ""}`}
          aria-label="Runtime status"
        >
          <div>
            <strong>
              {text(data?.runtime?.status ?? (data ? "Waiting" : "Connecting"))}
            </strong>
            <span>
              {data?.runtime?.message ??
                "Waiting for automatic backend startup."}
            </span>
          </div>
          <small>
            Feed · {text(data?.stream?.state ?? "Unknown")} · Last tick{" "}
            {when(data?.stream?.last_message ?? null)} IST
          </small>
          {data?.runtime?.capital_mismatch && (
            <p>
              Configured capital differs from this account.{" "}
              {rupees(data.runtime.configured_capital_paise)} configured;{" "}
              {rupees(balances.initial_capital_paise)} funded.
            </p>
          )}
          {data?.stream?.error && <p role="alert">{data.stream.error}</p>}
        </section>
        <section className="lf-balance" aria-label="Account balance">
          <div className="lf-section-title">
            <h2>Account balance</h2>
            <span>
              Equity <strong>{rupees(balances.equity_paise)}</strong>
            </span>
          </div>
          <div className="lf-metrics">
            {metrics.map(([label, key]) => (
              <div key={key}>
                <span>{label}</span>
                <strong
                  className={
                    key.includes("pnl") && balances[key] != null
                      ? balances[key] < 0
                        ? "lf-loss"
                        : "lf-gain"
                      : ""
                  }
                >
                  {rupees(balances[key])}
                </strong>
              </div>
            ))}
          </div>
          <div className="lf-balance-foot">
            <span>
              Initial capital {rupees(balances.initial_capital_paise)}
            </span>
            <span>
              Pending reserves {rupees(balances.pending_reserved_paise)}
            </span>
            <span>Costs {rupees(balances.costs_paise)}</span>
          </div>
        </section>
        <div className="lf-grid">
          <section className="lf-panel" aria-label="Live market chart">
            <div className="lf-section-title">
              <div>
                <h2>{chart?.label ?? "Market chart"}</h2>
                <p>Strategy · {strategy}</p>
              </div>
              <label className="lf-selector">
                Chart view · view only
                <select
                  aria-label="Chart instrument"
                  value={contract ? selection : "underlying"}
                  onChange={(e) => setSelection(e.target.value)}
                >
                  <option value="underlying">Underlying</option>
                  {options.map((p) => (
                    <option key={p.instrument_id} value={p.instrument_id}>
                      {p.contract ?? p.symbol ?? "Active contract"}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {chartError && (
              <p className="lf-alert" role="alert">
                Chart unavailable; previous prices may be stale. {chartError}
              </p>
            )}
            {chart && (chart.bars?.length || chart.forming) ? (
              <MarketChart
                data={chart}
                contract={contract}
                strategy={strategy}
              />
            ) : (
              <div className="lf-chart lf-empty">
                {instrument
                  ? "Waiting for market bars."
                  : "Waiting for the underlying instrument."}
              </div>
            )}
            <div className="lf-chart-foot">
              <span>
                INR · IST (Asia/Kolkata) · {chart?.source ?? "Source pending"}
              </span>
              <span>
                {chart?.forming
                  ? "Current candle forming"
                  : "Completed candles"}
              </span>
            </div>
          </section>
          <section
            className="lf-panel lf-team"
            aria-label="Rule-based agent team"
          >
            <div className="lf-section-title">
              <h2>Strategy &amp; agent team</h2>
              <span className="lf-pill">RULE-BASED</span>
            </div>
            <div className="lf-strategy">
              <strong>{strategy}</strong>
              <span>
                {text(state.status ?? "Waiting")} ·{" "}
                {text(state.regime ?? "Regime pending")}
              </span>
              <p>
                {state.message ??
                  "The backend will report its working strategy and decisions here."}
              </p>
            </div>
            <p className="lf-muted">
              Cooperating rule-based checks, not an LLM. Evaluates the
              implemented strategies only; messages report backend decisions,
              not predictions.
            </p>
            <div className="lf-messages">
              {data?.agent_team?.messages?.length ? (
                data.agent_team.messages.map((m: Row, i: number) => (
                  <article key={`${m.role}-${m.timestamp}-${i}`}>
                    <div>
                      <strong>{text(m.role)}</strong>
                      <span>{text(m.status)}</span>
                    </div>
                    <p>{text(m.message)}</p>
                    <time>{when(m.timestamp)} IST</time>
                  </article>
                ))
              ) : (
                <p className="lf-empty">No agent messages yet.</p>
              )}
            </div>
            {["reviews", "candidates"].map((key) => (
              <details key={key}>
                <summary>
                  {key === "reviews" ? "Strategy reviews" : "Candidates"} ·{" "}
                  {data?.agent_team?.[key]?.length ?? 0}
                </summary>
                {data?.agent_team?.[key]?.length ? (
                  data.agent_team[key].map((r: Row, i: number) => (
                    <article className="lf-review" key={i}>
                      <strong>
                        {text(r.strategy ?? r.name ?? r.contract)}
                      </strong>
                      {key === "reviews" ? (
                        <>
                          <p>{text(r.summary ?? r.classification)}</p>
                          <p>
                            Net P&amp;L{" "}
                            <strong>
                              {rupees(r.net_pnl_paise ?? r.net_pnl)}
                            </strong>{" "}
                            · {text(r.regime)}
                          </p>
                          <p>
                            {text(r.evidence_status)}
                            {r.recorded_exit_reason
                              ? ` · ${text(r.recorded_exit_reason)}`
                              : ""}
                          </p>
                          <time>{when(r.closed_at ?? r.timestamp)} IST</time>
                        </>
                      ) : (
                        <>
                          <p>
                            {r.eligible === true
                              ? "Eligible"
                              : r.eligible === false
                                ? "Excluded"
                                : text(r.status)}{" "}
                            · {text(r.regime)}
                          </p>
                          <p>{text(r.reason ?? r.exclusion)}</p>
                          {r.proxy && (
                            <p>
                              Prior-data proxy · {r.proxy.samples ?? 0} samples
                              · {text(r.proxy.evidence)}
                            </p>
                          )}
                        </>
                      )}
                    </article>
                  ))
                ) : (
                  <p>No {key} reported.</p>
                )}
              </details>
            ))}
          </section>
        </div>
        <section className="lf-panel" aria-label="Open F&O positions">
          <div className="lf-section-title">
            <h2>
              Open positions{" "}
              <span className="lf-count">{positions.length}</span>
            </h2>
            <span>Automatic fills appear here</span>
          </div>
          {positions.length ? (
            <div className="lf-table-scroll">
              <table>
                <thead>
                  <tr>
                    {[
                      "Contract / reason",
                      "Side / units",
                      "Entry",
                      "Live mark",
                      "Stop / target",
                      "Unrealized P&L",
                      "Status",
                    ].map((t) => (
                      <th key={t}>{t}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p, i) => (
                    <tr key={p.id ?? i}>
                      <td>
                        <strong>{p.contract ?? p.symbol}</strong>
                        <small>
                          {text(
                            p.reason ??
                              orders.find((o) => o.id === p.basket_id)
                                ?.reason ??
                              orders.find((o) => o.id === p.basket_id)
                                ?.signal ??
                              state.strategy ??
                              "Reason not reported",
                          )}
                        </small>
                      </td>
                      <td>
                        {p.signed_quantity > 0 ? "LONG" : "SHORT"} ·{" "}
                        {Math.abs(p.signed_quantity ?? p.quantity ?? 0)}
                      </td>
                      <td>{rupees(p.basis_price ?? p.entry_paise)}</td>
                      <td>{rupees(p.mark_paise)}</td>
                      <td>
                        {rupees(p.stop_paise)} / {rupees(p.target_paise)}
                      </td>
                      <td>{rupees(p.unrealized_pnl_paise)}</td>
                      <td>
                        {text(
                          p.status === "CLOSING" ? "EXIT PENDING" : p.status,
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="lf-empty">
              No open paper positions. Filled trades appear automatically.
            </p>
          )}
        </section>
        <section className="lf-panel" aria-label="F&O orders and fills">
          <div className="lf-section-title">
            <h2>
              Placed orders <span className="lf-count">{orders.length}</span>
            </h2>
            <span>Paper execution</span>
          </div>
          {orders.length ? (
            <div className="lf-table-scroll">
              <table>
                <thead>
                  <tr>
                    {[
                      "Order / time (IST)",
                      "Contract / strategy",
                      "Side / units",
                      "Status",
                      "Entry / exit",
                      "Stop / target",
                      "Net P&L",
                      "Reason",
                    ].map((t) => (
                      <th key={t}>{t}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o, i) => (
                    <tr key={o.id ?? i}>
                      <td>
                        #{o.id}
                        <small>{when(o.created_at)}</small>
                      </td>
                      <td>
                        <strong>{o.contract ?? o.symbol}</strong>
                        <small>{text(o.signal ?? o.strategy)}</small>
                      </td>
                      <td>
                        {o.side ?? (o.quantity > 0 ? "BUY" : "SELL")} ·{" "}
                        {Math.abs(o.quantity ?? 0)}
                      </td>
                      <td>
                        {o.status === "OPEN" ? "FILLED / OPEN" : text(o.status)}
                      </td>
                      <td>
                        {rupees(o.entry_paise)} / {rupees(o.exit_paise)}
                      </td>
                      <td>
                        {rupees(o.managed_stop ?? o.stop_paise)} /{" "}
                        {rupees(o.target_paise)}
                      </td>
                      <td>{rupees(o.net_pnl ?? o.net_pnl_paise)}</td>
                      <td>
                        {text(
                          o.reason || o.exit_reason || "Reason not reported",
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="lf-empty">
              No paper orders yet. The strategy status explains what the system
              is waiting for.
            </p>
          )}
        </section>
        <details className="lf-panel lf-events">
          <summary>Recent activity · {data?.events?.length ?? 0}</summary>
          {data?.events?.length ? (
            data.events.map((e: Row, i: number) => (
              <p key={e.id ?? i}>
                {when(e.timestamp)} IST ·{" "}
                <strong>{text(e.action ?? e.kind)}</strong> ·{" "}
                {text(e.message ?? e.details)}
              </p>
            ))
          ) : (
            <p>No activity reported.</p>
          )}
        </details>
        <footer className="lf-footer">
          <span>
            Simulated execution · Capital supplied by backend configuration
          </span>
          <span>
            {updated
              ? `Status updated ${when(updated)} IST`
              : "Connecting to backend…"}
          </span>
        </footer>
      </main>
    </div>
  );
}
