const puppeteer = require('puppeteer-core');
const path = require('path');

async function run() {
  console.log("Launching browser with new UA...");
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: '/Users/bommavj/.cache/puppeteer/chrome/mac_arm-146.0.7680.31/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ]
  });

  const page = await browser.newPage();
  
  // Set User Agent similar to a modern browser
  await page.setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36');

  console.log("Navigating to WhatsApp Web...");
  await page.goto('https://web.whatsapp.com', { waitUntil: 'networkidle2', timeout: 60000 });
  
  console.log("Waiting 25 seconds for page content to load...");
  await new Promise(r => setTimeout(r, 25000));
  
  const screenshotPath = path.join(__dirname, 'wa_screenshot_new_ua.png');
  console.log(`Saving screenshot to ${screenshotPath}...`);
  await page.screenshot({ path: screenshotPath });
  
  const html = await page.content();
  console.log("HTML length:", html.length);
  if (html.includes("Update Google Chrome") || html.includes("update") || html.includes("browser")) {
    console.log("Detected update message or similar in new UA!");
  } else {
    console.log("No update message detected in new UA!");
  }
  
  await browser.close();
  console.log("Done!");
}

run().catch(console.error);
