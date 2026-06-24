# wg-admin — Especificação de Design

> **Data:** 2026-06-17
> **Estado:** Aprovado (pendente de escrita do plano de implementação)
> **Target deployment:** servidor Linux genérico com WireGuard instalado; exemplo de referência `vpn.example.com`

---

## 1. Objetivo

Painel web minimalista para gestão de peers WireGuard. Permite a um admin único:

- Listar peers atuais com estado (último handshake, IP, tráfego rx/tx)
- Criar novos peers, atribuindo IP automaticamente
- Gerar `.conf` e QR code para download
- Ativar/desativar peers sem apagar
- Apagar peers
- Associar nome amigável e notas livres a cada peer

O painel é **usado esporadicamente (2-3 vezes por mês)** para gerir **20-50 peers**. O design otimiza para baixo consumo de recursos (compatível com servidor de 1GB RAM) e simplicidade de deploy.

---

## 2. Requisitos não-funcionais

| Requisito | Decisão |
|---|---|
| Stack | Python 3.11+ com Flask |
| Processo | systemd socket activation — 0 bytes RAM em idle |
| Auth | Password única de admin (Argon2id) |
| Exposição | Serviço próprio com TLS Let's Encrypt próprio (certbot standalone) |
| Compatibilidade | Funciona em qualquer Linux moderno, com ou sem Apache/Nginx |
| Storage | `state.json` encriptado (AES-256-GCM + HKDF-SHA256), master key em ficheiro separado |
| Aplicar mudanças | `systemctl restart wg-quick@wg0` (com brief disclaim dos peers ativos) |
| Target hardware | Mínimo 1GB RAM, 1 CPU core |

---

## 3. Arquitetura

### 3.1 Componentes

```
                    ┌──────────────────────────────────────┐
   Browser admin ──HTTPS──►  systemd socket (TCP 51821)   │
                              │ socket activation         │
                              ▼                           │
                          [ flask app ]  ←─ subprocess ──►│  wg show
                          (Python 3.11+)                  │  wg-quick
                              │                           │  wg genkey/pubkey
              ┌───────────────┼───────────────┐           │
              ▼               ▼               ▼           │
        state.json.enc   master.key    /etc/wireguard/   │
        (AES-256-GCM)    (0600 root)    wg0.conf         │
                                                       ◄─┘
```

### 3.2 Layout de ficheiros

```
/wg-admin/
├── app.py              # Entry point Flask, routes, auth middleware, CSRF
├── wg.py               # Wrapper subprocess: wg show, wg-quick, genkey, parse/import wg0.conf
├── state.py            # Load/save state.json.enc, schema dos metadados, IP allocation
├── crypto.py           # HKDF-SHA256, AES-GCM encrypt/decrypt, Argon2id hash
├── confgen.py          # Geração de .conf e QR code
├── templates/          # Jinja2: base.html, login.html, peers.html, peer_form.html
├── static/             # CSS mínimo, sem JS framework
├── venv/               # Python virtualenv
├── tests/              # Unit + integration tests
├── secrets/            # 0700 root
│   ├── master.key      # 32 bytes — input para HKDF
│   ├── session.key     # 32 bytes — assinar cookies
│   └── auth.ini        # Argon2id hash da password admin
├── config.ini          # Settings não-secretos (porta, endpoint, subnet, etc.)
├── state.json.enc      # Estado encriptado
├── state.json.enc.bak  # Backup do estado anterior
├── state.json.enc.bak1 # Backup de duas versões atrás
├── install.sh          # Script de instalação idempotente
├── uninstall.sh        # Script de remoção (com opção --keep-state)
├── requirements.txt    # flask, cryptography, argon2-cffi, qrcode
└── docs/               # Documentação (hardening, smoke-test, etc.)
```

### 3.3 Permissões

