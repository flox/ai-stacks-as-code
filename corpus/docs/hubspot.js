(function () {
  try {
    if (typeof window === 'undefined') return;

    var PROD_HOSTS = ['flox.dev', 'www.flox.dev', 'flox.mintlify.dev'];
    if (PROD_HOSTS.indexOf(location.hostname) === -1) return;

    if (document.getElementById('hs-script-loader')) return;

    var s = document.createElement('script');
    s.id = 'hs-script-loader';
    s.type = 'text/javascript';
    s.async = true;
    s.defer = true;
    s.src = '//js.hs-scripts.com/23414950.js';
    document.head.appendChild(s);
  } catch (e) { /* noop */ }
})();
