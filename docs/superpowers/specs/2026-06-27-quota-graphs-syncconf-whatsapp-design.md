# Design: Quotas, gráficos, syncconf hot-reload e partilha por WhatsApp

**Data:** 2026-06-27
**Estado:** Aprovado (pending implementation plan)
**Project:** wg-admin

## Resumo

Quatro features para tornar o wg-admin mais prático no dia-a-dia:

1. **syncconf hot-reload** — eliminar o downtime de ~1s ao criar peers novos.
2. **Cotas de tráfego** — por-peer (rolling 30 dias) e global, com auto-suspensão
   no caso per-peer e aviso visual+kill switch no caso global.
3. **Gráficos de bandwidth** — sparkline SVG por card, modal Chart.js por peer e
   gráfico global stacked no topo.
4. **Partilha por WhatsApp** — modal que pede DDI+número e envia o `.conf` como
   anexo via Web Share API (mobile) ou download+chat (desktop).

Feature bónus que emergiu durante o design: **kill switch VPN** no sidebar para
`systemctl stop/start wg-quick@wg0`.

## Decisões de produto (Q&A)

- **Cota**: rolling 30 dias, calculada como soma dos daily buckets existentes em
  `bandwidth.json`. Quando últimos 30 dias > limite → suspende. Quando baixa
  abaixo do limite → reativa. Mesmo limiar para suspender e reativar.
- **Cota global**: apenas contador no sidebar + banner vermelho no topo quando
  excedida. Não suspende automaticamente — admin decide via kill switch.
- **Unidade**: sempre GB, suporta decimal (ex: 2.5).
- **Gráficos**: sparkline no card + modal ao clicar + gráfico global no topo.
- **WhatsApp**: `.conf` como anexo, não inline. Modal pede DDI+número.
- **syncconf**: usado apenas no `create` (aditivo puro, zero downtime). Delete e
  toggle mantêm `wg-quick restart` (precisam de limpar PostUp/iptables).

## Arquitectura geral

```
┌─────────────────────────────────────────────────────────────┐
│ Sidebar                    │ Main content                    │
│                            │                                 │
│ • service online (pulse)   │ [Banner vermelho se global quota estourou]
│ • VPN: 23.4 / 100 GB (30d) │                                 │
│ • [Ativar/Desativar VPN]   │ Stats bar (total/ativos/importados)
│                            │                                 │
│ Nav:                       │ [Gráfico global 30d stacked]    │
│   Peers                    │                                 │
│   Change password          │ Peer grid:                      │
│                            │   ┌─────────────────────┐       │
│                            │   │ Card com sparkline   │       │
│                            │   │ + quota bar (se > 0) │       │
│                            │   │ + 5 botões:          │       │
│                            │   │  .conf/QR/WA/Edit/...│       │
│                            │   └─────────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

Novos módulos e responsabilidades:

- `src/wg_admin/quota.py` (NOVO) — verificação de cotas per-peer e global.
- `src/wg_admin/state.py` — schema migration para novos campos.
- `src/wg_admin/bandwidth.py` — timer chama quota check após sampling.
- `src/wg_admin/wg.py` — `wg_syncconf`, `wg_interface_active`, e `apply_state_to_wg` (movida de app.py).
- `src/wg_admin/app.py` — novas rotas, context processor, chama `wg.apply_state_to_wg`.
- `templates/peers.html` — sparklines, modal de bandwidth, banner de quota.
- `templates/base.html` — kill switch e contador global no sidebar.
- `templates/peer_form.html` + `peer_edit.html` — campo `quota_gb`.
- `static/vendor/chartjs.min.js` — Chart.js v4 local (sem CDN runtime).
- `static/js/bandwidth-modal.js` + `static/js/whatsapp-modal.js` — JS modular.

## Schema — peer

Adiciona 3 campos. Sem breaking change: peers antigos recebem defaults via
`migrate_state` no `load_state`.

```python
{
  "id": "...",
  "name": "...",
  "notes": "...",
  "public_key": "...",
  "private_key_enc": "...",
  "ip": "10.0.0.5",
  "disabled": false,                  # existe hoje — manual
  "quota_gb": 10.0,                   # NOVO — 0 = ilimitado
  "quota_suspended": false,           # NOVO — auto por cota
  "quota_state_updated_at": null,     # NOVO — ISO timestamp da última mudança
  "created_at": "..."
}
```

Renderização do wg0.conf: peer é comentado se `disabled` **OU** `quota_suspended`.

## Config — `config.ini`

Nova secção:

```ini
[quota]
global_quota_gb = 100   # 0 = ilimitado
```

### Defaults em `config.py`

`load_config` precisa de garantir que `cfg["quota"]` existe mesmo em
`config.ini` antigo (sem a secção). Adiciona um `ConfigParser` com defaults
pré-populados:

```python
DEFAULTS = {
    "quota": {"global_quota_gb": "0"},
    # ... outras secções default ...
}