- Serviço corre como **root** (necessário para `wg-quick` e leitura de `/etc/wireguard/wg0.conf`)
- `/wg-admin/secrets/` → `0700 root:root`
- `master.key`, `session.key` → `0600 root:root`
- `auth.ini` → `0600 root:root`
- `state.json.enc*` → `0600 root:root`
- Demais ficheiros de código → `0644 root:root`

---

## 4. Modelo de dados

### 4.1 Schema do estado (JSON antes de encriptar)

```json
{
  "version": 1,
  "created_at": "2026-06-17T14:23:01Z",
  "updated_at": "2026-06-17T14:25:33Z",
  "peers": [
    {
      "id": "a1b2c3d4",
      "name": "João — iPhone",
      "notes": "Acesso até 2026-09",
      "public_key": "Y73ATDEJlSfrmn4NvB84WPA6B7HkpHXIHW/TJIJ5kmw=",
      "private_key_enc": "<hex AES-GCM com master key>",
      "ip": "10.0.0.2",
      "disabled": false,
      "created_at": "2026-06-17T14:23:01Z",
      "imported_from_legacy": true
    }
  ]
}
```

**Notas:**
- `id`: 8 hex chars aleatórios (`secrets.token_hex(4)`)
- `private_key_enc`: a chave privada do peer é encriptada uma segunda vez com master key (defense-in-depth), porque pode ser exportada para o `.conf` mas não deve aparecer em texto cruado no disco
- `public_key`: não encriptada (é pública por natureza, também está em `/etc/wireguard/wg0.conf`)
- `imported_from_legacy`: marca peers importados do `wg0.conf` original; permite ao admin auditlar

### 4.2 Config file (`config.ini`)

```ini
[wg]
interface = wg0
subnet = 10.0.0.0/24
server_ip = 10.0.0.1

[peer_defaults]
endpoint_host = vpn.example.com
allowed_ips = 0.0.0.0/0
dns = 1.1.1.1, 1.0.0.1

[server]
listen_port = 51821
session_lifetime_seconds = 3600
```

---

## 5. Fluxo de dados por operação

### 5.1 Login (`POST /login`)

```
Browser → POST password
  → app.py verifica hash Argon2id contra secrets/auth.ini
  → rate limit: 5 tentativas por minuto por IP (ficheiro-based, ver §7.6)
  → cria session cookie (HMAC-signed com session.key, HttpOnly+Secure+SameSite=Strict)
  → redirect para /peers
```

### 5.2 Listar peers (`GET /peers`)

```
→ state.py: lê state.json.enc → decrypt com master key → dict
→ wg.py: subprocess `wg show wg0 dump` → parse
→ app.py cruza state (nome/notas/disabled) com stats live (endpoint, rx, tx, last-handshake)
→ render peers.html
```

### 5.3 Criar peer (`POST /peers/new`)

```
1. Validar input (nome obrigatório, notas opcional)
2. wg.py: `wg genkey` → deriving `wg pubkey` → obter PrivateKey + PublicKey
3. state.py: alocar próximo IP livre em 10.0.0.0/24 (saltando .1 e existentes)
4. state.py: modificar dict em memória (adicionar peer)
5. state.py: encrypt + atomic write (temp + fsync + rename)
6. wg.py: regenerar /etc/wireguard/wg0.conf completo a partir do state
7. wg.py: subprocess `systemctl restart wg-quick@wg0`
8. flash success → redirect /peers
```

State é persistido **antes** de aplicar ao wg runtime — garante que após um crash/restart do painel, o state e o conf podem ser regenerados de forma consistente.

### 5.4 Download .conf / QR (`GET /peers/<id>/{conf,qr}`)

```
→ state.py: load peer por id
→ confgen.py: render template com PrivateKey/Address/DNS (Interface)
                  + PublicKey do server/Endpoint/AllowedIPs (Peer)
→ devolve como attachment (config-<name>.conf) ou PNG QR
```

### 5.5 Enable/disable (`POST /peers/<id>/toggle`)

