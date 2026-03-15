#!/usr/bin/env tsx
/**
 * ClawFarm Screenshot Automation
 *
 * Captures polished screenshots of the ClawFarm dashboard for README/website.
 * Mocks all API responses so no real backend is needed — just the Next.js dev server.
 *
 * Usage:
 *   cd scripts/screenshots && npm install && npx playwright install chromium
 *   # In another terminal: cd ../../frontend && npm run dev
 *   npx tsx capture.ts
 */

import { chromium, type BrowserContext, type Page, type Route } from "playwright";
import { buildRouteHandler, TERMINAL_CONTENT, type MockMode } from "./mock-data.js";
import { mkdirSync, existsSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = join(__dirname, "..", "..");
const OUTPUT_DIR = join(PROJECT_ROOT, "assets", "screenshots");
const BASE_URL = process.env.BASE_URL || "http://localhost:3000";

const VIEWPORTS = {
  desktop: { width: 1920, height: 1080 },
  mobile: { width: 393, height: 852 },
} as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function setupRoutes(page: Page, mode: MockMode) {
  const handler = buildRouteHandler(mode);

  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const result = handler(url);

    if (result) {
      await route.fulfill({
        status: result.status,
        contentType: "application/json",
        body: JSON.stringify(result.body),
      });
    } else {
      // Unmatched API call — return 404 to avoid hanging
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Not mocked" }),
      });
    }
  });

  // Block WebSocket upgrades for terminal (they'd fail on non-terminal pages)
  await page.route("**/api/bots/*/terminal", async (route: Route) => {
    await route.abort("connectionrefused");
  });
}

async function waitForContent(page: Page, timeout = 3000) {
  // Wait for SWR data to load and render
  await page.waitForTimeout(timeout);
  // Hide Next.js dev indicator
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
  // Wait for any animations to settle
  await page.waitForTimeout(500);
}

async function screenshot(page: Page, name: string) {
  const path = join(OUTPUT_DIR, `${name}.png`);
  await page.screenshot({ path, fullPage: false });
  console.log(`  -> ${name}.png`);
}

// ---------------------------------------------------------------------------
// Screen Captures
// ---------------------------------------------------------------------------

async function captureLogin(context: BrowserContext, viewport: keyof typeof VIEWPORTS) {
  console.log(`\n[login-${viewport}]`);
  const page = await context.newPage();
  await setupRoutes(page, "login");
  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
  await waitForContent(page, 1500);
  await screenshot(page, `login-${viewport}`);
  await page.close();
}

async function captureDashboard(context: BrowserContext, viewport: keyof typeof VIEWPORTS) {
  console.log(`\n[dashboard-${viewport}]`);
  const page = await context.newPage();
  await setupRoutes(page, "authenticated");
  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await waitForContent(page, 4000); // Extra time for fleet chart SVG
  await screenshot(page, `dashboard-${viewport}`);
  return page; // Keep open for create-agent capture
}

async function captureCreateAgent(page: Page, viewport: keyof typeof VIEWPORTS) {
  console.log(`\n[create-agent-${viewport}]`);

  // Click the "+ Create new agent" button
  const createBtn = page.locator("text=Create new agent");
  await createBtn.click();
  await page.waitForTimeout(500);

  // Select the "default" template (first one, should already be selected)
  const templateBtn = page.locator('button:has-text("default")').first();
  await templateBtn.click();
  await page.waitForTimeout(300);

  // Type a name
  const nameInput = page.locator('input[placeholder*="research-bot"]');
  if (await nameInput.isVisible()) {
    await nameInput.fill("sales-assistant");
  }

  // Scroll to show the form nicely
  await page.evaluate(() => {
    const form = document.querySelector("form");
    if (form) form.scrollIntoView({ behavior: "instant", block: "start" });
  });
  await page.waitForTimeout(300);

  await screenshot(page, `create-agent-${viewport}`);
  await page.close();
}

