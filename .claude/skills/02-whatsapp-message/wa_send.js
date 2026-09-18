#!/usr/bin/env node
/**
 * 02-whatsapp-message — WhatsApp transport worker.
 *
 * Usage: node wa_send.js <job.json> <result.json>
 *
 * Owns the linked-device session and nothing else: send_whatsapp.py decides who gets
 * what, this only delivers it. Numbers are printed masked; the full ones live only in
 * the job file, which the Python side writes to the system temp dir and deletes.
 */
const fs = require("fs");
const path = require("path");
const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcodeTerminal = require("qrcode-terminal");
const qrcode = require("qrcode");

const [jobPath, resultPath] = process.argv.slice(2);
if (!jobPath || !resultPath) {
  console.error("usage: node wa_send.js <job.json> <result.json>");
  process.exit(2);
}

const job = JSON.parse(fs.readFileSync(jobPath, "utf8"));
const results = [];
let sender = "";
let finished = false;

const mask = (v) => String(v || "").replace(/\d{8,}/g, (m) => "****" + m.slice(-4));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));


function finish(code, reason) {
  if (finished) return;
  finished = true;
  fs.writeFileSync(
    resultPath,
    JSON.stringify({ sender: mask(sender), reason: reason || null, results }, null, 2),
    "utf8"
  );
  // The QR encodes a short-lived, single-use linking secret; don't leave it on disk once
  // the run is over, whether it was scanned, expired, or the run stopped for another reason.
  try { fs.unlinkSync(qrImagePath); } catch {}
  if (reason) console.error(`wa_send: stopped — ${reason}`);
  process.exit(code);
}

// Prefer Puppeteer's own downloaded Chromium (protocol-matched to the pinned
// puppeteer/whatsapp-web.js versions) unless a specific browser was configured.
// protocolTimeout is raised because the first connection on a real account can spend
// a while syncing chat history before the page settles.
const puppeteerOpts = {
  headless: process.env.WA_HEADFUL ? false : true,
  protocolTimeout: 300000,
  args: [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-setuid-sandbox",
    "--disable-accelerated-2d-canvas",
    "--no-first-run",
    "--no-zygote",
  ],
};
if (job.chrome) puppeteerOpts.executablePath = job.chrome;

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: job.auth_dir }),
  puppeteer: puppeteerOpts,
  // Without this, whatsapp-web.js writes a ".wwebjs_cache/" page-cache snapshot to the
  // process's cwd (the repo root, since Python launches this from there) — keep every
  // WhatsApp-Web artifact under the already git-ignored session directory instead.
  webVersionCache: { type: "local", path: path.join(job.auth_dir, "wwebjs_cache") },
});

// No linked session yet: show the QR and give the owner a bounded time to scan it (R16).
// Also written as a PNG next to the result file — terminal QR rendering can be unreliable
// (font aspect ratio, block-character clipping), and a plain image sidesteps that.
const qrImagePath = path.join(path.dirname(resultPath), "wa_link_qr.png");
let qrTimer = null;
client.on("qr", (qr) => {
  console.log("No linked device yet. On the sending phone: WhatsApp -> Settings ->");
  console.log("Linked devices -> Link a device, then scan this code:\n");
  qrcodeTerminal.generate(qr, { small: true });
  qrcode.toFile(qrImagePath, qr, { width: 400 }, (err) => {
    if (err) console.error(`wa_send: could not write QR image — ${err.message}`);
    else console.log(`\nOr open this image and scan it instead: ${qrImagePath}`);
  });
  if (!qrTimer) {
    qrTimer = setTimeout(
      () => client.destroy().finally(() => finish(3, "auth-timeout")),
      job.qr_timeout * 1000
    );
  }
});
client.on("authenticated", () => {
  if (qrTimer) clearTimeout(qrTimer);
});
client.on("auth_failure", () => finish(3, "auth"));
client.on("disconnected", () => finish(3, "auth"));

client.on("ready", async () => {
  try {
    sender = (client.info && client.info.wid && client.info.wid.user) || "";
    // Guard against a different account being linked than the one configured (R8).
    if (sender !== job.sender) {
      console.error(`wa_send: linked account is ${mask(sender)}, expected ${mask(job.sender)}`);
      await client.destroy();
      return finish(4, "sender-mismatch");
    }
    console.log(`wa_send: linked as ${mask(sender)}, ${job.recipients.length} to send (${job.mode})`);

    for (let i = 0; i < job.recipients.length; i++) {
      const r = job.recipients[i];
      const chatId = `${r.to}@c.us`;
      try {
        if (!(await client.isRegisteredUser(chatId))) {
          results.push({ person: r.person, status: "not_registered" });
          console.log(`  ${r.person}: not on WhatsApp`);
          continue;
        }
        const ids = [];
        for (const part of r.parts) {
          // sendMessage resolving without throwing is the send signal we trust: this
          // whatsapp-web.js version sometimes returns undefined (no .id to read) even
          // when the message was genuinely delivered — a known version-drift quirk
          // between the library and WhatsApp Web's current internals. A chat-history
          // re-check was tried and found unreliable in practice, so a missing id is
          // recorded as "unconfirmed" rather than treated as a failure (a real failure
          // still surfaces as sendMessage throwing, which the outer catch below handles).
          const msg = await client.sendMessage(chatId, part);
          ids.push(msg && msg.id && msg.id._serialized ? msg.id._serialized : "unconfirmed");
        }
        results.push({ person: r.person, status: "ok", ids, at: new Date().toISOString() });
        console.log(`  ${r.person}: sent ${ids.length} message(s) to ${mask(r.to)}`);
      } catch (err) {
        // One bad recipient must not cost everybody else their message (R10).
        results.push({ person: r.person, status: "error", error: mask(err.message) });
        console.error(`  ${r.person}: failed — ${mask(err.message)}`);
      }
      if (i < job.recipients.length - 1) {
        const wait = job.min_delay + Math.random() * (job.max_delay - job.min_delay);
        console.log(`  waiting ${wait.toFixed(1)}s`);
        await sleep(wait * 1000);
      }
    }
    // The last recipient has no trailing "wait between sends" pause to absorb it, so
    // give the final send a moment to actually reach WhatsApp's servers before the
    // browser is torn down — sendMessage can resolve on local state slightly ahead of
    // the network round trip, and destroying the page mid-flight can drop it silently.
    await sleep(5000);
    await client.destroy();
    finish(0, null);
  } catch (err) {
    console.error(`wa_send: ${mask(err.message)}`);
    await client.destroy().catch(() => {});
    finish(5, "worker-error");
  }
});

client.initialize().catch((err) => {
  console.error(`wa_send: could not start Chrome — ${err.message}`);
  finish(6, "launch-failed");
});
