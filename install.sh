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

  # --- Escolha de subnet (3 opções pré-definidas) ---
  echo ">>> Escolhe a subnet para a VPN (faixa de IPs virtuais):"
  echo "     1) 10.0.0.0/24      → range 10.0.0.1   a 10.0.0.254   (recomendado)"
  echo "     2) 10.66.66.0/24    → range 10.66.66.1 a 10.66.66.254 (clássico p/ VPN)"
  echo "     3) 192.168.99.0/24  → range 192.168.99.1 a 192.168.99.254"
  echo "     4) Outra (escrever à mão)"
  read -r -p "Opção [1]: " SUBNET_OPT
  SUBNET_OPT=${SUBNET_OPT:-1}
  case "$SUBNET_OPT" in
    1) SUBNET="10.0.0.0/24"; DEFAULT_IP="10.0.0.1" ;;
    2) SUBNET="10.66.66.0/24"; DEFAULT_IP="10.66.66.1" ;;
    3) SUBNET="192.168.99.0/24"; DEFAULT_IP="192.168.99.1" ;;
    4)
       while true; do
         read -r -p "Subnet (CIDR, ex: 10.5.0.0/24): " SUBNET
         if python3 -c "import ipaddress,sys; ipaddress.ip_network('$SUBNET', strict=False)" 2>/dev/null; then
           break
         fi
         info "Subnet inválida. Tenta outra vez (ex: 10.5.0.0/24)."
       done
       DEFAULT_IP=$(python3 -c "
import ipaddress
net = ipaddress.ip_network('$SUBNET', strict=False)
print(str(list(net.hosts())[0]))
")
       ;;
    *) err "Opção inválida" ;;
  esac

  # --- Server IP (com validação e range visualizado) ---
  FIRST_IP=$(python3 -c "import ipaddress; n=ipaddress.ip_network('$SUBNET',strict=False); print(str(list(n.hosts())[0]))")
  LAST_IP=$(python3 -c "import ipaddress; n=ipaddress.ip_network('$SUBNET',strict=False); print(str(list(n.hosts())[-1]))")
  info ""
  info "IPs disponíveis para o servidor na subnet $SUBNET: $FIRST_IP a $LAST_IP"
  info "Recomendado: $DEFAULT_IP (primeiro IP — fica como gateway do VPN)"

  while true; do
    read -r -p "Server IP [$DEFAULT_IP]: " SERVER_IP
    SERVER_IP=${SERVER_IP:-$DEFAULT_IP}
    if [[ "$(ip_in_subnet "$SERVER_IP" "$SUBNET")" == "OK" ]]; then
      break
    fi
    info "ERRO: $SERVER_IP não pertence a $SUBNET (válido: $FIRST_IP a $LAST_IP). Tenta outra vez."
  done

  # --- Porta UDP WireGuard ---
  echo ""
  echo ">>> Porta UDP onde o WireGuard vai ouvir (tem de estar aberta no router/security group):"
  echo "     1) 51820 (padrão WireGuard, recomendado)"
  echo "     2) 51821"
  echo "     3) 443 (disfarça-se de HTTPS, útil em redes restritas)"
  echo "     4) Outra (escrever à mão)"
  echo "   (também podes escrever a porta diretamente — ex: 51820)"
  read -r -p "Opção [1]: " PORT_OPT
  PORT_OPT=${PORT_OPT:-1}
  case "$PORT_OPT" in
    1) WG_PORT="51820" ;;
    2) WG_PORT="51821" ;;
    3) WG_PORT="443" ;;
    4) read -r -p "Porta UDP: " WG_PORT ;;
    *)
      if [[ "$PORT_OPT" =~ ^[0-9]+$ ]] && [ "$PORT_OPT" -ge 1 ] && [ "$PORT_OPT" -le 65535 ]; then
        WG_PORT="$PORT_OPT"
      else
        err "Opção inválida"
      fi
      ;;
  esac

  # Descobrir interface de rede default para NAT
  DEFAULT_IF=$(ip route show default 2>/dev/null | awk '{print $5; exit}')
  DEFAULT_IF=${DEFAULT_IF:-eth0}
  info ""
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
  # Stop primeiro caso esteja meio-up de tentativas anteriores
  systemctl stop wg-quick@wg0 2>/dev/null || true
  ip link del wg0 2>/dev/null || true
  systemctl start wg-quick@wg0
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
  info ""
  info "Configuração do painel web"
  info ""

  # Tentar detetar IP público para sugerir
  PUBLIC_IP=$(curl -s --max-time 3 https://api.ipify.org 2>/dev/null || echo "")
  if [[ -n "$PUBLIC_IP" ]]; then
    info "Detetei IP público: $PUBLIC_IP"
  fi

  echo ""
  echo "Endpoint (hostname ou IP) — onde os peers se vão ligar."
  echo "  • IP fixo: usa o IP (ex: $PUBLIC_IP)"
  echo "  • IP dinâmico: usa um hostname DDNS para não perder conexão quando o IP muda"
  echo "    (serviços gratuitos: duckdns.org, no-ip.com, dynv6.com)"
  echo "  • Enter vazio = usar o IP detetado acima"
  read -r -p "Endpoint [$PUBLIC_IP]: " ENDPOINT
  ENDPOINT=${ENDPOINT:-$PUBLIC_IP}

  if [[ -n "$ENDPOINT" ]]; then
    # Aviso sobre IP dinâmico
    if [[ "$ENDPOINT" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      info ""
      info "⚠️  Endpoint configurado como IP ($ENDPOINT)."
      info "   Se o teu IP público for dinâmico (muda com reinícios do router),"
      info "   os peers vão perder conexão quando mudar."
      info ""
      info "   Para resolver: configura um DDNS gratuito (duckdns.org / no-ip.com),"
      info "   cria um hostname a apontar para o IP, e re-corre install.sh com esse hostname."
      info ""
    fi
  fi

  echo ""
  echo ">>> Porta TCP para o painel web (HTTPS):"
  echo "     1) 51821 (recomendado — perto do WG, fácil de lembrar)"
  echo "     2) 8443 (alternativa comum para HTTPS)"
  echo "     3) 443 (HTTPS standard — pode entrar em conflito com outros serviços)"
  echo "     4) Outra (escrever à mão)"
  echo "   (também podes escrever a porta diretamente — ex: 51821)"
  read -r -p "Opção [1]: " LISTEN_OPT
  LISTEN_OPT=${LISTEN_OPT:-1}
  case "$LISTEN_OPT" in
    1) LISTEN_PORT="51821" ;;
    2) LISTEN_PORT="8443" ;;
    3) LISTEN_PORT="443" ;;
    4) read -r -p "Porta TCP: " LISTEN_PORT ;;
    *)
      if [[ "$LISTEN_OPT" =~ ^[0-9]+$ ]] && [ "$LISTEN_OPT" -ge 1 ] && [ "$LISTEN_OPT" -le 65535 ]; then
        LISTEN_PORT="$LISTEN_OPT"
      else
        err "Opção inválida"
      fi
      ;;
  esac

  cp "$INSTALL_DIR/config.ini.example" "$INSTALL_DIR/config.ini"
  sed -i "s/^endpoint_host = .*/endpoint_host = $ENDPOINT/" "$INSTALL_DIR/config.ini"
  sed -i "s/^listen_port = .*/listen_port = $LISTEN_PORT/" "$INSTALL_DIR/config.ini"

  # Propagar subnet e server_ip do wg0.conf para config.ini
  if [[ -f /etc/wireguard/wg0.conf ]]; then
    WG_SUBNET=$(python3 -c "
import sys
sys.path.insert(0, '$INSTALL_DIR/src')
from pathlib import Path
from wg_admin import wg
parsed = wg.parse_wg_conf(Path('/etc/wireguard/wg0.conf').read_text())
addr = parsed['interface'].get('Address', '/').split('/')[0]
prefix = parsed['interface'].get('Address', '/').split('/')[1] if '/' in parsed['interface'].get('Address', '') else '24'
import ipaddress
net = ipaddress.ip_network(f'{addr}/{prefix}', strict=False)
print(str(net))
")
    WG_SERVER_IP=$(python3 -c "
import sys
sys.path.insert(0, '$INSTALL_DIR/src')
from pathlib import Path
from wg_admin import wg
parsed = wg.parse_wg_conf(Path('/etc/wireguard/wg0.conf').read_text())
print(parsed['interface'].get('Address', '10.0.0.1/24').split('/')[0])
")
    sed -i "s|^subnet = .*|subnet = $WG_SUBNET|" "$INSTALL_DIR/config.ini"
    sed -i "s|^server_ip = .*|server_ip = $WG_SERVER_IP|" "$INSTALL_DIR/config.ini"
  fi
fi

# --- Sempre popular server_public_key (idempotente — tenta várias fontes) ---
info "A garantir server_public_key no config.ini"

SERVER_PUB=$("$INSTALL_DIR/venv/bin/python" <<'PYEOF'
import subprocess
import sys
import re
from pathlib import Path

# Método 1: wg show (mais fiável — lê do runtime se a interface estiver up)
for iface in ("wg0", "wg1", "wgserver"):
    try:
        r = subprocess.run(
            ["wg", "show", iface, "public-key"],
            capture_output=True, text=True, check=True, timeout=5
        )
        pub = r.stdout.strip()
        if pub and pub != "(none)":
            print(pub)
            sys.exit(0)
    except Exception:
        continue

# Método 2: procurar PrivateKey em wg0.conf e derivar via wg pubkey
try:
    conf = Path("/etc/wireguard/wg0.conf")
    if conf.exists():
        for line in conf.read_text().splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            m = re.match(r"^PrivateKey\s*=\s*(.+)$", line)
            if m:
                priv = m.group(1).strip()
                r = subprocess.run(
                    ["wg", "pubkey"], input=priv,
                    capture_output=True, text=True, check=True, timeout=5
                )
                pub = r.stdout.strip()
                if pub:
                    print(pub)
                    sys.exit(0)
except Exception as e:
    print(f"ERRO: {e}", file=sys.stderr)

# Método 3: procurar em qualquer ficheiro /etc/wireguard/*.conf
try:
    for conf in Path("/etc/wireguard").glob("*.conf"):
        for line in conf.read_text().splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            m = re.match(r"^PrivateKey\s*=\s*(.+)$", line)
            if m:
                priv = m.group(1).strip()
                r = subprocess.run(
                    ["wg", "pubkey"], input=priv,
                    capture_output=True, text=True, check=True, timeout=5
                )
                pub = r.stdout.strip()
                if pub:
                    print(f"Found in {conf.name}: {pub}", file=sys.stderr)
                    print(pub)
                    sys.exit(0)
except Exception:
    pass

sys.exit(1)
PYEOF
)

if [[ -n "$SERVER_PUB" ]]; then
  info "Server public key: $SERVER_PUB"
  "$INSTALL_DIR/venv/bin/python" -c "
import configparser
c = configparser.ConfigParser()
c.read('$INSTALL_DIR/config.ini')
c['wg']['server_public_key'] = '$SERVER_PUB'
with open('$INSTALL_DIR/config.ini', 'w') as f:
    c.write(f)
"
  # Verificação final
  ACTUAL_IN_CONFIG=$(grep '^server_public_key' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')
  if [[ "$ACTUAL_IN_CONFIG" == "$SERVER_PUB" ]]; then
    info "✓ config.ini tem server_public_key correto"
  else
    err "config.ini não tem server_public_key correto (esperado: $SERVER_PUB, obtido: $ACTUAL_IN_CONFIG)"
  fi
else
  err "Não consegui obter server public key por nenhum método. WireGuard está instalado? wg0.conf existe?"
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
    info "A aplicar peer novo ao WireGuard"
    # Stop primeiro se estiver a correr — 'restart' falha com 'already exists'
    if systemctl is-active --quiet wg-quick@wg0; then
      systemctl stop wg-quick@wg0 2>/dev/null || true
    fi
    systemctl start wg-quick@wg0
  fi
fi

# --- TLS setup: existing cert → certbot → self-signed ---
HOST=$(grep '^endpoint_host' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')
PORT=$(grep '^listen_port' "$INSTALL_DIR/config.ini" | cut -d= -f2 | tr -d ' ')

CERT_PATH=""
KEY_PATH=""
TLS_TYPE=""

CERT_DIR="/etc/letsencrypt/live/$HOST"
if [[ -d "$CERT_DIR" && -r "$CERT_DIR/fullchain.pem" && -r "$CERT_DIR/privkey.pem" ]]; then
  info "Cert Let's Encrypt já existe para $HOST — vou usar"
  CERT_PATH="$CERT_DIR/fullchain.pem"
  KEY_PATH="$CERT_DIR/privkey.pem"
  TLS_TYPE="letsencrypt"
elif [[ ! "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  # Hostname (não IP) → pode pedir cert Let's Encrypt
  info "A instalar certbot para obter certificado Let's Encrypt"
  detect_distro > /dev/null
  DISTRO=$(detect_distro)
  case "$DISTRO" in
    debian) apt-get install -y certbot ;;
    rhel) dnf install -y certbot ;;
    arch) pacman -Sy --noconfirm certbot ;;
    alpine) apk add --no-cache certbot ;;
  esac

  # Stop wg-admin se já estiver a correr (para libertar porta se for 80/443)
  systemctl stop wg-admin.service 2>/dev/null || true

  info "A pedir certificado a Let's Encrypt para $HOST..."
  if certbot certonly --standalone \
       --non-interactive \
       --agree-tos \
       --register-unsafely-without-email \
       -d "$HOST" 2>&1 | tail -10; then
    CERT_PATH="/etc/letsencrypt/live/$HOST/fullchain.pem"
    KEY_PATH="/etc/letsencrypt/live/$HOST/privkey.pem"
    TLS_TYPE="letsencrypt"
    info "Certificado Let's Encrypt obtido com sucesso!"
  else
    info "certbot falhou — vou gerar self-signed como fallback"
  fi
fi

# Self-signed fallback (para IP ou certbot falhado)
if [[ -z "$CERT_PATH" ]]; then
  info "A gerar certificado self-signed (válido 365 dias)"
  mkdir -p /etc/wg-admin
  if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
      -keyout /etc/wg-admin/selfsigned.key \
      -out /etc/wg-admin/selfsigned.crt \
      -subj "/CN=$HOST" \
      -addext "subjectAltName=IP:$HOST" 2>/dev/null
  else
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
      -keyout /etc/wg-admin/selfsigned.key \
      -out /etc/wg-admin/selfsigned.crt \
      -subj "/CN=$HOST" \
      -addext "subjectAltName=DNS:$HOST" 2>/dev/null
  fi
  chmod 600 /etc/wg-admin/selfsigned.key
  CERT_PATH="/etc/wg-admin/selfsigned.crt"
  KEY_PATH="/etc/wg-admin/selfsigned.key"
  TLS_TYPE="self-signed"
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
# Always restart, never just 'enable --now' — if service was already running
# with old env vars (e.g. TLS paths added on re-run), those wouldn't take effect.
systemctl enable wg-admin.service
systemctl restart wg-admin.service

# --- firewalld ---
if command -v firewall-cmd >/dev/null; then
  info "Opening firewalld"
  firewall-cmd --add-port="$PORT/tcp" --permanent || true
  firewall-cmd --reload || true
fi

# --- WG port (para mostrar nas instruções finais) ---
WG_PORT_CFG=$(grep -E '^listen_port|^endpoint_port' "$INSTALL_DIR/config.ini" | head -1 | cut -d= -f2 | tr -d ' ')
# Tentar obter a porta WG do wg0.conf se disponível
if [[ -f /etc/wireguard/wg0.conf ]]; then
  WG_PORT_CFG=$(grep '^ListenPort' /etc/wireguard/wg0.conf | head -1 | cut -d= -f2 | tr -d ' ')
fi

# --- Final message ---
cat <<EOF

╔══════════════════════════════════════════════════════════════════════╗
║                                                                       ║
║                    ✅  wg-admin instalado com sucesso!                ║
║                                                                       ║
╚══════════════════════════════════════════════════════════════════════╝

EOF

# ─── PASO 1: Abrir painel ───
cat <<EOF
━━━ 1. ABRIR O PAINEL WEB ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EOF

if [[ "$TLS_TYPE" == "letsencrypt" ]]; then
  cat <<EOF
  URL:  https://$HOST:$PORT/

  Password: a que definiste durante a instalação.
  Certificado TLS: Let's Encrypt válido (browser mostra cadeado verde ✅).

EOF
elif [[ "$TLS_TYPE" == "self-signed" ]]; then
  cat <<EOF
  URL:  https://$HOST:$PORT/

  Password: a que definiste durante a instalação.
  Certificado TLS: self-signed (gerado automaticamente).

  ⚠️  O browser vai mostrar aviso "não seguro" porque o certificado
     não é de uma entidade reconhecida (Let's Encrypt).
     É normal — podes clicar "Advanced → Proceed to site" para avançar.
     Tudo encriptado, só não é reconhecido pelo browser.

  Para ter certificado reconhecido (sem aviso):
    • Associa um domínio ao servidor (ex: vpn.example.com)
    • Re-corre: sudo bash /tmp/wg-admin/install.sh
    • Quando pedir endpoint, cola o domínio
    • Vai pedir cert Let's Encrypt automaticamente

EOF
else
  cat <<EOF
  URL:  http://$HOST:$PORT/   (HTTP — sem HTTPS)

  Para HTTPS: corre sudo bash /tmp/wg-admin/install.sh outra vez
  (vai instalar certbot + gerar certificado automaticamente).

EOF
fi

# ─── PASO 2: Firewalls ───
cat <<EOF
━━━ 2. ABRIR PORTAS NO FIREWALL DA CLOUD/ROTER ━━━━━━━━━━━━━━━━━━━━━━━━

  O teu servidor está atrás de firewalls adicionais que NÃO são controlados
  por este script. Precisas de abrir as portas manualmente:

  🟢 Porta UDP $WG_PORT_CFG  →  WireGuard (para os peers conectarem)
  🟢 Porta TCP $PORT          →  Painel web (para acederes de um browser)
EOF

# Detectar provider para dar instruções específicas
if curl -s --max-time 2 http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null | head -c 2 | grep -q "i-"; then
  cat <<EOF
  ** Estás na AWS EC2 **
  → Vai a: https://console.aws.amazon.com/ec2/ → Instances → seleciona a instância
    → Aba "Security" → click no Security Group → "Edit inbound rules"
    → Adiciona 2 regras:
      Type: Custom UDP, Port: $WG_PORT_CFG, Source: 0.0.0.0/0
      Type: Custom TCP, Port: $PORT, Source: 0.0.0.0/0
    → Save rules

  💡 Recomendação: usa Elastic IP para IP fixo (grátis enquanto associado):
    https://console.aws.amazon.com/vpc/ → Elastic IPs → Allocate
EOF
elif curl -s --max-time 2 http://169.254.169.254/metadata/v1/ 2>/dev/null | grep -q "."; then
  cat <<EOF
  ** Estás na DigitalOcean **
  → Networking → Firewalls → selecciona o firewall da droplet → Inbound Rules
  → Adiciona:
    Custom UDP, Port $WG_PORT_CFG, All IPv4
    Custom TCP, Port $PORT, All IPv4
EOF
elif curl -s --max-time 2 -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/id 2>/dev/null | grep -q "."; then
  cat <<EOF
  ** Estás no Google Cloud **
  → VPC Network → Firewall → Create Firewall Rule (×2):
    1) allow-wg:   UDP $WG_PORT_CFG, source 0.0.0.0/0
    2) allow-panel: TCP $PORT,        source 0.0.0.0/0
EOF
else
  cat <<EOF
  ** Servidor caseiro / VPS genérico **
  → Se tiveres router à frente, faz port-forwarding das portas acima
    para o IP local deste servidor.
  • ufw no servidor: já abri UDP $WG_PORT_CFG e TCP $PORT automaticamente.
EOF
fi

# ─── PASO 3: Endpoint ───
if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  cat <<EOF
━━━ 3. ⚠️  AVISO: ENDPOINT POR IP ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Configuraste o endpoint como IP ($HOST). Se o teu IP público mudar
  (reinícios de router, ISP renovar DHCP), os peers vão perder conexão.

  Para resolver se IP for dinâmico:

  ▸ Serviços gratuitos de DDNS:
    - https://duckdns.org  (mais simples, 30 seg a configurar)
    - https://no-ip.com
    - https://dynv6.com

  ▸ Passos típicos:
    1. Cria conta num dos serviços acima
    2. Escolhe um hostname (ex: minhavpn.duckdns.org)
    3. Aponta-o para o teu IP actual
    4. Instala o cliente DDNS no servidor para manter actualizado:
         sudo apt install ddclient
       (ou corre cron com curl a cada hora)
    5. Re-corre: sudo bash /tmp/wg-admin/install.sh
       Quando pedir endpoint, cola o hostname novo

EOF
fi

# ─── PASO 4: Conectar primeiro peer ───
cat <<EOF
━━━ 4. LIGAR-TE À VPN (primeiro peer) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Foi criado um peer "admin" automático durante a instalação.
  Para o importar num dispositivo:

  Opção A — App móvel (iOS/Android, app oficial WireGuard):
    1. Abre https://$HOST:$PORT/ no browser
    2. Faz login
    3. Clica em "QR" ao lado do peer "admin"
    4. Na app WireGuard → + → "Scan from QR code"

  Opção B — Computador (Linux/Mac/Windows):
    1. Faz download do ficheiro .conf (clica ".conf" no painel)
    2. Importa no cliente WireGuard:
       • Linux:   nmcli connection import type wireguard file admin.conf
       • Windows: "Import tunnel(s) from file" no app
       • macOS:   arrasta o .conf para a app

  Depois de ligares, confirma com:
    sudo wg show

EOF

# ─── Comandos úteis ───
cat <<EOF
━━━ COMANDOS ÚTEIS PARA O DIA-A-DIA ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Ver estado do painel:      sudo systemctl status wg-admin.service
  Reiniciar painel:          sudo systemctl restart wg-admin.service
  Ver logs do painel:        sudo journalctl -u wg-admin.service -f
  Ver peers WireGuard ativos: sudo wg show
  Ver IP público do servidor: curl https://api.ipify.org

  Reinstalar/Actualizar:      cd /tmp/wg-admin && git pull && sudo bash install.sh
  Desinstalar:                sudo bash /wg-admin/uninstall.sh

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✨ Documentação completa:  https://github.com/joaopaulooooo/wg-admin
  ✨ Issues / dúvidas:       https://github.com/joaopaulooooo/wg-admin/issues

EOF
