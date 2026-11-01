"""Tiny dependency-free i18n for wg-admin.

English is both the DEFAULT language and the catalog KEY: `translate()`
returns the key itself for English, and looks the key up in the language
catalog otherwise (missing keys fall back to English). Placeholders use
str.format syntax: translate(lang, "Peer created: {name}", name="x").

The language is a server-wide setting in config.ini ([server] language):
chosen during install, changeable on the settings page (applied via the
existing self-restart flow).

Rules for catalog keys:
- no apostrophes (keys are also embedded in single-quoted JS strings);
- placeholders as {name};
- HTML tags are NOT allowed in keys/values (templates are auto-escaped).
"""

DEFAULT_LANGUAGE = "en"
LANGUAGES = {"en": "English", "pt": "Português"}

CATALOGS = {
    "pt": {
        # ── shared ──────────────────────────────────────────────────────
        "Sign in →": "Entrar →",
        "PASSWORD": "PASSWORD",
        "Save": "Guardar",
        "Saving…": "A guardar…",
        "Cancel": "Cancelar",
        "Close": "Fechar",
        "Edit": "Editar",
        "Delete": "Apagar",
        "Activate": "Ativar",
        "Deactivate": "Desativar",
        "online": "online",
        "offline": "offline",
        "inactive": "inativo",
        "(inactive)": "(inativo)",
        "Copy": "Copiar",
        "Copied ✓": "Copiado ✓",
        ".conf": ".conf",

        # ── base / sidebar ──────────────────────────────────────────────
        "service online": "service online",
        "VPN: active": "VPN: ativa",
        "VPN: inactive": "VPN: desativada",
        "Deactivate VPN": "Desativar VPN",
        "Activate VPN": "Ativar VPN",
        "Deactivate VPN? All peers disconnect immediately.":
            "Desativar VPN? Todos os peers vão desconectar imediatamente.",
        "Peers": "Peers",
        "Password": "Password",
        "Server Status": "Server Status",
        "Settings": "Configurações",
        "// version": "// versão",
        "Sign out": "Sair",
        "30d": "30d",
        "↻ update to": "↻ atualizar p/",
        "updating…": "a atualizar…",
        "Update to {tag}? The panel downloads the new version and restarts itself (usually under a minute). WireGuard peers are NOT affected and your session is kept. Progress: journalctl -u wg-admin-selfupdate -f":
            "Atualizar para {tag}? O painel descarrega a versão nova e reinicia sozinho (normalmente <1 min). Os peers WireGuard NÃO são afetados e a tua sessão mantém-se. Progresso: journalctl -u wg-admin-selfupdate -f",

        # ── login ───────────────────────────────────────────────────────
        "// wireguard peer management": "// wireguard peer management",

        # ── peers ───────────────────────────────────────────────────────
        "Peers — wg-admin": "Peers — wg-admin",
        "// {total} total · {online} active": "// {total} total · {online} ativos",
        "Global quota exceeded — {used} / {quota} GB · 30d. Consider disabling the VPN in the sidebar or reducing individual quotas.":
            "Cota global excedida — {used} / {quota} GB · 30d. Considera desativar a VPN no sidebar ou reduzir cotas individuais.",
        "The last change has not been applied to WireGuard yet — it is retried automatically (within 5 min) or on the next change. Check the logs with journalctl -u wg-admin -e.":
            "A última alteração ainda não foi aplicada ao WireGuard — o sistema repete automaticamente (≤5 min) ou na próxima alteração. Ver logs com journalctl -u wg-admin -e.",
        "Total Peers": "Total Peers",
        "Connected now": "Conectados agora",
        "Imported": "Importados",
        "+ New peer": "+ Novo peer",
        "30-day traffic — all peers": "Tráfego 30d — todos os peers",
        "IP": "IP",
        "pubkey": "pubkey",
        "wg state": "estado wg",
        "NOT FOUND": "NÃO ENCONTRADO",
        "wg show has:": "wg show tem:",
        "(empty)": "(vazio)",
        "handshake {ago} ago": "handshake há {ago}",
        "no handshake": "sem handshake",
        "30 days": "30 dias",
        "total": "total",
        "key": "chave",
        "no private key": "sem privada",
        "Click to see details": "Clica para ver detalhes",
        "SUSPENDED": "SUSPENSO",
        "Imported from wg0.conf — use Generate keys to unlock .conf/QR":
            "Importado do wg0.conf — usa 'Gerar chaves' para desbloquear .conf/QR",
        "QR Code": "QR Code",
        "Generate keys": "Gerar chaves",
        "Rotate key": "Rodar chave",
        "Generate new keypair for {name}?": "Gerar novo par de chaves para {name}?",
        "Rotate the private key for {name}?": "Rodar a private key para {name}?",
        "The device current config STOPS WORKING until it re-imports the new QR/.conf. Continue?":
            "A config atual do dispositivo DEIXA DE FUNCIONAR até reimportares o novo QR/.conf. Continuar?",
        "Delete {name}? All active peers disconnect for ~1s.":
            "Apagar {name}? Todos os peers ativos vão desconectar por ~1s.",
        "QR Code —": "QR Code —",
        "Bandwidth —": "Bandwidth —",
        "Point the camera of the WireGuard app at the code":
            "Aponta a câmara da app WireGuard para o código",
        "to close · click outside to close": "para fechar · click fora para fechar",
        "PNG": "PNG",
        "No peers configured.": "Sem peers configurados.",
        "+ Create first peer": "+ Criar primeiro peer",

        # ── peer form ───────────────────────────────────────────────────
        "New peer — wg-admin": "Novo peer — wg-admin",
        "New peer": "Novo peer",
        "// a keypair is generated automatically": "// vai gerar par de chaves automaticamente",
        "Creating a peer restarts wg-quick. If you are accessing the panel over the VPN right now, your connection drops for ~1 second and the form may look unresponsive. Wait a few seconds and reload the page.":
            "Criar um peer vai reiniciar o wg-quick. Se estás neste momento a aceder ao painel via VPN, a tua conexão vai cair por ~1 segundo e o formulário pode parecer não responder. Espera uns segundos e volta a carregar a página.",
        "NAME": "NOME",
        "e.g. John iPhone": "ex: João iPhone",
        "NOTES (optional)": "NOTAS (opcional)",
        "e.g. temporary access until 2026-12": "ex: acesso temporário até 2026-12",
        "QUOTA (GB, 0 = unlimited)": "COTA (GB, 0 = ilimitado)",
        "Sum of the last 30 days. On overrun the peer is auto-suspended; it reactivates on its own when usage drops below the limit.":
            "Soma dos últimos 30 dias. Ao exceder, suspende automaticamente; reativa sozinho quando baixar abaixo do limite.",
        "Create peer": "Criar peer",
        "Creating…": "A criar…",

        # ── peer edit ───────────────────────────────────────────────────
        "Edit peer — wg-admin": "Editar peer — wg-admin",
        "Edit peer": "Editar peer",
        "NOTES": "NOTAS",
        "Private key (optional)": "Chave privada (opcional)",
        "Private key already present. To replace it, paste a new one below.":
            "Chave privada já presente. Para a substituir, cola uma nova abaixo.",
        "No private key stored. Paste this peer private key (read it from the client device) to enable .conf and QR downloads.":
            "Sem chave privada. Cola a chave privada deste peer (lê-la do dispositivo cliente) para permitir descarregar .conf e QR.",

        # ── change password ─────────────────────────────────────────────
        "Change password — wg-admin": "Mudar password — wg-admin",
        "Change password": "Mudar password",
        "// argon2id · m=16MB · t=3 · p=1": "// argon2id · m=16MB · t=3 · p=1",
        "CURRENT PASSWORD": "PASSWORD ATUAL",
        "NEW PASSWORD": "NOVA PASSWORD",
        "(min 8 characters)": "(mínimo 8 caracteres)",
        "CONFIRM": "CONFIRMAR",

        # ── server status ───────────────────────────────────────────────
        "Server Status — wg-admin": "Server Status — wg-admin",
        "// live · refresh 5s": "// live · refresh 5s",
        "CPU": "CPU",
        "— cores": "— cores",
        "MEMORY": "MEMÓRIA",
        "DISK": "DISCO",
        "— mounts": "— mounts",
        "loading…": "carregando…",
        "System": "Sistema",
        "HOSTNAME": "HOSTNAME",
        "UPTIME": "UPTIME",
        "LOAD AVG": "LOAD AVG",
        "KERNEL": "KERNEL",
        "ARCH": "ARCH",
        "PUBLIC IP": "IP PÚBLICO",
        "PEERS ONLINE": "PEERS ONLINE",
        "TOTAL PEERS": "TOTAL PEERS",
        "RX TOTAL": "RX TOTAL",
        "TX TOTAL": "TX TOTAL",
        "RX RATE": "RX RATE",
        "TX RATE": "TX RATE",
        "no physical disks": "sem discos físicos",
        "mount": "mount",
        "mounts": "mounts",
        "updated ": "atualizado ",
        "error: ": "erro: ",
        "down": "down",

        # ── settings ────────────────────────────────────────────────────
        "Settings — wg-admin": "Configurações — wg-admin",
        "WireGuard settings": "Configurações WireGuard",
        "// interface {iface} · conf at /etc/wireguard/{iface}.conf":
            "// interface {iface} · conf em /etc/wireguard/{iface}.conf",
        "Interface changes and key rotation restart wg-quick — if you are connected over the VPN, the link drops for ~2s. Peers reconnect on their own.":
            "Alterações de interface e rotação de chave reiniciam o wg-quick — se estás a aceder via VPN, a conexão cai por ~2s. Os peers reconectam sozinhos.",
        "Interface": "Interface",
        "ADDRESS (CIDR)": "ADDRESS (CIDR)",
        "LISTENPORT (UDP)": "LISTENPORT (UDP)",
        "MTU (optional)": "MTU (opcional)",
        "empty = auto": "vazio = auto",
        "POSTUP (one per line)": "POSTUP (um por linha)",
        "POSTDOWN (one per line)": "POSTDOWN (um por linha)",
        "Save and apply": "Guardar e aplicar",
        "Applying…": "A aplicar…",
        "Client defaults": "Defaults dos clientes",
        "Values used in the .conf/QR generated for new peers.":
            "Valores usados nos .conf/QR gerados para novos peers.",
        "ENDPOINT HOST": "ENDPOINT HOST",
        "ENDPOINT PORT": "ENDPOINT PORT",
        "ALLOWEDIPS (CIDRs, comma-separated)": "ALLOWEDIPS (CIDRs, vírgula)",
        "DNS (IPs, comma-separated, empty = none)": "DNS (IPs, vírgula, vazio = nenhum)",
        "Save defaults": "Guardar defaults",
        "Server keys": "Chaves do servidor",
        "CURRENT PUBLIC KEY": "PUBLIC KEY ATUAL",
        "(unavailable)": "(indisponível)",
        "The private key is never shown. Generated .conf/QR always use the live interface key — rotations apply by themselves.":
            "A private key nunca é mostrada. Os .conf/QR usam sempre a chave live da interface — rotações aplicam-se sozinho.",
        "Rotate the server private key?": "Rodar a private key do servidor?",
        "ALL clients stop connecting until they re-import a new .conf/QR.":
            "TODOS os clientes deixam de ligar até reimportarem um novo .conf/QR.",
        "Continue?": "Continuar?",
        "Rotate private key": "Rodar private key",
        "Rotating…": "A rodar…",
        "Services": "Serviços",
        "Restart wg-admin?": "Reiniciar o wg-admin?",
        "The panel is unavailable for ~2-3s.": "O painel fica indisponível ~2-3s.",
        "Restart wg-admin": "Reiniciar wg-admin",
        "Restart WireGuard (wg-quick@{iface})?": "Reiniciar o WireGuard (wg-quick@{iface})?",
        "ALL peers (including yours, if connected over the VPN) disconnect for ~1-2s.":
            "TODOS os peers (incluindo a tua ligação, se estiveres via VPN) desconectam por ~1-2s.",
        "Restart WireGuard": "Reiniciar WireGuard",
        "Restarting…": "A reiniciar…",
        "Changing the language restarts wg-admin in a few seconds.":
            "Mudar a linguagem reinicia o wg-admin em poucos segundos.",
        "Language · Idioma": "Language · Idioma",
        "Save · Guardar": "Save · Guardar",

        # ── error page ──────────────────────────────────────────────────
        "Error {code} — wg-admin": "Erro {code} — wg-admin",
        "← Back to peers": "← Voltar aos peers",

        # ── flash messages ──────────────────────────────────────────────
        "Invalid credentials.": "Credenciais inválidas",
        "Current password is incorrect.": "Password atual incorreta.",
        "New password must be at least 8 characters.":
            "Nova password tem de ter pelo menos 8 caracteres.",
        "The new passwords do not match.": "As passwords novas não coincidem.",
        "Password updated. Sign in again.": "Password atualizada. Inicia sessão novamente.",
        "Invalid name (required, max 100 characters, no line breaks).":
            "Nome inválido (obrigatório, máx. 100 caracteres, sem quebras de linha).",
        "Invalid quota (non-negative number).": "Cota inválida (número não negativo).",
        "Peer created in state, but the apply to wg failed — it will be retried automatically":
            "Peer criado no estado, mas falhou apply ao wg — será repetido automaticamente",
        "Peer created: {name} ({ip})": "Peer criado: {name} ({ip})",
        "Peer removed from state, but the apply failed — it will be retried automatically":
            "Peer removido do estado, mas apply falhou — será repetido automaticamente",
        "Peer deleted": "Peer apagado",
        "Toggle registered but the apply failed — it will be retried automatically":
            "Toggle registrado mas apply falhou — será repetido automaticamente",
        "This peer was imported from wg0.conf — the private key only exists on the client device. A .conf or QR cannot be generated. Create a new peer or delete and re-import this one manually.":
            "Este peer foi importado do wg0.conf — a chave privada só existe no dispositivo cliente. Não é possível gerar .conf nem QR. Cria um peer novo ou apaga este e volta a importar manualmente.",
        "Imported peer — private key not available. QR cannot be generated.":
            "Peer importado — chave privada não disponível. Não é possível gerar QR.",
        "Invalid private key: must be base64 (44 chars) and match the peer public key.":
            "Chave privada inválida: tem de ser base64 (44 chars) e corresponder à public key do peer.",
        "Private key updated — you can now download .conf and QR":
            "Chave privada atualizada — já podes descarregar .conf e QR",
        "Peer updated": "Peer atualizado",
        "Keys generated in state, but the apply to wg failed — it will be retried automatically":
            "Chaves geradas no estado, mas apply ao wg falhou — será repetido automaticamente",
        "New keypair for {name} — download the .conf/QR and re-import on the device; the old config stops working.":
            "Novo par de chaves para {name} — descarrega o .conf/QR e reimporta no dispositivo; a config antiga deixa de funcionar.",
        "VPN deactivated — all peers disconnected": "VPN desativada — todos os peers desconectados",
        "VPN reactivated": "VPN reativada",
        "Invalid address — use CIDR format, e.g. 10.0.0.1/24":
            "Endereço inválido — usa o formato CIDR, ex: 10.0.0.1/24",
        "Invalid ListenPort (1-65535)": "ListenPort inválido (1-65535)",
        "Invalid MTU (1280-9000, or empty)": "MTU inválido (1280-9000, ou vazio)",
        "Conf not found: {path}": "Conf não encontrado: {path}",
        "Failed to apply — conf unchanged or partially applied, check logs":
            "Falha ao aplicar — conf não alterado ou parcialmente aplicado, ver logs",
        "Failed to apply to WireGuard — it will be retried automatically (check logs)":
            "Falha ao aplicar ao WireGuard — será repetido automaticamente (ver logs)",
        "Interface updated and wg-quick restarted — peers reconnect in seconds":
            "Interface atualizada e wg-quick reiniciado — peers reconectam em segundos",
        "Invalid endpoint host (hostname or IP, no spaces or line breaks).":
            "Endpoint host inválido (hostname ou IP, sem espaços nem quebras de linha).",
        "Invalid endpoint port (1-65535)": "Porta do endpoint inválida (1-65535)",
        "Invalid AllowedIPs — comma-separated list of CIDRs":
            "AllowedIPs inválido — lista de CIDRs separados por vírgula",
        "Invalid DNS — comma-separated list of IPs (or empty)":
            "DNS inválido — lista de IPs separados por vírgula (ou vazio)",
        "Failed to write config.ini — check logs": "Falha ao gravar config.ini — ver logs",
        "Defaults saved — wg-admin restarts in a few seconds to reload":
            "Defaults gravados — wg-admin reinicia em segundos para recarregar",
        "Failed to rotate the key — check logs (the conf may still have the old key)":
            "Falha ao rodar a chave — ver logs (conf pode ter ficado com a chave antiga)",
        "Key rotated in conf, but the apply to wg failed — it will be retried automatically":
            "Chave rodada no conf, mas apply ao wg falhou — será repetido automaticamente",
        "Private key rotated. New public key: {pub} — peer .conf/QR files are already generated with the new key; clients must re-import the config.":
            "Private key rodada. Nova public key: {pub} — os .conf/QR dos peers já são gerados com a nova chave; os clientes têm de reimportar a config.",
        "wg-admin restart scheduled (~2s) — wait and reload the page":
            "Reinício do wg-admin agendado (~2s) — aguarde e recarregue a página",
        "wg-quick@{iface} restart scheduled (~1s) — peers disconnect and reconnect on their own":
            "Reinício do wg-quick@{iface} agendado (~1s) — os peers desconectam e reconectam sozinhos",
        "Invalid version tag.": "Tag de versão inválida.",
        "You are already on the latest version ({version}).":
            "Já estás na versão mais recente ({version}).",
        "An update is already in progress.": "Já há uma atualização em curso.",
        "Update to {tag} scheduled — the panel restarts by itself at the end (the session is kept; progress in journalctl -u wg-admin-selfupdate -f).":
            "Atualização para {tag} agendada — o painel reinicia sozinho no fim (a sessão mantém-se; progresso em journalctl -u wg-admin-selfupdate -f).",
        "Invalid language.": "Linguagem inválida.",
        "Language set to {language} — wg-admin restarts in a few seconds.":
            "Linguagem alterada para {language} — wg-admin reinicia em segundos.",
        "Invalid CSRF token or expired session.": "CSRF token inválido ou sessão expirada.",
        "Resource not found.": "Recurso não encontrado.",
        "Too many login attempts. Try again later.": "Muitas tentativas de login. Tente novamente mais tarde.",
        "Internal error.": "Erro interno.",
    }
}


def normalize_language(value) -> str:
    """Coerce a config value to a supported language code ('en' default)."""
    v = (value or "").strip().lower()
    return v if v in LANGUAGES else DEFAULT_LANGUAGE


def translate(lang: str, text: str, /, **kwargs) -> str:
    """Translate `text` (an English key) into `lang`, then apply
    str.format placeholders if any kwargs were given.

    Positional-only parameters keep placeholder names like {language}
    or {text} from colliding with the function signature.
    """
    lang = normalize_language(lang)
    if lang != DEFAULT_LANGUAGE:
        text = CATALOGS.get(lang, {}).get(text, text)
    return text.format(**kwargs) if kwargs else text
