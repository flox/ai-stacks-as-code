(function () {
  try {
    if (typeof window === 'undefined') return;

    var PROD_HOSTS = ['flox.dev', 'www.flox.dev', 'flox.mintlify.dev'];
    var AMPLITUDE_API_KEY = PROD_HOSTS.indexOf(location.hostname) !== -1
      ? '6f89e78c468a7da6fecfd717437fcb32'
      : '820bc93b6feb09134fb8ed7b25af09a1';

    window._hsp = window._hsp || [];
    window._hsp.push([
      'addPrivacyConsentListener',
      function (consent) {
        try {
          if (!(consent && consent.categories && consent.categories.analytics)) return;
          if (document.getElementById('amplitude-script-loaded')) return;

          var b = document.createElement('script');
          b.id = 'amplitude-script-loaded';
          b.type = 'text/javascript';
          b.async = true;
          b.src = 'https://cdn.amplitude.com/script/' + AMPLITUDE_API_KEY + '.js';
          b.onload = function () {
            try {
              if (!window.amplitude || typeof window.amplitude.init !== 'function') return;

              if (window.sessionReplay && typeof window.sessionReplay.plugin === 'function') {
                window.amplitude.add(window.sessionReplay.plugin({ sampleRate: 0.3 }));
              }

              window.amplitude.init(AMPLITUDE_API_KEY, {
                fetchRemoteConfig: true,
                autocapture: {
                  attribution: true,
                  fileDownloads: true,
                  formInteractions: true,
                  pageViews: true,
                  sessions: true,
                  elementInteractions: true,
                  networkTracking: true,
                  webVitals: true,
                  frustrationInteractions: {
                    thrashedCursor: true,
                    errorClicks: true,
                    deadClicks: true,
                    rageClicks: true
                  }
                }
              });
            } catch (e) { /* noop */ }
          };
          document.head.appendChild(b);
        } catch (e) { /* noop */ }
      }
    ]);
  } catch (e) { /* noop */ }
})();
