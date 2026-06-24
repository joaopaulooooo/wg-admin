#!/usr/bin/env bash
# install.sh — idempotent installer for wg-admin.
# Run as root: sudo bash install.sh
set -euo pipefail

INSTALL_DIR="/wg-admin"
PYTHON_MIN_VERSION="3.11"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo ">>> $*"; }

# --- Pre-flight ---
[[ $EUID -eq 0 ]] || err "Must run as root (use sudo)."

if ! command -v python3 >/dev/null; then
  info "python3 not found — installing via apt"
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip
fi

# Ensure python3-venv is available (missing on fresh Ubuntu 24.04/25.04 even if python3 is present)
if ! python3 -c "import ensurepip" 2>/dev/null; then
  info "python3-venv/ensurepip em falta — a instalar"
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip || \
    err "Não consegui instalar python3-venv. Instala manualmente: apt install python3-venv"
fi

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
[[ "$(printf "%s\n%s" "$PYTHON_MIN_VERSION" "$PYVER" | sort -V | head -1)" == "$PYTHON_MIN_VERSION" ]] \
  || err "Python >= $PYTHON_MIN_VERSION required (have $PYVER)"

# --- WireGuard auto-install ---
detect_distro() {
  if [[ -f /etc/debian_version ]]; then echo "debian"
  elif [[ -f /etc/fedora-release ]] || [[ -f /etc/redhat-release ]]; then echo "rhel"
  elif [[ -f /etc/arch-release ]]; then echo "arch"
  elif [[ -f /etc/alpine-release ]]; then echo "alpine"
  else echo "unknown"; fi
}

install_wireguard() {
  local distro; distro=$(detect_distro)
  info "Detected distro: $distro"
  case "$distro" in
    debian)
      info "Installing wireguard via apt"
      apt-get update -y
      DEBIAN_FRONTEND=noninteractive apt-get install -y \
        wireguard wireguard-tools iptables iproute2
      ;;
    rhel)
      info "Installing wireguard via dnf"
      dnf install -y epel-release || true
      dnf install -y wireguard-tools iptables iproute
      ;;
    arch)
      info "Installing wireguard via pacman"
      pacman -Sy --noconfirm wireguard-tools iptables iproute2
      ;;
    alpine)
      info "Installing wireguard via apk"
      apk add --no-cache wireguard-tools-wg wireguard-tools-wg-quick iptables iproute2
      ;;
    *)
      err "Distro não suportado para auto-install. Instala o WireGuard manualmente e volta a correr."
      ;;
  esac
}

ensure_wireguard() {
  if command -v wg >/dev/null && command -v wg-quick >/dev/null; then
    return
  fi
  info "WireGuard não encontrado — vou instalar"
  install_wireguard
  command -v wg >/dev/null || err "Instalação do WireGuard falhou (wg não disponível)"
  command -v wg-quick >/dev/null || err "Instalação do WireGuard falhou (wg-quick não disponível)"
  info "WireGuard instalado com sucesso"
}

# Validate that an IP belongs to a subnet (CIDR notation)
ip_in_subnet() {
  local ip="$1" subnet="$2"
  python3 -c "
import sys, ipaddress
try:
    net = ipaddress.ip_network('$subnet', strict=False)
    addr = ipaddress.ip_address('$ip')
    print('OK' if addr in net else 'FAIL')
except ValueError as e:
    print('FAIL')
"
}

