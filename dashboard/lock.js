/*
 * Password lock for a static copy of the map (made by tools/build_site.py).
 *
 * Every file in data/ is encrypted (AES-GCM; the key comes from the password
 * through PBKDF2-SHA256). This page asks for the password once, checks it
 * against data/lock.json, and keeps the key on this device until someone
 * presses Lock. Without the password the data files are unreadable, even to
 * someone who downloads them directly. The page's code is not secret.
 */
(function () {
  "use strict";
  const SAVED = "riziki.lock";
  const enc = new TextEncoder(), dec = new TextDecoder();
  const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const b64 = (u8) => btoa(Array.from(u8, (b) => String.fromCharCode(b)).join(""));
  let key = null;

  function saved() {
    try { return JSON.parse(localStorage.getItem(SAVED) || "null"); } catch (e) { return null; }
  }

  // Everything this site keeps on the device: the key and the maps opened.
  function wipe() {
    try {
      for (const k of Object.keys(localStorage)) if (k.startsWith("riziki.")) localStorage.removeItem(k);
    } catch (e) { /* storage blocked: nothing saved anyway */ }
  }

  async function inflate(bytes) {
    const s = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Uint8Array(await new Response(s).arrayBuffer());
  }

  async function decrypt(box, k) {
    const plain = new Uint8Array(await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: unb64(box.iv) }, k, unb64(box.data)));
    return box.z ? inflate(plain) : plain;
  }

  async function keyBytes(password, lock) {
    const base = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
    return new Uint8Array(await crypto.subtle.deriveBits(
      { name: "PBKDF2", hash: "SHA-256", salt: unb64(lock.salt), iterations: lock.iter }, base, 256));
  }

  const useKey = (raw) => crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["decrypt"]);

  async function opens(k, lock) {
    try { return dec.decode(await decrypt(lock.check, k)) === "riziki"; } catch (e) { return false; }
  }

  // data/lock.json, or why it couldn't be had.
  async function getLock(dataURL) {
    let r;
    try { r = await fetch(dataURL + "lock.json", { cache: "no-store" }); }
    catch (e) { throw new Error("offline"); }
    if (!r.ok) throw new Error("missing");
    try { return await r.json(); } catch (e) { throw new Error("missing"); }
  }

  const SAY = {
    offline: "You're offline. Connect once to open the map; after that it opens offline too.",
    missing: "This copy of the map is incomplete (no data/lock.json). It needs rebuilding with tools/build_site.py.",
    insecure: "This page needs a secure address (https://) to open.",
    wrong: "That password didn't work.",
  };

  // Resolves once the data can be read: at once if this device already has the
  // key, otherwise after the password is entered.
  async function ready(dataURL) {
    if (!(window.crypto && crypto.subtle)) return ask(dataURL, null, "insecure");
    let lock = null, problem = null;
    try { lock = await getLock(dataURL); } catch (e) { problem = e.message; }
    const s = saved();
    if (s && s.key && (!lock || s.salt === lock.salt)) {
      try {
        const k = await useKey(unb64(s.key));
        if (!lock || (await opens(k, lock))) { key = k; return; }
      } catch (e) { /* a damaged saved key: ask again */ }
    }
    if (s) wipe();                       // the password changed since this device last opened it
    return ask(dataURL, lock, problem);
  }

  function ask(dataURL, lock, problem) {
    const box = document.getElementById("lock"), form = document.getElementById("lock-form");
    const pw = document.getElementById("lock-pw"), msg = document.getElementById("lock-msg");
    const btn = form.querySelector("button");
    box.hidden = false;
    document.body.classList.add("locked");
    msg.textContent = problem ? SAY[problem] : "";
    pw.focus();
    return new Promise((resolve) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (problem === "insecure") return;
        btn.disabled = true;
        msg.textContent = "Checking…";
        try {
          if (!lock) lock = await getLock(dataURL);
          const raw = await keyBytes(pw.value, lock);
          const k = await useKey(raw);
          if (!(await opens(k, lock))) { msg.textContent = SAY.wrong; pw.select(); return; }
          try { localStorage.setItem(SAVED, JSON.stringify({ salt: lock.salt, key: b64(raw) })); }
          catch (err) { /* can't remember it here: asked again next time */ }
          key = k;
          pw.value = "";
          msg.textContent = "";
          box.hidden = true;
          document.body.classList.remove("locked");
          resolve();
        } catch (err) {
          msg.textContent = SAY[err.message] || "Couldn't open the map: " + (err.message || err);
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  // A data file as the page uses it.
  async function open(box) {
    if (!key) throw new Error("locked");
    return JSON.parse(dec.decode(await decrypt(box, key)));
  }

  function forget() {
    wipe();
    location.reload();
  }

  window.RizikiLock = { ready, open, forget, wipe, hasKey: () => !!saved()?.key };
})();
