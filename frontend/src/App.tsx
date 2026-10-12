import { useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  BookOpen,
  Boxes,
  BrainCircuit,
  ChevronRight,
  Database,
  Download,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LineChart,
  ListOrdered,
  Plus,
  Radio,
  RefreshCw,
  Settings,
  ShieldCheck,
  ShieldOff,
  SkipForward,
  Wallet,
  X,
} from "lucide-react";
import { api, rupees, when } from "./api";
import { CandleChart, Empty, Modal, Table } from "./components";
import { DataPage } from "./pages/Data";
import { ResearchPage } from "./pages/Research";
import { OptionsPage } from "./pages/Options";
import { ActivityPage } from "./pages/Activity";

import { FnoAutoPage } from "./pages/FnoAuto";
import { AutoPaperPage } from "./pages/AutoPaper";

const pages = [
  ["F&O Auto", Boxes],
  ["Auto Paper", BrainCircuit],
  ["Dashboard", LayoutDashboard],
  ["Activity & Automation", Activity],
  ["Live Paper Trading", Radio],
  ["Paper Portfolio", Wallet],
  ["Strategies", Activity],
  ["Backtesting", LineChart],
  ["Historical Research", FlaskConical],
  ["Historical Replay", SkipForward],
  ["Options / F&O", Boxes],
  ["Trade Journal", BookOpen],
  ["Account Ledger", ListOrdered],
  ["Risk Dashboard", ShieldCheck],
  ["Data Management", Database],
  ["AI Analysis", BrainCircuit],
  ["Settings", Settings],
] as const;

