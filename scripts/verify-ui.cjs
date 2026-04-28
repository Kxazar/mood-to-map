const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

function firstExistingPath(candidates) {
  return candidates.find((candidate) => candidate && fs.existsSync(candidate));
}

async function main() {
  const url = process.argv[2] || "http://77.232.41.200:8080/";
  const outDir = path.resolve("output");
  fs.mkdirSync(outDir, { recursive: true });

  const executablePath =
    process.env.BROWSER_EXECUTABLE ||
    firstExistingPath([
      "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
      "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
      "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      "/usr/bin/chromium-browser",
      "/usr/bin/chromium",
      "/usr/bin/google-chrome",
    ]);
  const browser = await chromium.launch({
    headless: true,
    executablePath,
    args: ["--no-proxy-server", "--proxy-bypass-list=*"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const consoleMessages = [];
  const failedResponses = [];
  page.on("console", (msg) => consoleMessages.push({ type: msg.type(), text: msg.text() }));
  page.on("pageerror", (err) => consoleMessages.push({ type: "pageerror", text: err.message }));
  page.on("response", (res) => {
    if (res.status() >= 400) {
      failedResponses.push({ status: res.status(), url: res.url() });
    }
  });

  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForSelector("#routeForm", { timeout: 45000 });
  const initialText = await page.locator("body").innerText();
  const initialRouteTitle = await page.locator("#routeTitle").innerText();
  const initialStopCount = await page.locator(".stop-card").count();
  const leafletLoaded = await page.evaluate(() => Boolean(window.L && document.querySelector(".leaflet-tile")));
  await page.waitForFunction(() => document.querySelectorAll(".poi-label").length >= 15, undefined, {
    timeout: 90000,
  });
  const initialPoiCount = await page.locator(".poi-label").count();
  await page.locator(".poi-label").first().click();
  await page.waitForSelector(".attraction-copy-row", { timeout: 120000 });
  const attractionTitle = await page.locator("#attractionTitle").innerText();
  const attractionHasPhoto = await page.locator("#attractionPhoto").evaluate((node) => Boolean(node.getAttribute("src")));
  const attractionCopyRows = await page.locator(".attraction-copy-row").count();

  await page.waitForFunction(() => !document.querySelector(".primary-action").disabled, undefined, {
    timeout: 130000,
  });
  await page.fill("#prompt", "A quiet local art walk with coffee, shade, and one waterfront ending.");
  await page.selectOption("#persona", "local");
  await page.selectOption("#duration", "2h");
  await page.selectOption("#startTime", "16:00");
  await page.check("#useAiPlaces");
  await page.click(".primary-action");
  let generationTimedOut = false;
  try {
    await page.waitForFunction(
      () =>
        document.querySelectorAll(".stop-card").length > 0 &&
        document.querySelectorAll(".stop-time").length > 0 &&
        document.querySelectorAll(".stop-detail").length > 0 &&
        !document.querySelector(".primary-action").disabled,
      undefined,
      { timeout: 180000 }
    );
  } catch {
    generationTimedOut = true;
  }

  const afterTitle = await page.locator("#routeTitle").innerText();
  const afterSource = await page.locator("#routeSource").innerText();
  const afterMeta = await page.locator("#routeMeta").innerText();
  const isAiPlaces = afterSource.toUpperCase().includes("AI PLACES");
  const timelineCount = await page.locator(".stop-time").count();
  const detailCount = await page.locator(".stop-detail").count();
  const variantCount = await page.locator(".variant-bar button").count();
  const routeToolsVisible = await page.locator("#routeTools").evaluate((node) => !node.hidden);
  const fullMapsHref = await page.locator("#routeMapsLink").getAttribute("href");
  const stopMapsCount = await page.locator(".stop-actions a").count();
  const replaceButtonCount = await page.locator("[data-action='replace-stop']").count();
  await page.locator("[data-action='toggle-lock']").first().click();
  const lockedCount = await page.locator(".stop-card.is-locked").count();
  const mapLabelCount = await page.locator(".map-label").count();
  const afterStops = await page.locator(".stop-card h3").evaluateAll((nodes) =>
    nodes.map((node) => node.textContent.trim())
  );
  const mapCaptionLength = (await page.locator("#mapCaption").innerText()).length;
  const mapPathCount = await page.locator("path.leaflet-interactive").count();
  const screenshotPath = path.join(outDir, "mood-to-map-verify.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await browser.close();

  const errors = consoleMessages.filter((msg) => msg.type === "error" || msg.type === "pageerror");
  console.log(
    JSON.stringify(
      {
        url,
        pageHasText: initialText.trim().length > 0,
        initialRouteTitle,
        initialStopCount,
        leafletLoaded,
        initialPoiCount,
        attractionTitle,
        attractionHasPhoto,
        attractionCopyRows,
        afterTitle,
        afterSource,
        afterMeta,
        isAiPlaces,
        timelineCount,
        detailCount,
        variantCount,
        routeToolsVisible,
        fullMapsHrefStartsMaps: fullMapsHref?.startsWith("https://www.google.com/maps/dir/") || false,
        stopMapsCount,
        replaceButtonCount,
        lockedCount,
        mapLabelCount,
        afterStops,
        generationTimedOut,
        mapCaptionLength,
        mapPathCount,
        errors,
        failedResponses,
        screenshotPath,
        executablePath: executablePath || "playwright-managed",
      },
      null,
      2
    )
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
