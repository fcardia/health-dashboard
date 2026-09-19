/* Fills window.DATA for the installed app.

   dashboard.html has the payload baked in and boots on its own. This build
   ships the same page with the marker left null, so the data comes from
   IndexedDB instead - put there either by the Import button below or by the
   share target in sw.js. Nothing is ever fetched from the network: the data
   never leaves the phone. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  function banner(msg, bad) {
    var wrap = document.querySelector(".wrap");
    var b = document.createElement("div");
    b.className = "hd-banner" + (bad ? " bad" : "");
    b.textContent = msg;
    wrap.insertBefore(b, wrap.firstChild);
    setTimeout(function () { b.style.opacity = "0"; }, 4000);
    setTimeout(function () { if (b.parentNode) b.parentNode.removeChild(b); }, 4400);
  }

  function fmtWhen(ts) {
    if (!ts) return null;
    var d = new Date(ts);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(d.getDate()) + "/" + p(d.getMonth() + 1) + "/" + d.getFullYear() +
           " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  /* Validate hard before storing. A half-read or wrong file must not be able to
     destroy the data already on the phone. */
  function parsePayload(text) {
    var d = JSON.parse(text);
    if (!d || typeof d !== "object") throw new Error("not an object");
    if (!Array.isArray(d.days) || !d.days.length) throw new Error("no days in it");
    if (!d.user || !Array.isArray(d.weeks)) throw new Error("missing user/weeks");
    return d;
  }

  function store(d) {
    return self.idbSet("data", d).then(function () {
      return self.idbSet("importedAt", Date.now());
    });
  }

  function importFile(file) {
    if (!file) return Promise.resolve();
    return file.text()
      .then(function (text) { return store(parsePayload(text)); })
      .then(function () { location.replace(location.pathname); })
      .catch(function (err) {
        console.error("[health] import failed:", err);
        banner("Could not read that file - " + (err.message || err) +
               ". Your existing data is untouched.", true);
      });
  }

  /* One reusable hidden input rather than a fresh one per click, so repeated
     imports do not pile up dead nodes in the DOM. */
  var input = null;
  function fileInput() {
    if (input) return input;
    input = document.createElement("input");
    input.type = "file";
    input.id = "hd-file";
    input.accept = "application/json,.json";
    input.style.display = "none";
    input.addEventListener("change", function () {
      /* Reset only once the read has finished: clearing .value detaches the
         File, and doing it synchronously leaves file.text() hanging forever -
         a tap that silently does nothing. (On success the page reloads and
         this never runs.) */
      importFile(input.files[0]).then(function () { input.value = ""; });
    });
    document.body.appendChild(input);
    return input;
  }
  function pickFile() { fileInput().click(); }

  function addImportButton() {
    var themebtn = $("themebtn");
    if (!themebtn || $("importbtn")) return;
    var b = document.createElement("button");
    b.className = "iconbtn";
    b.id = "importbtn";
    b.type = "button";
    b.textContent = "Import";
    b.addEventListener("click", pickFile);
    themebtn.parentNode.insertBefore(b, themebtn);
    fileInput();
  }

  /* buildShell() writes #foot, so this has to run after __boot(). The import
     date is the whole safety net for a manual sync: it has to be impossible to
     read week-old numbers thinking they are current. */
  function stampFooter(d, importedAt) {
    var foot = $("foot");
    if (!foot) return;
    var when = fmtWhen(importedAt);
    foot.textContent = "Data built " + (d.generated || "?") +
      (when ? " · imported to this phone " + when : "") +
      ". Tap Import (or share data.json to this app) after re-running the builder.";
  }

  function welcome() {
    if ($("tabs")) $("tabs").style.display = "none";
    if ($("filters")) $("filters").style.display = "none";
    var sub = $("hdr-sub");
    if (sub) sub.textContent = "No data on this phone yet";

    var panels = $("panels");
    panels.innerHTML = "";
    var box = document.createElement("div");
    box.className = "empty hd-welcome";
    box.innerHTML =
      "<p><b>Nothing imported yet.</b></p>" +
      "<p>On your PC, run <code>Update dashboard.bat</code>. It writes " +
      "<code>data.json</code> next to the dashboard (and into your Drive folder " +
      "if you set one up).</p>" +
      "<p>Then tap Import below and pick that file — Google Drive shows up as a " +
      "source in the file picker, so you can grab it straight from there.</p>";
    var b = document.createElement("button");
    b.className = "iconbtn";
    b.type = "button";
    b.textContent = "Import data.json";
    b.style.marginTop = "10px";
    b.addEventListener("click", pickFile);
    box.appendChild(b);
    panels.appendChild(box);
  }

  function start() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("sw.js").catch(function () { /* http:// or private mode */ });
    }

    /* the share target redirects back here with the outcome */
    var q = location.search;
    if (q.indexOf("imported=1") > -1) banner("Data imported.");
    else if (q.indexOf("imported=0") > -1) banner("That share was not a valid data.json - nothing changed.", true);

    Promise.all([self.idbGet("data"), self.idbGet("importedAt")])
      .then(function (r) {
        var d = r[0];
        if (d && d.days && d.days.length) {
          window.DATA = d;
          window.__boot();
          stampFooter(d, r[1]);
        } else {
          welcome();
        }
        addImportButton();
      })
      .catch(function (err) {
        welcome();
        addImportButton();
        banner("Could not open local storage: " + (err.message || err), true);
      });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