export default function App() {
  const [page, setPage] = useState("F&O Auto"),
    [health, setHealth] = useState<any>(null),
    [instruments, setInstruments] = useState<any[]>([]),
    [datasets, setDatasets] = useState<any[]>([]),
    [strategies, setStrategies] = useState<any[]>([]),
    [accounts, setAccounts] = useState<any[]>([]);
  const [accountId, setAccountId] = useState(""),
    [instrumentId, setInstrumentId] = useState(""),
    [tf, setTf] = useState("5m"),
    [snapshot, setSnapshot] = useState<any>(null),
    [market, setMarket] = useState<any>({ candles: [], signals: [] }),
    [orders, setOrders] = useState<any[]>([]),
    [ledger, setLedger] = useState<any[]>([]),
    [journal, setJournal] = useState<any[]>([]),
    [risk, setRisk] = useState<any[]>([]),
    [alerts, setAlerts] = useState<any[]>([]);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [modal, setModal] = useState(false),
    [ai, setAi] = useState<any>(null),
    [createMode, setCreateMode] = useState("REPLAY");
  const [playing, setPlaying] = useState(false);
  async function refresh() {
    const [h, i, d, s, a] = await Promise.all([
      api("/health"),
      api("/instruments"),
      api("/datasets"),
      api("/strategies"),
      api("/accounts"),
    ]);
    setHealth(h);
    setInstruments(i);
    setDatasets(d);
    setStrategies(s);
    setAccounts(a);
    if (!instrumentId && i.length) setInstrumentId(String(i[0].id));
    if (!accountId && a.length) setAccountId(String(a[0].id));
  }
  async function refreshAccount() {
    if (!accountId) return;
    const [a, o, l, j, r, al] = await Promise.all([
      api("/accounts/" + accountId),
      api("/accounts/" + accountId + "/orders"),
      api("/accounts/" + accountId + "/ledger"),
      api("/accounts/" + accountId + "/journal"),
      api("/accounts/" + accountId + "/risk"),
      api("/alerts"),
    ]);
    setSnapshot(a);
    setOrders(o);
    setLedger(l);
    setJournal(j);
    setRisk(r.events);
    setAlerts(al);
  }
  async function refreshMarket() {
    if (!instrumentId) {
      setMarket({ candles: [], signals: [] });
      return;
    }
    const replayView =
      [
        "Historical Replay",
        "Dashboard",
        "Paper Portfolio",
        "Strategies",
      ].includes(page) && accountId;
    setMarket(
      await api(
        "/candles?instrument_id=" +
          instrumentId +
          "&timeframe=" +
          tf +
          (replayView ? "&account_id=" + accountId : ""),
      ),
    );
  }
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setPlaying(false);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void act(refresh);
  }, []);
  useEffect(() => {
    setSnapshot(null);
    void act(refreshAccount);
  }, [accountId]);
  useEffect(() => {
    setMarket({ candles: [], signals: [] });
    void act(refreshMarket);
  }, [instrumentId, tf, accountId, page]);
  const activeInst = instruments.find((i) => String(i.id) === instrumentId);
  const selectedDataset = datasets.find((d) => d.id === snapshot?.dataset_id);
  const step = () =>
    act(async () => {
      const result = await api(
        "/accounts/" + accountId + "/replay/step",
        "POST",
      );
      if (result.finished) setPlaying(false);
      await refreshAccount();
      await refreshMarket();
    });
  useEffect(() => {
    setPlaying(false);
  }, [accountId, page]);
  useEffect(() => {
    if (!playing || busy || snapshot?.halted || snapshot?.mode !== "REPLAY")
      return;
    const timer = window.setTimeout(step, 1000);
    return () => window.clearTimeout(timer);
  }, [playing, busy, snapshot?.clock, snapshot?.halted]);
  const positions = snapshot?.positions ?? [];

  function accountMetrics() {
    return (
      <div className="metrics account-metrics">
        {[
          ["Initial capital", snapshot?.initial_capital],
          ["Current equity", snapshot?.equity_paise],
          ["Available cash", snapshot?.available_cash_paise],
          ["Realized P&L", snapshot?.realized_pnl],
          ["Unrealized P&L", snapshot?.unrealized_pnl_paise],
          ["Total costs", snapshot?.costs],
        ].map(([label, value]) => (
          <div key={String(label)}>
            <span>{label}</span>
            <strong>{rupees(value as number)}</strong>
          </div>
        ))}
      </div>
    );
  }
  function positionTable() {
    return (
      <Table
        rows={positions}
        columns={[
          { key: "symbol", label: "Instrument" },
          { key: "quantity", label: "Qty" },
          {
            key: "entry_price",
            label: "Entry",
            render: (r) => rupees(r.entry_price),
          },
          {
            key: "mark_paise",
            label: "Mark",
            render: (r) => rupees(r.mark_paise),
          },
          {
            key: "stop_paise",
            label: "Stop",
            render: (r) => rupees(r.stop_paise),
          },
          {
            key: "unrealized_pnl_paise",
            label: "Unrealized P&L",
            render: (r) => rupees(r.unrealized_pnl_paise),
          },
        ]}
        empty="No open paper positions"
      />
    );
  }
  function orderTable() {
    return (
      <Table
        rows={orders}
        columns={[
          { key: "symbol", label: "Instrument" },
          { key: "side", label: "Side" },
          { key: "quantity", label: "Qty" },
          { key: "order_type", label: "Type" },
          {
            key: "status",
            label: "Status",
            render: (r) => (
              <span className={"status " + r.status.toLowerCase()}>
                {r.status}
              </span>
            ),
          },
          { key: "price", label: "Fill", render: (r) => rupees(r.price) },
          { key: "reason", label: "Reason" },
          {
            key: "cancel",
            label: "",
            render: (r) =>
              r.status === "PENDING" ? (
                <button
                  className="icon"
                  title="Cancel paper order"
                  onClick={() =>
                    act(async () => {
                      await api(
                        "/accounts/" + accountId + "/orders/" + r.id,
                        "DELETE",
                      );
                      await refreshAccount();
                    })
                  }
                >
                  <X size={15} />
                </button>
              ) : null,
          },
        ]}
        empty="No paper orders"
      />
    );
  }
  function orderForm() {
    return (
      <form
        className="order-form"
        onSubmit={(e) => {
          e.preventDefault();
          const form = e.currentTarget;
          const f = new FormData(form);
          act(async () => {
            await api("/accounts/" + accountId + "/orders", "POST", {
              instrument_id: Number(instrumentId),
              side: f.get("side"),
              quantity: Number(f.get("quantity")),
              order_type: f.get("order_type"),
              limit_price: f.get("limit_price")
                ? Number(f.get("limit_price"))
                : null,
              stop_loss: f.get("stop_loss") ? Number(f.get("stop_loss")) : null,
              target: f.get("target") ? Number(f.get("target")) : null,
              trailing_distance: f.get("trailing")
                ? Number(f.get("trailing"))
                : null,
              request_key: crypto.randomUUID(),
            });
            await refreshAccount();
          });
        }}
      >
        <h3>Paper order</h3>
        <div className="form-grid">
          <label>
            Side
            <select name="side">
              <option>BUY</option>
              <option>SELL</option>
            </select>
          </label>
          <label>
            Type
            <select name="order_type">
              <option>MARKET</option>
              <option>LIMIT</option>
            </select>
          </label>
          <label>
            Quantity
            <input
              type="number"
              name="quantity"
              min={activeInst?.lot_size ?? 1}
              step={activeInst?.lot_size ?? 1}
              defaultValue={activeInst?.lot_size ?? 1}
              key={instrumentId}
              required
            />
          </label>
          <label>
            Limit (INR)
            <input type="number" min=".01" step=".01" name="limit_price" />
          </label>
          <label>
            Stop (INR)
            <input type="number" min=".01" step=".01" name="stop_loss" />
          </label>
          <label>
            Target (INR)
            <input type="number" min=".01" step=".01" name="target" />
          </label>
          <label className="full">
            Trailing distance (INR)
            <input type="number" min=".01" step=".01" name="trailing" />
          </label>
        </div>
        <button
          className="primary"
          disabled={!accountId || !instrumentId || busy || snapshot?.halted}
        >
          Submit paper order
        </button>
        <small>
          Simulated capital only. Full contract lots; no real orders.
        </small>
      </form>
    );
  }
  const marketToolbar = (
    <div className="toolbar market-toolbar">
      <select
        aria-label="Instrument"
        value={instrumentId}
        onChange={(e) => setInstrumentId(e.target.value)}
      >
        <option value="">Select instrument</option>
        {instruments.map((i) => (
          <option key={i.id} value={i.id}>
            {i.symbol}
          </option>
        ))}
      </select>
      <div className="segmented">
        {["1m", "5m", "15m", "1h", "1d"].map((t) => (
          <button
            key={t}
            className={tf === t ? "selected" : ""}
            onClick={() => setTf(t)}
          >
            {t}
          </button>
        ))}
      </div>
      <select
        aria-label="All timeframes"
        value={tf}
        onChange={(e) => setTf(e.target.value)}
      >
        {["1m", "3m", "5m", "10m", "15m", "30m", "1h", "1d"].map((t) => (
          <option key={t}>{t}</option>
        ))}
      </select>
      <span className="tag">
        {snapshot?.mode === "REPLAY" ? "REPLAY DATA" : "IMPORTED DATA"}
      </span>
    </div>
  );

  return (
    <div className="app-shell" aria-busy={busy}>
      <aside>
        <div className="brand">
          <div className="brand-icon">
            <BarChart3 size={22} />
          </div>
          <div>
            PaperLab<small>NSE RESEARCH WORKSPACE</small>
          </div>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {pages.map(([name, Icon]) => (
            <button
              key={name}
              onClick={() => setPage(name)}
              className={page === name ? "active" : ""}
            >
              <Icon size={17} />
              <span>{name}</span>
              {page === name && <ChevronRight size={14} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <ShieldCheck size={18} />
          <div>
            Paper execution only<small>No broker order access</small>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header>
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <strong>{page}</strong>
          </div>
          <div className="header-right">
            <span className="paper-badge">PAPER TRADING</span>
            <button
              className="icon"
              title="Refresh workspace"
              onClick={() =>
                act(async () => {
                  await refresh();
                  await refreshAccount();
                  await refreshMarket();
                })
              }
              disabled={busy}
            >
              <RefreshCw size={17} className={busy ? "spinning" : ""} />
            </button>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">SIMULATED CAPITAL / NO REAL ORDERS</div>
              <h1>{page}</h1>
            </div>
            {page !== "F&O Auto" && <div className="account-selector">
              <select
                aria-label="Paper account"
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
              >
                <option value="">No account selected</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} / {a.mode}
                  </option>
                ))}
              </select>
              <button
                className="icon"
                title="Create new paper account"
                onClick={() => setModal(true)}
              >
                <Plus size={18} />
              </button>
            </div>}
          </div>
          {error && (
            <div className="error" role="alert">
              <span>{error}</span>
              <button
                className="icon"
                title="Dismiss error"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {snapshot?.halted === 1 && (
            <div className="error">{snapshot.halt_reason}</div>
          )}
          {!health && (
            <div className="notice">
              {busy
                ? "Connecting to local backend..."
                : "Backend unavailable. Start the local server and refresh."}
            </div>
          )}
          {page === "F&O Auto" && <FnoAutoPage refresh={refresh} onAccount={setAccountId} />}
          {page === "Auto Paper" && <AutoPaperPage datasets={datasets} refresh={refresh} />}
          {page === "Dashboard" && (
            <>
              {accountMetrics()}
              <div className="workspace-grid">
                <section className="chart-section">
                  <div className="section-head">
                    <h2>Market overview</h2>
                    <span className="tag">
                      {market.regime?.regime ?? "AWAITING DATA"}
                    </span>
                  </div>
                  {marketToolbar}
                  <CandleChart rows={market.candles} />
                  <div className="chart-footer">
                    <span>
                      {activeInst?.symbol ?? "No instrument"} / Asia/Kolkata
                    </span>
                    <span>{market.candles.length} visible candles</span>
                  </div>
                </section>
                <section className="context-panel">
                  <div className="panel-title">
                    <Gauge size={18} />
                    <h3>Research context</h3>
                  </div>
                  <h4>{market.regime?.regime ?? "No regime available"}</h4>
                  <p>
                    {market.regime?.reasons?.join(". ") ??
                      "A regime appears after sufficient imported candle history."}
                  </p>
                  <hr />
                  <h4>Account integrity</h4>
                  <p className="integrity">
                    {snapshot?.reconciliation?.ok
                      ? "Ledger reconciled"
                      : snapshot
                        ? "Reconciliation failed"
                        : "No paper account"}
                  </p>
                  <dl>
                    <dt>Data mode</dt>
                    <dd>{health?.mode ?? "--"}</dd>
                    <dt>Broker orders</dt>
                    <dd>Never enabled</dd>
                    <dt>Virtual funding</dt>
                    <dd>No auto top-ups</dd>
                    <dt>Upstox</dt>
                    <dd>
                      {health?.upstox_enabled
                        ? "Enabled, data only"
                        : "Disconnected"}
                    </dd>
                  </dl>
                  <button onClick={() => setPage("Data Management")}>
                    Manage datasets <ChevronRight size={15} />
                  </button>
                </section>
              </div>
              <div className="section-head">
                <h3>Open positions</h3>
                <button
                  className="quiet"
                  onClick={() => setPage("Paper Portfolio")}
                >
                  View portfolio <ChevronRight size={14} />
                </button>
              </div>
              {positionTable()}
            </>
          )}
          {page === "Data Management" && (
            <DataPage
              instruments={instruments}
              datasets={datasets}
              refresh={refresh}
              act={act}
            />
          )}
          {["Backtesting", "Historical Research"].includes(page) && (
            <ResearchPage
              key={page}
              datasets={datasets}
              instruments={instruments}
              strategies={strategies}
              act={act}
              research={page === "Historical Research"}
            />
          )}
          {page === "Activity & Automation" && (
            <ActivityPage
              act={act}
              accountId={accountId}
              instruments={instruments}
              datasets={datasets}
              strategies={strategies}
              snapshot={snapshot}
              refreshAccount={refreshAccount}
            />
          )}
          {page === "Options / F&O" && (
            <OptionsPage
              act={act}
              accountId={accountId}
              instruments={instruments}
              snapshot={snapshot}
              refreshAccount={refreshAccount}
            />
          )}
          {page === "Historical Replay" && (
            <>
              {accountMetrics()}
              <div className="replay-strip">
                <div>
                  <span className="tag">CANDLE REPLAY</span>
                  <strong>
                    {snapshot?.clock ? when(snapshot.clock) : "Not started"}
                  </strong>
                  <small>
                    {selectedDataset?.name ??
                      "Create a replay account with a dataset"}
                  </small>
                </div>
                <button
                  className="primary"
                  disabled={
                    snapshot?.mode !== "REPLAY" || busy || snapshot?.halted
                  }
                  onClick={step}
                >
                  <SkipForward size={17} /> Next candle
                </button>
                <button
                  disabled={snapshot?.mode !== "REPLAY" || snapshot?.halted}
                  onClick={() => setPlaying(!playing)}
                >
                  {playing ? "Pause replay" : "Play · 1 candle/s"}
                </button>
              </div>
              {selectedDataset && tf !== selectedDataset.timeframe && (
                <div className="notice">
                  Dataset interval is {selectedDataset.timeframe}.
                  <button
                    className="quiet"
                    onClick={() => setTf(selectedDataset.timeframe)}
                  >
                    Use dataset interval
                  </button>
                </div>
              )}
              <div className="workspace-grid">
                <section>
                  {marketToolbar}
                  <CandleChart rows={market.candles} />
                </section>
                {orderForm()}
              </div>
              <div className="notice subtle">
                Orders fill at the next recorded open, subject to risk checks.
                Protective stops are checked at candle opens, not within bars.
              </div>
              {orderTable()}
            </>
          )}
          {page === "Live Paper Trading" && (
            <>
              <div className="notice">
                Live data requires independently approved usage rights.
                Education mode cannot fetch live quotes. REST polling is manual;
                no background or tick-level execution is running.
              </div>
              {accountMetrics()}
              {marketToolbar}
              <div className="toolbar">
                <button
                  disabled={snapshot?.mode !== "LIVE" || !instrumentId || busy}
                  onClick={() =>
                    act(async () => {
                      const r = await api(
                        "/accounts/" +
                          accountId +
                          "/live/poll?instrument_id=" +
                          instrumentId,
                        "POST",
                      );
                      await refreshAccount();
                      setSnapshot(r.snapshot);
                    })
                  }
                >
                  <RefreshCw size={16} /> Poll market data & process paper
                  orders
                </button>
              </div>
              <div className="two-column">
                {orderForm()}
                <section>
                  <h3>Paper positions</h3>
                  {positionTable()}
                </section>
              </div>
              {orderTable()}
            </>
          )}
          {page === "Paper Portfolio" && (
            <>
              {accountMetrics()}
              <h3>Positions</h3>
              {positionTable()}
              <h3 className="space-top">Orders</h3>
              {orderTable()}
            </>
          )}
          {page === "Strategies" && (
            <>
              <div className="notice subtle">
                Deterministic long-side research rules. Signals do not submit
                orders automatically.
              </div>
              {marketToolbar}
              <Table
                rows={strategies}
                columns={[
                  { key: "name", label: "Strategy" },
                  { key: "description", label: "Rules" },
                  { key: "direction", label: "Direction" },
                ]}
              />
              <h3 className="space-top">Current research signals</h3>
              <Table
                rows={market.signals ?? []}
                columns={[
                  { key: "strategy", label: "Strategy" },
                  { key: "direction", label: "Direction" },
                  { key: "entry_price", label: "Observed close (INR)" },
                  { key: "stop_loss", label: "Model stop" },
                  { key: "target", label: "Model target" },
                  { key: "reason", label: "Reason" },
                ]}
                empty="No qualifying signals in the visible data"
              />
            </>
          )}
          {page === "Account Ledger" && (
            <>
              {accountMetrics()}
              <div className="section-head">
                <h2>Immutable event ledger</h2>
                {accountId && (
                  <a
                    className="button"
                    href={"/api/accounts/" + accountId + "/ledger.csv"}
                    download
                  >
                    <Download size={16} /> Export CSV
                  </a>
                )}
              </div>
              <Table
                rows={ledger}
                columns={[
                  {
                    key: "timestamp",
                    label: "Timestamp",
                    render: (r) => when(r.timestamp),
                  },
                  { key: "event_type", label: "Event" },
                  { key: "symbol", label: "Symbol" },
                  { key: "quantity", label: "Qty" },
                  {
                    key: "gross_amount",
                    label: "Notional",
                    render: (r) => rupees(r.gross_amount),
                  },
                  {
                    key: "fees",
                    label: "Costs",
                    render: (r) => rupees(r.fees),
                  },
                  {
                    key: "realized_pnl",
                    label: "Realized P&L",
                    render: (r) => rupees(r.realized_pnl),
                  },
                  {
                    key: "balance_after",
                    label: "Cash after",
                    render: (r) => rupees(r.balance_after),
                  },
                  { key: "source", label: "Evidence" },
                ]}
                empty="Create an account to start its immutable ledger"
              />
              <details>
                <summary>Hash-chain evidence</summary>
                <pre>
                  {JSON.stringify(
                    ledger.map((e) => ({
                      event_id: e.event_id,
                      prev_hash: e.prev_hash,
                      event_hash: e.event_hash,
                    })),
                    null,
                    2,
                  )}
                </pre>
              </details>
            </>
          )}
          {page === "Trade Journal" && (
            <>
              <h2>Closed paper trades</h2>
              <Table
                rows={journal}
                columns={[
                  { key: "symbol", label: "Instrument" },
                  {
                    key: "entry_time",
                    label: "Entry",
                    render: (r) => when(r.entry_time),
                  },
                  {
                    key: "exit_time",
                    label: "Exit",
                    render: (r) => when(r.exit_time),
                  },
                  { key: "strategy", label: "Strategy" },
                  { key: "quantity", label: "Qty" },
                  {
                    key: "net_pnl",
                    label: "Net P&L",
                    render: (r) => rupees(r.net_pnl),
                  },
                  {
                    key: "notes",
                    label: "Notes",
                    render: (r) => (
                      <input
                        aria-label={"Notes for trade " + r.id}
                        defaultValue={r.notes}
                        onBlur={(e) => {
                          const notes = e.target.value;
                          if (notes !== r.notes)
                            act(async () => {
                              await api("/journal/" + r.id, "PATCH", {
                                notes,
                                tags: r.tags,
                              });
                              await refreshAccount();
                            });
                        }}
                      />
                    ),
                  },
                ]}
                empty="No closed paper trades yet"
              />
            </>
          )}
          {page === "Risk Dashboard" && (
            <>
              {accountMetrics()}
              <div className="section-head">
                <h2>Risk controls & events</h2>
                <button
                  className="danger"
                  disabled={!accountId || snapshot?.halted}
                  onClick={() => {
                    if (
                      confirm(
                        "Permanently halt this experiment and cancel pending paper orders? Open positions remain frozen.",
                      )
                    )
                      act(async () => {
                        await api("/accounts/" + accountId + "/halt", "POST");
                        await refreshAccount();
                      });
                  }}
                >
                  <ShieldOff size={16} /> Halt experiment
                </button>
              </div>
              {snapshot && (
                <div className="risk-settings">
                  {Object.entries(JSON.parse(snapshot.risk_json)).map(
                    ([key, value]) => (
                      <div key={key}>
                        <span>{key.replaceAll("_", " ")}</span>
                        <strong>{String(value)}</strong>
                      </div>
                    ),
                  )}
                </div>
              )}
              <Table
                rows={risk}
                columns={[
                  {
                    key: "timestamp",
                    label: "When",
                    render: (r) => when(r.timestamp),
                  },
                  { key: "reason", label: "Risk event" },
                ]}
                empty="No risk rejections recorded"
              />
              <h3>Alerts</h3>
              <Table
                rows={alerts}
                columns={[
                  { key: "kind", label: "Type" },
                  { key: "message", label: "Message" },
                  {
                    key: "acknowledged",
                    label: "Status",
                    render: (r) =>
                      r.acknowledged ? (
                        "Acknowledged"
                      ) : (
                        <button
                          onClick={() =>
                            act(async () => {
                              await api(
                                "/alerts/" + r.id + "/acknowledge",
                                "POST",
                              );
                              await refreshAccount();
                            })
                          }
                        >
                          Acknowledge
                        </button>
                      ),
                  },
                ]}
                empty="No alerts"
              />
            </>
          )}
          {page === "AI Analysis" && (
            <>
              <div className="section-head">
                <div>
                  <h2>Local research commentary</h2>
                  <p>
                    Optional Qwen. No trading tools, financial write access, or
                    paid API.
                  </p>
                </div>
                <button
                  onClick={() =>
                    act(async () =>
                      setAi(
                        await api(
                          "/ai/analyze" +
                            (accountId ? "?account_id=" + accountId : ""),
                          "POST",
                        ),
                      ),
                    )
                  }
                  disabled={busy}
                >
                  <BrainCircuit size={17} /> Analyze stored results
                </button>
              </div>
              {ai ? (
                <section className="ai-response">
                  <span className="tag">{ai.status}</span>
                  <p>{ai.response}</p>
                  <small>{ai.label}</small>
                </section>
              ) : (
                <Empty title="No AI analysis requested">
                  The platform works fully without a model.
                </Empty>
              )}
            </>
          )}
          {page === "Settings" && (
            <>
              <h2>Local configuration</h2>
              <button
                onClick={() =>
                  act(async () => {
                    const response = await fetch("/api/operations/backup", {
                      method: "POST",
                      headers: { "X-PaperLab": "local" },
                    });
                    if (!response.ok) throw new Error("Backup failed");
                    const url = URL.createObjectURL(await response.blob());
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = "paperlab-backup.sqlite";
                    link.click();
                    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
                  })
                }
              >
                Download verified database backup
              </button>
              <dl className="settings">
                <dt>Execution environment</dt>
                <dd>Internal PaperBroker / SQLite</dd>
                <dt>Data policy</dt>
                <dd>{health?.mode}</dd>
                <dt>Latest permitted data</dt>
                <dd>{when(health?.data_cutoff)}</dd>
                <dt>Upstox credentials</dt>
                <dd>
                  Backend environment only; never stored in this interface
                </dd>
                <dt>Market-data integration</dt>
                <dd>{health?.upstox_enabled ? "Enabled" : "Disabled"}</dd>
                <dt>Local Qwen</dt>
                <dd>{health?.ai_enabled ? "Enabled" : "Disabled"}</dd>
                <dt>Default funding</dt>
                <dd>INR 1,000 once, on explicit account creation</dd>
              </dl>
              <div className="notice">
                Education mode enforces a 30-day lag. Recent/live access is
                disabled unless the backend is configured for licensed-private
                use with a documented permission reference. An API token alone
                does not establish data-use rights.
              </div>
              <h3>Upstox historical import</h3>
              <form
                className="toolbar form-toolbar"
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  act(async () => {
                    await api("/upstox/history", "POST", {
                      instrument_id: Number(f.get("instrument")),
                      start: f.get("start"),
                      end: f.get("end"),
                      timeframe: tf,
                    });
                    await refresh();
                  });
                }}
              >
                <label>
                  Instrument
                  <select name="instrument" required>
                    <option value="">Select</option>
                    {instruments
                      .filter((i) => i.provider_key)
                      .map((i) => (
                        <option key={i.id} value={i.id}>
                          {i.symbol}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  From
                  <input type="date" name="start" required />
                </label>
                <label>
                  Through
                  <input type="date" name="end" required />
                </label>
                <button>Import {tf} history</button>
              </form>
              <p className="muted">
                See docs/REGULATORY.md and README.md for configuration, API
                sources and current limitations.
              </p>
            </>
          )}
        </main>
        <footer>
          <span className="connection-dot" /> Local research environment{" "}
          <span>Asia/Kolkata market timestamps</span>
          <span>v0.7.0 / Automated F&O</span>
        </footer>
      </div>
      {modal && (
        <Modal title="New paper experiment" close={() => setModal(false)}>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(async () => {
                const a = await api("/accounts", "POST", {
                  name: f.get("name"),
                  initial_capital: Number(f.get("capital")),
                  mode: createMode,
                  dataset_id:
                    createMode === "REPLAY" ? Number(f.get("dataset")) : null,
                  risk: {
                    risk_pct: Number(f.get("risk")),
                    daily_loss_pct: Number(f.get("daily")),
                  },
                  costs: JSON.parse(String(f.get("costs"))),
                });
                setAccountId(String(a.id));
                await refresh();
                setModal(false);
              });
            }}
          >
            <label className="full">
              Experiment name
              <input
                name="name"
                placeholder="My first research account"
                required
              />
            </label>
            <label>
              Initial capital (INR)
              <input
                type="number"
                min="1"
                step=".01"
                name="capital"
                defaultValue="1000"
                required
              />
            </label>
            <label>
              Mode
              <select
                value={createMode}
                onChange={(e) => setCreateMode(e.target.value)}
              >
                <option>REPLAY</option>
                <option disabled={health?.mode !== "licensed_private"}>
                  LIVE
                </option>
              </select>
            </label>
            {createMode === "REPLAY" && (
              <label className="full">
                Dataset
                <select name="dataset" required>
                  <option value="">Select imported dataset</option>
                  {datasets.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              Risk per trade (%)
              <input
                name="risk"
                type="number"
                min=".01"
                max="10"
                step=".01"
                defaultValue="1"
                required
              />
            </label>
            <label>
              Daily loss limit (%)
              <input
                name="daily"
                type="number"
                min=".01"
                max="100"
                step=".01"
                defaultValue="2"
                required
              />
            </label>
            <label className="full">
              Simulated cost configuration (JSON)
              <textarea
                name="costs"
                defaultValue={'{"brokerage_paise":0,"slippage_bps":0}'}
                required
              />
            </label>
            <p className="full muted">
              Initial capital is immutable. No deposits or resets. Zero-cost
              defaults are uncalibrated.
            </p>
            <button className="primary full" disabled={busy}>
              Create paper account
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}
