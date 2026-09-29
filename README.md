# ai-graylog

Lab do **Assistente de Vendas** + observabilidade free.

## O que é

- App de jornada de venda: login, produtos, estoque, cliente, pré-venda, **CDC / CDCI / CP**, Monitor de Propostas, chat Lia
- **Postgres** (erros reais sob carga: pool/lock/estoque/limite de crédito)
- **OpenTelemetry → Collector (spanmetrics) → Jaeger** + aba **Monitor (SPM)**
- **Prometheus** (só backend de métricas RED para o Jaeger — sem Grafana)
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
| Jaeger (Search + **Monitor/SPM**) | http://HOST:16686 |
| Prometheus (raw SPM) | http://HOST:9090 |

Login lab: `vendedor1` / `lab123`  
CPF lab: `52998224725`

## APIs (jornada)

- `POST /UserAuthentication/api/Authorize`
- `GET /Products/api/Products/Search`
- `GET /Stock/api/Stock/Find`
- `POST /Stock/api/Stock/Restock` (lab: repõe estoque após JMeter)
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

## Observabilidade (conexões)

Não é só ler log: o lab amarra **UI → log → trace → SPM**.

| Sinal | Onde |
|-------|------|
| `X-Request-Id` / `X-Test-Run-Id` / `X-Journey-Step` | Header da UI/JMeter |
| `trace_id` | Campo GELF no Graylog + Trace ID no Jaeger |
| `duration_ms` / `error_type` | Log de acesso e erros tipados |
| `biz_event` | Rótulo de evento de negócio no log (`sale_created`, `plan_simulated`, `proposal_created`…) — não é o HTTP em si |
| linkPatterns | No Jaeger: clique “Ver logs deste trace no Graylog” |
| **Monitor (SPM)** | Jaeger → **Monitor**: dropdown por funcionalidade — `assistente-auth`, `catalogo`, `cliente`, `estoque`, `pagamento`, `venda`, `propostas`, `cp` |
| **Graylog dashboards** | `Negócio · Overview` (KPIs + funil + call-chain CDC) · `Negócio · por funcionalidade` (abas por domínio + aba Performance) — Total vendido (R$)=GMV |

Pipeline SPM: `app → otel-collector (spanmetrics) → Prometheus ← Jaeger Query`.

Jaeger UI: tema dark habilitado — use o ícone de tema no topo.

```bash
# stack completa (app + Graylog + Jaeger SPM)
docker compose up -d --build
```

## JMeter

Plano simples (jornada CDC/CDCI/CP):

```bash
jmeter -n -t jmeter/sample-app.jmx -l /tmp/assistente.jtl
```

Plano **Dash Beauty** (massa variada — CSV com SKU/CPF/staff/meios/buscas + browse + CP + erros):

```bash
cd jmeter && ./run-dash-beauty.sh
# opcional: THREADS=8 LOOPS=15 RAMP=15 ./run-dash-beauty.sh
# regenerar CSV/JMX: python3 gen_dash_beauty.py
```

Popula bem: vazão/p95 por endpoint, mix à vista/CDC/CDCI, funil `X-Journey-Step`, propostas, CP e alguns 4xx.

Correlação: `X-Test-Run-Id`, `X-Request-Id`, `trace_id` (Graylog + Jaeger).