ensure_wg0_conf() {
  if [[ -f /etc/wireguard/wg0.conf ]]; then
    return
  fi
  info ""
  info "Não encontrei /etc/wireguard/wg0.conf — vou criar configuração inicial"
  info ""
  read -r -p "Subnet [10.0.0.0/24]: " SUBNET
  SUBNET=${SUBNET:-10.0.0.0/24}

  while true; do
    read -r -p "Server IP na subnet [10.0.0.1]: " SERVER_IP
    SERVER_IP=${SERVER_IP:-10.0.0.1}
    if [[ "$(ip_in_subnet "$SERVER_IP" "$SUBNET")" == "OK" ]]; then
      break
    fi
    info "ERRO: $SERVER_IP não pertence a $SUBNET. Tenta outra vez."
  done

  read -r -p "Listen port UDP [51820]: " WG_PORT
  WG_PORT=${WG_PORT:-51820}

  # Descobrir interface de rede default para NAT
  DEFAULT_IF=$(ip route show default 2>/dev/null | awk '{print $5; exit}')
  DEFAULT_IF=${DEFAULT_IF:-eth0}
  info "Interface para NAT (default route): $DEFAULT_IF"

  # Prefix da subnet
  PREFIX=${SUBNET##*/}

  # Gerar server private key
  info "A gerar server keypair"
  SERVER_PRIV=$(wg genkey)

  # Criar config
  mkdir -p /etc/wireguard
  chmod 700 /etc/wireguard
  cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = $SERVER_IP/$PREFIX
ListenPort = $WG_PORT
PrivateKey = $SERVER_PRIV
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o $DEFAULT_IF -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o $DEFAULT_IF -j MASQUERADE
EOF
  chmod 600 /etc/wireguard/wg0.conf

  # Ativar IP forwarding (persistent via /etc/sysctl.d/ — works on Ubuntu 25.04 too)
  info "A ativar IP forwarding"
  sysctl -w net.ipv4.ip_forward=1 >/dev/null
  mkdir -p /etc/sysctl.d
  echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-wg-admin.conf
  sysctl --system >/dev/null 2>&1 || true

  # Abrir porta UDP
  if command -v firewall-cmd >/dev/null; then
    info "A abrir porta $WG_PORT/udp no firewalld"
    firewall-cmd --add-port="$WG_PORT/udp" --permanent
    firewall-cmd --reload
  elif command -v ufw >/dev/null; then
    info "A abrir porta $WG_PORT/udp no ufw"
    ufw allow "$WG_PORT/udp"
  fi

  # Arrancar wg-quick
  info "A arrancar wg-quick@wg0"
  systemctl enable --now wg-quick@wg0
  sleep 1
  if systemctl is-active --quiet wg-quick@wg0; then
    info "WireGuard a correr"
  else
    err "wg-quick não arrancou — ver logs: journalctl -u wg-quick@wg0 -e"
  fi

  SERVER_PUB=$(echo "$SERVER_PRIV" | wg pubkey)
  info "Setup completo. Server public key: $SERVER_PUB"
  info ""
}

ensure_wireguard
ensure_wg0_conf

for cmd in wg wg-quick systemctl; do
  command -v "$cmd" >/dev/null || err "$cmd not found"
done

# --- Directories ---
info "Creating $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"/{secrets,templates,static,systemd}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp -r "$SCRIPT_DIR"/src "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR"/templates "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR"/static "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR"/systemd "$INSTALL_DIR/"
cp "$SCRIPT_DIR"/requirements*.txt "$INSTALL_DIR/"
cp "$SCRIPT_DIR"/config.ini.example "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/install.sh" ] && cp "$SCRIPT_DIR/install.sh" "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/uninstall.sh" ] && cp "$SCRIPT_DIR/uninstall.sh" "$INSTALL_DIR/"

# --- venv + deps ---
info "Creating venv and installing deps"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# --- Secrets ---
info "Generating secrets (if missing)"
if [[ ! -s "$INSTALL_DIR/secrets/master.key" ]]; then
    head -c 32 /dev/urandom > "$INSTALL_DIR/secrets/master.key"
fi
if [[ ! -s "$INSTALL_DIR/secrets/session.key" ]]; then
    head -c 32 /dev/urandom > "$INSTALL_DIR/secrets/session.key"
