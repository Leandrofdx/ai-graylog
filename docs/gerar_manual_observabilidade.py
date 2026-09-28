#!/usr/bin/env python3
"""Gera o manual formal DOCX de Observabilidade — Assistente de Vendas (lab)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(__file__).resolve().parent / "Manual_Observabilidade_Assistente_Vendas_Lab.docx"


def set_run_font(run, *, bold=False, size=11, color=None, italic=False):
    run.bold = bold
    run.italic = italic
    run.font.size = Pt(size)
    run.font.name = "Calibri"
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:eastAsia"), "Calibri")
    if color:
        run.font.color.rgb = color


def add_heading(doc, text, level=1):
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        set_run_font(run, bold=True, size=16 if level == 1 else 13 if level == 2 else 12)
    return p


def add_para(doc, text, *, bold=False, italic=False, size=11, space_after=8):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    run = p.add_run(text)
    set_run_font(run, bold=bold, italic=italic, size=size)
    return p


def add_bullet(doc, text, *, level=0):
    p = doc.add_paragraph(text, style="List Bullet")
    p.paragraph_format.left_indent = Cm(0.75 * (level + 1))
    for run in p.runs:
        set_run_font(run, size=11)
    return p


def add_numbered(doc, text):
    p = doc.add_paragraph(text, style="List Number")
    for run in p.runs:
        set_run_font(run, size=11)
    return p


def add_table(doc, headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        run = p.add_run(h)
        set_run_font(run, bold=True, size=10, color=RGBColor(0x1A, 0x1A, 0x2E))
    for r_i, row in enumerate(rows):
        cells = table.rows[r_i + 1].cells
        for c_i, val in enumerate(row):
            cells[c_i].text = ""
            p = cells[c_i].paragraphs[0]
            run = p.add_run(str(val))
            set_run_font(run, size=10)
    doc.add_paragraph()
    return table


def add_code(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.left_indent = Cm(0.5)
    run = p.add_run(text)
    set_run_font(run, size=9)
    run.font.name = "Consolas"
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), "Consolas")
    rFonts.set(qn("w:hAnsi"), "Consolas")
    return p


def build():
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)

    # --- Capa ---
    for _ in range(3):
        doc.add_paragraph()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = title.add_run("MANUAL DE OBSERVABILIDADE")
    set_run_font(r, bold=True, size=22, color=RGBColor(0x0F, 0x17, 0x2A))

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run(
        "Lab Assistente de Vendas — Estratégia, Implementação\ne Mentalidade para Sistemas de Varejo / Crédito"
    )
    set_run_font(r, size=14, color=RGBColor(0x33, 0x33, 0x33))

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = meta.add_run(
        f"\nDocumento técnico-didático consolidado\n"
        f"Base: laboratório ai-graylog (Assistente estilo Colombo)\n"
        f"Data: {date.today().strftime('%d/%m/%Y')}\n"
        f"Público: engenheiros, SRE, produto e líderes técnicos"
    )
    set_run_font(r, size=11, italic=True)

    note = doc.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = note.add_run(
        "\n\nEste manual descreve o modelo mental e o passo a passo usados no laboratório.\n"
        "O sistema de produção (Assistente real) tipicamente não possui este nível de observabilidade;\n"
        "o lab existe para ensinar como chegar lá com disciplina e evidência."
    )
    set_run_font(r, size=10, italic=True, color=RGBColor(0x55, 0x55, 0x55))

    doc.add_page_break()

    # --- Sumário textual ---
    add_heading(doc, "Sumário", 1)
    for item in [
        "1. Objetivo e escopo",
        "2. Mentalidade: por que observabilidade muda o jogo",
        "3. O problema que resolvemos",
        "4. Estratégia consolidada do laboratório",
        "5. Arquitetura de referência (mapa do lab)",
        "6. Os três pilares e a correlação",
        "7. Ferramentas: responsabilidade, o que olhar, quando usar",
        "8. Contrato de telemetria (padrão a implementar)",
        "9. Guia de implementação do zero (passo a passo)",
        "10. Dashboards com intenção",
        "11. Playbook de investigação de incidentes",
        "12. Exemplos guiados (estoque, lentidão, crédito)",
        "13. Do laboratório ao sistema real",
        "14. Próximos passos e maturidade",
        "15. Anti-padrões a evitar",
        "16. Glossário rápido",
        "Anexo A — Queries e comandos úteis",
        "Anexo B — URLs e credenciais do lab",
    ]:
        add_para(doc, item, space_after=2)

    doc.add_page_break()

    # 1
    add_heading(doc, "1. Objetivo e escopo", 1)
    add_para(
        doc,
        "Este manual consolida a experiência do laboratório Assistente de Vendas (ai-graylog): "
        "uma aplicação no estilo do Assistente Colombo (login, catálogo, estoque, cliente, CDC/CDCI/CP, "
        "pré-venda, propostas e chat), instrumentada de ponta a ponta com logs estruturados, traces "
        "OpenTelemetry e métricas RED por domínio funcional.",
    )
    add_para(
        doc,
        "Objetivo didático: capacitar o profissional a (i) compreender a mentalidade de observabilidade "
        "orientada à jornada de venda; (ii) implementar o mesmo modelo em outro sistema; "
        "(iii) investigar incidentes com evidência, expondo exatamente onde está o problema.",
    )
    add_para(doc, "Escopo técnico do lab:", bold=True)
    for b in [
        "App Flask com APIs espelhadas da jornada real",
        "Postgres com estoque e limite de crédito consumíveis",
        "Logs GELF → Graylog (negócio, funil, erros tipados)",
        "OpenTelemetry → Collector → Jaeger + spanmetrics → Prometheus (SPM)",
        "UI com dock “Rastreio” (links correlacionados)",
        "Carga JMeter / scripts para popular dashboards",
        "Deploy Docker Compose / EC2 com hostname público (DuckDNS)",
    ]:
        add_bullet(doc, b)

    # 2
    add_heading(doc, "2. Mentalidade: por que observabilidade muda o jogo", 1)
    add_para(
        doc,
        "Observabilidade não é “ter ferramenta”. É a capacidade de fazer perguntas novas sobre o "
        "sistema em produção e obter respostas com evidência — sem ter previsto cada dashboard.",
    )
    add_para(doc, "Mudança de mentalidade necessária:", bold=True)
    add_table(
        doc,
        ["Antes (comum)", "Depois (lab / maduro)"],
        [
            ["“Olha o log do servidor”", "“Qual error_type e em qual etapa da jornada?”"],
            ["“A API deu 409”", "“409 InsufficientStock no SKU-88, qty=2, stock=0”"],
            ["“Está lento”", "“p95 CreatePreSales e span SQL X ms no trace T”"],
            ["Culpa genérica no ‘backend’", "Domínio: estoque vs crédito vs pagamento vs DB"],
            ["Dashboard por vaidade", "Dashboard que responde uma pergunta de negócio"],
            ["Ferramenta primeiro", "Jornada e contrato de telemetria primeiro"],
        ],
    )
    add_para(
        doc,
        "Princípio central do lab: instrumentar a venda como cadeia de eventos correlacionados "
        "(headers + trace_id + error_type + biz_event), não “o servidor em geral”.",
        italic=True,
    )

    # 3
    add_heading(doc, "3. O problema que resolvemos", 1)
    add_para(
        doc,
        "No sistema real, quando a venda falha, o relato típico é vago (“travou na pré-venda”). "
        "Sem correlação e sem erros tipados, o time discute opinião. Com o modelo deste lab, "
        "o time discute evidência: etapa, API, causa de negócio ou gargalo técnico.",
    )
    add_para(doc, "Classes de problema que a telemetria deve separar:", bold=True)
    add_table(
        doc,
        ["Classe", "Exemplo", "Sinal principal"],
        [
            ["Negócio", "Sem estoque / crédito negado", "error_type + campos (item_id, cpf, amount)"],
            ["Jornada", "Parou depois de simular plano", "journey_step / biz_event"],
            ["Performance", "Demora demais", "duration_ms, p95, span longo no Jaeger"],
            ["Infra / DB", "Timeout de pool sob carga", "DbPoolTimeout, span DB, SPM"],
            ["Cliente / UI", "Sessão / clique", "X-Request-Id / test_run_id"],
        ],
    )

    # 4
    add_heading(doc, "4. Estratégia consolidada do laboratório", 1)
    add_para(
        doc,
        "Estratégia em uma frase: instrumentar a jornada de venda como cadeia correlacionada e "
        "expor os três pilares (logs, traces, métricas) em ferramentas free, com dashboards "
        "orientados a perguntas de negócio e a um playbook de investigação.",
    )
    add_para(doc, "Pilares da estratégia:", bold=True)
    for i, t in enumerate(
        [
            "Modelar a jornada (sale.*) antes de escolher ferramenta.",
            "Emitir logs estruturados com campos estáveis (não só texto).",
            "Propagar IDs de correlação (request, test_run, journey, trace).",
            "Tipar erros de negócio (error_type), não apenas HTTP status.",
            "Marcar eventos de negócio (biz_event) para funil e GMV.",
            "Tratar domínios (auth, estoque, venda…) como fronteiras observáveis (SPM).",
            "Permitir falha real no lab (estoque/crédito/pool) para treinar diagnóstico.",
            "Deep links da UI (Rastreio) para Graylog/Jaeger/SPM com hostname público.",
        ],
        1,
    ):
        add_numbered(doc, t)

    # 5
    add_heading(doc, "5. Arquitetura de referência (mapa do lab)", 1)
    add_code(
        doc,
        "Browser (Assistente UI)\n"
        "   │  X-Request-Id · X-Test-Run-Id · X-Journey-Step · X-Staff-Id\n"
        "   ▼\n"
        "App Flask (APIs estilo Colombo)\n"
        "   ├── Postgres (estoque, crédito, pré-venda, propostas)\n"
        "   ├── Logs GELF ──► Graylog   (negócio, funil, error_type, amount)\n"
        "   └── OTel spans ─► Collector ─► Jaeger\n"
        "                         └── spanmetrics ─► Prometheus ◄─ Jaeger Monitor (SPM)",
    )
    add_para(
        doc,
        "A aplicação é um único processo, mas emite lab_domain / spm_service "
        "(assistente-venda, assistente-estoque, …) para simular bounded contexts. "
        "No sistema real, cada microserviço ocuparia naturalmente essa fronteira.",
    )

    # 6
    add_heading(doc, "6. Os três pilares e a correlação", 1)
    add_heading(doc, "6.1 Logs", 2)
    add_para(
        doc,
        "Respondem “o que aconteceu”. No lab: GELF com duration_ms, error_type, biz_event, "
        "payment_method, amount, item_id, journey_step, trace_id, etc.",
    )
    add_heading(doc, "6.2 Traces", 2)
    add_para(
        doc,
        "Respondem “onde no caminho”. OpenTelemetry instrumenta a requisição; Jaeger mostra a "
        "árvore de spans (HTTP → regra → DB) e atributos como error.type.",
    )
    add_heading(doc, "6.3 Métricas (RED / SPM)", 2)
    add_para(
        doc,
        "Respondem “quanto e quão rápido no tempo”. Rate, Errors, Duration por domínio "
        "(assistente-*). Úteis para tendência e incidente coletivo.",
    )
    add_heading(doc, "6.4 Por que a correlação é o coração", 2)
    add_para(
        doc,
        "Sem o mesmo identificador nos três sinais, você tem ruído. Com trace_id + test_run_id + "
        "journey_step, o dock Rastreio leva do clique do vendedor ao log e ao span da mesma venda.",
    )
    add_table(
        doc,
        ["Identificador", "Escopo", "Uso"],
        [
            ["X-Request-Id", "Uma chamada HTTP", "Linha a linha / um clique"],
            ["X-Test-Run-Id", "Sessão UI ou run JMeter", "Jornada completa"],
            ["X-Journey-Step", "Etapa nomeada (sale.*)", "Funil e localização semântica"],
            ["trace_id", "Trace OpenTelemetry", "Amarrar Graylog ↔ Jaeger"],
            ["error_type", "Classe de falha", "Dashboard e alerta de negócio"],
            ["biz_event", "Fato de negócio", "Funil / GMV (não confundir com HTTP)"],
        ],
    )

    # 7
    add_heading(doc, "7. Ferramentas: responsabilidade, o que olhar, quando usar", 1)
    add_para(
        doc,
        "Cada ferramenta tem um papel. Usar a ferramenta errada é a causa mais comum de "
        "investigação longa e inconclusiva.",
    )

    add_heading(doc, "7.1 Assistente UI — dock Rastreio", 2)
    add_para(doc, "Responsabilidade: ponto de partida. Expõe IDs e deep links no momento do uso.", italic=True)
    add_para(doc, "Olhe: step (journey), HTTP status, trace curto, links Jaeger/Graylog/SPM.")
    add_para(doc, "Quando: imediatamente após o sintoma do vendedor (ou do seu próprio teste).")

    add_heading(doc, "7.2 Graylog", 2)
    add_para(
        doc,
        "Responsabilidade: o que aconteceu — busca e agregação de eventos e erros de negócio.",
        italic=True,
    )
    add_para(doc, "Olhe: error_type, http_status, http_path, duration_ms, biz_event, amount, item_id, cpf.")
    add_para(doc, "Dashboards do lab: Negócio · Overview; Negócio · por funcionalidade (abas + Performance).")
    add_para(doc, "Quando: classificar a falha (estoque vs crédito vs validação) e medir funil/GMV.")

    add_heading(doc, "7.3 Jaeger (Search / Trace)", 2)
    add_para(
        doc,
        "Responsabilidade: onde no caminho interno da requisição (spans, filhos, DB, status ERROR).",
        italic=True,
    )
    add_para(doc, "Olhe: árvore do trace, barras de duração, atributos error.type, spans SQL longos.")
    add_para(doc, "Quando: lentidão, dúvida se a falha foi regra rápida ou gargalo técnico.")

    add_heading(doc, "7.4 Jaeger Monitor (SPM) + Prometheus", 2)
    add_para(
        doc,
        "Responsabilidade: quanto e quão rápido por domínio no tempo (RED).",
        italic=True,
    )
    add_para(doc, "Olhe: serviço assistente-venda / pagamento / …; request rate, error rate, latency p95.")
    add_para(doc, "Quando: saber se o caso é isolado ou epidemia no domínio.")

    add_heading(doc, "7.5 Postgres (no lab)", 2)
    add_para(
        doc,
        "Responsabilidade: tornar o lab crível — estoque decrementa, crédito consome, pool pode estourar. "
        "Observabilidade sem falha real é teatro.",
        italic=True,
    )

    add_heading(doc, "7.6 Fluxo recomendado entre ferramentas", 2)
    add_code(
        doc,
        "Sintoma → UI Rastreio → Graylog (classificar) → Jaeger Trace (localizar)\n"
        "         → SPM (tendência) → Ação (código / estoque / limite / infra)",
    )

    # 8
    add_heading(doc, "8. Contrato de telemetria (padrão a implementar)", 1)
    add_para(
        doc,
        "Antes de “instalar Graylog”, documente um contrato. O lab já pratica este contrato de fato:",
    )
    add_heading(doc, "8.1 Headers de entrada (cliente → API)", 2)
    add_table(
        doc,
        ["Header", "Obrigatório", "Descrição"],
        [
            ["X-Request-Id", "Sim (ou gerar no server)", "ID único da chamada"],
            ["X-Test-Run-Id", "Recomendado", "Sessão / run de teste"],
            ["X-Journey-Step", "Sim na jornada", "Etapa estável sale.*"],
            ["X-Staff-Id", "Sim quando autenticado", "Vendedor"],
        ],
    )
    add_heading(doc, "8.2 Campos de log (exemplos)", 2)
    add_bullet(doc, "Identidade: service, spm_service, lab_domain, trace_id, span_id, request_id")
    add_bullet(doc, "HTTP: http_method, http_path, http_route, http_status, duration_ms")
    add_bullet(doc, "Negócio: biz_event, payment_method, plan_name, amount, item_id, cpf, staff_id")
    add_bullet(doc, "Falha: error_type (InsufficientStock, CreditLimitExceeded, …)")
    add_heading(doc, "8.3 Etapas de jornada (exemplo do lab)", 2)
    for s in [
        "sale.login",
        "sale.validate_customer",
        "sale.check_credit",
        "sale.search_product",
        "sale.check_stock",
        "sale.list_plans",
        "sale.simulate_plan",
        "sale.create_presale",
        "sale.list_proposals",
        "sale.integrate_proposal",
        "sale.create_cp",
    ]:
        add_bullet(doc, s)
    add_heading(doc, "8.4 Erros tipados (exemplo)", 2)
    for e in [
        "ValidationError",
        "Unauthorized",
        "ProductNotFound / CustomerNotFound",
        "InsufficientStock",
        "CreditNotApproved / CreditLimitExceeded / LimitsNotFound",
        "DbPoolTimeout / DbOperationalError",
    ]:
        add_bullet(doc, e)

    # 9
    add_heading(doc, "9. Guia de implementação do zero (passo a passo)", 1)
    add_para(
        doc,
        "Ordem recomendada — a mesma sequência que tornou o lab útil. Pule etapas e o dashboard vira decoração.",
    )
    add_heading(doc, "Passo 0 — Modelar a jornada", 2)
    add_para(
        doc,
        "Liste a venda real em etapas estáveis. Cada etapa vira X-Journey-Step. Sem nomes estáveis, "
        "não há funil confiável.",
    )
    add_heading(doc, "Passo 1 — App + banco com falha real", 2)
    add_para(
        doc,
        "APIs da jornada; Postgres com produtos/estoque/crédito/pré-venda. Erros de negócio como "
        "HTTP + JSON { \"error\": \"InsufficientStock\", ... }.",
    )
    add_heading(doc, "Passo 2 — Logs estruturados", 2)
    add_para(
        doc,
        "Em cada request: campos alinhados ao contrato. Transporte GELF (ou JSON para o stack da empresa). "
        "Regra: se não é campo, não filtra bem.",
    )
    add_heading(doc, "Passo 3 — Traces OpenTelemetry", 2)
    add_para(
        doc,
        "Instrumentar framework HTTP (+ DB se possível). Exportar OTLP → Collector → backend de traces. "
        "Habilitar spanmetrics para RED.",
    )
    add_heading(doc, "Passo 4 — Correlação na UI / BFF", 2)
    add_para(
        doc,
        "Gerar e propagar headers. Exibir dock com step, status, trace e deep links. "
        "Atenção: em HTTP não seguro (ex.: host público :8080), crypto.randomUUID pode falhar — use fallback. "
        "Links nunca devem apontar para 127.0.0.1 quando o usuário está remoto.",
    )
    add_heading(doc, "Passo 5 — Dashboards com intenção", 2)
    add_para(
        doc,
        "Overview de negócio (GMV, funil, mix, erros tipados) + abas por domínio com inventário fixo de "
        "endpoints (vazão, média, máx, p95). No Graylog 7, o id da série do search_type deve coincidir "
        "com o nome efetivo do widget (effectiveName), senão a tabela fica em branco.",
    )
    add_heading(doc, "Passo 6 — Carga controlada", 2)
    add_para(
        doc,
        "JMeter ou scripts com mix de meios, SKUs, CPFs e alguns 4xx. Sem carga o gráfico é vazio; "
        "carga caótica mente.",
    )
    add_heading(doc, "Passo 7 — Deploy com o mesmo contrato", 2)
    add_para(
        doc,
        "Docker Compose / EC2: mesmos campos e nomes. Hostname público nos deep links (DuckDNS). "
        "Security Group: 8080 (app), 443/9000 (Graylog), 16686 (Jaeger).",
    )

    # 10
    add_heading(doc, "10. Dashboards com intenção", 1)
    add_para(doc, "Dois níveis (como no lab):", bold=True)
    add_bullet(doc, "Negócio · Overview — KPIs, funil, call-chain CDC, mix de pagamento, erros tipados, GMV.")
    add_bullet(
        doc,
        "Negócio · por funcionalidade — cada aba só com endpoints daquele domínio + Performance.",
    )
    add_para(
        doc,
        "Pergunta guia ao criar widget: “Que decisão alguém toma ao ver este número?” "
        "Se não houver resposta, não crie o widget.",
        italic=True,
    )

    # 11
    add_heading(doc, "11. Playbook de investigação de incidentes", 1)
    for i, t in enumerate(
        [
            "Pegue o Rastreio: step + HTTP + trace_id (+ test_run_id).",
            "Graylog pelo trace_id: leia error_type e campos de negócio.",
            "Se lentidão ou dúvida interna: abra o mesmo trace no Jaeger.",
            "Se “está com todo mundo?”: SPM do domínio (error rate / p95).",
            "Só então aja (código, estoque, limite, infra) — com evidência citada.",
        ],
        1,
    ):
        add_numbered(doc, t)
    add_para(
        doc,
        "Essa sequência é o que torna a abordagem capaz de expor exatamente onde está o problema: "
        "ela força classificação (Graylog) antes de otimização (Jaeger/SPM).",
        italic=True,
    )

    # 12
    add_heading(doc, "12. Exemplos guiados", 1)

    add_heading(doc, "12.1 “Não consigo fechar a pré-venda”", 2)
    add_para(doc, "UI Rastreio: step=sale.create_presale, HTTP 409, trace T.")
    add_para(
        doc,
        "Graylog (Logs do trace): error_type=InsufficientStock, item_id=SKU-88, qty=2, stock=0, "
        "path=/SalesOrder/api/CreatePreSales.",
    )
    add_para(
        doc,
        "Jaeger: span CreatePreSales ERROR com error.type=InsufficientStock; duração baixa; "
        "SELECT FOR UPDATE sem lock interminável.",
    )
    add_para(doc, "SPM assistente-venda: error rate normal → caso isolado (SKU zerado); alta → onda de estoque.")
    add_para(
        doc,
        "Veredito: falha de negócio na validação de estoque na etapa de pré-venda; não é timeout de DB. "
        "Ação: repor estoque / impedir qty inválida na UI — não “escalar Postgres”.",
        bold=True,
    )

    add_heading(doc, "12.2 “Está lento”", 2)
    add_para(doc, "UI: step sale.simulate_plan, HTTP 200, percepção de demora.")
    add_para(doc, "Graylog: duration_ms alto no BatchSimulate.")
    add_para(doc, "Jaeger: span filho (SQL ou dependência) concentra o tempo.")
    add_para(doc, "SPM assistente-pagamento: p95 sobe no horário → domínio pagamento degradado.")

    add_heading(doc, "12.3 “Crédito às vezes não aprova”", 2)
    add_para(doc, "Graylog: error_type=CreditNotApproved, product_type=CDCI, cpf=…")
    add_para(doc, "Funil: sale.check_credit aparece; sale.create_presale some nesses casos.")
    add_para(doc, "SPM: pico em assistente-cliente, não em assistente-venda.")
    add_para(
        doc,
        "Veredito: o problema está antes da pré-venda (limites), não em CreatePreSales.",
        bold=True,
    )

    # 13
    add_heading(doc, "13. Do laboratório ao sistema real", 1)
    add_para(
        doc,
        "Não é obrigatório copiar Graylog + Jaeger + Prometheus. É obrigatório copiar responsabilidades:",
    )
    add_table(
        doc,
        ["No lab", "No real (exemplos)"],
        [
            ["Graylog + error_type / biz_event", "Datadog Logs / Elastic / CloudWatch + JSON"],
            ["Jaeger + trace_id", "Datadog APM / New Relic / Elastic APM / Tempo"],
            ["SPM por domínio", "RED dashboards por bounded context / serviço"],
            ["Dock Rastreio", "Deep link no backoffice / ferramenta de suporte"],
            ["JMeter Dash Beauty", "Testes de carga + synthetic journeys"],
        ],
    )
    add_para(doc, "Sugestão de adoção no Assistente real:", bold=True)
    for t in [
        "Escrever o contrato de telemetria (1–2 páginas) com Product + Engenharia.",
        "Começar pelo BFF/API da jornada crítica (pré-venda CDC), não pelo monolito inteiro.",
        "Logs JSON + trace_id no APM já existente na empresa.",
        "Alertas por taxa de error_type (negócio), não só CPU.",
        "SLOs simples: p95 CreatePreSales; taxa de 5xx; taxa de InsufficientStock se fizer sentido operacional.",
    ]:
        add_numbered(doc, t)

    # 14
    add_heading(doc, "14. Próximos passos e maturidade", 1)
    add_heading(doc, "Curto prazo (lab)", 2)
    for b in [
        "Quebrar de propósito e narrar o veredito em uma frase (playbook).",
        "Criar um widget para uma única pergunta de negócio.",
        "Desenhar o funil do sistema real com os mesmos nomes de step.",
    ]:
        add_bullet(doc, b)
    add_heading(doc, "Médio prazo (produção)", 2)
    for b in [
        "Contrato de telemetria oficial.",
        "Instrumentação da jornada crítica.",
        "Alertas e runbooks ligados a error_type / SLO.",
    ]:
        add_bullet(doc, b)
    add_heading(doc, "Longo prazo", 2)
    for b in [
        "OpenTelemetry como padrão de vendor.",
        "Sampling inteligente (100% erros + amostra de sucesso).",
        "Exemplars (métrica → exemplo de trace).",
        "Separar telemetria de produto (funil/GMV) da de plataforma (k8s/CPU).",
    ]:
        add_bullet(doc, b)

    # 15
    add_heading(doc, "15. Anti-padrões a evitar", 1)
    for b in [
        "Cinquenta dashboards sem jornada definida.",
        "Log só em texto livre (“erro ao processar”).",
        "Só métricas de infra (“CPU alta”) sem ligar à venda.",
        "HTTP 409 sem error_type — todos os conflitos parecem iguais.",
        "Deep links com 127.0.0.1 em ambiente remoto.",
        "Assumir secure context (crypto.randomUUID) em HTTP público.",
        "Widgets multi-série no Graylog com id ≠ effectiveName (células em branco).",
        "Payload enorme na argv do curl no bootstrap (ARG_MAX) — use arquivo/stdin.",
        "Estoque/crédito de lab que esgotam e invalidam demos — seed alto + restock/reset no boot.",
    ]:
        add_bullet(doc, b)

    # 16
    add_heading(doc, "16. Glossário rápido", 1)
    add_table(
        doc,
        ["Termo", "Significado"],
        [
            ["Observabilidade", "Capacidade de inferir estado interno por sinais externos"],
            ["Trace / Span", "Caminho de uma requisição / etapa dentro desse caminho"],
            ["RED", "Rate, Errors, Duration — métricas de serviço"],
            ["SPM", "Service Performance Monitoring (Monitor no Jaeger)"],
            ["GELF", "Formato de log estruturado usado pelo Graylog"],
            ["OTLP", "Protocolo de exportação OpenTelemetry"],
            ["GMV", "Gross Merchandise Value — soma de amount das vendas"],
            ["Bounded context", "Fronteira de domínio (venda, estoque, pagamento…)"],
            ["error_type", "Classificação estável da falha de negócio/técnica"],
            ["biz_event", "Fato de negócio no log (sale_created, …)"],
        ],
    )

    # Anexos
    doc.add_page_break()
    add_heading(doc, "Anexo A — Queries e comandos úteis", 1)
    add_heading(doc, "A.1 Graylog", 2)
    add_code(doc, 'error_type:InsufficientStock\nerror_type:CreditNotApproved\nbiz_event:sale_created\njourney_step:sale.create_presale AND http_status:>=400\nduration_ms:>500 AND http_path:*CreatePreSales*\ntrace_id:SEU_TRACE_ID\ntest_run_id:"ui-SEU_RUN"')
    add_heading(doc, "A.2 EC2 — atualizar lab", 2)
    add_code(
        doc,
        "cd ~/ai-graylog\n"
        "git pull --ff-only origin main\n"
        "bash scripts/bootstrap-ec2.sh\n"
        "# ou só a app:\n"
        "sudo docker compose up -d --build app\n"
        "# recriar dashboards se preciso:\n"
        "sudo docker compose run --rm -e GRAYLOG_FORCE_DASHBOARDS=1 graylog-init",
    )
    add_heading(doc, "A.3 Lab — repor estoque / crédito", 2)
    add_code(
        doc,
        'curl -X POST http://HOST:8080/Stock/api/Stock/Restock -H \'Content-Type: application/json\' -d \'{"force":true}\'\n'
        "curl -X POST http://HOST:8080/Customer/api/ResetCreditLimit -H 'Content-Type: application/json' -d '{}'",
    )

    add_heading(doc, "Anexo B — URLs e credenciais do lab", 1)
    add_table(
        doc,
        ["Serviço", "URL típica (DuckDNS)"],
        [
            ["Assistente UI", "http://leandrofdx.duckdns.org:8080"],
            ["Graylog", "https://leandrofdx.duckdns.org/"],
            ["Jaeger", "http://leandrofdx.duckdns.org:16686"],
            ["Jaeger SPM", "http://leandrofdx.duckdns.org:16686/monitor"],
            ["Prometheus (interno/SG)", "http://HOST:9090"],
        ],
    )
    add_para(doc, "Login Assistente: vendedor1 / lab123 · CPF lab: 52998224725")
    add_para(doc, "Login Graylog: admin / admin")

    doc.add_paragraph()
    end = doc.add_paragraph()
    end.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = end.add_run(
        "— Fim do manual —\n"
        "Consolidado a partir do laboratório ai-graylog / Assistente de Vendas.\n"
        "Use como referência de implementação e como material de capacitação."
    )
    set_run_font(r, size=10, italic=True, color=RGBColor(0x55, 0x55, 0x55))

    doc.save(OUT)
    print(f"OK: {OUT}")


if __name__ == "__main__":
    build()
