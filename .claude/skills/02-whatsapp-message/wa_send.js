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
// v1.2 (R18-R22): every wait in this file is bounded, and the result file always tells the
// truth about how far the run got, even if this process is killed.
const READY_TIMEOUT = Number(job.ready_timeout || 120) * 1000;
const SEND_TIMEOUT = Number(job.send_timeout || 60) * 1000;
const DESTROY_CAP = 15000;
// v2.0 (R23): after the last send, wait at most this long for every message to reach the phone.
const ACK_TIMEOUT = Number(job.ack_timeout ?? 45) * 1000;
const SETTLE_MIN = 5000;
let stage = "starting";
let inFlight = null; // the recipient whose send has started but not yet been recorded

// v2.0 (R23): delivery receipts. This library version returns no message from sendMessage and
// gives message_create no serialized id (chats are addressed by @lid now), so a receipt is
// matched to what we sent by its exact body text. Each body names its person and that day's
// list, so two pending messages never share one. ack: -1 error, 0 pending, 1 server,
// 2 delivered to the phone, 3 read, 4 played. null = no receipt seen at all.
const receipts = new Map(); // body -> { ack }
const partsOf = new Map(); // person -> [body, ...]
const acksFor = (person) => (partsOf.get(person) || []).map((b) => receipts.get(b).ack);
function onReceipt(msg, ack) {
  if (!msg || !msg.fromMe || typeof msg.body !== "string") return;
  const rec = receipts.get(msg.body) || receipts.get(msg.body.trim());
  if (!rec || typeof ack !== "number") return;
  // Which message is this receipt really for? msg.timestamp is when that message was created.
  const age = typeof msg.timestamp === "number" ? msg.timestamp - rec.sentAt : null;
  console.log(`  [${stamp()}] receipt ${rec.person}: ack ${ack}` + (age === null ? "" : ` (message created ${age >= 0 ? "+" : ""}${age}s vs this send)`));
  if (rec.ack === null || ack > rec.ack) rec.ack = ack;
}

const mask = (v) => String(v || "").replace(/\d{8,}/g, (m) => "****" + m.slice(-4));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stamp = () => new Date().toTimeString().slice(0, 8);
// A wedged browser must not be able to hold the exit hostage: cap client.destroy().
const capped = (p, ms) => Promise.race([Promise.resolve(p).catch(() => {}), sleep(ms)]);

function setStage(s) {
  stage = s;
  console.log(`  [${stamp()}] stage: ${mask(s)}`);
}

// Written after every recipient (R20). Temp file + rename, so a kill can never leave it half-written.
function writeResult(reason) {
  const tmp = resultPath + ".tmp";
  fs.writeFileSync(
    tmp,
    JSON.stringify({
      sender: mask(sender), reason: reason || null, stage, in_flight: inFlight,
      in_flight_acks: inFlight ? acksFor(inFlight) : null,
      results: results.map((row) => ({ ...row, acks: acksFor(row.person) })),
    }, null, 2),
    "utf8"
  );
  fs.renameSync(tmp, resultPath);
}


function finish(code, reason) {
  if (finished) return;
  finished = true;
  writeResult(reason);
  // The QR encodes a short-lived, single-use linking secret; don't leave it on disk once
  // the run is over, whether it was scanned, expired, or the run stopped for another reason.
  try { fs.unlinkSync(qrImagePath); } catch {}
  if (reason) console.error(`wa_send: stopped — ${reason} (last stage: ${stage})`);
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
  clearTimeout(launchTimer); // no session: the QR timer below takes over (R16)
  setStage("waiting for QR scan");
  console.log("No linked device yet. On the sending phone: WhatsApp -> Settings ->");
  console.log("Linked devices -> Link a device, then scan this code:\n");
  qrcodeTerminal.generate(qr, { small: true });
  qrcode.toFile(qrImagePath, qr, { width: 400 }, (err) => {
    if (err) console.error(`wa_send: could not write QR image — ${err.message}`);
    else console.log(`\nOr open this image and scan it instead: ${qrImagePath}`);
  });
  if (!qrTimer) {
    qrTimer = setTimeout(
      () => capped(client.destroy(), DESTROY_CAP).then(() => finish(3, "auth-timeout")),
      job.qr_timeout * 1000
    );
  }
});
client.on("authenticated", () => {
  if (qrTimer) clearTimeout(qrTimer);
  setStage("authenticated");
});
client.on("loading_screen", (pct) => setStage(`loading ${pct}%`));
client.on("message_create", (msg) => onReceipt(msg, msg && msg.ack));
client.on("message_ack", (msg, ack) => onReceipt(msg, ack));
client.on("auth_failure", () => finish(3, "auth"));
client.on("disconnected", () => finish(3, "auth"));