fi
chmod 0700 "$INSTALL_DIR/secrets"
chmod 0600 "$INSTALL_DIR/secrets"/*.key

# --- Admin password ---
if [[ ! -f "$INSTALL_DIR/secrets/auth.ini" ]]; then
  info "Setting admin password"
  read -r -s -p "Admin password: " PWD1; echo
  read -r -s -p "Confirm: " PWD2; echo
  [[ "$PWD1" == "$PWD2" ]] || err "Passwords don't match"
  HASH=$("$INSTALL_DIR/venv/bin/python" -c "
import sys
from argon2 import PasswordHasher
print(PasswordHasher(time_cost=3, memory_cost=16384, parallelism=1).hash(sys.argv[1]))
" "$PWD1")
  cat > "$INSTALL_DIR/secrets/auth.ini" <<EOF
password_hash = $HASH
EOF
  chmod 0600 "$INSTALL_DIR/secrets/auth.ini"
fi

# --- config.ini ---
if [[ ! -f "$INSTALL_DIR/config.ini" ]]; then
  info "Configuring"
  read -r -p "Endpoint hostname (e.g. vpn.example.com): " ENDPOINT
  read -r -p "Listen port [51821]: " LISTEN_PORT
  LISTEN_PORT=${LISTEN_PORT:-51821}
  cp "$INSTALL_DIR/config.ini.example" "$INSTALL_DIR/config.ini"
  sed -i "s/^endpoint_host = .*/endpoint_host = $ENDPOINT/" "$INSTALL_DIR/config.ini"
  sed -i "s/^listen_port = .*/listen_port = $LISTEN_PORT/" "$INSTALL_DIR/config.ini"

  # Auto-populate server_public_key from /etc/wireguard/wg0.conf
  if [[ -f /etc/wireguard/wg0.conf ]]; then
    SERVER_PRIV=$(grep '^PrivateKey' /etc/wireguard/wg0.conf | head -1 | sed 's/.*= *//; s/ *$//')
    if [[ -n "$SERVER_PRIV" ]]; then
      SERVER_PUB=$(echo "$SERVER_PRIV" | wg pubkey 2>/dev/null || true)
      if [[ -n "$SERVER_PUB" ]]; then
        info "Detected server public key: $SERVER_PUB"
        "$INSTALL_DIR/venv/bin/python" -c "
import configparser
c = configparser.ConfigParser()
c.read('$INSTALL_DIR/config.ini')
c['wg']['server_public_key'] = '$SERVER_PUB'
with open('$INSTALL_DIR/config.ini', 'w') as f:
    c.write(f)
"
      fi
    fi
  fi
fi

# --- Import existing peers ---
if [[ ! -f "$INSTALL_DIR/state.json.enc" && -f /etc/wireguard/wg0.conf ]]; then
  info "Importing existing peers from /etc/wireguard/wg0.conf"
  info "(chaves privadas dos peers existentes não são importáveis — só existem nos dispositivos)"
  "$INSTALL_DIR/venv/bin/python" -c "
import sys
sys.path.insert(0, '$INSTALL_DIR/src')
from pathlib import Path
from wg_admin import state, wg
parsed = wg.parse_wg_conf(Path('/etc/wireguard/wg0.conf').read_text())
s = state.empty_state()
for p in parsed['peers']:
    ip = p.get('AllowedIPs', '').split('/')[0]
    if not ip: continue
    s['peers'].append({
        'id': state.new_peer_id(),
        'name': f'peer-{ip.split(\".\")[-1]}',
        'notes': 'imported from existing wg0.conf',
        'public_key': p['PublicKey'],
        'private_key_enc': '',  # legacy peers: only the client device has the private key
        'ip': ip,
        'disabled': p.get('disabled', False),
        'created_at': state.utc_now_iso(),
        'imported_from_legacy': True,
    })
master = Path('$INSTALL_DIR/secrets/master.key').read_bytes()
state.save_state(Path('$INSTALL_DIR/state.json.enc'), s, master)
print(f'Imported {len(s[\"peers\"])} peers.')
"
fi

