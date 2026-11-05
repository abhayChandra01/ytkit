// Runs before first paint so the page never flashes the wrong theme.
(function () {
  var theme = 'dark';
  try {
    var saved = localStorage.getItem('ytkit:theme');
    if (saved === 'light' || saved === 'dark') theme = saved;
    else if (window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches) theme = 'light';
  } catch (e) { /* storage blocked */ }
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.classList.add('js');
})();
