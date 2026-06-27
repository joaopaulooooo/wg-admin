(function() {
  const modal = document.getElementById('bw-modal');
  if (!modal) return;

  const title = modal.querySelector('.peer-name');
  const canvas = modal.querySelector('#bw-chart');
  let chart = null;
  let lastFocused = null;

  async function open(trigger) {
    const peerId = trigger.dataset.peerId;
    const peerName = trigger.dataset.peerName;
    title.textContent = peerName;

    lastFocused = document.activeElement;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';

    try {
      const resp = await fetch(`/api/bandwidth/${peerId}`);
      const data = await resp.json();
      if (chart) chart.destroy();
      chart = new Chart(canvas, {
        type: 'line',
        data: {
          labels: data.dates,
          datasets: [
            { label: 'Download (rx)', data: data.rx, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.1)', tension: 0.3 },
            { label: 'Upload (tx)', data: data.tx, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', tension: 0.3 },
          ],
        },
        options: {
          responsive: true,
          scales: {
            y: {
              ticks: { callback: (v) => formatBytes(v) },
            },
          },
          plugins: { legend: { labels: { color: '#ccc' } } },
        },
      });
    } catch (e) {
      console.error('Failed to load bandwidth data', e);
    }

    setTimeout(() => modal.querySelector('.modal-close').focus(), 50);
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    const units = ['KB', 'MB', 'GB', 'TB'];
    let v = bytes / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(1) + ' ' + units[i];
  }

  function close() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    if (lastFocused) lastFocused.focus();
  }

  document.querySelectorAll('.peer-sparkline').forEach(el => {
    el.addEventListener('click', (e) => { e.preventDefault(); open(el); });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(el); }
    });
  });

  modal.querySelectorAll('[data-modal-close]').forEach(el => {
    el.addEventListener('click', close);
  });

  document.addEventListener('keydown', (e) => {
    if (!modal.classList.contains('open')) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
  });
})();