def load_config(path):
    parser = configparser.ConfigParser()
    for section, values in DEFAULTS.items():
        parser.add_section(section)
        for k, v in values.items():
            parser.set(section, k, v)
    parser.read(path)
    return parser
```

Assim `cfg["quota"].getfloat("global_quota_gb", 0)` nunca lança KeyError.

---

## Feature 1: syncconf hot-reload

### Nova função em `wg.py`

```python
def wg_syncconf(interface: str = "wg0") -> bool:
    """Aplica mudanças ao interface em runtime sem restart.

    Faz `wg-quick strip <interface>` para remover PostUp/PostDown/Address/etc
    e envia o resultado via stdin a `wg syncconf <interface> /dev/stdin`.

    Retorna True se aplicou com sucesso, False se falhou.
    """
    try:
        strip = subprocess.run(
            ["wg-quick", "strip", interface],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["wg", "syncconf", interface, "/dev/stdin"],
            input=strip.stdout, capture_output=True, text=True, check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
```

### `_apply_state_to_wg` move de `app.py` para `wg.py`

Hoje `_apply_state_to_wg` vive em `app.py`. Como `bandwidth.main` precisa de a
chamar após quota check (e bandwidth.py já importa de wg.py), movê-la para
`wg.py` evita import circular. Renomeada para `apply_state_to_wg` (pública).

```python
# em wg.py
def apply_state_to_wg(s: dict, cfg, mode: str = "syncconf") -> None:
    """Regenera /etc/wireguard/<interface>.conf a partir de state e aplica.

    mode="syncconf": tenta wg syncconf primeiro (zero downtime), fallback restart.
    mode="restart": wg-quick restart directo (necessário para limpar PostUp/iptables).
    """
    interface_path = Path(f"/etc/wireguard/{cfg['wg']['interface']}.conf")
    # ... escrever wg0.conf como antes ...

    if mode == "syncconf":
        if wg_syncconf(cfg["wg"]["interface"]):
            return
    wg_quick_restart(cfg["wg"]["interface"])
```

`app.py` passa a chamar `wg.apply_state_to_wg(s, cfg, mode=...)` em vez do
module-level `_apply_state_to_wg`. Mantém-se o monkeypatch dos testes via
`wg_admin.wg.apply_state_to_wg`.

### Quem usa o quê

- `peer_new` (create) → `mode="syncconf"` (zero downtime ao adicionar).
- `peer_delete` → `mode="restart"` (limpa regras PostUp/iptables).
- `peer_toggle` → `mode="restart"` (mesma razão).
- `bandwidth.main` (quota suspend) → `mode="syncconf"` se possível, fallback restart.

### Caso limite — syncconf não cobre mudanças PostUp

Syncconf aplica apenas os peers, não roda PostUp/PostDown. Como o wg-admin
gera confs com PostUp para NAT, remover um peer via syncconf deixaria regras
iptables órfãs. Por isso delete/toggle usam restart completo.

---

## Feature 3: Cotas de tráfego

### `quota.py` — funções

```python
def check_quotas(state_data: dict, bw: dict, global_quota_gb: float) -> list:
    """Actualiza quota_suspended em cada peer conforme uso 30d.

    Retorna lista de mudanças: [{"peer_id", "name", "action": "suspend"|"reactivate"}].
    """
    changes = []
    for peer in state_data["peers"]:
        if peer.get("quota_gb", 0) <= 0:
            continue  # ilimitado
        bw_stats = bandwidth.get_peer_stats(bw, peer["public_key"])
        used_gb = (bw_stats["thirty_day_rx"] + bw_stats["thirty_day_tx"]) / (1024**3)
        was_suspended = peer.get("quota_suspended", False)
        if used_gb > peer["quota_gb"]:
            if not was_suspended:
                peer["quota_suspended"] = True
                peer["quota_state_updated_at"] = state.utc_now_iso()
                changes.append({"peer_id": peer["id"], "name": peer["name"], "action": "suspend"})
        else:
            if was_suspended:
                peer["quota_suspended"] = False
                peer["quota_state_updated_at"] = state.utc_now_iso()
                changes.append({"peer_id": peer["id"], "name": peer["name"], "action": "reactivate"})
    return changes


def global_usage_gb(bw: dict) -> float:
    """Soma últimos 30 dias de todos os peers (para display no sidebar)."""
    cutoff = bandwidth.cutoff_date_str()
    total = 0
    for peer_data in bw.get("peers", {}).values():
        for d, v in peer_data.get("daily", {}).items():
            if d > cutoff:
                total += v["rx"] + v["tx"]
    return total / (1024**3)


def global_quota_exceeded(bw: dict, global_quota_gb: float) -> bool:
    if global_quota_gb <= 0:
        return False
    return global_usage_gb(bw) > global_quota_gb
```

### Integração no timer

`bandwidth.main()` (CLI invocada pelo systemd timer) ganha quota check após
`track_sample`. Caminhos de state/config/master_key passam a ser constantes
de módulo em `bandwidth.py` (mesmo valor que `app.py`), com monkeypatch em
testes.

```python
# em bandwidth.py
STATE_PATH = Path("/wg-admin/state.json.enc")
CONFIG_PATH = Path("/wg-admin/config.ini")
SECRETS_DIR = Path("/wg-admin/secrets")

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "track":
        track_sample(path)
        # Quota check — só se state e master_key acessíveis
        try:
            master_key = (SECRETS_DIR / "master.key").read_bytes()
            state_data = state.load_state(STATE_PATH, master_key)
            cfg = config.load_config(CONFIG_PATH)
            global_q = cfg["quota"].getfloat("global_quota_gb", 0)
            changes = quota.check_quotas(state_data, load_bandwidth(path), global_q)
            if changes:
                state.save_state(STATE_PATH, state_data, master_key)
                wg.apply_state_to_wg(state_data, cfg, mode="syncconf")
        except Exception as e:
            print(f"WARN: quota check failed: {e}", file=sys.stderr)
        return 0
```

### UI — sidebar

```html
<div class="sidebar-quota">
  <span class="text-sm text-muted">VPN · 30d</span>
  <div class="quota-display {{ 'danger' if global_exceeded }}">
    {{ global_used_gb|round(1) }} / {{ global_quota_gb|int }} GB
  </div>
  {% if global_exceeded %}
  <span class="text-sm text-danger">cota excedida</span>
  {% endif %}
</div>
```

Valores injectados via context processor (uma leitura de `bandwidth.json` por
request).

### UI — topo da `/peers`

```html
{% if global_exceeded %}
<div class="banner banner-danger">
  ⚠ Cota global excedida — {{ global_used_gb|round(1) }} / {{ global_quota_gb|int }} GB · 30d.
  Considera suspender a VPN ou reduzir cotas individuais.
</div>
{% endif %}
```

### UI — card de peer

Quota bar aparece só se `quota_gb > 0`:

```html
{% if peer.quota_gb and peer.quota_gb > 0 %}
<div class="quota-bar" title="{{ used_gb|round(2) }} / {{ peer.quota_gb }} GB">
  <div class="quota-fill {{ quota_class }}"
       style="width: {{ quota_pct if quota_pct <= 100 else 100 }}%"></div>
</div>
{% if peer.quota_suspended %}
<span class="badge badge-danger">SUSPENSO</span>
{% endif %}
{% endif %}
```

`quota_class`: `ok` (<70%), `warn` (70-95%), `danger` (>95% ou suspenso).

### UI — form create/edit

Novo campo após `notes`. Peer criado novo tem `quota_gb=0` por default (via
`migrate_state` em peer init, ou explícito no POST handler):

```html
<label for="quota_gb">COTA (GB, 0 = ilimitado)</label>
<input type="number" id="quota_gb" name="quota_gb" step="0.1" min="0"
       value="{{ peer.quota_gb if peer.quota_gb is defined else 0 }}" placeholder="0">
<p class="hint">Soma dos últimos 30 dias. Ao exceder, suspende automaticamente;
   reativa sozinho quando baixar abaixo do limite.</p>
```

POST handler lê `quota_gb = float(request.form.get("quota_gb", 0) or 0)`.

### Edge cases

- Admin faz disable manualmente (`disabled=True`) e entretanto a quota passa:
  ambos `disabled` e `quota_suspended` ficam True. UI mostra ambos os badges.
- Admin edita `quota_gb` via form: campo é lido no POST, gravado, próximo tick
  do timer reavalia.
- Peer importado (sem `private_key_enc`): quota aplica-se igual — contagem de
  tráfego é por pubkey no `bandwidth.json`, não depende de ter private key.
- Global quota = 0 (default): contador mostra só "X GB", sem limite, sem banner.

---

## Feature 7: Gráficos

### Biblioteca

- **Chart.js v4** local em `static/vendor/chartjs.min.js` (~70KB). Sem CDN runtime.
- **Sparklines**: SVG inline, sem JS lib.

### Endpoints

```python
@app.route("/api/bandwidth/<peer_id>")
@login_required
def api_peer_bandwidth(peer_id):
    s = state.load_state(STATE_PATH, app.config["MASTER_KEY"])
    peer = state.find_peer_by_id(s, peer_id) or abort(404)
    bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
    peer_bw = bw.get("peers", {}).get(peer["public_key"], {})
    daily = peer_bw.get("daily", {})
    today = datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    return jsonify({
        "dates": dates,
        "rx": [daily.get(d, {"rx": 0})["rx"] for d in dates],
        "tx": [daily.get(d, {"tx": 0})["tx"] for d in dates],
    })


@app.route("/api/bandwidth/global")
@login_required
def api_global_bandwidth():
    bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
    # Top 5 peers por uso 30d; resto em "outros"
    # ... retorna {"dates", "series": [{"name", "data": [...]}]}
```

### Sparkline

Helper em Python — dados prontos no template, sem JS:

```python
def sparkline_path(values: list[int], width=80, height=24) -> str:
    """Gera 'd' do SVG path. Returns '' se sem dados."""
    non_zero = [v for v in values if v > 0]
    if not non_zero:
        return ""
    max_v = max(values)
    step = width / (len(values) - 1) if len(values) > 1 else 0
    points = []
    for i, v in enumerate(values):
        x = i * step
        y = height - (v / max_v) * height
        points.append(f"{x:.1f},{y:.1f}")
    return "M " + " L ".join(points)
```

No `peers_list`, preparar `sparkline_data[peer_id] = (path, total_gb)` para o
template. Template renderiza:

```html
{% if pv.sparkline %}
<svg class="sparkline" viewBox="0 0 80 24" width="80" height="24"
     data-peer-id="{{ peer.id }}" data-peer-name="{{ peer.name }}">
  <path d="{{ pv.sparkline }}" fill="none"
        stroke="var(--accent)" stroke-width="1.5"/>
</svg>
{% else %}
<span class="text-muted text-sm">sem dados</span>
{% endif %}
```

### Modal bandwidth

Template paralelo ao modal QR. JS busca `/api/bandwidth/<id>` e renderiza
line chart com 2 séries:

```javascript
const resp = await fetch(`/api/bandwidth/${peerId}`);
const data = await resp.json();
new Chart(ctx, {
  type: "line",
  data: {
    labels: data.dates,
    datasets: [
      { label: "Download (rx)", data: data.rx, borderColor: "#10b981" },
      { label: "Upload (tx)", data: data.tx, borderColor: "#3b82f6" },
    ],
  },
  options: { responsive: true, scales: { y: { ticks: formatBytes } } },
});
```

Trigger: clica em qualquer sparkline.

### Gráfico global no topo

`<canvas id="global-chart">` acima da grid de peers. JS busca
`/api/bandwidth/global` no load da página. Stacked area, top 5 peers nomeados,
resto aggregado em "outros".

### Performance

- `bandwidth.json` com 30 dias × N peers: ~30N entradas. Para 100 peers, 3000
  entradas, ~150KB JSON. Endpoints fazem parsing sob demanda sem cache
  (aceitável para painel admin).

---

## Partilha por WhatsApp

### Constraint técnica

`wa.me/?text=...` não suporta anexar ficheiros. Soluções:

- **Mobile** (HTTPS obrigatório, já temos): Web Share API Level 2 com
  `navigator.share({ files: [File], text })`. Abre folha de partilha nativa;
  utilizador escolhe WhatsApp; cliente recebe `.conf` como anexo.
- **Desktop**: Web Share API não suporta ficheiros. Fluxo:
  1. Clica em "Abrir WhatsApp".
  2. Browser descarrega automaticamente o `.conf` (Blob + `<a download>`).
  3. Abre `wa.me/<numero>?text=<mensagem-curta>` em nova aba.
  4. Admin anexa manualmente o ficheiro que acabou de descarregar.

### Botão no card

```html
{% if peer.private_key_enc %}
<a href="#" class="btn secondary wa-trigger"
   data-peer-id="{{ peer.id }}"
   data-peer-name="{{ peer.name }}">
  <svg>...</svg> WhatsApp
</a>
{% endif %}
```

### Modal — input de telefone

```html
<div class="modal" id="wa-modal" ...>
  <div class="modal-card">
    <button class="modal-close" data-modal-close>×</button>
    <h2>Partilhar via WhatsApp — <span class="peer-name"></span></h2>

    <label for="wa-ddi">DDI (código do país)</label>
    <select id="wa-ddi">
      <option value="351">🇵🇹 +351 (PT)</option>
      <option value="55">🇧🇷 +55 (BR)</option>
      <option value="34">🇪🇸 +34 (ES)</option>
      <option value="33">🇫🇷 +33 (FR)</option>
      <option value="1">🇺🇸 +1 (US)</option>
      <option value="">Outro (escreve DDI+número abaixo)</option>
    </select>

    <label for="wa-phone">NÚMERO</label>
    <input type="tel" id="wa-phone" placeholder="912 345 678">

    <p class="hint">
      Se DDI = "Outro", escreve DDI+número juntos (ex: <code>44 7700 900123</code>).
      Caso contrário, só o número sem DDI. Apenas dígitos.
    </p>

    <details>
      <summary>Pré-visualizar mensagem</summary>
      <pre class="wa-preview"></pre>
    </details>

    <div class="modal-actions">
      <button id="wa-send" class="btn" disabled>Abrir WhatsApp</button>
      <button data-modal-close class="btn secondary">Cancelar</button>
    </div>
  </div>
</div>
```

### JS — fluxo de envio

```javascript
async function open(trigger) {
  const peerId = trigger.dataset.peerId;
  const peerName = trigger.dataset.peerName;
  modal.querySelector(".peer-name").textContent = peerName;

  // Busca .conf em runtime (mantém o modal rápido)
  const resp = await fetch(`/peers/${peerId}/conf`);
  const confText = await resp.text();

  const file = new File([confText], `wg-${peerName}.conf`, { type: "text/plain" });
  const message = `Olá ${peerName}! Segue em anexo a configuração da tua VPN wg-admin.\n` +
                  `Importa em: app WireGuard → Adicionar → Importar tunnel(s) from file.`;
  modal.querySelector(".wa-preview").textContent = message;

  modal.dataset.confText = confText;
  modal.dataset.fileName = `wg-${peerName}.conf`;
  modal.dataset.message = message;
  // ... abrir modal
}

function send() {
  const ddi = document.getElementById("wa-ddi").value;
  const phone = document.getElementById("wa-phone").value.replace(/\D/g, "");
  const fullNumber = ddi + phone;
  if (fullNumber.length < 6) return;

  const message = modal.dataset.message;
  const confText = modal.dataset.confText;
  const fileName = modal.dataset.fileName;

  // Mobile path: Web Share API
  if (navigator.canShare && navigator.canShare({ files: [new File([confText], fileName)] })) {
    const file = new File([confText], fileName, { type: "text/plain" });
    navigator.share({ text: message, files: [file] }).catch(() => {});
    close();
    return;
  }

  // Desktop path: download + chat
  const blob = new Blob([confText], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);

  setTimeout(() => {
    window.open(`https://wa.me/${fullNumber}?text=${encodeURIComponent(message)}`,
                "_blank", "noopener");
  }, 500);
  close();
}
```

### UX details

- **Último DDI lembrado** em `localStorage` (`wa.ddi`).
- Botão "Abrir WhatsApp" fica `disabled` até `phone.length >= 6`.
- Não guardamos o número após envio — privacidade.
- Se `.conf` > 1500 chars: warning no modal "mensagem longa, pode não caber no WhatsApp" antes do fallback.

---

## Kill switch VPN (feature bónus)

### Botão no sidebar

```html
<div class="sidebar-vpn-control">
  <form method="post" action="/vpn/toggle"
        onsubmit="return confirm('Desativar VPN? Todos os peers vão desconectar.')">
    <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
    <button type="submit" class="btn {{ 'danger' if vpn_active else 'success' }}">
      {{ 'Desativar VPN' if vpn_active else 'Ativar VPN' }}
    </button>
  </form>
</div>
```

### Rota

```python
@app.route("/vpn/toggle", methods=["POST"])
@login_required
def vpn_toggle():
    interface = cfg["wg"]["interface"]
    if wg.wg_interface_active(interface):
        subprocess.run(["systemctl", "stop", f"wg-quick@{interface}"], check=True)
        flash("VPN desativada — todos os peers desconectados", "warning")
    else:
        subprocess.run(["systemctl", "start", f"wg-quick@{interface}"], check=True)
        flash("VPN reativada", "success")
    return redirect(request.referrer or url_for("peers_list"))
```

### Helper em `wg.py`

```python
def wg_interface_active(interface: str = "wg0") -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", f"wg-quick@{interface}"],
        )
        return result.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