```
→ state.py: flip disabled flag
→ wg.py: peer disabled → secção [Peer] comentada no wg0.conf (linhas prefixadas com #)
→ wg.py: systemctl restart wg-quick@wg0
```

### 5.6 Apagar peer (`POST /peers/<id>/delete`)

```
→ confirmação no frontend (JS confirm())
→ state.py: remover peer do state
→ wg.py: regenerar wg0.conf sem o peer
→ wg.py: systemctl restart wg-quick@wg0
```

### 5.7 Princípio: `wg0.conf` regenerado, nunca editado

Em qualquer mutação (create/delete/toggle), o `wg0.conf` é regenerado na íntegra a partir do state — nunca editado in-place. `state.json.enc` é a única fonte de verdade.

---

## 6. Crypto e autenticação

### 6.1 Ficheiros de segredo (todos `0600` root, gerados no install)

| Ficheiro | Conteúdo | Tamanho |
|---|---|---|
| `/wg-admin/secrets/master.key` | `os.urandom(32)` | 32 bytes |
| `/wg-admin/secrets/session.key` | `os.urandom(32)` | 32 bytes |
| `/wg-admin/secrets/auth.ini` | `password_hash = $argon2id$...` (PHC string) | ~100 bytes |

### 6.2 Formato do `state.json.enc`

JSON envelope em volta do ciphertext:

```json
{
  "version": 1,
  "kdf": "hkdf-sha256",
  "kdf_salt": "<hex 16 bytes>",
  "kdf_info": "wireguard-admin-state-v1",
  "cipher": "aes-256-gcm",
  "nonce": "<hex 12 bytes>",
  "ciphertext": "<hex dados + tag GCM>"
}
```

- **`kdf_salt`**: aleatório em cada write (16 bytes)
- **`kdf_info`**: domain separation string hardcoded `b"wireguard-admin-state-v1"`
- **`nonce`**: aleatório em cada write (12 bytes), nunca reutilizado
- **`ciphertext`**: plaintext JSON + tag de autenticação GCM (16 bytes no final)

### 6.3 Pipeline de encriptação (write)

```python
plaintext = json.dumps(state_dict).encode()
salt = os.urandom(16)
nonce = os.urandom(12)
aes_key = HKDF(
    algorithm=SHA256(),
    length=32,
    salt=salt,
    info=b"wireguard-admin-state-v1"
).derive(master_key)
ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, associated_data=None)
# write JSON envelope atomicamente: temp + fsync + rename
```

### 6.4 Pipeline de desencriptação (read)

```python
envelope = json.load(f)
aes_key = HKDF(SHA256(), 32, envelope["kdf_salt"], envelope["kdf_info"]).derive(master_key)
plaintext = AESGCM(aes_key).decrypt(envelope["nonce"], envelope["ciphertext"], None)
# GCM InvalidTag → exception, serviço recusa arrancar
```

GCM é authenticated encryption — qualquer tampering resulta em erro. Sem decifra parcial.

### 6.5 Password de admin (Argon2id)

- **Algoritmo:** Argon2id
- **Parâmetros:** `m=16384` (16 MB), `t=3`, `p=1` — calibrados para hardware modesto, ~0.2s por verificação
- **Formato:** PHC string (`$argon2id$v=19$m=16384,t=3,p=1$<salt>$<hash>`)
- **Biblioteca:** `argon2-cffi`

### 6.6 Sessões

- Cookie `wg_admin_session` assinado com `session.key` via `itsdangerous.URLSafeTimedSerializer`
- Payload: `{"admin": True, "exp": <unix_ts>}`
- **Lifetime:** 1 hora
- Atributos: `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`
- Logout = apagar cookie

### 6.7 CSRF

- Token por sessão (`secrets.token_urlsafe(32)`), guardado na sessão
- Forms incluem `<input type="hidden" name="csrf_token">`
- `before_request` valida token em todos POST/PUT/DELETE — 403 se falhar
- Implementação manual (sem Flask-WTF)

