/* Service worker for the installed dashboard.

   Two jobs:
     1. cache the shell so the app opens offline (the data itself lives in
        IndexedDB, not here - nothing personal is ever put in the cache);
     2. receive a shared data.json from the Android share sheet and store it,
        so "Drive -> Share -> Health Dashboard" updates the app in one gesture.

   CACHE_VERSION is rewritten by build_dashboard.py on every build, which is
   what retires the previous cache. */
"use strict";

importScripts("idb-lite.js");

var CACHE_VERSION = "2026-09-19-2033";
var CACHE = "health-shell-" + CACHE_VERSION;

var ASSETS = [
  "./", "./index.html", "./data-loader.js", "./idb-lite.js",
  "./manifest.webmanifest", "./icon-192.png", "./icon-512.png", "./icon.svg"
];

var HOME = new URL("./", self.location).href;

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function (c) { return c.addAll(ASSETS); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.map(function (k) {
          return k !== CACHE ? caches.delete(k) : null;
        }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

/* The share sheet POSTs here. Validate before storing: a malformed share must
   never wipe the data already on the phone. */
function handleShare(request) {
  return request.formData()
    .then(function (form) { return form.get("data").text(); })
    .then(function (text) {
      var d = JSON.parse(text);
      if (!d || !Array.isArray(d.days) || !d.days.length) throw new Error("not a dashboard payload");
      return self.idbSet("data", d).then(function () {
        return self.idbSet("importedAt", Date.now());
      });
    })
    .then(function () { return Response.redirect(HOME + "?imported=1", 303); })
    .catch(function () { return Response.redirect(HOME + "?imported=0", 303); });
}

self.addEventListener("fetch", function (e) {
  var req = e.request;
  var url = new URL(req.url);

  if (req.method === "POST" && url.pathname.replace(/\/$/, "").endsWith("/share")) {
    e.respondWith(handleShare(req));
    return;
  }
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  /* Cache-first: this is a personal app whose shell only changes when the
     builder runs, and a new build means a new CACHE_VERSION anyway. */
  e.respondWith(
    caches.match(req, { ignoreSearch: true }).then(function (hit) {
      if (hit) return hit;
      return fetch(req).catch(function () {
        return req.mode === "navigate"
          ? caches.match("./index.html", { ignoreSearch: true })
          : Response.error();
      });
    })
  );
});
