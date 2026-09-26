# ai-graylog

**LabOps Assistant** — UI estilo DevOps Agent + Graylog + Postgres + OpenTelemetry/Jaeger (tudo free).

## URLs

| Serviço | URL |
|---------|-----|
| Assistente (UI) | http://HOST:8080 |
| Graylog | https://leandrofdx.duckdns.org/ (ou :9000) |
| Jaeger (traces) | http://HOST:16686 |
| MCP Graylog | https://leandrofdx.duckdns.org/api/mcp |

## Stack

- App chat com tools: `system_status`, `search_logs` (Graylog API), `db_stats` (Postgres)
- Logs GELF → Graylog com `trace_id` / `service`
- Spans OTLP → Jaeger
- Postgres 16 (pool pequeno → `DbPoolTimeout` sob carga real)
- Sem erros aleatórios artificiais

## Local / EC2

```bash
docker compose up -d --build
```

JMeter:

```bash
jmeter -n -t jmeter/sample-app.jmx -l /tmp/labops.jtl
```

Correlação: header `X-Test-Run-Id` + `trace_id` no Graylog e Jaeger.


## EC2 (teste barato)

Sugestão: **t3.medium** Spot, Ubuntu 24.04, 20–30 GB, SG com `22`/`9000`/`8080` só no seu IP.

### Um comando na EC2 (Amazon Linux ou Ubuntu)

```bash
curl -fsSL https://raw.githubusercontent.com/Leandrofdx/ai-graylog/main/scripts/install-ec2.sh | bash
```

Isso clona o repo, instala Docker, ajusta o sistema e sobe Graylog + app.

### Do seu Mac (alternativa)

```bash
chmod +x scripts/*.sh
./scripts/deploy-to-ec2.sh ubuntu@IP_PUBLICO ~/.ssh/sua-chave.pem
```

### Ou clone manual

```bash
git clone https://github.com/Leandrofdx/ai-graylog.git
cd ai-graylog
./scripts/bootstrap-ec2.sh
```

O bootstrap instala Docker, ajusta `vm.max_map_count`, gera `.env` com heap menor e sobe o stack.

## HTTPS (necessário para MCP na AWS DevOps Agent)

A AWS exige endpoint `https://...`. Com IP público, use cert self-signed + Nginx:

```bash
# Na EC2 (stack já instalado)
cd ~/ai-graylog && git pull
curl -fsSL "https://raw.githubusercontent.com/Leandrofdx/ai-graylog/main/scripts/enable-https.sh?v=1" | bash
```

- UI: `https://SEU_IP/`
- MCP: `https://SEU_IP/api/mcp`
- Security Group: liberar **TCP 443**

No formulário AWS: Endpoint URL = `https://SEU_IP/api/mcp`

> Cert self-signed pode ser rejeitado pela AWS. Se isso acontecer, use domínio + Let's Encrypt:

```bash
# 1) Crie um DNS A: graylog.seudominio.com -> IP da EC2
# 2) SG: libere TCP 80 e 443 (0.0.0.0/0)
# 3) Na EC2:
cd ~/ai-graylog && git pull
export DOMAIN=graylog.seudominio.com
export EMAIL=seu@email.com
bash scripts/enable-https-letsencrypt.sh
```

MCP na AWS: `https://SEU_DOMINIO/api/mcp`

## Parar

```bash
docker compose down        # mantém dados
docker compose down -v     # apaga volumes
```