### 6.8 Bibliotecas Python

```
flask>=3.0          # web framework
cryptography>=42    # AES-GCM + HKDF-SHA256
argon2-cffi>=23     # password hashing
qrcode>=7           # QR codes
itsdangerous>=2     # session cookie signing (já vem com Flask)
```

---

## 7. Modelo de segurança

### 7.1 Fronteiras de confiança

| Ataque | Defesa |
|---|---|
| Sniffing na rede | TLS (Let's Encrypt) |
| Password guessing | Argon2id + rate limit 5 tent/min por IP (ficheiro-based — ver 7.6) |
| Session hijack | Cookie HttpOnly+Secure+SameSite=Strict, lifetime 1h |
| CSRF | Token por sessão, validado em todos POST |
| Leak do state.json | AES-256-GCM (metadata encriptada) |
| Leak do master.key | Sem defesa adicional — assume-se root protegido |
| Directory traversal | Flask route strict, sem user input em paths |

### 7.2 Limitações assumidas (não defendidas)

1. Password admin comprometida — não há 2FA (escolha consciente)
2. Root do servidor comprometido — game over
3. Zero-day em Flask/cryptography/Python — dependemos de upstream patches
4. Ataques físicos ao servidor — fora do scope
5. Multi-admin sem auditoria entre users — password única = sem "quem fez o quê"

### 7.3 Operações atómicas

Toda a write de state segue:

```
1. Ler state.json.enc atual → decrypt → dict
2. Modificar dict em memória
3. flock(state.json.enc.lock) — exclusive
4. Re-read state.json.enc, re-aplicar mudança (check)
5. Novo salt + nonce + encrypt
6. Write para state.json.enc.tmp
7. fsync(tmp)
8. os.rename(tmp, state.json.enc) — atómico
9. unlock
```

`rename` POSIX é atómico — estado antigo ou novo, nunca intermédio.

`wg0.conf` segue o mesmo padrão (temp + fsync + rename) em `/etc/wireguard/`.

### 7.4 Ordem das operações em mutações

```
1. Validar input           ← falha = nada mudou
2. Gerar chaves (wg)       ← falha = nada mudado
3. Alocar IP
4. Escrever state.json.enc ← estado reflete intenção
5. Regenerar wg0.conf      ← atomic rename
6. systemctl restart wg-quick@wg0
```

Se passo 6 falhar: state e conf estão consistentes entre si mas divergem do wg runtime. Banner vermelho na UI: "Última alteração não aplicada — correr `systemctl restart wg-quick@wg0`".

Se passo 5 falhar: state tem peer mas conf não. Mesma estratégia.

### 7.5 Backups automáticos

Antes de cada write:
- `state.json.enc` → `state.json.enc.bak`
- `state.json.enc.bak` → `state.json.enc.bak1`

3 versões: atual + 2 backups.

### 7.6 Rate limit de auth (ficheiro-based)

Como cada request via socket activation é um processo Python novo, estado em memória não persiste entre requests. Implementação:

- **Ficheiro:** `/wg-admin/secrets/auth_ratelimit.json` (`0600 root`)
- **Schema:** `{"<client_ip>": {"fails": <int>, "first_fail_at": <unix_ts>, "blocked_until": <unix_ts>}}`
- **Operação atómica:** flock durante read-modify-write (igual ao state)
- **Política:**
  - 5 falhas consecutivas dentro de 60s → block 5 minutos
  - Em block: returns 429 Too Many Requests sem verificar password (evita work inútil)
  - Entry expira após 1h sem atividade
- **Proteção adicional:** `LimitIntervalSec=` e `LimitBurst=` no systemd unit limitam requests/seg ao socket

Limpeza de entries expirados: feita lazy no mesmo read-modify-write (sem cron).

---

## 8. Tratamento de erros

| Falha | Comportamento |
|---|---|
| `state.json.enc` não existe | Primeiro arranque — estado vazio, arranca normal |
| `state.json.enc` existe mas decrypt falha | Serviço **não arranca**. Log: "STATE_CORRUPT: ver backup state.json.enc.bak" |
| `master.key` em falta ou tamanho errado | Serviço não arranca. Log: "rerun install" |
| `auth.ini` em falta | Serviço não arranca. Log: "rerun setup to set admin password" |
| `wg show` retorna erro | Listagem funciona com stats vazios + banner "stats indisponíveis" |
| `wg-quick restart` retorna non-zero | Banner de erro persistente até próximo sucesso |
| `/etc/wireguard/` não escrevível | Setup falha claramente |
| Cert TLS expirado | Serviço arranca, browser reclama |
| flock bloqueado | Timeout 5s, depois erro 503 |

### 8.1 Logging

- **Destino:** systemd journal (stderr → journald)
- **Níveis:** INFO, WARN, ERROR
- **Proibido em logs:** passwords, private keys, master key, session key
- **Permitido:** peer names, IPs, ações, timestamps, IPs de cliente

Exemplo:
```
INFO 2026-06-17T14:23:01Z peer=10.0.0.5 name="joao-iphone" action=created client_ip=192.168.1.42
WARN 2026-06-17T14:24:55Z auth=failed client_ip=203.0.113.7 reason="invalid_password"
```

---

## 9. Testes

### 9.1 Pirâmide

| Camada | Cobertura | Quando |
|---|---|---|
| Unit tests (`tests/test_*.py`) | Crypto, state, wg.py (subprocess mocked), confgen, IP allocation | `pytest` antes de cada release |
| Integration script (`tests/integration.sh`) | Netns com WG real, fluxo completo (create/delete/toggle/conf/QR) | Manual, antes de deploy |
| Smoke manual checklist (`docs/smoke-test.md`) | Login, criar peer, download .conf, importar num WG client real, verificar conectividade | Após cada install |

### 9.2 Unit tests críticos

```python
# test_crypto.py
- encrypt_decrypt_roundtrip          # write→read = original
- decrypt_tampered_ciphertext_fails  # bit flip → InvalidTag
- decrypt_wrong_master_key_fails
- different_salts_produce_different_keys

# test_state.py
- load_missing_returns_empty
- save_then_load_preserves_data      # 50 peers
- save_creates_backup_file
- concurrent_write_locks             # flock works

# test_wg.py (com subprocess.run mocked)
- parse_wg_show_dump
- import_existing_peers_from_conf
- generate_conf_from_state           # roundtrip: conf→state→conf = igual
- next_free_ip_skips_used

# test_confgen.py
- generated_conf_has_endpoint
- generated_conf_has_allowed_ips
- qr_code_is_valid_png
```

### 9.3 CI

GitHub Actions (ou equivalente) com matrix Python 3.11/3.12 em Linux.

---

## 10. Deployment

### 10.1 Pré-requisitos

- Linux moderno (systemd ≥ 235 para socket activation maduro)
- Python 3.11+
- WireGuard já instalado e configurado (`wg0` interface funcional)
- `wg-quick@wg0.service` ativo
- Acesso root
- Porta TCP livre para o painel (default 51821)
- Domínio apontando para o servidor (para TLS)

### 10.2 Install script (`install.sh`)

Bash idempotente:

```
1. Verificar pré-reqs: Python 3.11+, wg, wg-quick, systemd, certbot
2. Verificar que corre como root
3. Criar /wg-admin/, /wg-admin/secrets/, /wg-admin/venv/
4. python3 -m venv venv && pip install -r requirements.txt
5. Se master.key não existe → os.urandom(32)
6. Se session.key não existe → os.urandom(32)
7. Se auth.ini não existe → prompt interativo para password
8. Escrever config.ini (perguntar endpoint_host, subnet, etc.)
9. Primeiro arranque: importar peers de /etc/wireguard/wg0.conf
10. Instalar wg-admin.socket + wg-admin.service em /etc/systemd/system/
11. systemctl daemon-reload && systemctl enable --now wg-admin.socket
12. Abrir firewalld: firewall-cmd --add-port=51821/tcp --permanent
13. Sugestão final para certbot:
      certbot certonly --standalone -d vpn.example.com \
        --pre-hook "systemctl stop wg-admin.socket" \
        --post-hook "systemctl start wg-admin.socket"
14. Imprimir URL final: https://vpn.example.com:51821
```

### 10.3 Atualização

```bash
./install.sh   # novamente — preserva state.json.enc e secrets/
```

### 10.4 Uninstall

```bash
./uninstall.sh [--keep-state]
```

`--keep-state` faz backup de `state.json.enc` + `secrets/` para `/tmp/wg-admin-backup-<ts>.tar.gz`.

### 10.5 Distribuição

- Repo git público
- Install one-liner:
  ```bash
  git clone https://github.com/<user>/wg-admin.git /tmp/wg-admin && \
  sudo bash /tmp/wg-admin/install.sh
  ```

---

## 11. Hardening do systemd unit

Unit com restrições defensivas (em `/etc/systemd/system/wg-admin.service`):

```ini
[Service]
Type=exec
User=root
WorkingDirectory=/wg-admin
ExecStart=/wg-admin/venv/bin/python /wg-admin/app.py
Restart=on-failure
RestartSec=5

# Hardening
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/wg-admin /etc/wireguard
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
LockPersonality=yes
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_SYS_ADMIN

# Rate limit no nível do socket (defesa em profundidade)
StartLimitIntervalSec=60
StartLimitBurst=30
```

Notas:
- `ReadWritePaths=/wg-admin /etc/wireguard` é obrigatório porque `ProtectSystem=strict` torna `/` só de leitura
- `CAP_SYS_ADMIN` é necessário para `systemctl restart wg-quick` via `org.freedesktop.systemd1` (PolicyKit). Alternativa: socket systemd direto (mais seguro, mais código)
- `MemoryDenyWriteExecute=yes` omitido — `cryptography` usa algumas páginas WX para inicialização de asm; pode causar crash imprevisível
- `StartLimitBurst` limita respawns por minuto — defesa adicional contra loops de crash que consumiriam CPU

---

## 12. Questões em aberto para o plano de implementação

1. **Parse robusto do `wg show wg0 dump`** — output tem tab-separated fields; usar regex defensivo. Caso de teste: peer com nome com caracteres especiais (UTF-8)
2. **Importação de peers legacy:** preencher `name` com `peer-<last-octet>` por defeito; admin edita depois via UI. Marcar `imported_from_legacy=true`
3. **IP release em delete:** sim, IP volta à pool livre e é reutilizado pelo próximo peer (auto-alocação procura menor IP livre a partir de `.2`)
4. **Confirmação de restart do wg-quick:** UI mostra warning persistente na página de mutações: "Isto vai desconectar todos os peers ativos por ~1 segundo"
5. **Verificar tempo de Argon2id `m=16384,t=3,p=1` em CPU modesto** durante testes de integration
6. **Verificar API HKDF em `cryptography>=42`** — assinatura está estável desde 2019

---

## 13. Glossário

- **Peer**: cliente WireGuard (cada dispositivo que se conecta ao servidor)
- **Master key**: 32 bytes aleatórios que alimentam o HKDF para derivar a chave AES
- **PHC string**: formato canónico para hashes de password (Argon2, bcrypt, etc.) — facile de parsar e auditlar
- **Socket activation**: funcionalidade systemd que arranca o serviço on-demand quando uma porta é acedida
- **Domain separation**: string única passada ao HKDF (`info` parameter) que garante que chaves derivadas só são válidas para um contexto específico
