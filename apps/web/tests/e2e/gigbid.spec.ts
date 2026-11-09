import { expect, test, type Page } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8010";
const PASSWORD = "correct horse battery";
const JOB = "Build a customer churn prediction baseline";
const uniqueEmail = () => `${crypto.randomUUID()}@example.com`;

test.beforeEach(async ({ request }) => {
  await request.post(`${BACKEND}/__test/reset`);
});

async function signUp(page: Page) {
  const response = await page.request.post("/api/auth/signup", {
    data: { email: uniqueEmail(), password: PASSWORD, persona: "ai_ml_engineer" },
  });
  expect(response.ok()).toBeTruthy();
}

async function openProposal(page: Page) {
  await signUp(page);
  await page.request.post("/api/searches", {
    data: { query: "churn", skills: [], interval_minutes: 60 },
  });
  await page.goto("/inbox");
  await page.getByRole("button", { name: "Check for new jobs" }).click();
  const row = page.getByRole("listitem").filter({ hasText: JOB });
  await row.getByRole("link", { name: "Review proposal" }).click();
  await expect(page.getByRole("heading", { name: "Proposal studio" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve and submit" })).toBeEnabled();
}

test("onboarding creates an account and reaches the inbox", async ({ page }) => {
  await page.goto("/onboarding");
  await page.getByLabel("Email").fill(uniqueEmail());
  await page.getByLabel(/^Password/).fill(PASSWORD);
  await page.getByLabel("Profile").selectOption("data_analyst");
  await page.getByRole("button", { name: "Create account" }).click();

  await page.getByLabel(/Lowest project price/).fill("350");
  await page.getByLabel("Weekly Connects budget").fill("40");
  await page.getByLabel("Search for jobs about").fill("dashboard");
  await page.getByRole("button", { name: "Save preferences" }).click();

  await page.getByLabel(/Add a portfolio file/).setInputFiles({
    name: "work.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("I rebuilt a reporting dashboard in SQL."),
  });
  await page.getByLabel("Title for this work").fill("Reporting dashboard");
  await page.getByRole("button", { name: "Go to my inbox" }).click();

  await expect(page.getByRole("heading", { name: "Job inbox" })).toBeVisible();
  await expect(page.getByText("No jobs yet")).toBeVisible();
  await page.goto("/settings");
  await expect(page.getByLabel(/Lowest project price/)).toHaveValue("350");
  await expect(page.getByText("dashboard", { exact: false }).first()).toBeVisible();
});

test("approving a proposal submits it and moves it through the pipeline", async ({ page }) => {
  await openProposal(page);

  const claim = page.locator(".claim").first();
  await claim.focus();
  await expect(page.getByRole("tooltip").first()).toBeVisible();
  await expect(page.getByText("Connects cost")).toBeVisible();

  await page.reload(); // a refresh must not lose the pending approval
  await page.getByRole("button", { name: "Approve and submit" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Submitted" })).toBeVisible();

  await page.goto("/pipeline");
  await expect(page.getByRole("region", { name: "submitted" }).getByText(JOB)).toBeVisible();

  await page.goto("/runs");
  await page.getByRole("link", { name: "job-001" }).click();
  await expect(page.getByText("Writer drafted the proposal").first()).toBeVisible();
  await expect(page.getByText("Stopped: Submitted")).toBeVisible();
});

test("rejecting needs a reason and sends nothing", async ({ page }) => {
  await openProposal(page);
  await page.getByRole("button", { name: "Reject…" }).click();
  await expect(page.getByRole("button", { name: "Confirm rejection" })).toBeDisabled();
  await page.getByLabel("Reason for rejecting").fill("Budget is too low");
  await page.getByRole("button", { name: "Confirm rejection" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Rejected" })).toBeVisible();

  await page.goto("/pipeline");
  await expect(page.getByRole("region", { name: "submitted" }).getByText(JOB)).toHaveCount(0);
  await page.goto("/analytics");
  await expect(page.getByText("Budget is too low (1)")).toBeVisible();
});

test("editing regenerates, re-checks compliance and needs approval again", async ({ page }) => {
  await openProposal(page);
  const editor = page.getByLabel("Proposal text");

  await page.getByRole("button", { name: "Regenerate paragraph 1" }).click();
  await expect(editor).toHaveValue(/gradient boosted model with scikit-learn/);

  await editor.fill("Your churn project needs care. Email me at someone@example.com please.");
  await page.getByRole("button", { name: "Save edits and re-check" }).click();

  await expect(editor).toHaveValue(/\[removed\]/);
  await expect(editor).not.toHaveValue(/someone@example.com/);
  await expect(page.getByRole("button", { name: "Approve and submit" })).toBeEnabled();
  await expect(page.getByText(/POL-01/).first()).toBeVisible();
});

test("a marketplace outage shows a banner that clears on recovery", async ({ page, request }) => {
  await signUp(page);
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Job inbox" })).toBeVisible();
  const banner = page.getByText("Marketplace unavailable.");
  await expect(banner).toHaveCount(0);

  await request.post(`${BACKEND}/__test/marketplace/stop`);
  try {
    await page.reload();
    await expect(banner).toBeVisible();
  } finally {
    await request.post(`${BACKEND}/__test/marketplace/start`);
  }
  await page.reload();
  await expect(page.getByRole("heading", { name: "Job inbox" })).toBeVisible();
  await expect(banner).toHaveCount(0);
});

test("the inbox fits a phone screen and is reachable by keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await signUp(page);
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Job inbox" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
});
