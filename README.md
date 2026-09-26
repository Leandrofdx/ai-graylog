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

### Do seu Mac (recomendado)

```bash
chmod +x scripts/*.sh
./scripts/deploy-to-ec2.sh ubuntu@IP_PUBLICO ~/.ssh/sua-chave.pem
```

### Ou na própria EC2

```bash
git clone git@github.com:Leandrofdx/ai-graylog.git
cd ai-graylog
chmod +x scripts/*.sh
./scripts/bootstrap-ec2.sh
```

O bootstrap instala Docker, ajusta `vm.max_map_count`, gera `.env` com heap menor e sobe o stack.

## Parar

```bash
docker compose down        # mantém dados
docker compose down -v     # apaga volumes
```