```

### Context processor

```python
@app.context_processor
def inject_globals():
    bw = bandwidth.load_bandwidth(BANDWIDTH_PATH)
    global_q = cfg["quota"].getfloat("global_quota_gb", 0) if cfg.has_section("quota") else 0
    return {
        "vpn_active": wg.wg_interface_active(cfg["wg"]["interface"]),
        "global_used_gb": quota.global_usage_gb(bw),
        "global_quota_gb": global_q,
        "global_exceeded": quota.global_quota_exceeded(bw, global_q),
    }
```

Custo por request: 1 `systemctl is-active` (sub-ms) + 1 read do bandwidth.json
(que já é feito em `/peers`).

---

## Estratégia de testes

TDD onde quer que seja possível. Manter coverage > 90%.

### Testes unitários por módulo

**`wg.py`** (nova cobertura):
- `wg_syncconf` sucesso → True, faz 2 subprocess calls
- `wg_syncconf` com `wg-quick strip` a falhar → False
- `wg_syncconf` com `wg syncconf` a falhar → False
- `wg_interface_active` retorna True/False conforme `systemctl` exit code

**`quota.py`** (novo, ~12 testes):
- `check_quotas` com peer sem quota → não mexe
- `check_quotas` quando uso < quota → não suspende
- `check_quotas` quando uso > quota pela 1ª vez → suspende, retorna change
- `check_quotas` quando uso baixa → reativa, retorna change
- `check_quotas` quando uso mantém-se acima → não retorna change
- `global_usage_gb` soma correctly
- `global_quota_exceeded` com limite 0 → sempre False
- `global_quota_exceeded` quando uso > limite → True

**`state.py`**:
- `migrate_state` adiciona 3 campos a peer antigo
- `migrate_state` não sobrescreve campos já presentes

**`app.py`** (rotas novas):
- `GET /api/bandwidth/<peer_id>` 200 com 30 pontos
- `GET /api/bandwidth/<peer_id>` 404 se peer não existe
- `GET /api/bandwidth/global` com 0 peers → listas vazias
- `GET /api/bandwidth/global` com 5+ peers → top 5 + "outros"
- `POST /vpn/toggle` quando ativa → chama `systemctl stop`
- `POST /vpn/toggle` quando inativa → chama `systemctl start`
- `POST /vpn/toggle` sem CSRF → 403
- `POST /vpn/toggle` sem login → redirect
- `wg.apply_state_to_wg(mode="syncconf")` sucesso → não chama restart
- `wg.apply_state_to_wg(mode="syncconf")` falha → chama restart (fallback)
- `wg.apply_state_to_wg(mode="restart")` → só chama restart

**`bandwidth.py`** (integração quota):
- `main() track` chama `check_quotas` após sample
- `main() track` se quota muda → grava state + chama apply

### Testes de schema migration
- Carregar `state.json.enc` antigo (sem campos quota) → campos aparecem
- Salvar + recarregar → campos preservados
- Peer criado novo → tem `quota_gb=0`, `quota_suspended=False`

### Smoke test manual

Adicionar a `docs/smoke-test.md`:
- Criar peer com quota 0.001 GB. Num cliente ligado à VPN, fazer `curl http://speedtest.tele2.net/1MB.zip` (ou similar) para gerar ~1MB de tráfego. Esperar ≤5min (próximo tick do timer). Verificar no painel: peer aparece com badge "SUSPENSO" e `wg show wg0 dump` já não lista o peer.
- Editar quota para 10 GB, esperar próximo tick, verificar reativação.
- Click em WhatsApp → modal abre → preview mostra "Olá {nome}" → em desktop, clicar "Abrir WhatsApp" → `.conf` descarrega para Downloads e separador abre em `wa.me`.
- Click em sparkline → modal abre → gráfico renderiza com 30 pontos (alguns podem ser 0).
- Desativar VPN via sidebar → `wg show wg0` retorna vazio (interface down).
- Reativar VPN → peer volta a aparecer em `wg show` dentro de ~3s.

