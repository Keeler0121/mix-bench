/* Mix Bench service worker: keeps the app working with no signal. Change VERSION to make phones fetch a new copy. */
var VERSION = 'mixbench-2026-10-29-3';
var CORE = ['./', './index.html', './manifest.webmanifest', './icons/icon-180.png', './icons/icon-192.png', './icons/icon-512.png'];

self.addEventListener('install', function(e){
  e.waitUntil(caches.open(VERSION).then(function(c){ return c.addAll(CORE); }).then(function(){ return self.skipWaiting(); }));
});
self.addEventListener('activate', function(e){
  e.waitUntil(caches.keys().then(function(keys){ return Promise.all(keys.filter(function(k){ return k !== VERSION; }).map(function(k){ return caches.delete(k); })); }).then(function(){ return self.clients.claim(); }));
});
self.addEventListener('fetch', function(e){
  var req = e.request; if (req.method !== 'GET') return;
  var url = new URL(req.url);
  /* Google sign-in and Drive always go to the network and are never stored */
  if (url.hostname === 'accounts.google.com' || url.hostname === 'www.googleapis.com' || url.hostname === 'oauth2.googleapis.com') return;
  if (url.origin === self.location.origin) {
    /* the app itself: answer from the cache at once, refresh the cache in the background */
    e.respondWith(caches.open(VERSION).then(function(c){
      return c.match(req, {ignoreSearch:true}).then(function(hit){
        var net = fetch(req).then(function(r){ if (r && r.ok) c.put(req, r.clone()); return r; }).catch(function(){ return hit || c.match('./index.html'); });
        return hit || net;
      });
    }));
    return;
  }
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    e.respondWith(caches.open(VERSION).then(function(c){ return c.match(req).then(function(hit){ return hit || fetch(req).then(function(r){ if (r && (r.ok || r.type === 'opaque')) c.put(req, r.clone()); return r; }); }); }));
  }
});
self.addEventListener('notificationclick', function(e){
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type:'window', includeUncontrolled:true}).then(function(cs){ return cs.length ? cs[0].focus() : self.clients.openWindow('./'); }));
});
