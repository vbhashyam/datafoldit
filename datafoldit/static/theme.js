(function () {
  var theme = 'dark';
  try { theme = localStorage.getItem('dfit-theme') === 'light' ? 'light' : 'dark'; } catch (_) {}
  document.documentElement.dataset.theme = theme;
  document.addEventListener('DOMContentLoaded', function () {
    var toggle = document.querySelector('[data-theme-toggle]');
    if (!toggle) return;
    function label() { toggle.textContent = document.documentElement.dataset.theme === 'dark' ? '☀ Light mode' : '☾ Dark mode'; }
    label();
    toggle.addEventListener('click', function () {
      var next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('dfit-theme', next); } catch (_) {}
      label();
    });
  });
})();