# --- Create initial admin peer if state is empty (fresh install) ---
if [[ -f "$INSTALL_DIR/state.json.enc" ]]; then
  PEER_COUNT=$("$INSTALL_DIR/venv/bin/python" -c "
import sys
sys.path.insert(0, '$INSTALL_DIR/src')
from pathlib import Path
from wg_admin import state
master = Path('$INSTALL_DIR/secrets/master.key').read_bytes()
s = state.load_state(Path('$INSTALL_DIR/state.json.enc'), master)
print(len(s['peers']))
")
  if [[ "$PEER_COUNT" == "0" ]]; then
    info ""
    info "Estado sem peers — vou criar um inicial para te ligares"
    read -r -p "Nome do peer inicial [admin]: " PEER_NAME
    PEER_NAME=${PEER_NAME:-admin}
    PEER_NAME=${PEER_NAME//[^a-zA-Z0-9 ._-]/_}

    info "A gerar par de chaves para o peer"
    SUBNET_VAL=$(grep '^subnet' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')
    SERVER_IP_VAL=$(grep '^server_ip' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')

    "$INSTALL_DIR/venv/bin/python" <<PYEOF
import sys
sys.path.insert(0, "$INSTALL_DIR/src")
from pathlib import Path
from wg_admin import state, wg

master = Path("$INSTALL_DIR/secrets/master.key").read_bytes()
s = state.load_state(Path("$INSTALL_DIR/state.json.enc"), master)

# Gerar par de chaves do peer
priv, pub = wg.wg_genkey()

# Alocar IP
ip = state.allocate_ip(s, "$SUBNET_VAL", "$SERVER_IP_VAL")

# Adicionar ao state
peer = {
    "id": state.new_peer_id(),
    "name": "$PEER_NAME",
    "notes": "initial peer created during install",
    "public_key": pub,
    "private_key_enc": state.encrypt_private_key(priv, master),
    "ip": ip,
    "disabled": False,
    "created_at": state.utc_now_iso(),
}
state.add_peer(s, peer)
state.save_state(Path("$INSTALL_DIR/state.json.enc"), s, master)

# Regenerar wg0.conf com este peer
existing = wg.parse_wg_conf(Path("/etc/wireguard/wg0.conf").read_text())
wg_peers = [{
    "PublicKey": p["public_key"],
    "AllowedIPs": f"{p['ip']}/32",
    "disabled": p.get("disabled", False),
    "name": p["name"],
} for p in s["peers"]]
new_conf = wg.generate_wg_conf(existing["interface"], wg_peers)
Path("/etc/wireguard/wg0.conf").write_text(new_conf)

print(f"Peer criado: {peer['name']} ({peer['ip']})")
print(f"Public key: {peer['public_key']}")
PYEOF

    # Restart wg-quick para carregar o peer novo
    info "A reiniciar wg-quick para aplicar o peer novo"
    systemctl restart wg-quick@wg0
  fi
fi

# --- TLS cert detection ---
HOST=$(grep '^endpoint_host' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')
PORT=$(grep '^listen_port' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')

CERT_DIR="/etc/letsencrypt/live/$HOST"
CERT_PATH=""
KEY_PATH=""

if [[ -d "$CERT_DIR" && -r "$CERT_DIR/fullchain.pem" && -r "$CERT_DIR/privkey.pem" ]]; then
  info "Found existing Let's Encrypt cert for $HOST — using it for TLS"
  CERT_PATH="$CERT_DIR/fullchain.pem"
  KEY_PATH="$CERT_DIR/privkey.pem"
else
  info "No existing cert at $CERT_DIR — checking other options"
  # Try Apache's default cert path
  if [[ -d /etc/apache2 ]] && apache2ctl -S 2>/dev/null | grep -q "$HOST"; then
    info "Apache vhost for $HOST found — recommend using Apache reverse proxy"
  fi
fi

# --- run.py: entry point that reads TLS from env ---
info "Writing run.py entry point"
cat > "$INSTALL_DIR/run.py" <<'PYEOF'
#!/usr/bin/env python3
"""Production entry point — reads port + TLS paths from env."""
import os
import sys
sys.path.insert(0, "/wg-admin/src")

from wg_admin.app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("WG_ADMIN_HOST", "0.0.0.0")
    port = int(os.environ.get("WG_ADMIN_PORT", "51821"))
    cert = os.environ.get("WG_ADMIN_CERT")
    key = os.environ.get("WG_ADMIN_KEY")
    if cert and key:
        app.run(host=host, port=port, ssl_context=(cert, key))
    else:
        app.run(host=host, port=port)
PYEOF
chmod +x "$INSTALL_DIR/run.py"

# --- systemd service unit (no socket activation — Flask listens directly) ---
info "Installing systemd service"
SERVICE_FILE="/etc/systemd/system/wg-admin.service"
{
  echo "[Unit]"
  echo "Description=wg-admin Flask service"
  echo "After=network.target"
  echo ""
  echo "[Service]"
  echo "Type=simple"
  echo "User=root"
  echo "WorkingDirectory=$INSTALL_DIR"
  echo "Environment=PYTHONUNBUFFERED=1"
  echo "Environment=PYTHONPATH=$INSTALL_DIR/src"
  echo "Environment=WG_ADMIN_PORT=$PORT"
  if [[ -n "$CERT_PATH" ]]; then
    echo "Environment=WG_ADMIN_CERT=$CERT_PATH"
    echo "Environment=WG_ADMIN_KEY=$KEY_PATH"
  fi
  echo "ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/run.py"
  echo "Restart=on-failure"
  echo "RestartSec=5"
  echo ""
  echo "# Hardening"
  echo "ProtectSystem=strict"
  echo "ProtectHome=yes"
  echo "ReadWritePaths=$INSTALL_DIR /etc/wireguard"
  echo "NoNewPrivileges=yes"
  echo "PrivateTmp=yes"
  echo "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX"
  echo "LockPersonality=yes"
  echo ""
  echo "[Install]"
  echo "WantedBy=multi-user.target"
} > "$SERVICE_FILE"

# Remove old socket unit if present
if [[ -f /etc/systemd/system/wg-admin.socket ]]; then
  info "Removing old socket unit (not used anymore)"
  systemctl disable --now wg-admin.socket 2>/dev/null || true
  rm -f /etc/systemd/system/wg-admin.socket
fi

systemctl daemon-reload
systemctl enable --now wg-admin.service

# --- firewalld ---
if command -v firewall-cmd >/dev/null; then
  info "Opening firewalld"
  firewall-cmd --add-port="$PORT/tcp" --permanent || true
  firewall-cmd --reload || true
fi

# --- Final message ---
cat <<EOF

=================================================
wg-admin installed.

EOF

if [[ -n "$CERT_PATH" ]]; then
  cat <<EOF
TLS: using existing cert at $CERT_PATH
Panel: https://$HOST:$PORT/
EOF
elif command -v certbot >/dev/null; then
  cat <<EOF
TLS: no cert found — get one with:
  certbot certonly --standalone -d $HOST \\
    --pre-hook "systemctl stop wg-admin.service" \\
    --post-hook "systemctl start wg-admin.service"

Then re-run install.sh to wire the cert, or edit /etc/systemd/system/wg-admin.service
and add:
  Environment=WG_ADMIN_CERT=/etc/letsencrypt/live/$HOST/fullchain.pem
  Environment=WG_ADMIN_KEY=/etc/letsencrypt/live/$HOST/privkey.pem
  sudo systemctl daemon-reload && sudo systemctl restart wg-admin.service

Panel (HTTP only for now): http://$HOST:$PORT/
EOF
else
  cat <<EOF
TLS: certbot not installed — install with: apt install certbot
Panel (HTTP only): http://$HOST:$PORT/
EOF
fi

cat <<EOF
=================================================
EOF