### CI

Não muda — GitHub Actions já corre pytest + ruff em Python 3.11/3.12.

---

## Limitações e trade-offs

- **Cota sem hysteresis**: mesma threshold para suspender e reativar. Se uso
  fica exactamente no limite, pode oscillar entre ticks. Aceitável — dados
  acumulam-se lentamente.
- **syncconf só no create**: delete/toggle continuam a causar ~1s downtime.
  Documentado em README.
- **WhatsApp no desktop**: exige attach manual. Não há forma de contornar
  sem APIs proprietárias.
- **Web Share API requer HTTPS**: já temos TLS configurado no install.
- **Kill switch via systemctl**: se o unit falhar a iniciar, botão pode mostrar
  estado incorrecto até próximo request. Aceitável.
- **Quota check no bandwidth timer**: se o timer falhar (serviço parado), cotas
  não são verificadas. Considerar adicionar health-check do timer no futuro.

## Fora de âmbito (YAGNI)

- Tags/grupos de peers e busca/filtro (feature 4 da lista original).
- API REST com token (feature 5).
- Backup/restore criptografado (feature 6).
- Multi-admin + audit log (feature 9).
- Notificações Telegram/webhook (feature 8).
- TOTP 2FA.
- PSK (pre-shared keys).

Cada uma destas pode ser adicionada como spec separada se surja necessidade.
