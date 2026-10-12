import { test, expect, type Page } from "@playwright/test";
const base = process.env.PAPERLAB_UI_URL ?? "http://127.0.0.1:8000";
const seed = () => ({
  selected_account: 42,
  runtime: {
    status: "RUNNING",
    message: "Managed account started automatically.",
    account_id: 42,
    configured_capital_paise: 10000000,
    capital_mismatch: false,
  },
  sessions: [
    { account_id: 7, enabled: 1, state: {} },
    {
      account_id: 42,
      label: "NIFTY 50 index F&O",
      underlying_id: 11,
      enabled: 1,
      state: {
        status: "OPEN",
        strategy: "Trend following",
        regime: "TRENDING",
        message: "Protecting the open call with managed stop and target.",
      },
    },
  ],
  balances: {
    initial_capital_paise: 10000000,
    cash_paise: 9497500,
    available_cash_paise: 9397500,
    premium_in_positions_paise: 500000,
    margin_reserved_paise: 0,
    pending_reserved_paise: 100000,
    costs_paise: 2500,
    realized_net_pnl_paise: -2500,
    unrealized_pnl_paise: 10000,
    equity_paise: 10007500,
  },
  snapshot: {
    basket_positions: [
      {
        id: 1,
        basket_id: 9,
        instrument_id: 22,
        contract: "NIFTY 24000 CALL",
        signed_quantity: 50,
        basis_price: 10000,
        mark_paise: 10200,
        stop_paise: 8500,
        target_paise: 12500,
        unrealized_pnl_paise: 10000,
        status: "OPEN",
        reason: "Trend entry confirmed",
      },
    ],
  },
  orders: [
    {
      id: 9,
      contract: "NIFTY 24000 CALL",
      quantity: 50,
      signal: "Trend following",
      status: "OPEN",
      created_at: "2026-10-12T04:30:00Z",
      entry_paise: 10000,
      managed_stop: 8500,
      target_paise: 12500,
      reason: "Trend entry confirmed",
    },
  ],
  contracts: [{ id: 22, kind: "CALL" }],
  events: [],
  stream: { state: "CONNECTED", last_message: "2026-10-12T05:30:00Z" },
  agent_team: {
    messages: [
      {
        role: "Market observer",
        status: "READY",
        message: "Trend confirmed on closed bars.",
        timestamp: "2026-10-12T05:30:00Z",
        payload: {},
      },
      {
        role: "Risk check",
        status: "PASSED",
        message: "Premium and costs fit available cash.",
        timestamp: "2026-10-12T05:30:00Z",
        payload: {},
      },
    ],
    reviews: [
      {
        strategy: "Trend following",
        classification: "negative_gross_with_fees",
        net_pnl_paise: -2500,
        regime: "TREND",
        evidence_status: "ledger_backed",
        timestamp: "2026-10-12T05:30:00Z",
      },
    ],
    candidates: [{ contract: "NIFTY 24000 CALL", status: "ACTIVE" }],
  },
});
async function mock(page: Page, initial: any = seed()) {
  let data = initial,
    statusCalls = 0;
  const posts: string[] = [],
    chartIds: string[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST") {
      posts.push(url.pathname);
      if (url.pathname.endsWith("/pause") || url.pathname.endsWith("/close"))
        data = {
          ...data,
          sessions: data.sessions.map((s: any) =>
            s.account_id === 42 ? { ...s, enabled: 0 } : s,
          ),
        };
      if (url.pathname.endsWith("/start"))
        data = {
          ...data,
          sessions: data.sessions.map((s: any) =>
            s.account_id === 42 ? { ...s, enabled: 1 } : s,
          ),
        };
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/api/fno-auto/chart") {
      chartIds.push(url.searchParams.get("instrument_id")!);
      expect(url.searchParams.get("account_id")).toBe("42");
      const contract = url.searchParams.get("instrument_id") === "22",
        price = contract ? 10000 : 2400000;
      const bars = Array.from({ length: 65 }, (_, i) => ({
        timestamp: new Date(Date.UTC(2026, 9, 12, 4, 30 + i)).toISOString(),
        open: price + i * 10,
        close: price + i * 10 + 30,
        high: price + i * 10 + 70,
        low: price + i * 10 - 30,
        volume: 100 + i,
      }));
      return route.fulfill({
        json: {
          instrument_id: contract ? 22 : 11,
          label: contract ? "NIFTY 24000 CALL" : "NIFTY 50",
          bars,
          forming: {
            ...bars[64],
            timestamp: "2026-10-12T05:35:00Z",
            close: price + 710,
          },
          levels: { entry_paise: 10000, stop_paise: 8500, target_paise: 12500 },
          source: "SYNTHETIC UI TEST",
        },
      });
    }
    if (url.pathname === "/api/fno-auto") {
      statusCalls++;
      return route.fulfill({ json: data });
    }
    return route.fulfill({
      status: 404,
      json: { detail: "Unexpected API request" },
    });
  });
  return { posts, chartIds, calls: () => statusCalls };
}
test("focused live workspace, balances, real chart and view-only contract selection", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const api = await mock(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base);
  await expect(
    page.getByRole("heading", { name: "Live F&O workspace" }),
  ).toBeVisible();
  await expect(page.locator("nav,input,form")).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: /CSV|replay|backtest|setup|create|settings|dashboard/i,
    }),
  ).toHaveCount(0);
  const balance = page.getByRole("region", { name: "Account balance" });
  await expect(
    balance
      .locator(".lf-metrics > div")
      .filter({ hasText: "Cash after premium & fees" }),
  ).toContainText("₹94,975.00");
  await expect(
    balance.locator(".lf-metrics > div").filter({ hasText: "Available cash" }),
  ).toContainText("₹93,975.00");
  await expect(balance.locator(".lf-balance-foot")).toContainText(
    "Pending reserves ₹1,000.00",
  );
  await expect(balance).toContainText("₹5,000.00");
  await expect(
    page.getByRole("region", { name: "Open F&O positions" }),
  ).toContainText("NIFTY 24000 CALL");
  await expect(
    page.getByRole("region", { name: "F&O orders and fills" }),
  ).toContainText("FILLED / OPEN");
  await expect(
    page.getByText("Premium and costs fit available cash."),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: /NIFTY 50: candlesticks/ }),
  ).toBeVisible();
  await expect(page.locator(".lf-chart canvas")).toHaveCount(1);
  expect(api.chartIds[0]).toBe("11");
  await page.getByText("Strategy reviews · 1", { exact: true }).click();
  await expect(
    page.getByText("negative gross with fees", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".lf-review").filter({ hasText: "negative gross with fees" }),
  ).toContainText("-₹25.00");
  await expect.poll(() => api.chartIds.length).toBeGreaterThan(1);
  await expect.poll(api.calls).toBeGreaterThan(1);
  await page.screenshot({
    path: info.outputPath("live-fno-desktop.png"),
    fullPage: true,
  });
  await info.attach("desktop", {
    path: info.outputPath("live-fno-desktop.png"),
    contentType: "image/png",
  });
  await page.getByLabel("Chart instrument").selectOption("22");
  await expect(
    page.getByRole("img", { name: /Entry, SL and target levels/ }),
  ).toBeVisible();
  expect(api.chartIds).toContain("22");
  expect(api.posts).toEqual([]);
  await page.screenshot({
    path: info.outputPath("live-fno-contract.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
test("emergency controls target selected account and resume correctly", async ({
  page,
}) => {
  const api = await mock(page);
  await page.goto(base);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeEnabled();
  expect(api.posts).toEqual(["/api/fno-auto/42/pause"]);
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Close paper position & pause", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "Watch the position for fill confirmation",
  );
  expect(api.posts).toEqual([
    "/api/fno-auto/42/pause",
    "/api/fno-auto/42/start",
    "/api/fno-auto/42/close",
  ]);
});
test("empty configuration-blocked runtime needs no setup form", async ({
  page,
}, info) => {
  await mock(page, {
    runtime: {
      status: "CONFIGURATION_REQUIRED",
      message: "Configured capital is missing.",
      account_id: null,
    },
    sessions: [],
    selected_account: null,
    snapshot: null,
    balances: {},
    orders: [],
    agent_team: { messages: [] },
  });
  await page.goto(base);
  await expect(page.getByText("Configured capital is missing.")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Close paper position & pause" }),
  ).toBeDisabled();
  await expect(
    page.getByText("No open paper positions.", { exact: false }),
  ).toBeVisible();
  await expect(page.locator("nav,input,form")).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("live-fno-blocked.png"),
    fullPage: true,
  });
});
test("capital mismatch blocks resume but preserves emergency exit", async ({
  page,
}) => {
  const data = seed();
  data.sessions[1].enabled = 0;
  data.runtime.capital_mismatch = true;
  await mock(page, data);
  await page.goto(base);
  await expect(
    page.getByText("Configured capital differs from this account.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Close paper position & pause" }),
  ).toBeEnabled();
});
test("mobile has no page overflow and keeps positions accessible", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await mock(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base);
  await expect(page.locator(".lf-chart canvas")).toHaveCount(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await expect(
    page.getByRole("region", { name: "Open F&O positions" }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("live-fno-mobile.png"),
    fullPage: true,
  });
  await info.attach("mobile", {
    path: info.outputPath("live-fno-mobile.png"),
    contentType: "image/png",
  });
  expect(errors).toEqual([]);
});
test("status failures are explicit and do not invent balances", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 503, json: { detail: "Backend unavailable" } }),
  );
  await page.goto(base);
  await expect(page.getByRole("alert")).toContainText("Backend unavailable");
  await expect(
    page.getByRole("region", { name: "Account balance" }),
  ).toContainText("Unavailable");
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeDisabled();
});

test("risk waiting reason remains visible and chart failures are isolated", async ({
  page,
}) => {
  const data = seed();
  data.sessions[1].state.message =
    "Full premium exceeds the 1% risk budget. Waiting for an affordable lot.";
  await mock(page, data);
  await page.route("**/api/fno-auto/chart?**", (route) =>
    route.fulfill({ status: 503, json: { detail: "Fresh bars unavailable" } }),
  );
  await page.goto(base);
  await expect(
    page.getByText(
      "Full premium exceeds the 1% risk budget. Waiting for an affordable lot.",
    ),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Fresh bars unavailable");
  await expect(
    page.getByRole("region", { name: "Account balance" }),
  ).toContainText("₹94,975.00");
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
});
test("failed pause remains visible and does not claim success", async ({
  page,
}) => {
  await mock(page);
  await page.route("**/api/fno-auto/42/pause", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Pause could not be completed" },
    }),
  );
  await page.goto(base);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Pause could not be completed",
  );
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
  await expect(page.getByRole("status")).toHaveCount(0);
});
