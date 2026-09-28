#!/usr/bin/env python3
"""Cria streams + dashboards Graylog por funcionalidade (idempotente)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any

BASE = os.environ.get("GRAYLOG_URL", "http://127.0.0.1:9000").rstrip("/") + "/api"
USER = os.environ.get("GRAYLOG_USER", "admin")
PASSWORD = os.environ.get("GRAYLOG_PASSWORD", "admin")
RANGE = int(os.environ.get("GRAYLOG_DASH_RANGE", "86400"))

DOMAINS = [
    # Evitar leading wildcard (*foo*) — OpenSearch quebra com query_shard_exception.
    ("venda", "Venda", "pré-venda / mix de pagamento", r"http_path:\/SalesOrder\/*"),
    ("pagamento", "Pagamento", "planos e parcelas", r"http_path:\/PaymentCondition* OR http_path:\/InstallmentSimulator*"),
    ("catalogo", "Catálogo", "busca e categorias", r"http_path:\/Products\/*"),
    ("propostas", "Propostas", "integração financeira", r"http_path:\/MultiFinancial\/*"),
    ("cp", "Crédito Pessoal", "CP fora do carrinho", r"http_path:\/PersonalCredit\/*"),
    ("cliente", "Cliente", "CPF, limites, crédito", r"http_path:\/Customer\/*"),
    ("estoque", "Estoque", "disponibilidade", r"http_path:\/Stock\/*"),
    ("auth", "Auth", "login / sessão", r"http_path:\/UserAuthentication*"),
]

OVERVIEW_TITLE = "Negócio · Overview"
DOMAIN_TITLE = "Negócio · por funcionalidade"
LEGACY_TITLES = (
    "Assistente · Overview",
    "Assistente · por funcionalidade",
    "Negócio · por jornada",
    "Negócio · Jornada e Propostas",
    "Negócio · Performance",
)
SMOKE_TITLE = "Lab smoke dashboard"

# Inventário fixo e ordenado: só estes endpoints entram em cada aba (nada além).
DOMAIN_ROUTES = {
    "auth": [
        "POST /UserAuthentication/api/Authorize",
    ],
    "catalogo": [
        "GET /Products/api/Products/Search",
    ],
    "estoque": [
        "GET /Stock/api/Stock/Find",
        "POST /Stock/api/Stock/Restock",
    ],
    "cliente": [
        "GET /Customer/api/Customer/FindByCpfCnpj",
        "GET /Customer/api/CustomerLimits",
        "POST /Customer/api/ResetCreditLimit",
    ],
    "pagamento": [
        "POST /PaymentCondition/api/FinancialConditions/Find/<store_id>",
        "GET /PaymentCondition/api/PaymentCondition/Find/<store_id>",
        "POST /InstallmentSimulator/api/InstallmentSimulator/BatchSimulate",
    ],
    "venda": [
        "POST /SalesOrder/api/CreatePreSales",
    ],
    "propostas": [
        "GET /MultiFinancial/api/Proposals",
        "POST /MultiFinancial/api/IntegrateProposal",
    ],
    "cp": [
        "POST /PersonalCredit/api/Create",
        "GET /PersonalCredit/api/Proposals",
    ],
}

SALES_Q = '(biz_event:sale_created OR message:"pre-sale created")'
PROP_CREATED_Q = "biz_event:proposal_created"
PROP_INTEGRATED_Q = "biz_event:proposal_integrated"
# error_type vazio é enviado no access log — filtrar
ERR_TYPED = 'error_type:* AND NOT error_type:""'
ERR_HTTP = "http_status:>=400"
ERR_ANY = f"(({ERR_TYPED}) OR ({ERR_HTTP}))"
CALL_CHAIN_PATHS = (
    r'http_path:\/UserAuthentication\/api\/Authorize OR '
    r'http_path:\/Customer\/api\/Customer\/FindByCpfCnpj OR '
    r'http_path:\/Customer\/api\/CustomerLimits OR '
    r'http_path:\/Products\/api\/Products\/Search OR '
    r'http_path:\/PaymentCondition\/api\/FinancialConditions\/Find\/* OR '
    r'http_path:\/InstallmentSimulator\/api\/InstallmentSimulator\/BatchSimulate OR '
    r'http_path:\/SalesOrder\/api\/CreatePreSales OR '
    r'http_path:\/MultiFinancial\/api\/Proposals OR '
    r'http_path:\/MultiFinancial\/api\/IntegrateProposal'
)


def _escape_path(path: str) -> str:
    return path.replace("/", r"\/")


def _route_path_pattern(spec: str) -> tuple[str, str]:
    """'METHOD /path/<id>' → (METHOD, lucene path com wildcards)."""
    method, path = spec.split(" ", 1)
    path = path.strip().replace("<store_id>", "*")
    while "<" in path and ">" in path:
        a, b = path.index("<"), path.index(">")
        path = path[:a] + "*" + path[b + 1 :]
    return method.upper(), path


def domain_calls_query(key: str) -> str:
    """Somente os endpoints do inventário DOMAIN_ROUTES desta funcionalidade."""
    specs = DOMAIN_ROUTES.get(key) or []
    if not specs:
        return f"lab_domain:{key} AND service:assistente* AND _exists_:http_path"
    parts: list[str] = []
    for spec in specs:
        method, path = _route_path_pattern(spec)
        esc = _escape_path(path)
        # Filtrar por path (+ method). http_path cobre logs sem http_route.
        parts.append(f"(http_method:{method} AND http_path:{esc})")
    return f"service:assistente* AND ({' OR '.join(parts)})"


def add_latency_vazao_strip(tab: TabBuilder, scope: str, *, title_suffix: str = "") -> None:
    """Faixa padrão: Vazão + Média + Máx + p95 + erros + barra de vazão no tempo."""
    suf = f" · {title_suffix}" if title_suffix else ""
    q = scope
    q_lat = f"({scope}) AND _exists_:duration_ms" if "duration_ms" not in scope else scope

    tab.add_agg(
        f"Vazão (qtd){suf}",
        query=q,
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=1,
        width=2,
        height=2,
    )
    tab.add_agg(
        f"Média (ms){suf}",
        query=q_lat,
        visualization="numeric",
        series=[series_avg("duration_ms")],
        series_fns=["avg(duration_ms)"],
        col=3,
        width=2,
        height=2,
    )
    tab.add_agg(
        f"Máx (ms){suf}",
        query=q_lat,
        visualization="numeric",
        series=[series_max("duration_ms")],
        series_fns=["max(duration_ms)"],
        col=5,
        width=2,
        height=2,
    )
    tab.add_agg(
        f"p95 (ms){suf}",
        query=q_lat,
        visualization="numeric",
        series=[series_p95()],
        series_fns=["percentile(duration_ms,95.0)"],
        col=7,
        width=2,
        height=2,
    )
    tab.add_agg(
        f"Erros tipados{suf}",
        query=f"({q}) AND ({ERR_TYPED})",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=9,
        width=2,
        height=2,
    )
    tab.add_agg(
        f"HTTP 4xx/5xx{suf}",
        query=f"({q}) AND ({ERR_HTTP})",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=11,
        width=2,
        height=2,
    )
    tab.next_row(2)

    tab.add_agg(
        f"Vazão no tempo ≈ TPS{suf}",
        query=q,
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[time_group()],
        row_pivots=[time_pivot()],
        col=1,
        width=6,
        height=3,
        viz_config={"barmode": "stack", "axis_type": "linear"},
        rollup=False,
    )
    tab.add_agg(
        f"Endpoints · vazão + média + máx + p95{suf}",
        query=q_lat,
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        # 1 pivot só — 2 níveis (method+path) deixa métricas em branco no Graylog 7 UI
        row_groups=[values_group("http_path", 25)],
        row_pivots=[values_pivot("http_path", 25)],
        col=7,
        width=6,
        height=3,
        sort_count_desc=True,
        rollup=False,
    )
    tab.next_row(3)


def domain_sequence_label(key: str) -> str:
    specs = DOMAIN_ROUTES.get(key) or []
    return " → ".join(f"{i}.{s}" for i, s in enumerate(specs, 1))


def domain_inventory_markdown(key: str, label: str | None = None) -> str:
    """Lista fixa dos endpoints que compõem a funcionalidade (não depende de tráfego)."""
    specs = DOMAIN_ROUTES.get(key) or []
    lines = [
        f"**{label or key}** — {len(specs)} endpoint(s) desta funcionalidade",
        "",
        "| # | Método | Endpoint |",
        "| ---: | --- | --- |",
    ]
    for i, spec in enumerate(specs, 1):
        method, path = spec.split(" ", 1)
        lines.append(f"| {i} | `{method}` | `{path}` |")
    lines.append("")
    lines.append("**Sequência:** " + " → ".join(str(i) for i in range(1, len(specs) + 1)))
    return "\n".join(lines)


def add_domain_routes(tab: TabBuilder, key: str) -> None:
    """KPIs + tabela dinâmica (só endpoints desta funcionalidade) + vazão."""
    q = domain_calls_query(key)
    q_lat = f"({q}) AND _exists_:duration_ms"
    n = len(DOMAIN_ROUTES.get(key) or [])

    # 1) KPIs — vazão / média / máx / p95 / erros
    tab.add_agg(
        "Vazão (qtd)",
        query=q,
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=1,
        width=2,
        height=2,
    )
    tab.add_agg(
        "Média (ms)",
        query=q_lat,
        visualization="numeric",
        series=[series_avg("duration_ms")],
        series_fns=["avg(duration_ms)"],
        col=3,
        width=2,
        height=2,
    )
    tab.add_agg(
        "Máx (ms)",
        query=q_lat,
        visualization="numeric",
        series=[series_max("duration_ms")],
        series_fns=["max(duration_ms)"],
        col=5,
        width=2,
        height=2,
    )
    tab.add_agg(
        "p95 (ms)",
        query=q_lat,
        visualization="numeric",
        series=[series_p95()],
        series_fns=["percentile(duration_ms,95.0)"],
        col=7,
        width=2,
        height=2,
    )
    tab.add_agg(
        "Erros tipados",
        query=f"({q}) AND ({ERR_TYPED})",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=9,
        width=2,
        height=2,
    )
    tab.add_agg(
        "HTTP 4xx/5xx",
        query=f"({q}) AND ({ERR_HTTP})",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=11,
        width=2,
        height=2,
    )
    tab.next_row(2)

    # 2) Lista dinâmica — 1 pivot (http_path); method já está no filtro do inventário
    tab.add_agg(
        f"Lista de endpoints · {tab.title} ({n})",
        query=q_lat,
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("http_path", 25)],
        row_pivots=[values_pivot("http_path", 25)],
        height=max(4, 2 + n),
        sort_count_desc=True,
        rollup=False,
    )
    tab.next_row(max(4, 2 + n))

    # 3) Vazão no tempo
    tab.add_agg(
        "Vazão no tempo ≈ TPS",
        query=q,
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[time_group()],
        row_pivots=[time_pivot()],
        col=1,
        width=6,
        height=3,
        viz_config={"barmode": "stack", "axis_type": "linear"},
        rollup=False,
    )
    tab.add_agg(
        "Vazão por endpoint no tempo",
        query=q,
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[time_group()],
        row_pivots=[time_pivot()],
        column_groups=[values_group("http_path", 12)],
        column_pivots=[values_pivot("http_path", 12)],
        col=7,
        width=6,
        height=3,
        viz_config={"barmode": "stack", "axis_type": "linear"},
        rollup=False,
    )
    tab.next_row(3)

    tab.add_messages(
        f"Logs · {domain_sequence_label(key)}"[:180],
        query=q,
        fields=["timestamp", "http_method", "http_path", "http_route", "duration_ms", "http_status", "journey_step", "biz_event", "message"],
        height=3,
    )
    tab.next_row(3)


def _curl_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("SSLKEYLOGFILE", None)
    return env


def api(method: str, path: str, body: Any | None = None, *, accept_json: bool = True) -> Any:
    """Chama a API Graylog via curl. Body grande vai em arquivo (-d @file) — evita
    OSError [Errno 7] Argument list too long no dashboard 'por funcionalidade'."""
    cmd = [
        "curl",
        "-sS",
        "-f",
        "-u",
        f"{USER}:{PASSWORD}",
        "-H",
        "X-Requested-By: assistente-lab-bootstrap",
        "-H",
        f"Accept: {'application/json' if accept_json else '*/*'}",
        "-X",
        method,
        f"{BASE}{path}",
    ]
    tmp_path: str | None = None
    try:
        if body is not None:
            # Não passar JSON na argv — o search do dashboard estoura ARG_MAX no Alpine/EC2.
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                json.dump(body, fh, ensure_ascii=False)
                tmp_path = fh.name
            cmd += ["-H", "Content-Type: application/json", "--data-binary", f"@{tmp_path}"]
        p = subprocess.run(cmd, capture_output=True, text=True, env=_curl_env())
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    if p.returncode != 0:
        raise RuntimeError(f"{method} {path} -> {p.stderr.strip() or p.stdout.strip() or p.returncode}")
    raw = p.stdout.strip()
    if not raw:
        return {}
    if not accept_json:
        return {"_text": raw}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_text": raw}


def wait_ready() -> None:
    print("==> Aguardando Graylog API (dashboards)...", flush=True)
    for i in range(90):
        try:
            api("GET", "/system")
            return
        except RuntimeError:
            time.sleep(2)
            if i == 89:
                raise SystemExit("Graylog não ficou pronto a tempo.")


def default_index_set() -> str:
    data = api("GET", "/system/indices/index_sets?skip=0&limit=50")
    for item in data.get("index_sets") or []:
        if item.get("default"):
            return item["id"]
    raise RuntimeError("Default index set não encontrado")


def list_streams() -> dict[str, dict]:
    data = api("GET", "/streams")
    return {s["title"]: s for s in data.get("streams") or []}


def ensure_stream(title: str, description: str, field: str, value: str, index_set_id: str) -> str:
    existing = list_streams().get(title)
    if existing:
        print(f"• Stream já existe: {title}", flush=True)
        return existing["id"]
    created = api(
        "POST",
        "/streams",
        {
            "entity": {
                "title": title,
                "description": description,
                "matching_type": "AND",
                "remove_matches_from_default_stream": False,
                "index_set_id": index_set_id,
            },
            "share_request": None,
        },
    )
    sid = created["stream_id"]
    api(
        "POST",
        f"/streams/{sid}/rules",
        {"field": field, "type": 1, "inverted": False, "value": value},
    )
    api("POST", f"/streams/{sid}/resume", {})
    print(f"• Stream criado: {title} ({sid})", flush=True)
    return sid


def list_views() -> dict[str, dict]:
    out: dict[str, dict] = {}
    page = 1
    while True:
        data = api("GET", f"/views?page={page}&per_page=50")
        for view in data.get("views") or []:
            out[view["title"]] = view
        total = int(data.get("total") or 0)
        if page * 50 >= total:
            break
        page += 1
    return out


def delete_view(view_id: str) -> None:
    try:
        api("DELETE", f"/views/{view_id}")
    except RuntimeError as exc:
        print(f"! falha ao apagar view {view_id}: {exc}", flush=True)


def uid() -> str:
    return str(uuid.uuid4())


def domain_query(key: str, path_fallback: str = "") -> str:
    """Aba = somente endpoints do inventário DOMAIN_ROUTES (path_fallback ignorado)."""
    return domain_calls_query(key)


def series_label(fn: str) -> str:
    """Rótulos amigáveis — Graylog 7 não tem unit currency; amount = R$.
    Evitar acentos nos nomes de série de latência: bug de UI deixa células em branco.

    IMPORTANTE (Graylog 7 DataTable): o search_type.series[].id PRECISA ser o
    effectiveName (= config.name do widget). A UI monta as células com
    lodash.get(row, [..., effectiveName]). Se o id for "count()" e o name for
    "Vazao (qtd)", a tabela fica com colunas vazias mesmo com dados na API.
    """
    return {
        "count()": "Vazao (qtd)",
        "sum(amount)": "Total (R$)",
        "avg(amount)": "Ticket medio (R$)",
        "avg(installments)": "Parcelas medias",
        "avg(duration_ms)": "Media (ms)",
        "max(duration_ms)": "Max (ms)",
        "percentile(duration_ms,95.0)": "p95 (ms)",
        "percentile(duration_ms,95)": "p95 (ms)",
        "avg(hits)": "Hits medios",
    }.get(fn, fn)


def series_sum(field: str) -> dict:
    fn = f"sum({field})"
    return {"type": "sum", "id": series_label(fn), "field": field}


def series_count(_name: str = "count()") -> dict:
    return {"type": "count", "id": series_label("count()"), "field": None}


def series_avg(field: str) -> dict:
    fn = f"avg({field})"
    return {"type": "avg", "id": series_label(fn), "field": field}


def series_max(field: str) -> dict:
    fn = f"max({field})"
    return {"type": "max", "id": series_label(fn), "field": field}


def series_p95(field: str = "duration_ms") -> dict:
    # Graylog literal: percentile(field,95.0) — id = rótulo do widget
    fn = f"percentile({field},95.0)"
    return {
        "type": "percentile",
        "id": series_label(fn),
        "field": field,
        "percentile": 95.0,
    }


def series_card(field: str) -> dict:
    fn = f"card({field})"
    return {"type": "card", "id": series_label(fn), "field": field}


# Séries padrão de latência + volume (ids = rótulos do DataTable)
LAT_SERIES = [
    series_count(),
    series_avg("duration_ms"),
    series_max("duration_ms"),
    series_p95("duration_ms"),
]
LAT_FNS = [
    "count()",
    "avg(duration_ms)",
    "max(duration_ms)",
    "percentile(duration_ms,95.0)",
]
COUNT_SERIES_ID = series_label("count()")


def time_group() -> dict:
    return {
        "type": "time",
        "fields": ["timestamp"],
        "interval": {"type": "auto", "scaling": 1.0},
    }


def values_group(field: str, limit: int = 15) -> dict:
    # Search-type Graylog: "field" singular (não "fields") — evita métricas em branco na UI.
    return {
        "type": "values",
        "field": field,
        "limit": limit,
        "skip_empty_values": True,
    }


def values_pivot(field: str, limit: int = 15) -> dict:
    return {
        "fields": [field],
        "type": "values",
        "config": {"limit": limit, "skip_empty_values": True},
    }


def pivot_search_type(
    st_id: str,
    *,
    series: list[dict],
    row_groups: list[dict] | None = None,
    column_groups: list[dict] | None = None,
    rollup: bool = True,
    sort_count_desc: bool = False,
    query: str | None = None,
) -> dict:
    sort = []
    if sort_count_desc:
        # field = series id (= effectiveName / rótulo), não a function string
        sort = [{"type": "series", "field": COUNT_SERIES_ID, "direction": "Descending"}]
    # IMPORTANTE: query no search_type — se ficar null a UI do Graylog
    # executa só o query da aba "ativa"/global e pode listar todos os endpoints.
    q = {"type": "elasticsearch", "query_string": query} if query else None
    return {
        "id": st_id,
        "type": "pivot",
        "name": "chart",
        "timerange": {"type": "relative", "range": RANGE},
        "query": q,
        "streams": [],
        "stream_categories": [],
        "series": series,
        "row_groups": row_groups or [],
        "column_groups": column_groups or [],
        "sort": sort,
        "rollup": rollup,
        "filter": None,
        "filters": [],
    }


def messages_search_type(st_id: str, limit: int = 25, query: str | None = None) -> dict:
    q = {"type": "elasticsearch", "query_string": query} if query else None
    return {
        "id": st_id,
        "type": "messages",
        "timerange": {"type": "relative", "range": RANGE},
        "query": q,
        "streams": [],
        "stream_categories": [],
        "limit": limit,
        "offset": 0,
        "sort": [{"field": "timestamp", "order": "DESC"}],
        "decorators": [],
        "filter": None,
        "filters": [],
    }


def agg_widget(
    wid: str,
    *,
    query: str,
    visualization: str,
    series_fns: list[str],
    row_pivots: list[dict] | None = None,
    column_pivots: list[dict] | None = None,
    rollup: bool = True,
    viz_config: dict | None = None,
) -> dict:
    return {
        "id": wid,
        "type": "aggregation",
        "filter": None,
        "filters": [],
        "timerange": {"type": "relative", "range": RANGE},
        "query": {"type": "elasticsearch", "query_string": query},
        "streams": [],
        "stream_categories": [],
        "config": {
            "row_pivots": row_pivots or [],
            "column_pivots": column_pivots or [],
            "series": [
                {"config": {"name": series_label(fn), "thresholds": []}, "function": fn} for fn in series_fns
            ],
            "sort": [],
            "visualization": visualization,
            "visualization_config": viz_config,
            "formatting_settings": None,
            "rollup": rollup,
            "event_annotation": False,
            "row_limit": None,
            "column_limit": 15,
            "units": {},
        },
        "description": None,
        "context": None,
    }


def messages_widget(wid: str, *, query: str, fields: list[str]) -> dict:
    return {
        "id": wid,
        "type": "messages",
        "filter": None,
        "filters": [],
        "timerange": {"type": "relative", "range": RANGE},
        "query": {"type": "elasticsearch", "query_string": query},
        "streams": [],
        "stream_categories": [],
        "config": {
            "fields": fields,
            "show_message_row": True,
            "show_summary": False,
            "decorators": [],
            "units": {},
        },
        "description": None,
        "context": None,
    }


def time_pivot() -> dict:
    return {
        "fields": ["timestamp"],
        "type": "time",
        "config": {"interval": {"type": "auto", "scaling": 1.0}},
    }


class TabBuilder:
    def __init__(self, title: str, base_query: str):
        self.query_id = uid()
        self.title = title
        self.base_query = base_query
        self.widgets: list[dict] = []
        self.mapping: dict[str, list[str]] = {}
        self.positions: dict[str, dict] = {}
        self.titles: dict[str, str] = {}
        self.search_types: list[dict] = []
        self._row = 1

    def _place(self, wid: str, col: int, width: int | str, height: int = 3) -> None:
        self.positions[wid] = {"col": col, "row": self._row, "height": height, "width": width}

    def next_row(self, height: int = 3) -> None:
        self._row += height

    def add_agg(
        self,
        title: str,
        *,
        query: str | None = None,
        visualization: str,
        series: list[dict],
        series_fns: list[str],
        row_groups: list[dict] | None = None,
        column_groups: list[dict] | None = None,
        row_pivots: list[dict] | None = None,
        column_pivots: list[dict] | None = None,
        col: int = 1,
        width: int | str = "Infinity",
        height: int = 3,
        sort_count_desc: bool = False,
        viz_config: dict | None = None,
        rollup: bool = True,
    ) -> None:
        wid, stid = uid(), uid()
        q = query if query is not None else self.base_query
        self.widgets.append(
            agg_widget(
                wid,
                query=q,
                visualization=visualization,
                series_fns=series_fns,
                row_pivots=row_pivots,
                column_pivots=column_pivots,
                rollup=rollup,
                viz_config=viz_config,
            )
        )
        self.search_types.append(
            pivot_search_type(
                stid,
                series=series,
                row_groups=row_groups,
                column_groups=column_groups,
                rollup=rollup,
                sort_count_desc=sort_count_desc,
                query=q,
            )
        )
        self.mapping[wid] = [stid]
        self.titles[wid] = title
        self._place(wid, col, width, height)

    def add_text(
        self,
        title: str,
        text: str,
        *,
        col: int = 1,
        width: int | str = "Infinity",
        height: int = 3,
    ) -> None:
        """Widget Text/Markdown — sem search type (lista estática)."""
        wid = uid()
        self.widgets.append(
            {
                "id": wid,
                "type": "text",
                "filter": None,
                "filters": [],
                "timerange": None,
                "query": None,
                "streams": [],
                "stream_categories": [],
                "config": {"text": text},
                "description": None,
                "context": None,
            }
        )
        self.mapping[wid] = []
        self.titles[wid] = title
        self._place(wid, col, width, height)

    def add_messages(
        self,
        title: str,
        *,
        query: str,
        fields: list[str],
        col: int = 1,
        width: int | str = "Infinity",
        height: int = 4,
        limit: int = 25,
    ) -> None:
        wid, stid = uid(), uid()
        self.widgets.append(messages_widget(wid, query=query, fields=fields))
        self.search_types.append(messages_search_type(stid, limit=limit, query=query))
        self.mapping[wid] = [stid]
        self.titles[wid] = title
        self._place(wid, col, width, height)

    def to_state(self) -> dict:
        return {
            "selected_fields": None,
            "static_message_list_id": None,
            "titles": {"tab": {"title": self.title}, "widget": self.titles},
            "widgets": self.widgets,
            "widget_mapping": self.mapping,
            "positions": self.positions,
            "formatting": {"highlighting": []},
            "display_mode_settings": {},
        }

    def to_query(self) -> dict:
        return {
            "id": self.query_id,
            "timerange": {"type": "relative", "range": RANGE},
            "filter": None,
            "filters": [],
            "query": {"type": "elasticsearch", "query_string": self.base_query},
            "search_types": self.search_types,
        }


def fill_overview(tab: TabBuilder) -> None:
    """Overview = KPIs de negócio + conteúdo de Jornada/Propostas (sem corte)."""
    sales = SALES_Q

    # --- KPIs negócio ---
    tab.add_agg(
        "Vendas (qtd)",
        query=sales,
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=1,
        width=3,
        height=2,
    )
    tab.add_agg(
        "Total vendido (R$)",
        query=f"{sales} AND _exists_:amount",
        visualization="numeric",
        series=[series_sum("amount")],
        series_fns=["sum(amount)"],
        col=4,
        width=3,
        height=2,
    )
    tab.add_agg(
        "Ticket médio (R$)",
        query=f"{sales} AND _exists_:amount",
        visualization="numeric",
        series=[series_avg("amount")],
        series_fns=["avg(amount)"],
        col=7,
        width=3,
        height=2,
    )
    tab.add_agg(
        "CP criados",
        query="biz_event:cp_created",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=10,
        width=3,
        height=2,
    )
    tab.next_row(2)

    # --- KPIs propostas (ex-Jornada) ---
    tab.add_agg(
        "Propostas criadas",
        query=PROP_CREATED_Q,
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=1,
        width=3,
        height=2,
    )
    tab.add_agg(
        "Propostas integradas",
        query=PROP_INTEGRATED_Q,
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=4,
        width=3,
        height=2,
    )
    tab.add_agg(
        "Total integrado (R$)",
        query=f"{PROP_INTEGRATED_Q} AND _exists_:amount",
        visualization="numeric",
        series=[series_sum("amount")],
        series_fns=["sum(amount)"],
        col=7,
        width=3,
        height=2,
    )
    tab.add_agg(
        "Pendentes (status)",
        query="biz_event:proposal_created AND proposal_status:pending_integration",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=10,
        width=3,
        height=2,
    )
    tab.next_row(2)

    # --- Latência / vazão (topo, visível) ---
    add_latency_vazao_strip(tab, "service:assistente*")

    # --- Funil ---
    tab.add_agg(
        "Funil · etapas da jornada (count)",
        query="journey_step:sale.* AND service:assistente*",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("journey_step", 15)],
        row_pivots=[values_pivot("journey_step", 15)],
        col=1,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Funil · latência por etapa (média/máx/p95)",
        query="journey_step:sale.* AND service:assistente* AND _exists_:duration_ms",
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("journey_step", 15)],
        row_pivots=[values_pivot("journey_step", 15)],
        col=7,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.next_row(4)

    tab.add_agg(
        "Funil · biz_event de negócio",
        query=(
            "biz_event:(product_search OR plan_simulated OR sale_created OR "
            "proposal_created OR proposal_integrated OR cp_created)"
        ),
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("biz_event", 12)],
        row_pivots=[values_pivot("biz_event", 12)],
        col=1,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Funil · latência por biz_event",
        query=(
            "biz_event:(product_search OR plan_simulated OR sale_created OR "
            "proposal_created OR proposal_integrated OR cp_created) AND _exists_:duration_ms"
        ),
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("biz_event", 12)],
        row_pivots=[values_pivot("biz_event", 12)],
        col=7,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.next_row(4)

    # --- Call-chain CDC/CDCI cross-domínio ---
    tab.add_agg(
        "Call-chain CDC/CDCI · vazão + média + máx + p95",
        query=f"({CALL_CHAIN_PATHS}) AND service:assistente* AND _exists_:duration_ms",
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("http_path", 20)],
        row_pivots=[values_pivot("http_path", 20)],
        col=1,
        width=6,
        height=5,
        sort_count_desc=True,
        rollup=False,
    )
    tab.add_agg(
        "Call-chain · vazão no tempo ≈ TPS",
        query=f"({CALL_CHAIN_PATHS}) AND service:assistente*",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[time_group()],
        row_pivots=[time_pivot()],
        col=7,
        width=6,
        height=5,
        viz_config={"barmode": "stack", "axis_type": "linear"},
        rollup=False,
    )
    tab.next_row(5)

    tab.add_agg(
        "Criadas vs integradas por linha (CDC/CDCI)",
        query="biz_event:(proposal_created OR proposal_integrated) AND _exists_:product_type",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("product_type", 8)],
        column_groups=[values_group("biz_event", 4)],
        row_pivots=[values_pivot("product_type", 8)],
        column_pivots=[values_pivot("biz_event", 4)],
        col=1,
        width=6,
        height=3,
        rollup=False,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Financeira (integrações)",
        query=f"{PROP_INTEGRATED_Q} AND _exists_:financeira",
        visualization="pie",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("financeira", 8)],
        row_pivots=[values_pivot("financeira", 8)],
        col=7,
        width=6,
        height=3,
        sort_count_desc=True,
    )
    tab.next_row(3)

    tab.add_agg(
        "Simulou plano",
        query="biz_event:plan_simulated",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=1,
        width=4,
        height=2,
    )
    tab.add_agg(
        "Vendas CDC/CDCI",
        query=f'{SALES_Q} AND payment_method:(CDC OR CDCI)',
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=5,
        width=4,
        height=2,
    )
    tab.add_agg(
        "Integrações",
        query=PROP_INTEGRATED_Q,
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=9,
        width=4,
        height=2,
    )
    tab.next_row(2)

    # --- Mix / planos / categorias (Overview original) ---
    tab.add_agg(
        "% share por meio (qtd)",
        query=f"{sales} AND _exists_:payment_method",
        visualization="pie",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("payment_method", 10)],
        row_pivots=[values_pivot("payment_method", 10)],
        col=1,
        width=4,
        height=4,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Meios: qtd + valores em R$",
        query=f"{sales} AND _exists_:payment_method AND _exists_:amount",
        visualization="table",
        series=[series_count(), series_sum("amount"), series_avg("amount")],
        series_fns=["count()", "sum(amount)", "avg(amount)"],
        row_groups=[values_group("payment_method", 10)],
        row_pivots=[values_pivot("payment_method", 10)],
        col=5,
        width=8,
        height=4,
        sort_count_desc=True,
    )
    tab.next_row(4)

    tab.add_agg(
        "Vendas no tempo por meio",
        query=f"{sales} AND _exists_:payment_method",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[time_group()],
        column_groups=[values_group("payment_method", 8)],
        row_pivots=[time_pivot()],
        column_pivots=[values_pivot("payment_method", 8)],
        height=3,
        viz_config={"barmode": "stack", "axis_type": "linear"},
        rollup=False,
    )
    tab.next_row(3)

    tab.add_agg(
        "Planos: qtd + valores em R$",
        query=f'{sales} AND _exists_:plan_name AND NOT plan_name:""',
        visualization="table",
        series=[series_count(), series_sum("amount"), series_avg("amount")],
        series_fns=["count()", "sum(amount)", "avg(amount)"],
        row_groups=[values_group("plan_name", 12)],
        row_pivots=[values_pivot("plan_name", 12)],
        col=1,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.add_agg(
        "% share planos (qtd)",
        query=f'{sales} AND _exists_:plan_name AND NOT plan_name:""',
        visualization="pie",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("plan_name", 10)],
        row_pivots=[values_pivot("plan_name", 10)],
        col=7,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.next_row(4)

    tab.add_agg(
        "Categorias: qtd + valores em R$",
        query=f'{sales} AND product_category:* AND NOT product_category:""',
        visualization="table",
        series=[series_count(), series_sum("amount"), series_avg("amount")],
        series_fns=["count()", "sum(amount)", "avg(amount)"],
        row_groups=[values_group("product_category", 10)],
        row_pivots=[values_pivot("product_category", 10)],
        col=1,
        width=6,
        height=3,
        sort_count_desc=True,
    )
    tab.add_agg(
        "% share categoria (qtd)",
        query=f'{sales} AND product_category:* AND NOT product_category:""',
        visualization="pie",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("product_category", 10)],
        row_pivots=[values_pivot("product_category", 10)],
        col=7,
        width=6,
        height=3,
        sort_count_desc=True,
    )
    tab.next_row(3)

    tab.add_agg(
        "Top SKU por valor (R$)",
        query=f"{sales} AND _exists_:product_name",
        visualization="table",
        series=[series_sum("amount"), series_count(), series_avg("amount")],
        series_fns=["sum(amount)", "count()", "avg(amount)"],
        row_groups=[values_group("product_name", 15)],
        row_pivots=[values_pivot("product_name", 15)],
        col=1,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Vendas / valor (R$) por vendedor",
        query=f"{sales} AND _exists_:staff_id",
        visualization="table",
        series=[series_count(), series_sum("amount")],
        series_fns=["count()", "sum(amount)"],
        row_groups=[values_group("staff_id", 15)],
        row_pivots=[values_pivot("staff_id", 15)],
        col=7,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.next_row(4)

    tab.add_agg(
        "Buscas sem resultado (hits=0)",
        query='biz_event:product_search AND hits:0 AND query:* AND NOT query:""',
        visualization="table",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("query", 15)],
        row_pivots=[values_pivot("query", 15)],
        col=1,
        width=6,
        height=3,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Crédito negado por linha",
        query="error_type:CreditNotApproved",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("product_type", 8)],
        row_pivots=[values_pivot("product_type", 8)],
        col=7,
        width=3,
        height=3,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Sem estoque (InsufficientStock)",
        query="error_type:InsufficientStock",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("item_id", 10)],
        row_pivots=[values_pivot("item_id", 10)],
        col=10,
        width=3,
        height=3,
        sort_count_desc=True,
    )
    tab.next_row(3)

    # --- Erros (negócio tipado + HTTP) ---
    add_error_widgets(tab, scope="service:assistente*", full=True)

    tab.add_messages(
        "Últimas vendas",
        query=sales,
        fields=[
            "timestamp",
            "payment_method",
            "plan_name",
            "installments",
            "product_category",
            "product_name",
            "amount",
            "financeira",
            "sale_id",
            "proposal_id",
            "staff_id",
        ],
        height=5,
    )
    tab.next_row(5)

    tab.add_messages(
        "Propostas (criadas / integradas)",
        query="biz_event:(proposal_created OR proposal_integrated)",
        fields=[
            "timestamp",
            "biz_event",
            "proposal_status",
            "product_type",
            "plan_name",
            "installments",
            "amount",
            "financeira",
            "proposal_id",
            "sale_id",
            "test_run_id",
            "journey_step",
        ],
        height=5,
    )


def add_error_widgets(tab: TabBuilder, *, scope: str, full: bool = False) -> None:
    """Widgets de erro tipado (error_type) e HTTP (>=400)."""
    base = f"({scope}) AND {ERR_ANY}"
    typed = f"({scope}) AND ({ERR_TYPED})"
    http_err = f"({scope}) AND ({ERR_HTTP})"

    tab.add_agg(
        "Erros tipados (qtd)",
        query=typed,
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=1,
        width=3,
        height=2,
    )
    tab.add_agg(
        "HTTP 4xx/5xx (qtd)",
        query=http_err,
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=4,
        width=3,
        height=2,
    )
    tab.add_agg(
        "HTTP 5xx (qtd)",
        query=f"({scope}) AND http_status:>=500",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=7,
        width=3,
        height=2,
    )
    tab.add_agg(
        "Crédito negado (qtd)",
        query=f"({scope}) AND error_type:CreditNotApproved",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=10,
        width=3,
        height=2,
    )
    tab.next_row(2)
    if full:
        tab.add_agg(
            "InsufficientStock (qtd)",
            query=f"({scope}) AND error_type:InsufficientStock",
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=1,
            width=3,
            height=2,
        )
        tab.add_agg(
            "InsufficientStock por SKU",
            query=f"({scope}) AND error_type:InsufficientStock",
            visualization="bar",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("item_id", 10)],
            row_pivots=[values_pivot("item_id", 10)],
            col=4,
            width=9,
            height=2,
            sort_count_desc=True,
        )
        tab.next_row(2)

    tab.add_agg(
        "Erros por error_type",
        query=typed,
        visualization="pie" if not full else "table",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("error_type", 15)],
        row_pivots=[values_pivot("error_type", 15)],
        col=1,
        width=6 if full else 6,
        height=3 if not full else 4,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Erros por status HTTP",
        query=http_err,
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[values_group("http_status", 10)],
        row_pivots=[values_pivot("http_status", 10)],
        col=7,
        width=6,
        height=3 if not full else 4,
        sort_count_desc=True,
    )
    tab.next_row(4 if full else 3)

    if full:
        tab.add_agg(
            "Erros por domínio (lab_domain)",
            query=f"{base} AND _exists_:lab_domain",
            visualization="bar",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("lab_domain", 12)],
            row_pivots=[values_pivot("lab_domain", 12)],
            col=1,
            width=6,
            height=3,
            sort_count_desc=True,
        )
        tab.add_agg(
            "Erros por endpoint (http_path)",
            query=f"{base} AND _exists_:http_path",
            visualization="table",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("http_path", 15)],
            row_pivots=[values_pivot("http_path", 15)],
            col=7,
            width=6,
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)

    tab.add_messages(
        "Últimos erros",
        query=base,
        fields=[
            "timestamp",
            "error_type",
            "http_status",
            "lab_domain",
            "http_method",
            "http_route",
            "http_path",
            "journey_step",
            "message",
        ],
        height=4,
    )
    tab.next_row(4)


def fill_performance(tab: TabBuilder) -> None:
    scope = "service:assistente* AND _exists_:duration_ms"

    # KPIs gerais
    tab.add_agg(
        "Req (janela)",
        query="service:assistente*",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=1,
        width=2,
        height=2,
    )
    tab.add_agg(
        "Latência média (ms)",
        query=scope,
        visualization="numeric",
        series=[series_avg("duration_ms")],
        series_fns=["avg(duration_ms)"],
        col=3,
        width=2,
        height=2,
    )
    tab.add_agg(
        "Latência máx (ms)",
        query=scope,
        visualization="numeric",
        series=[series_max("duration_ms")],
        series_fns=["max(duration_ms)"],
        col=5,
        width=2,
        height=2,
    )
    tab.add_agg(
        "Latência p95 (ms)",
        query=scope,
        visualization="numeric",
        series=[series_p95()],
        series_fns=["percentile(duration_ms,95.0)"],
        col=7,
        width=2,
        height=2,
    )
    tab.add_agg(
        "Erros tipados",
        query=f"service:assistente* AND ({ERR_TYPED})",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=9,
        width=2,
        height=2,
    )
    tab.add_agg(
        "HTTP 4xx/5xx",
        query=f"service:assistente* AND ({ERR_HTTP})",
        visualization="numeric",
        series=[series_count()],
        series_fns=["count()"],
        col=11,
        width=2,
        height=2,
    )
    tab.next_row(2)

    # Vazão geral + por domínio
    tab.add_agg(
        "Vazão geral (req no tempo) ≈ TPS visual",
        query="service:assistente*",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[time_group()],
        row_pivots=[time_pivot()],
        height=3,
        viz_config={"barmode": "stack", "axis_type": "linear"},
        rollup=False,
    )
    tab.next_row(3)

    tab.add_agg(
        "Vazão por domínio (req no tempo)",
        query="service:assistente* AND _exists_:lab_domain",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[time_group()],
        column_groups=[values_group("lab_domain", 12)],
        row_pivots=[time_pivot()],
        column_pivots=[values_pivot("lab_domain", 12)],
        height=4,
        viz_config={"barmode": "stack", "axis_type": "linear"},
        rollup=False,
    )
    tab.next_row(4)

    tab.add_agg(
        "Domínio · qtd + média + máx + p95 (ms)",
        query=f"{scope} AND _exists_:lab_domain",
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("lab_domain", 12)],
        row_pivots=[values_pivot("lab_domain", 12)],
        height=4,
        sort_count_desc=True,
    )
    tab.next_row(4)

    tab.add_agg(
        "Endpoint · qtd + média + máx + p95 (ms)",
        query=f"{scope} AND _exists_:http_path",
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("http_path", 25)],
        row_pivots=[values_pivot("http_path", 25)],
        height=5,
        sort_count_desc=True,
        rollup=False,
    )
    tab.next_row(5)

    tab.add_agg(
        "Vazão por endpoint (req no tempo)",
        query="service:assistente* AND _exists_:http_path",
        visualization="bar",
        series=[series_count()],
        series_fns=["count()"],
        row_groups=[time_group()],
        column_groups=[values_group("http_path", 12)],
        row_pivots=[time_pivot()],
        column_pivots=[values_pivot("http_path", 12)],
        height=4,
        viz_config={"barmode": "stack", "axis_type": "linear"},
        rollup=False,
    )
    tab.next_row(4)

    tab.add_agg(
        "Latência por etapa (journey_step)",
        query="service:assistente* AND journey_step:sale.* AND _exists_:duration_ms",
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("journey_step", 15)],
        row_pivots=[values_pivot("journey_step", 15)],
        col=1,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Latência por biz_event",
        query="service:assistente* AND _exists_:biz_event AND _exists_:duration_ms",
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("biz_event", 15)],
        row_pivots=[values_pivot("biz_event", 15)],
        col=7,
        width=6,
        height=4,
        sort_count_desc=True,
    )
    tab.next_row(4)

    tab.add_agg(
        "Pré-venda · latência por meio (ms)",
        query=f"{SALES_Q} AND _exists_:payment_method AND _exists_:duration_ms",
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("payment_method", 10)],
        row_pivots=[values_pivot("payment_method", 10)],
        col=1,
        width=6,
        height=3,
        sort_count_desc=True,
    )
    tab.add_agg(
        "Call-chain · qtd + média + máx + p95",
        query=f"({CALL_CHAIN_PATHS}) AND service:assistente* AND _exists_:duration_ms",
        visualization="table",
        series=LAT_SERIES,
        series_fns=LAT_FNS,
        row_groups=[values_group("http_path", 15)],
        row_pivots=[values_pivot("http_path", 15)],
        col=7,
        width=6,
        height=3,
        sort_count_desc=True,
    )
    tab.next_row(3)

    tab.add_messages(
        "Requests lentos (>200ms)",
        query="service:assistente* AND duration_ms:>200",
        fields=[
            "timestamp",
            "duration_ms",
            "lab_domain",
            "http_route",
            "http_path",
            "journey_step",
            "biz_event",
            "payment_method",
            "http_status",
            "error_type",
            "message",
        ],
        height=5,
    )
    tab.next_row(5)
    add_error_widgets(tab, scope="service:assistente*", full=False)


def fill_domain(tab: TabBuilder, key: str) -> None:
    # Inventário fixo da funcionalidade — nunca lab_domain “aberto”
    q = domain_calls_query(key)
    sales = f"service:assistente* AND {SALES_Q}"

    add_domain_routes(tab, key)
    add_error_widgets(tab, scope=f"({q})", full=False)

    if key == "pagamento":
        tab.add_agg(
            "Simulações por linha (CDC / CDCI)",
            query=f"({q}) AND (biz_event:plan_simulated OR biz_event:payment_plans_listed OR _exists_:product_type)",
            visualization="pie",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("product_type", 8)],
            row_pivots=[values_pivot("product_type", 8)],
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_agg(
            "Planos simulados (qtd)",
            query=f'({q}) AND biz_event:plan_simulated AND _exists_:plan_name AND NOT plan_name:""',
            visualization="table",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("plan_name", 15)],
            row_pivots=[values_pivot("plan_name", 15)],
            col=1,
            width=6,
            height=4,
            sort_count_desc=True,
        )
        tab.add_agg(
            "Planos fechados na venda",
            query=f'{sales} AND _exists_:plan_name AND NOT plan_name:""',
            visualization="table",
            series=[series_count(), series_sum("amount")],
            series_fns=["count()", "sum(amount)"],
            row_groups=[values_group("plan_name", 15)],
            row_pivots=[values_pivot("plan_name", 15)],
            col=7,
            width=6,
            height=4,
            sort_count_desc=True,
        )
        tab.next_row(4)
        tab.add_agg(
            "Simulações (qtd)",
            query=f"({q}) AND biz_event:plan_simulated",
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=1,
            width=4,
            height=2,
        )
        tab.add_agg(
            "Vendas financiadas",
            query=f'{sales} AND payment_method:(CDC OR CDCI)',
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=5,
            width=4,
            height=2,
        )
        tab.add_agg(
            "Latência BatchSimulate (média / máx / p95)",
            query=f"({q}) AND http_path:\\/InstallmentSimulator\\/api\\/InstallmentSimulator\\/BatchSimulate AND _exists_:duration_ms",
            visualization="numeric",
            series=[series_avg("duration_ms"), series_max("duration_ms"), series_p95()],
            series_fns=["avg(duration_ms)", "max(duration_ms)", "percentile(duration_ms,95.0)"],
            col=9,
            width=4,
            height=2,
        )
        tab.next_row(2)
        tab.add_messages(
            "Simulações recentes",
            query=f"({q}) AND (biz_event:plan_simulated OR biz_event:payment_plans_listed)",
            fields=["timestamp", "product_type", "plan_name", "amount", "installments", "duration_ms", "message"],
            height=4,
        )
        return

    if key == "venda":
        tab.add_agg(
            "Mix de pagamento",
            query=f"{sales} AND _exists_:payment_method",
            visualization="pie",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("payment_method", 10)],
            row_pivots=[values_pivot("payment_method", 10)],
            col=1,
            width=4,
            height=3,
            sort_count_desc=True,
        )
        tab.add_agg(
            "Meios: qtd + valores em R$",
            query=f"{sales} AND _exists_:payment_method AND _exists_:amount",
            visualization="table",
            series=[series_count(), series_sum("amount"), series_avg("amount")],
            series_fns=["count()", "sum(amount)", "avg(amount)"],
            row_groups=[values_group("payment_method", 10)],
            row_pivots=[values_pivot("payment_method", 10)],
            col=5,
            width=8,
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_agg(
            "Vendas",
            query=sales,
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=1,
            width=3,
            height=2,
        )
        tab.add_agg(
            "Total vendido (R$)",
            query=f"{sales} AND _exists_:amount",
            visualization="numeric",
            series=[series_sum("amount")],
            series_fns=["sum(amount)"],
            col=4,
            width=3,
            height=2,
        )
        tab.add_agg(
            "Ticket médio (R$)",
            query=f"{sales} AND _exists_:amount",
            visualization="numeric",
            series=[series_avg("amount")],
            series_fns=["avg(amount)"],
            col=7,
            width=3,
            height=2,
        )
        tab.add_agg(
            "Parcelas médias",
            query=f"{sales} AND installments:>0",
            visualization="numeric",
            series=[series_avg("installments")],
            series_fns=["avg(installments)"],
            col=10,
            width=3,
            height=2,
        )
        tab.next_row(2)
        tab.add_agg(
            "Plano fechado na venda",
            query=f"{sales} AND _exists_:plan_name",
            visualization="table",
            series=[series_count(), series_sum("amount")],
            series_fns=["count()", "sum(amount)"],
            row_groups=[values_group("plan_name", 12)],
            row_pivots=[values_pivot("plan_name", 12)],
            col=1,
            width=6,
            height=4,
            sort_count_desc=True,
        )
        tab.add_agg(
            "Categoria vendida",
            query=f'{sales} AND product_category:* AND NOT product_category:""',
            visualization="pie",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("product_category", 10)],
            row_pivots=[values_pivot("product_category", 10)],
            col=7,
            width=6,
            height=4,
            sort_count_desc=True,
        )
        tab.next_row(4)
        tab.add_agg(
            "SKU / produto",
            query=f"{sales} AND _exists_:product_name",
            visualization="table",
            series=[series_count(), series_sum("amount"), series_avg("amount")],
            series_fns=["count()", "sum(amount)", "avg(amount)"],
            row_groups=[values_group("product_name", 15)],
            row_pivots=[values_pivot("product_name", 15)],
            height=4,
            sort_count_desc=True,
        )
        tab.next_row(4)
        tab.add_agg(
            "Sem estoque (InsufficientStock)",
            query="error_type:InsufficientStock",
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=1,
            width=4,
            height=2,
        )
        tab.add_agg(
            "InsufficientStock por SKU",
            query="error_type:InsufficientStock",
            visualization="bar",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("item_id", 10)],
            row_pivots=[values_pivot("item_id", 10)],
            col=5,
            width=8,
            height=2,
            sort_count_desc=True,
        )
        tab.next_row(2)
        tab.add_messages(
            "Pré-vendas recentes",
            query=sales,
            fields=[
                "timestamp",
                "payment_method",
                "plan_name",
                "installments",
                "product_category",
                "product_name",
                "amount",
                "sale_id",
                "proposal_id",
                "duration_ms",
            ],
            height=5,
        )
        return

    if key == "catalogo":
        tab.add_agg(
            "Categoria nos resultados da busca",
            query=f'({q}) AND biz_event:product_search AND product_category:* AND NOT product_category:""',
            visualization="pie",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("product_category", 10)],
            row_pivots=[values_pivot("product_category", 10)],
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_agg(
            "Termos mais buscados",
            query=f'({q}) AND biz_event:product_search AND query:* AND NOT query:""',
            visualization="table",
            series=[series_count(), series_avg("hits")],
            series_fns=["count()", "avg(hits)"],
            row_groups=[values_group("query", 20)],
            row_pivots=[values_pivot("query", 20)],
            col=1,
            width=6,
            height=4,
            sort_count_desc=True,
        )
        tab.add_agg(
            "Buscas sem resultado (hits=0)",
            query=f'({q}) AND biz_event:product_search AND hits:0 AND query:* AND NOT query:""',
            visualization="table",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("query", 15)],
            row_pivots=[values_pivot("query", 15)],
            col=7,
            width=6,
            height=4,
            sort_count_desc=True,
        )
        tab.next_row(4)
        tab.add_agg(
            "Categoria vendida (pré-vendas)",
            query='biz_event:sale_created AND product_category:* AND NOT product_category:""',
            visualization="bar",
            series=[series_count(), series_sum("amount")],
            series_fns=["count()", "sum(amount)"],
            row_groups=[values_group("product_category", 10)],
            row_pivots=[values_pivot("product_category", 10)],
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_messages(
            "Buscas recentes",
            query=f"({q}) AND biz_event:product_search",
            fields=["timestamp", "query", "hits", "product_category", "duration_ms", "message"],
            height=4,
        )
        return

    if key == "propostas":
        tab.add_agg(
            "Criadas",
            query=f"({q}) AND {PROP_CREATED_Q}",
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=1,
            width=3,
            height=2,
        )
        tab.add_agg(
            "Integradas",
            query=f"({q}) AND {PROP_INTEGRATED_Q}",
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=4,
            width=3,
            height=2,
        )
        tab.add_agg(
            "Total integrado (R$)",
            query=f"({q}) AND {PROP_INTEGRATED_Q} AND _exists_:amount",
            visualization="numeric",
            series=[series_sum("amount")],
            series_fns=["sum(amount)"],
            col=7,
            width=3,
            height=2,
        )
        tab.add_agg(
            "Latência integrar (média / máx / p95)",
            query=f"({q}) AND {PROP_INTEGRATED_Q} AND _exists_:duration_ms",
            visualization="numeric",
            series=[series_avg("duration_ms"), series_max("duration_ms"), series_p95()],
            series_fns=["avg(duration_ms)", "max(duration_ms)", "percentile(duration_ms,95.0)"],
            col=10,
            width=3,
            height=2,
        )
        tab.next_row(2)
        tab.add_agg(
            "Integrações por linha (CDC / CDCI)",
            query=f"({q}) AND {PROP_INTEGRATED_Q}",
            visualization="pie",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("product_type", 8)],
            row_pivots=[values_pivot("product_type", 8)],
            col=1,
            width=6,
            height=3,
            sort_count_desc=True,
        )
        tab.add_agg(
            "Financeira",
            query=f"({q}) AND _exists_:financeira",
            visualization="bar",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("financeira", 8)],
            row_pivots=[values_pivot("financeira", 8)],
            col=7,
            width=6,
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_agg(
            "Plano integrado",
            query=f"({q}) AND {PROP_INTEGRATED_Q} AND _exists_:plan_name",
            visualization="table",
            series=[series_count(), series_sum("amount")],
            series_fns=["count()", "sum(amount)"],
            row_groups=[values_group("plan_name", 12)],
            row_pivots=[values_pivot("plan_name", 12)],
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_messages(
            "Propostas / integrações",
            query=f"({q}) AND biz_event:(proposal_created OR proposal_integrated OR proposals_listed)",
            fields=[
                "timestamp",
                "biz_event",
                "proposal_status",
                "product_type",
                "plan_name",
                "installments",
                "amount",
                "financeira",
                "proposal_id",
                "duration_ms",
            ],
            height=4,
        )
        return

    if key == "cp":
        tab.add_agg(
            "CP criados",
            query=f"({q}) AND biz_event:cp_created",
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=1,
            width=4,
            height=2,
        )
        tab.add_agg(
            "Volume CP (R$)",
            query=f"({q}) AND biz_event:cp_created AND _exists_:amount",
            visualization="numeric",
            series=[series_sum("amount")],
            series_fns=["sum(amount)"],
            col=5,
            width=4,
            height=2,
        )
        tab.add_agg(
            "Parcelas médias CP",
            query=f"({q}) AND biz_event:cp_created AND installments:>0",
            visualization="numeric",
            series=[series_avg("installments")],
            series_fns=["avg(installments)"],
            col=9,
            width=4,
            height=2,
        )
        tab.next_row(2)
        tab.add_agg(
            "Planos CP",
            query=f"({q}) AND biz_event:cp_created AND _exists_:plan_name",
            visualization="bar",
            series=[series_count(), series_sum("amount")],
            series_fns=["count()", "sum(amount)"],
            row_groups=[values_group("plan_name", 10)],
            row_pivots=[values_pivot("plan_name", 10)],
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_messages(
            "Créditos pessoais",
            query=f'({q}) AND (biz_event:cp_created OR message:"personal credit")',
            fields=["timestamp", "plan_name", "installments", "amount", "cpf", "duration_ms", "message"],
            height=4,
        )
        return

    if key == "cliente":
        tab.add_agg(
            "Crédito negado (qtd)",
            query=f"({q}) AND error_type:CreditNotApproved",
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=1,
            width=4,
            height=2,
        )
        tab.add_agg(
            "Consultas de limite",
            query=f'({q}) AND (message:"customer limits" OR http_path:\\/Customer\\/api\\/CustomerLimits)',
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=5,
            width=4,
            height=2,
        )
        tab.add_agg(
            "Clientes encontrados",
            query=f'({q}) AND message:"customer found"',
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=9,
            width=4,
            height=2,
        )
        tab.next_row(2)
        tab.add_agg(
            "Negados por linha (CDC/CDCI/CP)",
            query=f"({q}) AND error_type:CreditNotApproved",
            visualization="pie",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("product_type", 8)],
            row_pivots=[values_pivot("product_type", 8)],
            col=1,
            width=6,
            height=3,
            sort_count_desc=True,
        )
        tab.add_agg(
            "Linha consultada",
            query=f"({q}) AND _exists_:product_type",
            visualization="bar",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("product_type", 8)],
            row_pivots=[values_pivot("product_type", 8)],
            col=7,
            width=6,
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_messages(
            "Cliente / limite",
            query=q,
            fields=["timestamp", "cpf", "product_type", "available_limit", "error_type", "customer_name", "duration_ms", "message"],
            height=4,
        )
        return

    if key == "auth":
        tab.add_agg(
            "Logins por vendedor",
            query=f'({q}) AND (message:"login ok" OR _exists_:staff_id)',
            visualization="bar",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("staff_id", 15)],
            row_pivots=[values_pivot("staff_id", 15)],
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_messages(
            "Sessões",
            query=q,
            fields=["timestamp", "staff_id", "store_id", "duration_ms", "message"],
            height=4,
        )
        return

    if key == "estoque":
        tab.add_agg(
            "Itens consultados",
            query=f"({q}) AND _exists_:item_id",
            visualization="bar",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("item_id", 15)],
            row_pivots=[values_pivot("item_id", 15)],
            col=1,
            width=8,
            height=3,
            sort_count_desc=True,
        )
        tab.add_agg(
            "InsufficientStock",
            query="error_type:InsufficientStock",
            visualization="numeric",
            series=[series_count()],
            series_fns=["count()"],
            col=9,
            width=4,
            height=3,
        )
        tab.next_row(3)
        tab.add_agg(
            "Sem estoque por SKU",
            query="error_type:InsufficientStock",
            visualization="bar",
            series=[series_count()],
            series_fns=["count()"],
            row_groups=[values_group("item_id", 10)],
            row_pivots=[values_pivot("item_id", 10)],
            height=3,
            sort_count_desc=True,
        )
        tab.next_row(3)
        tab.add_messages(
            "Consultas de estoque",
            query=q,
            fields=["timestamp", "item_id", "stock", "duration_ms", "message"],
            height=4,
        )
        return

    tab.add_messages("Eventos", query=q, fields=["timestamp", "message", "http_path", "biz_event"], height=5)


def create_dashboard(title: str, summary: str, description: str, tabs: list[TabBuilder]) -> str:
    search = api(
        "POST",
        "/views/search",
        {"queries": [t.to_query() for t in tabs], "parameters": []},
    )
    state = {t.query_id: t.to_state() for t in tabs}
    view = api(
        "POST",
        "/views",
        {
            "entity": {
                "type": "DASHBOARD",
                "title": title,
                "summary": summary,
                "description": description,
                "search_id": search["id"],
                "properties": [],
                "requires": {},
                "state": state,
            },
            "share_request": None,
        },
    )
    vid = view["id"]
    ui = BASE[: -len("/api")]
    print(f"• Dashboard: {title} → {ui}/dashboards/{vid}", flush=True)
    return vid


def ensure_dashboard(title: str, summary: str, description: str, tabs: list[TabBuilder]) -> str:
    views = list_views()
    force = os.environ.get("GRAYLOG_FORCE_DASHBOARDS", "").lower() in {"1", "true", "yes"}
    if title in views and not force:
        vid = views[title]["id"]
        ui = BASE[: -len("/api")]
        print(f"• Dashboard já existe: {title} → {ui}/dashboards/{vid}", flush=True)
        return vid
    if title in views and force:
        print(f"• Recriando dashboard: {title}", flush=True)
        delete_view(views[title]["id"])
    return create_dashboard(title, summary, description, tabs)


def ensure_lab_domain_pipeline() -> None:
    """Deriva lab_domain do http_path quando o app ainda não enviou o campo."""
    print("==> Pipeline lab_domain (enrichment)", flush=True)
    rules_wanted = [
        (
            "assistente-domain-auth",
            'rule "assistente-domain-auth"\nwhen\n  has_field("http_path") AND contains(lowercase(to_string($message.http_path)), "userauthentication")\nthen\n  set_field("lab_domain", "auth");\n  set_field("spm_service", "assistente-auth");\nend\n',
        ),
        (
            "assistente-domain-catalogo",
            'rule "assistente-domain-catalogo"\nwhen\n  has_field("http_path") AND contains(to_string($message.http_path), "/Products/") AND (!has_field("lab_domain") OR to_string($message.lab_domain)=="")\nthen\n  set_field("lab_domain", "catalogo");\n  set_field("spm_service", "assistente-catalogo");\nend\n',
        ),
        (
            "assistente-domain-estoque",
            'rule "assistente-domain-estoque"\nwhen\n  has_field("http_path") AND contains(to_string($message.http_path), "/Stock/") AND (!has_field("lab_domain") OR to_string($message.lab_domain)=="")\nthen\n  set_field("lab_domain", "estoque");\n  set_field("spm_service", "assistente-estoque");\nend\n',
        ),
        (
            "assistente-domain-cliente",
            'rule "assistente-domain-cliente"\nwhen\n  has_field("http_path") AND contains(to_string($message.http_path), "/Customer/") AND (!has_field("lab_domain") OR to_string($message.lab_domain)=="")\nthen\n  set_field("lab_domain", "cliente");\n  set_field("spm_service", "assistente-cliente");\nend\n',
        ),
        (
            "assistente-domain-pagamento",
            'rule "assistente-domain-pagamento"\nwhen\n  has_field("http_path") AND (contains(to_string($message.http_path), "/PaymentCondition") OR contains(to_string($message.http_path), "/InstallmentSimulator")) AND (!has_field("lab_domain") OR to_string($message.lab_domain)=="")\nthen\n  set_field("lab_domain", "pagamento");\n  set_field("spm_service", "assistente-pagamento");\nend\n',
        ),
        (
            "assistente-domain-venda",
            'rule "assistente-domain-venda"\nwhen\n  has_field("http_path") AND contains(to_string($message.http_path), "/SalesOrder/") AND (!has_field("lab_domain") OR to_string($message.lab_domain)=="")\nthen\n  set_field("lab_domain", "venda");\n  set_field("spm_service", "assistente-venda");\nend\n',
        ),
        (
            "assistente-domain-propostas",
            'rule "assistente-domain-propostas"\nwhen\n  has_field("http_path") AND contains(to_string($message.http_path), "/MultiFinancial/") AND (!has_field("lab_domain") OR to_string($message.lab_domain)=="")\nthen\n  set_field("lab_domain", "propostas");\n  set_field("spm_service", "assistente-propostas");\nend\n',
        ),
        (
            "assistente-domain-cp",
            'rule "assistente-domain-cp"\nwhen\n  has_field("http_path") AND contains(to_string($message.http_path), "/PersonalCredit/") AND (!has_field("lab_domain") OR to_string($message.lab_domain)=="")\nthen\n  set_field("lab_domain", "cp");\n  set_field("spm_service", "assistente-cp");\nend\n',
        ),
        (
            "assistente-domain-plataforma",
            'rule "assistente-domain-plataforma"\nwhen\n  has_field("http_path") AND has_field("service") AND contains(to_string($message.service), "assistente") AND (!has_field("lab_domain") OR to_string($message.lab_domain)=="")\nthen\n  set_field("lab_domain", "plataforma");\n  set_field("spm_service", "assistente-plataforma");\nend\n',
        ),
    ]

    existing_rules = {r.get("title"): r for r in (api("GET", "/system/pipelines/rule") or [])}
    rule_titles: list[str] = []
    for title, source in rules_wanted:
        if title in existing_rules:
            print(f"• Rule já existe: {title}", flush=True)
        else:
            api("POST", "/system/pipelines/rule", {"title": title, "description": title, "source": source})
            print(f"• Rule criada: {title}", flush=True)
        rule_titles.append(title)

    rule_lines = "\n".join(f'  rule "{t}";' for t in rule_titles)
    pipe_source = f'pipeline "Assistente · lab_domain"\nstage 0 match either\n{rule_lines}\nend\n'
    pipelines = api("GET", "/system/pipelines/pipeline") or []
    pipe_title = "Assistente · lab_domain"
    pipe = next((p for p in pipelines if p.get("title") == pipe_title), None)
    if pipe:
        api(
            "PUT",
            f"/system/pipelines/pipeline/{pipe['id']}",
            {
                "id": pipe["id"],
                "title": pipe_title,
                "description": "Classifica logs por funcionalidade (lab_domain)",
                "source": pipe_source,
            },
        )
        pipe_id = pipe["id"]
        print(f"• Pipeline atualizado: {pipe_title}", flush=True)
    else:
        created = api(
            "POST",
            "/system/pipelines/pipeline",
            {
                "title": pipe_title,
                "description": "Classifica logs por funcionalidade (lab_domain)",
                "source": pipe_source,
            },
        )
        pipe_id = created["id"]
        print(f"• Pipeline criado: {pipe_title}", flush=True)

    try:
        api(
            "POST",
            "/system/pipelines/connections/to_stream",
            {"stream_id": "000000000000000000000001", "pipeline_ids": [pipe_id]},
        )
        print("• Pipeline conectado ao Default Stream", flush=True)
    except RuntimeError as exc:
        print(f"! conexão pipeline/stream: {exc}", flush=True)


def main() -> int:
    wait_ready()
    idx = default_index_set()

    views = list_views()
    if SMOKE_TITLE in views:
        print(f"• Removendo {SMOKE_TITLE}", flush=True)
        delete_view(views[SMOKE_TITLE]["id"])
    for legacy in LEGACY_TITLES:
        if legacy in views:
            print(f"• Removendo dashboard legado: {legacy}", flush=True)
            delete_view(views[legacy]["id"])
            views = list_views()

    streams = list_streams()
    if "Lab · auth" in streams:
        try:
            api("DELETE", f"/streams/{streams['Lab · auth']['id']}")
            print("• Removido stream Lab · auth", flush=True)
        except RuntimeError:
            pass

    print("==> Streams por funcionalidade", flush=True)
    for key, label, blurb, _ in DOMAINS:
        ensure_stream(
            f"Assistente · {label}",
            f"{blurb} (lab_domain={key})",
            "lab_domain",
            key,
            idx,
        )
    ensure_stream(
        "Assistente · plataforma",
        "rotas auxiliares / health",
        "lab_domain",
        "plataforma",
        idx,
    )

    ensure_lab_domain_pipeline()

    print("==> Dashboard Overview (negócio + jornada)", flush=True)
    overview = TabBuilder("Overview", "service:assistente*")
    fill_overview(overview)
    id_overview = ensure_dashboard(
        OVERVIEW_TITLE,
        "KPIs, funil, call-chain CDC, mix e Total vendido (R$)",
        "Visão de negócio do Assistente Colombo (lab). "
        "GMV = Total vendido em R$. biz_event = rótulo de evento de negócio no log. "
        "Call-chain CDC/CDCI: Authorize → FindByCpf → Limits → Search → FinancialConditions → "
        "BatchSimulate → CreatePreSales → Proposals → IntegrateProposal.",
        [overview],
    )

    print("==> Dashboard por funcionalidade (abas + Performance)", flush=True)
    tabs: list[TabBuilder] = []
    for key, label, blurb, path_fb in DOMAINS:
        tab = TabBuilder(label, domain_calls_query(key))
        fill_domain(tab, key)
        tabs.append(tab)
    perf = TabBuilder("Performance", "service:assistente*")
    fill_performance(perf)
    tabs.append(perf)
    routes_doc = " | ".join(
        f"{k}: " + " → ".join(v) for k, v in DOMAIN_ROUTES.items()
    )
    id_domain = ensure_dashboard(
        DOMAIN_TITLE,
        "Cada aba = só os endpoints daquela funcionalidade · aba Performance",
        "Inventário fixo por aba (DOMAIN_ROUTES). Não lista tráfego de outras funcionalidades. "
        "Vazão = count() no tempo (≈ TPS). Sequências: "
        + routes_doc,
        tabs,
    )

    print("==> Dashboards prontos.", flush=True)
    print(f"IDS overview={id_overview} domain={id_domain}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO: {exc}", file=sys.stderr)
        raise
