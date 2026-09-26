# ai-graylog

Lab do **Assistente de Vendas** (UI no estilo Colombo) + observabilidade free.

## O que é

- App parecida com o Assistente Colombo: login vendedor, produtos, estoque, cliente, pré-venda, chat Lia
- **Postgres** (erros reais sob carga: pool/lock/estoque)
- **OpenTelemetry → Jaeger**
- **Logs GELF → Graylog** com `trace_id`

Não é um clone do DevOps Agent.

## URLs

| Serviço | URL |
|---------|-----|
| Assistente UI | http://HOST:8080 |
| Graylog | https://leandrofdx.duckdns.org/ |
| Jaeger | http://HOST:16686 |

Login lab: `vendedor1` / `lab123`  
CPF lab: `52998224725`

## APIs (jornada)

- `POST /UserAuthentication/api/Authorize`
- `GET /Products/api/Products/Search`
- `GET /Stock/api/Stock/Find`
- `GET /Customer/api/Customer/FindByCpfCnpj`
- `POST /SalesOrder/api/CreatePreSales`
- `POST /chat/api/chat`

## Subir

```bash
docker compose up -d --build
# se mudar schema: docker volume rm graylog-local_postgres_data
```

## JMeter

```bash
jmeter -n -t jmeter/sample-app.jmx -l /tmp/assistente.jtl
```

Correlação: `X-Test-Run-Id`, `X-Request-Id`, `trace_id` (Graylog + Jaeger).
