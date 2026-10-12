import { useEffect, useState } from "react";
import { api, rupees, when } from "../api";
import { Table } from "../components";
import { LearningPanel } from "./Learning";

export function ActivityPage({
  act,
  accountId,
  instruments,
  datasets,
  strategies,
  snapshot,
  refreshAccount,
}: any) {
  const [stream, setStream] = useState<any>({ quotes: [] }),
    [events, setEvents] = useState<any[]>([]),
    [rules, setRules] = useState<any[]>([]),
    [jobs, setJobs] = useState<any[]>([]),
    [orders, setOrders] = useState<any[]>([]),
    [decisions, setDecisions] = useState<any[]>([]),
    [deliveries, setDeliveries] = useState<any[]>([]);
  const [driver, setDriver] = useState<any>({ state: "STOPPED" }),
    [baskets, setBaskets] = useState<any[]>([]);
  const [learning, setLearning] = useState<any>({ jobs: [], profiles: [] });
  const [strategyMode, setStrategyMode] = useState("adaptive");
  const [selected, setSelected] = useState<number[]>([]),
    [category, setCategory] = useState(""),
    [tab, setTab] = useState("Timeline"),
    [pollError, setPollError] = useState(""),
    [tickets, setTickets] = useState<any[]>([]),
    [result, setResult] = useState<any>(null),
    [enableFills, setEnableFills] = useState(false);
  async function load() {
    const values = await Promise.all([
      api("/stream"),
      api(
        "/activities?" +
          new URLSearchParams({
            ...(accountId ? { account_id: accountId } : {}),
            ...(category ? { category } : {}),
          }),
      ),
      api("/automation"),
      api("/basket-backtests"),
      ...(accountId
        ? [
            api(`/accounts/${accountId}/orders`),
            api(`/accounts/${accountId}/decisions`),
            api(`/accounts/${accountId}/deliveries`),
            api(`/accounts/${accountId}/baskets`),
          ]
        : []),
      api("/learning"),
      api("/replay-worker"),
    ]);
    setStream(values[0]);
    setEvents(values[1]);
    setRules(values[2]);
    setJobs(values[3]);
    setOrders(accountId ? values[4] : []);
    setDecisions(accountId ? values[5] : []);
    setDeliveries(accountId ? values[6] : []);
    setBaskets(accountId ? values[7] : []);
    setDriver(values[values.length - 1]);
    setLearning(values[values.length - 2]);
    setPollError("");
  }
  useEffect(() => {
    let disposed = false,
      timer: number;
    const poll = async () => {
      try {
        if (!disposed) await load();
      } catch (e) {
        if (!disposed) setPollError(String(e));
      } finally {
        if (!disposed) timer = window.setTimeout(poll, 2000);
      }
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [accountId, category]);
  const action = (fn: () => Promise<any>) =>
    act(async () => {
      await fn();
      await load();
      await refreshAccount();
    });
  const instrumentSelect = (name: string, optional = false) => (
    <label>
      Contract {name}
      <select name={name} aria-label={"Contract " + name} required={!optional}>
        <option value="">Select contract</option>
        {instruments.map((i: any) => (
          <option key={i.id} value={i.id}>
            {i.symbol} · {i.kind} · lot {i.lot_size}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <div className="activity-page">
      <div className="section-head">
        <div>
          <h2>Activity & automation</h2>
          <p>
            Live feed, paper execution, delivery and research. Updates every two
            seconds.
          </p>
        </div>
        <span className="tag">PAPER ONLY</span>
      </div>
      {pollError && (
        <p className="error">Activity refresh failed: {pollError}</p>
      )}
      <div className="metrics">
        <div className="metric">
          <small>Stream</small>
          <strong>{stream.state ?? "Loading"}</strong>
        </div>
        <div className="metric">
          <small>Accepted / rejected updates</small>
          <strong>
            {stream.accepted ?? 0} / {stream.rejected ?? 0}
          </strong>
        </div>
        <div className="metric">
          <small>Reconnects</small>
          <strong>{stream.reconnects ?? 0}</strong>
        </div>
        <div className="metric">
          <small>Last message</small>
          <strong>{when(stream.last_received)}</strong>
        </div>
      </div>
      <section>
        <h3>Live WebSocket feed</h3>
        <p>
          Requires licensed-private mode, a valid backend Upstox token and
          registered provider keys. Restarting the app leaves streaming and
          automation stopped.
        </p>
        <div className="stream-contracts">
          {instruments
            .filter((i: any) => i.provider_key)
            .map((i: any) => (
              <label key={i.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(i.id)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, i.id]
                        : selected.filter((x) => x !== i.id),
                    )
                  }
                />
                {i.symbol}
              </label>
            ))}
        </div>
        {!instruments.some((i: any) => i.provider_key) && (
          <p className="notice">
            Register instruments with their Upstox provider keys in Data
            Management.
          </p>
        )}
        <label className="inline-check">
          <input
            type="checkbox"
            checked={enableFills}
            onChange={(e) => setEnableFills(e.target.checked)}
            disabled={snapshot?.mode !== "LIVE"}
          />
          Process queued paper orders and protective exits for the selected LIVE
          account
        </label>
        <div className="toolbar">
          <button
            className="primary"
            disabled={
              !selected.length ||
              ["CONNECTED", "CONNECTING", "RECONNECTING", "STOPPING"].includes(
                stream.state,
              )
            }
            onClick={() =>
              action(() =>
                api("/stream/start", "POST", {
                  instrument_ids: selected,
                  account_ids:
                    enableFills && accountId ? [Number(accountId)] : [],
                }),
              )
            }
          >
            Start stream
          </button>
          <button onClick={() => action(() => api("/stream/stop", "POST"))}>
            Stop stream
          </button>
        </div>
        {stream.error && <p className="notice">{stream.error}</p>}
        <Table
          rows={stream.quotes}
          columns={[
            { key: "instrument_key", label: "Instrument" },
            {
              key: "price_paise",
              label: "Observed price",
              render: (r: any) => rupees(r.price_paise),
            },
            {
              key: "timestamp",
              label: "Last trade",
              render: (r: any) => when(r.timestamp),
            },
            {
              key: "stale",
              label: "Freshness",
              render: (r: any) => (r.stale ? "STALE — no execution" : "Fresh"),
            },
          ]}
        />
      </section>
      <LearningPanel
        accountId={accountId}
        datasets={datasets}
        learning={learning}
        act={action}
      />
      <section aria-label="Automatic strategy execution">
        <h3>Automatic strategy execution</h3>
        {snapshot?.mode === "REPLAY" && (
          <div className="notice">
            <p>
              Background replay: {driver.state} · Account{" "}
              {driver.account_id ?? "—"} · {when(driver.snapshot?.clock)} ·
              Equity {rupees(driver.snapshot?.equity_paise)}
            </p>
            <button
              disabled={driver.state === "RUNNING"}
              onClick={() =>
                action(() =>
                  api("/replay-worker/start", "POST", {
                    account_id: Number(accountId),
                    interval_seconds: 1,
                  }),
                )
              }
            >
              Start background replay
            </button>
            <button
              onClick={() => action(() => api("/replay-worker/stop", "POST"))}
            >
              Pause background replay
            </button>
            {driver.error && <p>{driver.error}</p>}
          </div>
        )}
        {snapshot?.mode === "LIVE" && (
          <p>
            Stream account equity:{" "}
            {rupees(
              stream.snapshots?.find((a: any) => String(a.id) === accountId)
                ?.equity_paise,
            )}
          </p>
        )}
        <p>
          One long strategy per account. Replay evaluates completed candles and
          queues next-open orders. Live mode uses observed one-minute stream
          bars, requires 51 completed bars for a fixed strategy, and fills on
          subsequent fresh updates. Missing bars and reconnects require warm-up
          again.
        </p>
        <p>
          Adaptive mode chooses among the available strategies after 200
          completed bars. It reassesses every 20 bars using up to 300 past bars,
          requires at least 3 closed simulation trades and positive results, and
          may choose to wait. A closed loss pauses entries for 10 bars and
          excludes that strategy for 100 bars. Position limits can decrease
          after losses; a 5% equity drawdown permanently stops adaptive entries
          for that account. Historical rankings do not predict profits.
        </p>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            action(() =>
              api(`/accounts/${accountId}/automation`, "POST", {
                instrument_id: Number(f.get("A")),
                strategy: f.get("strategy"),
                max_quantity: Number(f.get("quantity")),
                atr_stop:
                  strategyMode === "learned_daily" ? 1.5 : Number(f.get("atr")),
                learning_profile_id:
                  strategyMode === "learned_daily"
                    ? Number(f.get("profile"))
                    : null,
              }),
            );
          }}
        >
          {instrumentSelect("A")}
          <label>
            Strategy
            <select
              name="strategy"
              value={strategyMode}
              onChange={(e) => setStrategyMode(e.target.value)}
            >
              <option value="adaptive">
                Adaptive — select strategies automatically
              </option>
              <option value="learned_daily">
                CSV learned daily — validated trend settings
              </option>
              {strategies.map((s: any) => (
                <option key={s.name ?? s.id} value={s.name ?? s.id}>
                  {s.name ?? s.id}
                </option>
              ))}
            </select>
          </label>
          {strategyMode === "learned_daily" && (
            <label>
              Validated daily profile
              <select name="profile" required>
                <option value="">Select passing profile</option>
                {learning.profiles
                  .filter((p: any) => p.approved)
                  .map((p: any) => (
                    <option key={p.id} value={p.id}>
                      #{p.id} · {p.ticker} · {p.isin}
                    </option>
                  ))}
              </select>
            </label>
          )}
          <label>
            Maximum units
            <input
              type="number"
              name="quantity"
              min="1"
              step="1"
              defaultValue="1"
              required
            />
          </label>
          <label>
            ATR stop multiplier
            <input
              type="number"
              name="atr"
              min=".1"
              max="20"
              step=".1"
              defaultValue="1.5"
              disabled={strategyMode === "learned_daily"}
              required
            />
          </label>
          <button className="primary" disabled={!accountId || snapshot?.halted}>
            Enable paper strategy
          </button>
          <button
            type="button"
            disabled={!accountId}
            onClick={() =>
              action(() => api(`/accounts/${accountId}/automation`, "DELETE"))
            }
          >
            Stop strategy & cancel its pending entries
          </button>
        </form>
        {strategyMode === "learned_daily" && (
          <p className="notice">
            Daily mode uses the profile's learned ATR settings. Live use
            requires a separately registered SPOT instrument with matching
            Upstox ISIN and current lot/tick metadata. Completed daily history
            refreshes automatically from Upstox; fresh stream prices execute
            paper orders. At most one entry per daily signal. Loss cooldowns
            count daily bars, not minutes. Daily data does not train intraday
            strategies.
          </p>
        )}
        <Table
          rows={rules.filter((r: any) => String(r.account_id) === accountId)}
          columns={[
            { key: "strategy", label: "Strategy" },
            {
              key: "enabled",
              label: "Status",
              render: (r: any) => (r.enabled ? "Enabled" : "Stopped"),
            },
            { key: "max_quantity", label: "Maximum units" },
          ]}
        />
        {rules
          .filter(
            (r: any) =>
              String(r.account_id) === accountId &&
              ["adaptive", "learned_daily"].includes(r.strategy),
          )
          .map((r: any) => (
            <div key={r.id} aria-label="Adaptive strategy monitor">
              <h4>Adaptive strategy monitor</h4>
              <p>
                <strong>
                  {r.adaptive?.selected || "No strategy selected"}
                </strong>{" "}
                · {r.adaptive?.status || "Waiting for completed market data"}
              </p>
              <p>{r.adaptive?.reason}</p>
              {r.adaptive?.trend && (
                <p>
                  Daily trend: <strong>{r.adaptive.trend.regime}</strong> ·{" "}
                  {r.adaptive.trend.reasons.join("; ")}
                </p>
              )}
              <p>
                Completed bars: {r.adaptive?.bars ?? 0} / 200 minimum ·
                Cooldown: {r.adaptive?.cooldown_remaining ?? 0} bars · Current
                unit cap: {r.adaptive?.quantity_cap ?? r.max_quantity} ·
                Drawdown: {(r.adaptive?.drawdown_pct ?? 0).toFixed(2)}%
              </p>
              {r.adaptive?.last_loss && (
                <p>
                  Last losing strategy: {r.adaptive.last_loss.strategy} · Net
                  P&amp;L: {rupees(r.adaptive.last_loss.net_pnl_paise)}
                </p>
              )}
              <Table
                rows={r.adaptive?.ranking ?? []}
                columns={[
                  { key: "strategy", label: "Candidate" },
                  { key: "trades", label: "Closed trades" },
                  {
                    key: "net_pnl_paise",
                    label: "Historical net P&L",
                    render: (x: any) => rupees(x.net_pnl_paise),
                  },
                  {
                    key: "drawdown_pct",
                    label: "Drawdown %",
                    render: (x: any) => x.drawdown_pct?.toFixed(2) ?? "—",
                  },
                  { key: "reason", label: "Assessment" },
                ]}
              />
            </div>
          ))}
      </section>
      <section>
        <h3>Physical delivery simulation</h3>
        <p>
          Import delivery evidence in Options / F&O. Cash and share obligations
          are netted within one basket. Insufficient funds or shares produce a
          blocked obligation; no cash is injected and no shares are invented.
        </p>
        <button
          disabled={!accountId || snapshot?.mode !== "REPLAY"}
          onClick={() =>
            action(() => api(`/accounts/${accountId}/delivery`, "POST"))
          }
        >
          Apply due physical deliveries
        </button>
      </section>
      <section>
        <h3>Multi-leg backtest</h3>
        <p>
          Schedule basket entries and exits after visible candle times. The same
          execution and risk engine runs against an isolated copy; your paper
          account is unchanged. Maximum 2,000 replay timestamps per run.
        </p>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            const legs = [
              {
                instrument_id: Number(f.get("A")),
                quantity: Number(f.get("qa")),
              },
            ];
            if (f.get("B"))
              legs.push({
                instrument_id: Number(f.get("B")),
                quantity: Number(f.get("qb")),
              });
            setTickets([
              ...tickets,
              {
                strategy: f.get("strategy"),
                legs,
                enter_after: f.get("enter"),
                exit_after: f.get("exit"),
                stop_loss: f.get("stop") ? Number(f.get("stop")) : null,
              },
            ]);
          }}
        >
          <label>
            Basket strategy
            <select name="strategy">
              {[
                "bull_call",
                "bear_put",
                "covered_call",
                "protective_put",
                "long_call",
                "long_put",
                "future",
              ].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          {instrumentSelect("A")}
          <label>
            Signed units A
            <input name="qa" type="number" step="1" defaultValue="1" required />
          </label>
          {instrumentSelect("B", true)}
          <label>
            Signed units B
            <input name="qb" type="number" step="1" defaultValue="-1" />
          </label>
          <label>
            Enter after (ISO with timezone)
            <input
              name="enter"
              placeholder="2024-01-02T09:20:00+05:30"
              required
            />
          </label>
          <label>
            Exit after (ISO with timezone)
            <input
              name="exit"
              placeholder="2024-01-02T10:20:00+05:30"
              required
            />
          </label>
          <label>
            Futures stop (INR)
            <input name="stop" type="number" step=".01" min=".01" />
          </label>
          <button>Add scheduled basket</button>
        </form>
        <Table
          rows={tickets.map((t, i) => ({ ...t, index: i }))}
          columns={[
            { key: "strategy", label: "Strategy" },
            { key: "enter_after", label: "Enter after" },
            { key: "exit_after", label: "Exit after" },
            {
              key: "remove",
              label: "",
              render: (r: any) => (
                <button
                  onClick={() =>
                    setTickets(tickets.filter((_, i) => i !== r.index))
                  }
                >
                  Remove
                </button>
              ),
            },
          ]}
        />
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            action(() =>
              api("/basket-backtests", "POST", {
                dataset_id: Number(f.get("dataset")),
                initial_capital: Number(f.get("capital")),
                risk: { risk_pct: Number(f.get("risk")) },
                costs: snapshot ? JSON.parse(snapshot.cost_json) : {},
                tickets,
                apply_delivery: f.get("delivery") === "on",
                apply_cash_settlement: f.get("cash") === "on",
              }),
            );
          }}
        >
          <label>
            Dataset
            <select name="dataset" required>
              <option value="">Select dataset</option>
              {datasets.map((d: any) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Initial INR
            <input
              name="capital"
              type="number"
              min="1"
              defaultValue="1000"
              required
            />
          </label>
          <label>
            Risk per basket (%)
            <input
              name="risk"
              type="number"
              min=".1"
              max="10"
              step=".1"
              defaultValue="1"
              required
            />
          </label>
          <label className="inline-check">
            <input name="delivery" type="checkbox" />
            Apply physical delivery evidence
          </label>
          <label className="inline-check">
            <input name="cash" type="checkbox" />
            Apply cash settlement evidence
          </label>
          <button disabled={!tickets.length} className="primary">
            Run isolated backtest
          </button>
        </form>
        <p className="muted">
          Costs use the selected account’s configuration, or zero when no
          account is selected. Configure costs before interpreting results.
        </p>
      </section>
      <section>
        <div className="toolbar">
          {[
            "Timeline",
            "Orders",
            "Baskets",
            "Decisions",
            "Deliveries",
            "Backtests",
          ].map((t) => (
            <button
              className={tab === t ? "primary" : ""}
              key={t}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>
        {tab === "Timeline" && (
          <>
            <label>
              Category
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                <option value="">All</option>
                {[
                  "stream",
                  "automation",
                  "execution",
                  "delivery",
                  "backtest",
                  "replay",
                ].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <Table
              rows={events}
              columns={[
                {
                  key: "timestamp",
                  label: "Time",
                  render: (r: any) => when(r.timestamp),
                },
                { key: "category", label: "Category" },
                { key: "action", label: "Activity" },
                { key: "details", label: "Details" },
              ]}
            />
          </>
        )}
        {tab === "Orders" && (
          <Table
            rows={orders}
            columns={[
              { key: "symbol", label: "Instrument" },
              { key: "side", label: "Side" },
              { key: "quantity", label: "Units" },
              { key: "status", label: "Status" },
              { key: "reason", label: "Reason" },
              {
                key: "price",
                label: "Fill",
                render: (r: any) => rupees(r.price),
              },
            ]}
          />
        )}
        {tab === "Baskets" && (
          <Table
            rows={baskets}
            columns={[
              { key: "id", label: "Basket" },
              { key: "strategy", label: "Strategy" },
              { key: "status", label: "Status" },
              { key: "reason", label: "Reason" },
            ]}
          />
        )}
        {tab === "Decisions" && (
          <Table
            rows={decisions}
            columns={[
              {
                key: "timestamp",
                label: "Bar time",
                render: (r: any) => when(r.timestamp),
              },
              { key: "outcome", label: "Decision" },
              { key: "details", label: "Rationale" },
            ]}
          />
        )}
        {tab === "Deliveries" && (
          <Table
            rows={deliveries}
            columns={[
              { key: "basket_id", label: "Basket" },
              {
                key: "timestamp",
                label: "Time",
                render: (r: any) => when(r.timestamp),
              },
              { key: "status", label: "Status" },
              { key: "details", label: "Obligations" },
            ]}
          />
        )}
        {tab === "Backtests" && (
          <Table
            rows={jobs}
            columns={[
              { key: "id", label: "Run" },
              { key: "status", label: "Status" },
              { key: "progress", label: "Progress %" },
              { key: "error", label: "Error" },
              {
                key: "actions",
                label: "Actions",
                render: (r: any) => (
                  <>
                    <button
                      onClick={() =>
                        action(async () =>
                          setResult(await api(`/basket-backtests/${r.id}`)),
                        )
                      }
                    >
                      View result
                    </button>
                    {["QUEUED", "RUNNING"].includes(r.status) && (
                      <button
                        onClick={() =>
                          action(() =>
                            api(`/basket-backtests/${r.id}`, "DELETE"),
                          )
                        }
                      >
                        Cancel
                      </button>
                    )}
                  </>
                ),
              },
            ]}
          />
        )}
        {result && (
          <div>
            <h3>
              Run {result.id} · {result.status}
            </h3>
            <p>
              Final equity: {rupees(result.result?.final_equity_paise)} · Net
              P&L: {rupees(result.result?.net_pnl_paise)} · Reconciliation:{" "}
              {result.result?.reconciliation?.ok ? "Passed" : "Unavailable"}
            </p>
            <p>{result.result?.assumptions ?? result.error}</p>
            <Table
              rows={result.result?.baskets ?? []}
              columns={[
                { key: "strategy", label: "Basket" },
                { key: "status", label: "Status" },
                { key: "reason", label: "Reason" },
              ]}
            />
            <button
              onClick={() => {
                const url = URL.createObjectURL(
                  new Blob([JSON.stringify(result, null, 2)], {
                    type: "application/json",
                  }),
                );
                const a = document.createElement("a");
                a.href = url;
                a.download = `basket-run-${result.id}.json`;
                a.click();
                setTimeout(() => URL.revokeObjectURL(url), 1000);
              }}
            >
              Download complete result & ledger
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