async function captureBotDetail(context: BrowserContext, viewport: keyof typeof VIEWPORTS) {
  console.log(`\n[detail-${viewport}]`);
  const page = await context.newPage();
  await setupRoutes(page, "authenticated");
  await page.goto(`${BASE_URL}/bots/customer-support`, { waitUntil: "networkidle" });
  await waitForContent(page, 3000);

  // Expand config and soul sections for a richer screenshot
  const configSummary = page.locator("summary:has-text('OpenClaw Config')");
  if (await configSummary.isVisible()) {
    await configSummary.click();
    await page.waitForTimeout(200);
  }

  await screenshot(page, `detail-${viewport}`);
  return page; // Keep open for terminal capture
}

async function captureTerminal(context: BrowserContext, viewport: keyof typeof VIEWPORTS) {
  console.log(`\n[terminal-${viewport}]`);

  const page = await context.newPage();
  await setupRoutes(page, "authenticated");

  // Remove the abort route for terminal WebSocket
  await page.unroute("**/api/bots/*/terminal");

  // Use Playwright's native WebSocket interception
  await page.routeWebSocket("**/api/bots/*/terminal", (ws) => {
    // Encode terminal content as base64 (matching the terminal-dialog's decode path)
    const raw = TERMINAL_CONTENT;
    const bytes = new TextEncoder().encode(raw);
    const encoded = Buffer.from(bytes).toString("base64");
    const msg = JSON.stringify({ type: "data", data: encoded });

    // Send the fake terminal output after a short delay
    setTimeout(() => {
      ws.send(msg);
    }, 200);
  });

  await page.goto(`${BASE_URL}/bots/customer-support`, { waitUntil: "networkidle" });
  await waitForContent(page, 2000);

  // Click Terminal button
  const termBtn = page.locator('button:has-text("Terminal")').first();
  if (!(await termBtn.isEnabled())) {
    console.log("  Terminal button disabled, skipping");
    await page.close();
    return;
  }
  await termBtn.click();

  // Wait for xterm to render the injected content
  await page.waitForSelector(".xterm-screen", { timeout: 5000 }).catch(() => {
    console.log("  xterm-screen not found, waiting longer...");
  });
  await page.waitForTimeout(2500);

  await screenshot(page, `terminal-${viewport}`);
  await page.close();
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  if (!existsSync(OUTPUT_DIR)) {
    mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  console.log("ClawFarm Screenshot Capture");
  console.log(`Base URL: ${BASE_URL}`);
  console.log(`Output: ${OUTPUT_DIR}\n`);

  // Check if frontend is reachable
  try {
    const res = await fetch(BASE_URL, { signal: AbortSignal.timeout(3000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    console.error(`Cannot reach ${BASE_URL} — is the frontend dev server running?`);
    console.error("  cd ../frontend && npm run dev");
    process.exit(1);
  }

  const browser = await chromium.launch({ headless: true });

  for (const [viewport, size] of Object.entries(VIEWPORTS) as [keyof typeof VIEWPORTS, typeof VIEWPORTS[keyof typeof VIEWPORTS]][]) {
    console.log(`\n${"=".repeat(50)}`);
    console.log(`Viewport: ${viewport} (${size.width}x${size.height})`);
    console.log("=".repeat(50));

    const context = await browser.newContext({
      viewport: size,
      deviceScaleFactor: viewport === "mobile" ? 3 : 2,
      colorScheme: "dark",
    });

    // 1. Login
    await captureLogin(context, viewport);

    // 2. Dashboard + 3. Create Agent (reuses dashboard page)
    const dashPage = await captureDashboard(context, viewport);
    await captureCreateAgent(dashPage, viewport);

    // 4. Bot Detail
    const detailPage = await captureBotDetail(context, viewport);
    await detailPage.close();

    // 5. Terminal (separate page with WebSocket mock)
    await captureTerminal(context, viewport);

    await context.close();
  }

  await browser.close();

  console.log(`\nDone! Screenshots saved to ${OUTPUT_DIR}/`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
