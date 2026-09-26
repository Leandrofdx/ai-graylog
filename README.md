# ai-graylog

Lab do **Assistente de Vendas** (UI no estilo Colombo) + observabilidade free.

## O que é

- App parecida com o Assistente Colombo: login, produtos, estoque, cliente, pré-venda, **CDC / CDCI / CP**, Monitor de Propostas, chat Lia
- **Postgres** (erros reais sob carga: pool/lock/estoque/limite de crédito)
- **OpenTelemetry → Jaeger**
- **Logs GELF → Graylog** com `trace_id`

## Pagamentos (lab)

| Tipo | O que é | Tender |
|------|---------|--------|
| À vista / Cartão / PIX | Pré-venda simples | — |
| **CDC** | Crediare / Financeira 12 | `2006` |
| **CDCI** | Fin25 / Financeira 25 | `2011` |
| **CP** | Crédito Pessoal (fora do carrinho) | — |

Fluxo CDC/CDCI: Limites → FinancialConditions → BatchSimulate (“Ver mais planos”) → CreatePreSales (ASS) → Monitor → Integrar financeira.

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
- `GET /Customer/api/CustomerLimits`
- `POST /PaymentCondition/api/FinancialConditions/Find/{storeId}`
- `GET /PaymentCondition/api/PaymentCondition/Find/{storeId}`
- `POST /InstallmentSimulator/api/InstallmentSimulator/BatchSimulate`
- `POST /SalesOrder/api/CreatePreSales` (`paymentType`: avista\|cartao\|pix\|cdc\|cdci)
- `GET /MultiFinancial/api/Proposals`
- `POST /MultiFinancial/api/IntegrateProposal`
- `POST /PersonalCredit/api/Create`
- `GET /PersonalCredit/api/Proposals`
- `POST /chat/api/chat`

## Subir

```bash
docker compose up -d --build
# schema CDC/CDCI/CP: recreate volume
docker compose stop postgres app
docker compose rm -f postgres app
docker volume rm graylog-local_postgres_data 2>/dev/null || true
docker compose up -d --build
```

## JMeter

```bash
jmeter -n -t jmeter/sample-app.jmx -l /tmp/assistente.jtl
```

Thread groups: **01 CDC**, **02 CDCI**, **03 CP**.

Correlação: `X-Test-Run-Id`, `X-Request-Id`, `trace_id` (Graylog + Jaeger).
