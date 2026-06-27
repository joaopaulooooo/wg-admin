(function() {
  const canvas = document.getElementById('global-chart');
  if (!canvas) return;

  const colors = ['#10b981', '#3b82f6', '#f59e0b', '#ec4899', '#8b5cf6', '#6b7280'];

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    const units = ['KB', 'MB', 'GB', 'TB'];
    let v = bytes / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(1) + ' ' + units[i];
  }

  fetch('/api/bandwidth/global')
    .then(r => r.json())
    .then(data => {
      const datasets = data.series.map((s, i) => ({
        label: s.name,
        data: s.data,
        backgroundColor: colors[i % colors.length] + '99',
        borderColor: colors[i % colors.length],
        fill: true,
        tension: 0.3,
      }));
      new Chart(canvas, {
        type: 'line',
        data: { labels: data.dates, datasets },
        options: {
          responsive: true,
          scales: {
            y: { stacked: true, ticks: { callback: (v) => formatBytes(v) } },
          },
          plugins: { legend: { labels: { color: '#ccc' } } },
        },
      });
    })
    .catch(e => console.error('Failed to load global bandwidth', e));
})();
