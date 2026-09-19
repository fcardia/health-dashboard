/* Tiny IndexedDB key/value store, shared by the page and the service worker
   (the share target writes from the worker, the Import button from the page).
   Loaded with a plain <script> in the page and importScripts() in the worker,
   so it hangs everything off `self` and pulls in no dependencies. */
(function (scope) {
  "use strict";

  var DB = "health", STORE = "kv", VERSION = 1;
  var opening = null;

  function open() {
    if (opening) return opening;
    opening = new Promise(function (resolve, reject) {
      var rq = indexedDB.open(DB, VERSION);
      rq.onupgradeneeded = function () {
        if (!rq.result.objectStoreNames.contains(STORE)) rq.result.createObjectStore(STORE);
      };
      rq.onsuccess = function () { resolve(rq.result); };
      rq.onerror = function () { reject(rq.error); };
    });
    return opening;
  }

  function tx(mode, fn) {
    return open().then(function (db) {
      return new Promise(function (resolve, reject) {
        var t = db.transaction(STORE, mode);
        var rq = fn(t.objectStore(STORE));
        t.oncomplete = function () { resolve(rq ? rq.result : undefined); };
        t.onerror = function () { reject(t.error); };
        t.onabort = function () { reject(t.error); };
      });
    });
  }

  scope.idbGet = function (key) {
    return tx("readonly", function (s) { return s.get(key); });
  };
  scope.idbSet = function (key, value) {
    return tx("readwrite", function (s) { s.put(value, key); });
  };
})(self);
