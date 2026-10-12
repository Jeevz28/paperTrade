import { useEffect, useState } from "react";
import { api, rupees } from "../api";
import { Table } from "../components";

export function Derivatives({
  act,
  accountId,
  instruments,
  snapshot,
  refreshAccount,
}: any) {
  const [baskets, setBaskets] = useState<any[]>([]),
    [history, setHistory] = useState<any>(null),
    [report, setReport] = useState<any>(null);
  const load = async () => {
    setBaskets(accountId ? await api(`/accounts/${accountId}/baskets`) : []);
  };
  useEffect(() => {
    void act(load);
  }, [accountId, snapshot?.clock]);
  const contract = (name: string, required = true) => (
    <label>
      Contract {name}
      <select name={name} required={required}>
        <option value="">Select instrument</option>
        {instruments.map((i: any) => (
          <option value={i.id} key={i.id}>
            {i.symbol} · {i.kind} · lot {i.lot_size}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <>
      <section>
        <h3>Atomic replay execution</h3>
        <p>
          One basket per account. Positive quantity buys; negative quantity
          sells. All legs fill together at the next synchronized observed open.
          Futures require time-stamped margin evidence and a stop.
        </p>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            void act(async () => {
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
              await api(`/accounts/${accountId}/baskets`, "POST", {
                strategy: f.get("strategy"),
                legs,
                stop_loss: f.get("stop") ? Number(f.get("stop")) : null,
                request_key: crypto.randomUUID(),
              });
              await load();
            });
          }}
        >
          <label>
            Strategy
            <select name="strategy">
              {[
                "future",
                "long_call",
                "long_put",
                "bull_call",
                "bear_put",
                "covered_call",
                "protective_put",
              ].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          {contract("A")}
          <label>
            Signed quantity A
            <input name="qa" type="number" required step="1" defaultValue="1" />
          </label>
          {contract("B", false)}
          <label>
            Signed quantity B
            <input name="qb" type="number" step="1" defaultValue="-1" />
          </label>
          <label>
            Futures stop (INR)
            <input name="stop" type="number" min=".01" step=".01" />
          </label>
          <button
            className="primary"
            disabled={!accountId || snapshot?.mode !== "REPLAY"}
          >
            Queue basket
          </button>
        </form>
        <Table
          rows={baskets}
          columns={[
            { key: "id", label: "ID" },
            { key: "strategy", label: "Strategy" },
            { key: "status", label: "State" },
            { key: "reason", label: "Reason" },
            {
              key: "action",
              label: "Action",
              render: (r: any) => (
                <>
                  {r.status === "OPEN" && (
                    <button
                      onClick={() =>
                        act(async () => {
                          await api(
                            `/accounts/${accountId}/baskets/${r.id}/close`,
                            "POST",
                          );
                          await load();
                        })
                      }
                    >
                      Queue close
                    </button>
                  )}
                  {r.status === "PENDING" && (
                    <button
                      onClick={() =>
                        act(async () => {
                          await api(
                            `/accounts/${accountId}/baskets/${r.id}`,
                            "DELETE",
                          );
                          await load();
                        })
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
        <h3>
          Basket positions · reserved margin{" "}
          {rupees(snapshot?.used_margin_paise)}
        </h3>
        <Table
          rows={snapshot?.basket_positions ?? []}
          columns={[
            { key: "symbol", label: "Contract" },
            { key: "signed_quantity", label: "Quantity" },
            {
              key: "basis_price",
              label: "Basis",
              render: (r: any) => rupees(r.basis_price),
            },
            {
              key: "mark_paise",
              label: "Observed mark",
              render: (r: any) => rupees(r.mark_paise),
            },
            {
              key: "unrealized_pnl_paise",
              label: "P&L",
              render: (r: any) => rupees(r.unrealized_pnl_paise),
            },
          ]}
        />
        <button
          disabled={!accountId}
          onClick={() =>
            act(async () => {
              setReport(await api(`/accounts/${accountId}/settle`, "POST"));
              await refreshAccount();
              await load();
            })
          }
        >
          Apply published cash settlements
        </button>
        {report && (
          <p className="notice">
            Applied evidence:{" "}
            {report.applied_evidence.join(", ") ||
              "None available at this replay clock"}
          </p>
        )}
      </section>
      <section>
        <h3>Import point-in-time evidence</h3>
        <p>
          CSV schemas are in docs/EVIDENCE.md. Values use INR; IV uses decimal
          units. Physical delivery is applied from Activity & Automation.
          Calendar imports are reference records, not an execution gate.
        </p>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            const kind = f.get("kind");
            f.delete("kind");
            void act(async () => {
              await api(`/evidence/${kind}`, "POST", f);
            });
          }}
        >
          <label>
            Evidence type
            <select name="kind">
              {[
                "margin",
                "settlement",
                "delivery",
                "option_history",
                "calendar",
              ].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label>
            Source / publication reference
            <input name="source" required />
          </label>
          <label>
            CSV
            <input type="file" name="file" accept=".csv" required />
          </label>
          <button>Validate and import</button>
        </form>
      </section>
      <section>
        <h3>Observed IV & open interest history</h3>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            void act(async () =>
              setHistory(
                await api(
                  `/options/history?instrument_id=${f.get("A")}${accountId ? "&account_id=" + accountId : ""}`,
                ),
              ),
            );
          }}
        >
          {contract("A")}
          <button>Load visible history</button>
        </form>
        {history && (
          <>
            <p>
              IV percentile: {history.iv_percentile ?? "Unavailable"} · OI
              change: {history.oi_change ?? "Unavailable"}
            </p>
            <p>{history.reason}</p>
            <Table
              rows={history.rows}
              columns={[
                { key: "timestamp", label: "Timestamp" },
                { key: "iv", label: "IV (decimal)" },
                { key: "open_interest", label: "Open interest" },
              ]}
            />
          </>
        )}
      </section>
    </>
  );
}
