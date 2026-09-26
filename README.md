# ai-graylog

Graylog **7.1.9** + OpenSearch + MongoDB + API Flask de exemplo (logs via GELF) + script JMeter.

## Local

```bash
cp .env.example .env
docker compose up -d --build
```

- Graylog: http://127.0.0.1:9000 (`admin` / `admin`)
- App: http://127.0.0.1:8080

### Endpoints da app

| Método | Path |
|--------|------|
| GET | `/health` |
| GET | `/api/products` |
| GET | `/api/products/{sku}` |
| GET | `/api/users/{id}` |
| GET | `/api/orders` |
| GET | `/api/orders/{id}` |
| POST | `/api/orders` |
| POST | `/api/payments` |
| GET | `/api/inventory/{sku}` |

`ERROR_RATE` (default `0.18`) gera 404/429/500/503/etc. aleatórios para análise no Graylog.

### JMeter

```bash
jmeter -n -t jmeter/sample-app.jmx -l /tmp/run.jtl
```

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

> Cert self-signed pode ser rejeitado pela AWS. Se isso acontecer, use domínio + Let's Encrypt ou ALB+ACM.

## Parar

```bash
docker compose down        # mantém dados
docker compose down -v     # apaga volumes
```
