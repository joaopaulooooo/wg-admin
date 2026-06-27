(function() {
  const modal = document.getElementById('wa-modal');
  if (!modal) return;

  const title = modal.querySelector('.peer-name');
  const phoneInput = document.getElementById('wa-phone');
  const ddiSelect = document.getElementById('wa-ddi');
  const sendBtn = document.getElementById('wa-send');
  const preview = modal.querySelector('.wa-preview');
  let lastFocused = null;
  let currentPeer = { id: '', name: '' };

  // Restore last DDI from localStorage
  const savedDdi = localStorage.getItem('wa.ddi');
  if (savedDdi !== null) ddiSelect.value = savedDdi;

  function messageFor(name) {
    return `Olá ${name}! Segue em anexo a configuração da tua VPN wg-admin.\nImporta em: app WireGuard → Adicionar → Importar tunnel(s) from file.`;
  }

  async function open(trigger) {
    currentPeer = { id: trigger.dataset.peerId, name: trigger.dataset.peerName };
    title.textContent = currentPeer.name;
    preview.textContent = messageFor(currentPeer.name);
    phoneInput.value = '';
    sendBtn.disabled = true;

    lastFocused = document.activeElement;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    setTimeout(() => phoneInput.focus(), 50);
  }

  function close() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    if (lastFocused) lastFocused.focus();
  }

  phoneInput.addEventListener('input', () => {
    sendBtn.disabled = phoneInput.value.replace(/\D/g, '').length < 6;
  });

  ddiSelect.addEventListener('change', () => {
    localStorage.setItem('wa.ddi', ddiSelect.value);
  });

  async function send() {
    const ddi = ddiSelect.value;
    const phone = phoneInput.value.replace(/\D/g, '');
    const fullNumber = ddi + phone;
    if (fullNumber.length < 6) return;

    const message = messageFor(currentPeer.name);
    const fileName = `wg-${currentPeer.name}.conf`;

    // Fetch .conf text
    let confText = '';
    try {
      const resp = await fetch(`/peers/${currentPeer.id}/conf`);
      confText = await resp.text();
    } catch (e) {
      alert('Falha ao buscar .conf.');
      return;
    }

    // Mobile path: Web Share API with file attachment
    if (navigator.canShare && navigator.canShare({ files: [new File([confText], fileName, { type: 'text/plain' })] })) {
      const file = new File([confText], fileName, { type: 'text/plain' });
      try {
        await navigator.share({ text: message, files: [file] });
        close();
        return;
      } catch (e) {
        if (e.name === 'AbortError') return;  // user cancelled
        // fall through to desktop path
      }
    }

    // Desktop path: download .conf + open chat
    const blob = new Blob([confText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    setTimeout(() => {
      window.open(
        `https://wa.me/${fullNumber}?text=${encodeURIComponent(message)}`,
        '_blank', 'noopener'
      );
    }, 500);
    close();
  }

  sendBtn.addEventListener('click', send);

  document.querySelectorAll('.wa-trigger').forEach(btn => {
    btn.addEventListener('click', (e) => { e.preventDefault(); open(btn); });
  });

  modal.querySelectorAll('[data-modal-close]').forEach(el => {
    el.addEventListener('click', close);
  });

  document.addEventListener('keydown', (e) => {
    if (!modal.classList.contains('open')) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
  });
})();