client.on("ready", async () => {
  clearTimeout(launchTimer);
  setStage("ready");
  try {
    sender = (client.info && client.info.wid && client.info.wid.user) || "";
    // Guard against a different account being linked than the one configured (R8).
    if (sender !== job.sender) {
      console.error(`wa_send: linked account is ${mask(sender)}, expected ${mask(job.sender)}`);
      await capped(client.destroy(), DESTROY_CAP);
      return finish(4, "sender-mismatch");
    }
    console.log(`wa_send: linked as ${mask(sender)}, ${job.recipients.length} to send (${job.mode})`);

    for (let i = 0; i < job.recipients.length; i++) {
      const r = job.recipients[i];
      inFlight = r.person;
      setStage(`sending ${r.person}`);
      writeResult(null); // a kill from here on names this person as in flight (R20, R21)
      const outcome = await Promise.race([
        sendOne(r).then((row) => ({ row })),
        sleep(SEND_TIMEOUT).then(() => ({ timedOut: true })),
      ]);
      if (outcome.timedOut) {
        // Delivery is unknown: never call this a failure, or a retry could double-message them (R19).
        results.push({ person: r.person, status: "unconfirmed", error: "send timed out - delivery unknown, check the chat" });
        inFlight = null;
        console.error(`  ${r.person}: no answer within ${SEND_TIMEOUT / 1000}s - delivery unknown`);
        await capped(client.destroy(), DESTROY_CAP);
        return finish(8, "send-timeout");
      }
      results.push(outcome.row);
      inFlight = null;
      writeResult(null);
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
    // v2.0 (R23): that settle now also waits for receipts — until every message is on its
    // phone (or failed outright), or ACK_TIMEOUT has passed, but never less than SETTLE_MIN.
    setStage("waiting for delivery receipts");
    const settleStart = Date.now();
    const allSettled = () => [...receipts.values()].every((r) => r.ack !== null && (r.ack >= 2 || r.ack < 0));
    while (Date.now() - settleStart < Math.max(ACK_TIMEOUT, SETTLE_MIN)) {
      if (Date.now() - settleStart >= SETTLE_MIN && allSettled()) break;
      await sleep(500);
    }
    for (const row of results) {
      if (partsOf.has(row.person)) console.log(`  ${row.person}: receipt ${acksFor(row.person).join(",")}`);
    }
    writeResult(null); // receipts are on disk before teardown, in case teardown is killed
    await capped(client.destroy(), DESTROY_CAP);
    finish(0, null);
  } catch (err) {
    console.error(`wa_send: ${mask(err.message)}`);
    await capped(client.destroy(), DESTROY_CAP);
    finish(5, "worker-error");
  }
});

// One recipient's check-and-send. Never throws: a bad recipient must not cost everybody else
// their message (R10), so its error becomes that recipient's result row.
async function sendOne(r) {
  const chatId = `${r.to}@c.us`;
  try {
    if (!(await client.isRegisteredUser(chatId))) {
      console.log(`  ${r.person}: not on WhatsApp`);
      return { person: r.person, status: "not_registered" };
    }
    const ids = [];
    partsOf.set(r.person, r.parts);
    const sentAt = Math.floor(Date.now() / 1000);
    for (const part of r.parts) receipts.set(part, { ack: null, person: r.person, sentAt }); // before sending: acks can beat sendMessage's return
    for (const part of r.parts) {
      // sendMessage resolving without throwing is the send signal we trust: this
      // whatsapp-web.js version sometimes returns undefined (no .id to read) even
      // when the message was genuinely delivered - a known version-drift quirk
      // between the library and WhatsApp Web's current internals. A chat-history
      // re-check was tried and found unreliable in practice, so a missing id is
      // recorded as "unconfirmed" rather than treated as a failure (a real failure
      // still surfaces as sendMessage throwing, which is caught below).
      const msg = await client.sendMessage(chatId, part);
      ids.push(msg && msg.id && msg.id._serialized ? msg.id._serialized : "unconfirmed");
    }
    console.log(`  ${r.person}: sent ${ids.length} message(s) to ${mask(r.to)}`);
    return { person: r.person, status: "ok", ids, at: new Date().toISOString() };
  } catch (err) {
    // A failed send has nothing to wait a receipt for (R23's settle would otherwise sit out ACK_TIMEOUT).
    for (const part of partsOf.get(r.person) || []) receipts.delete(part);
    partsOf.delete(r.person);
    console.error(`  ${r.person}: failed - ${mask(err.message)}`);
    return { person: r.person, status: "error", error: mask(err.message) };
  }
}

// R18: if WhatsApp Web never becomes ready, give up cleanly with nothing sent.
const launchTimer = setTimeout(async () => {
  console.error(`wa_send: not ready within ${READY_TIMEOUT / 1000}s`);
  await capped(client.destroy(), DESTROY_CAP);
  finish(7, "startup-timeout");
}, READY_TIMEOUT);

setStage("launching");
client.initialize().catch((err) => {
  clearTimeout(launchTimer);
  console.error(`wa_send: could not start Chrome — ${err.message}`);
  finish(6, "launch-failed");
});
