import { useState } from "react";
import { api } from "../api";
import { Table } from "../components";
import { Derivatives } from "./Derivatives";

export function OptionsPage(props: any) {
  const { act } = props;
  const [result, setResult] = useState<any>(null),
    [chain, setChain] = useState<any>(null);
  return (
    <>
      <div className="section-head">
        <div>
          <h2>Options laboratory</h2>
          <p>
            Analytical estimates stay separate from actual option execution
            prices.
          </p>
        </div>
        <span className="tag">MODEL ANALYTICS</span>
      </div>
      <div className="two-column">
        <section>
          <h3>Greeks & implied volatility</h3>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(async () =>
                setResult(
                  await api("/options/greeks", "POST", {
                    spot: Number(f.get("spot")),
                    strike: Number(f.get("strike")),
                    years: Number(f.get("days")) / 365,
                    rate: Number(f.get("rate")) / 100,
                    vol: Number(f.get("vol")) / 100,
                    kind: f.get("kind"),
                    premium: f.get("premium") ? Number(f.get("premium")) : null,
                  }),
                ),
              );
            }}
          >
            {[
              ["spot", "Underlying scenario (INR)"],
              ["strike", "Strike (INR)"],
              ["days", "Days to expiry"],
              ["rate", "Risk-free rate (%)"],
              ["vol", "Volatility (%)"],
            ].map(([name, label]) => (
              <label key={name}>
                {label}
                <input
                  type="number"
                  step="any"
                  name={name}
                  required
                  min={name === "rate" ? "-100" : ".00001"}
                />
              </label>
            ))}
            <label>
              Option type
              <select name="kind">
                <option>CALL</option>
                <option>PUT</option>
              </select>
            </label>
            <label>
              Observed premium for IV (optional)
              <input type="number" min=".01" step="any" name="premium" />
            </label>
            <button className="primary">Calculate</button>
          </form>
          {result && (
            <>
              <p className="notice">{result.label}</p>
              <Table
                rows={Object.entries(result)
                  .filter(([k]) => k !== "label")
                  .map(([metric, value]) => ({ metric, value }))}
                columns={[
                  { key: "metric", label: "Metric" },
                  {
                    key: "value",
                    label: "Value",
                    render: (r) =>
                      typeof r.value === "number"
                        ? r.value.toFixed(6)
                        : r.value,
                  },
                ]}
              />
            </>
          )}
        </section>
        <section>
          <h3>Observed option chain</h3>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(async () =>
                setChain(
                  await api(
                    "/upstox/option-chain?instrument_key=" +
                      encodeURIComponent(String(f.get("key"))) +
                      "&expiry=" +
                      f.get("expiry"),
                  ),
                ),
              );
            }}
          >
            <label className="full">
              Underlying instrument key
              <input name="key" placeholder="NSE_INDEX|Nifty 50" required />
            </label>
            <label>
              Expiry
              <input name="expiry" type="date" required />
            </label>
            <button>Fetch chain</button>
          </form>
          <p className="muted">
            Requires licensed-private mode, documented data permission and an
            enabled Upstox token. No fabricated option history.
          </p>
          {chain && (
            <>
              <p>Snapshot received: {chain.received_at}</p>
              <Table
                rows={chain.rows.map((r: any) => ({
                  strike: r.strike_price,
                  call: r.call_options?.market_data?.ltp,
                  put: r.put_options?.market_data?.ltp,
                  call_oi: r.call_options?.market_data?.oi,
                  put_oi: r.put_options?.market_data?.oi,
                }))}
                columns={[
                  { key: "strike", label: "Strike" },
                  { key: "call", label: "Call premium" },
                  { key: "put", label: "Put premium" },
                  { key: "call_oi", label: "Call OI" },
                  { key: "put_oi", label: "Put OI" },
                ]}
              />
            </>
          )}
        </section>
      </div>
      <Derivatives {...props} />
    </>
  );
}
