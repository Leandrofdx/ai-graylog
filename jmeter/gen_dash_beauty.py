#!/usr/bin/env python3
"""Gera data/mix.csv + dash-beauty.jmx (massa variada p/ dashboards Graylog)."""
from __future__ import annotations

import csv
import random
import xml.sax.saxutils as xu
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT_JMX = ROOT / "dash-beauty.jmx"
OUT_MIX = DATA / "mix.csv"

STAFF = [("vendedor1", "lab123"), ("vendedor2", "lab123")]
CPFS = ["52998224725", "39053344705"]
PRODUCTS = [
    ("SKU-7", 4599.90),
    ("SKU-42", 129.90),
    ("SKU-99", 1899.00),
    ("SKU-15", 499.00),
    ("SKU-88", 2499.00),
]
SEARCHES = ["Mouse", "Note", "Monitor", "Teclado", "Smart", "Pro", "Wireless", "27", "xyz_sem_hit", ""]
PAYMENTS = [
    ("avista", "", "", 4),
    ("cdc", "2006", "6", 2),
    ("cdc", "2006", "12", 5),
    ("cdc", "2006", "18", 2),
    ("cdc", "2006", "24", 1),
    ("cdci", "2011", "12", 2),
    ("cdci", "2011", "20", 4),
    ("cdci", "2011", "24", 2),
    ("cdci", "2011", "36", 1),
]


def esc(s: str) -> str:
    return xu.escape(s, {"'": "&apos;", '"': "&quot;"})


def weighted_payment(rng: random.Random) -> tuple[str, str, str]:
    bag: list[tuple[str, str, str]] = []
    for pt, tender, inst, w in PAYMENTS:
        bag.extend([(pt, tender, inst)] * w)
    return rng.choice(bag)


def write_csvs(n: int = 80, seed: int = 42) -> None:
    rng = random.Random(seed)
    DATA.mkdir(parents=True, exist_ok=True)

    rows = []
    for i in range(n):
        staff, pwd = rng.choice(STAFF)
        cpf = rng.choice(CPFS)
        item, price = rng.choice(PRODUCTS)
        qty = rng.choice([1, 1, 1, 2])
        amount = round(price * qty, 2)
        pay, tender, inst = weighted_payment(rng)
        if pay == "cdci" and cpf == "39053344705":
            pay, tender, inst = "cdc", "2006", "12"
        rows.append(
            {
                "row": i + 1,
                "staffId": staff,
                "password": pwd,
                "cpf": cpf,
                "itemId": item,
                "qty": qty,
                "amount": f"{amount:.2f}",
                "paymentType": pay,
                "tenderTypeId": tender or "0",
                "installments": inst or "0",
                "searchQ": rng.choice(SEARCHES),
                "productType": "CDC" if pay == "avista" else pay.upper(),
                "financed": "1" if pay != "avista" else "0",
            }
        )
    with OUT_MIX.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    with (DATA / "products.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["itemId"])
        for item, _ in PRODUCTS:
            w.writerow([item])

    with (DATA / "searches.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["q"])
        for q in SEARCHES:
            w.writerow([q])

    print(f"• {OUT_MIX} ({len(rows)} linhas)")
    print(f"• {DATA / 'products.csv'}")
    print(f"• {DATA / 'searches.csv'}")


def sampler(name: str, method: str, path: str, body: str | None = None, journey: str = "") -> str:
    path_e = esc(path)
    if body is not None:
        raw = esc(body)
        args = f"""          <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments">
              <elementProp name="" elementType="HTTPArgument">
                <boolProp name="HTTPArgument.always_encode">false</boolProp>
                <stringProp name="Argument.value">{raw}</stringProp>
                <stringProp name="Argument.metadata">=</stringProp>
              </elementProp>
            </collectionProp>
          </elementProp>"""
    else:
        args = """          <boolProp name="HTTPSampler.postBodyRaw">false</boolProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="args">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>"""

    if journey:
        tree = f"""        <hashTree>
          <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="j" enabled="true">
            <collectionProp name="HeaderManager.headers">
              <elementProp name="" elementType="Header">
                <stringProp name="Header.name">X-Journey-Step</stringProp>
                <stringProp name="Header.value">{esc(journey)}</stringProp>
              </elementProp>
            </collectionProp>
          </HeaderManager>
          <hashTree/>
        </hashTree>"""
    else:
        tree = "        <hashTree/>"

    return f"""        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{esc(name)}" enabled="true">
          <stringProp name="HTTPSampler.path">{path_e}</stringProp>
          <stringProp name="HTTPSampler.method">{method}</stringProp>
{args}
        </HTTPSamplerProxy>
{tree}
"""


def http_defaults() -> str:
    return """        <ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Defaults">
          <stringProp name="HTTPSampler.domain">${HOST}</stringProp>
          <stringProp name="HTTPSampler.port">${PORT}</stringProp>
          <stringProp name="HTTPSampler.protocol">${PROTOCOL}</stringProp>
          <stringProp name="HTTPSampler.implementation">HttpClient4</stringProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="args">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>
        </ConfigTestElement>
        <hashTree/>
"""


def headers(test_run_suffix: str = "", staff_var: str = "${staffId}") -> str:
    tr = "${TEST_RUN_ID}" + (test_run_suffix if test_run_suffix else "")
    return f"""        <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Headers">
          <collectionProp name="HeaderManager.headers">
            <elementProp name="" elementType="Header">
              <stringProp name="Header.name">Content-Type</stringProp>
              <stringProp name="Header.value">application/json</stringProp>
            </elementProp>
            <elementProp name="" elementType="Header">
              <stringProp name="Header.name">X-Test-Run-Id</stringProp>
              <stringProp name="Header.value">{esc(tr)}</stringProp>
            </elementProp>
            <elementProp name="" elementType="Header">
              <stringProp name="Header.name">X-Request-Id</stringProp>
              <stringProp name="Header.value">${{__UUID}}</stringProp>
            </elementProp>
            <elementProp name="" elementType="Header">
              <stringProp name="Header.name">X-Staff-Id</stringProp>
              <stringProp name="Header.value">{esc(staff_var)}</stringProp>
            </elementProp>
          </collectionProp>
        </HeaderManager>
        <hashTree/>
"""


def think(lo: int, hi: int) -> str:
    return f"""        <UniformRandomTimer guiclass="UniformRandomTimerGui" testclass="UniformRandomTimer" testname="think {lo}-{hi}ms">
          <stringProp name="ConstantTimer.delay">{lo}</stringProp>
          <stringProp name="RandomTimer.range">{hi - lo}</stringProp>
        </UniformRandomTimer>
        <hashTree/>
"""


def write_jmx() -> None:
    # bodies with ${vars} — not f-string interpolated
    auth_body = '{\n  "staffId": "${staffId}",\n  "password": "${password}"\n}'
    fin_body = '{\n  "productType": "${paymentType}"\n}'
    sim_body = '{\n  "productType": "${paymentType}",\n  "amount": ${amount},\n  "minInstallments": ${installments}\n}'
    sale_fin = (
        '{\n  "staffId": "${staffId}",\n  "cpf": "${cpf}",\n  "itemId": "${itemId}",\n'
        '  "qty": ${qty},\n  "paymentType": "${paymentType}",\n'
        '  "tenderTypeId": ${tenderTypeId},\n  "installments": ${installments}\n}'
    )
    sale_avi = (
        '{\n  "staffId": "${staffId}",\n  "cpf": "${cpf}",\n  "itemId": "${itemId}",\n'
        '  "qty": ${qty},\n  "paymentType": "avista"\n}'
    )
    integ = '{\n  "proposalId": "${proposalId}"\n}'
    auth_cp = '{\n  "staffId": "vendedor1",\n  "password": "lab123"\n}'
    cp_create = (
        '{\n  "staffId": "vendedor1",\n  "cpf": "${cpf}",\n'
        '  "amount": ${__Random(200,900)},\n  "installments": ${__Random(6,24)}\n}'
    )
    bad_login = '{\n  "staffId": "vendedor1",\n  "password": "errada"\n}'

    parts: list[str] = []
    parts.append(
        """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Dash Beauty · massa variada">
      <stringProp name="TestPlan.comments">CSV mix (SKU/CPF/staff/meios/buscas) + browse + CP + ruido. Props: -Jthreads=6 -Jloops=12 -Jramp=10</stringProp>
      <boolProp name="TestPlan.tearDown_on_shutdown">true</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="UDV">
        <collectionProp name="Arguments.arguments">
          <elementProp name="HOST" elementType="Argument">
            <stringProp name="Argument.name">HOST</stringProp>
            <stringProp name="Argument.value">127.0.0.1</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
          <elementProp name="PORT" elementType="Argument">
            <stringProp name="Argument.name">PORT</stringProp>
            <stringProp name="Argument.value">8080</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
          <elementProp name="PROTOCOL" elementType="Argument">
            <stringProp name="Argument.name">PROTOCOL</stringProp>
            <stringProp name="Argument.value">http</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
          <elementProp name="TEST_RUN_ID" elementType="Argument">
            <stringProp name="Argument.name">TEST_RUN_ID</stringProp>
            <stringProp name="Argument.value">dash-${__time(yyyyMMdd-HHmmss)}</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
          <elementProp name="storeId" elementType="Argument">
            <stringProp name="Argument.name">storeId</stringProp>
            <stringProp name="Argument.value">1001</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
        </collectionProp>
      </elementProp>
    </TestPlan>
    <hashTree>
"""
    )

    # TG1 Mix
    parts.append(
        """      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="01 · Mix vendas (CSV)">
        <stringProp name="ThreadGroup.num_threads">${__P(threads,6)}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">${__P(ramp,10)}</stringProp>
        <boolProp name="ThreadGroup.same_user_on_next_iteration">false</boolProp>
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop">
          <stringProp name="LoopController.loops">${__P(loops,12)}</stringProp>
          <boolProp name="LoopController.continue_forever">false</boolProp>
        </elementProp>
      </ThreadGroup>
      <hashTree>
"""
    )
    parts.append(http_defaults())
    parts.append(headers())
    parts.append(
        """        <CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="CSV mix">
          <stringProp name="filename">data/mix.csv</stringProp>
          <stringProp name="fileEncoding">UTF-8</stringProp>
          <stringProp name="variableNames">row,staffId,password,cpf,itemId,qty,amount,paymentType,tenderTypeId,installments,searchQ,productType,financed</stringProp>
          <boolProp name="ignoreFirstLine">true</boolProp>
          <stringProp name="delimiter">,</stringProp>
          <boolProp name="quotedData">false</boolProp>
          <boolProp name="recycle">true</boolProp>
          <boolProp name="stopThread">false</boolProp>
          <stringProp name="shareMode">shareMode.all</stringProp>
        </CSVDataSet>
        <hashTree/>
"""
    )
    parts.append(think(80, 350))
    parts.append(sampler("Authorize", "POST", "/UserAuthentication/api/Authorize", auth_body, "sale.login"))
    parts.append(sampler("Products Search", "GET", "/Products/api/Products/Search?q=${searchQ}", None, "sale.search_product"))
    parts.append(sampler("Stock Find", "GET", "/Stock/api/Stock/Find?itemId=${itemId}", None, "sale.check_stock"))
    parts.append(sampler("Customer Find", "GET", "/Customer/api/Customer/FindByCpfCnpj?cpf=${cpf}", None, "sale.validate_customer"))
    parts.append(
        sampler(
            "CustomerLimits",
            "GET",
            "/Customer/api/CustomerLimits?cpf=${cpf}&amp;productType=${productType}",
            None,
            "sale.check_credit",
        )
    )
    parts.append(
        """        <IfController guiclass="IfControllerPanel" testclass="IfController" testname="Se financiado">
          <stringProp name="IfController.condition">${__jexl3("${financed}" == "1")}</stringProp>
          <boolProp name="IfController.evaluateAll">false</boolProp>
          <boolProp name="IfController.useExpression">true</boolProp>
        </IfController>
        <hashTree>
"""
    )
    parts.append(
        sampler(
            "FinancialConditions",
            "POST",
            "/PaymentCondition/api/FinancialConditions/Find/${storeId}",
            fin_body,
            "sale.list_plans",
        )
    )
    parts.append(
        sampler(
            "PaymentCondition Find",
            "GET",
            "/PaymentCondition/api/PaymentCondition/Find/${storeId}",
            None,
            "sale.list_plans",
        )
    )
    parts.append(
        sampler(
            "BatchSimulate",
            "POST",
            "/InstallmentSimulator/api/InstallmentSimulator/BatchSimulate",
            sim_body,
            "sale.simulate_plan",
        )
    )
    # CreatePreSales + extract (mesmo hashTree)
    sale_fin_esc = esc(sale_fin)
    parts.append(
        f"""        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="CreatePreSales financiado" enabled="true">
          <stringProp name="HTTPSampler.path">/SalesOrder/api/CreatePreSales</stringProp>
          <stringProp name="HTTPSampler.method">POST</stringProp>
          <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments">
              <elementProp name="" elementType="HTTPArgument">
                <boolProp name="HTTPArgument.always_encode">false</boolProp>
                <stringProp name="Argument.value">{sale_fin_esc}</stringProp>
                <stringProp name="Argument.metadata">=</stringProp>
              </elementProp>
            </collectionProp>
          </elementProp>
        </HTTPSamplerProxy>
        <hashTree>
          <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="j" enabled="true">
            <collectionProp name="HeaderManager.headers">
              <elementProp name="" elementType="Header">
                <stringProp name="Header.name">X-Journey-Step</stringProp>
                <stringProp name="Header.value">sale.create_presale</stringProp>
              </elementProp>
            </collectionProp>
          </HeaderManager>
          <hashTree/>
          <JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" testname="Extract proposalId">
            <stringProp name="JSONPostProcessor.referenceNames">proposalId</stringProp>
            <stringProp name="JSONPostProcessor.jsonPathExprs">$.proposalId</stringProp>
            <stringProp name="JSONPostProcessor.match_numbers">1</stringProp>
            <stringProp name="JSONPostProcessor.defaultValues"></stringProp>
          </JSONPostProcessor>
          <hashTree/>
          <IfController guiclass="IfControllerPanel" testclass="IfController" testname="Integrar se proposalId">
            <stringProp name="IfController.condition">${{__jexl3("${{proposalId}}" != "")}}</stringProp>
            <boolProp name="IfController.evaluateAll">false</boolProp>
            <boolProp name="IfController.useExpression">true</boolProp>
          </IfController>
          <hashTree>
"""
    )
    parts.append(sampler("IntegrateProposal", "POST", "/MultiFinancial/api/IntegrateProposal", integ, "sale.integrate_proposal"))
    parts.append(sampler("List Proposals", "GET", "/MultiFinancial/api/Proposals", None, "sale.list_proposals"))
    parts.append("          </hashTree>\n        </hashTree>\n        </hashTree>\n")

    parts.append(
        """        <IfController guiclass="IfControllerPanel" testclass="IfController" testname="Se a vista">
          <stringProp name="IfController.condition">${__jexl3("${financed}" == "0")}</stringProp>
          <boolProp name="IfController.evaluateAll">false</boolProp>
          <boolProp name="IfController.useExpression">true</boolProp>
        </IfController>
        <hashTree>
"""
    )
    parts.append(sampler("CreatePreSales a vista", "POST", "/SalesOrder/api/CreatePreSales", sale_avi, "sale.create_presale"))
    parts.append("        </hashTree>\n      </hashTree>\n")

    # TG2 Browse
    parts.append(
        """      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="02 · Catalogo + Estoque">
        <stringProp name="ThreadGroup.num_threads">${__P(browseThreads,4)}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">8</stringProp>
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop">
          <stringProp name="LoopController.loops">${__P(browseLoops,25)}</stringProp>
          <boolProp name="LoopController.continue_forever">false</boolProp>
        </elementProp>
      </ThreadGroup>
      <hashTree>
"""
    )
    parts.append(http_defaults())
    parts.append(headers("-browse", "vendedor${__Random(1,2)}"))
    parts.append(
        """        <CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="CSV searches">
          <stringProp name="filename">data/searches.csv</stringProp>
          <stringProp name="fileEncoding">UTF-8</stringProp>
          <stringProp name="variableNames">q</stringProp>
          <boolProp name="ignoreFirstLine">true</boolProp>
          <stringProp name="delimiter">,</stringProp>
          <boolProp name="recycle">true</boolProp>
          <boolProp name="stopThread">false</boolProp>
          <stringProp name="shareMode">shareMode.all</stringProp>
        </CSVDataSet>
        <hashTree/>
        <CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="CSV products">
          <stringProp name="filename">data/products.csv</stringProp>
          <stringProp name="fileEncoding">UTF-8</stringProp>
          <stringProp name="variableNames">browseItem</stringProp>
          <boolProp name="ignoreFirstLine">true</boolProp>
          <stringProp name="delimiter">,</stringProp>
          <boolProp name="recycle">true</boolProp>
          <boolProp name="stopThread">false</boolProp>
          <stringProp name="shareMode">shareMode.all</stringProp>
        </CSVDataSet>
        <hashTree/>
"""
    )
    parts.append(think(50, 200))
    parts.append(sampler("Browse Search", "GET", "/Products/api/Products/Search?q=${q}", None, "sale.search_product"))
    parts.append(sampler("Browse Stock", "GET", "/Stock/api/Stock/Find?itemId=${browseItem}", None, "sale.check_stock"))
    parts.append("      </hashTree>\n")

    # TG3 CP
    parts.append(
        """      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="03 · Credito Pessoal">
        <stringProp name="ThreadGroup.num_threads">${__P(cpThreads,2)}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">5</stringProp>
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop">
          <stringProp name="LoopController.loops">${__P(cpLoops,10)}</stringProp>
          <boolProp name="LoopController.continue_forever">false</boolProp>
        </elementProp>
      </ThreadGroup>
      <hashTree>
"""
    )
    parts.append(http_defaults())
    parts.append(headers("-cp", "vendedor1"))
    parts.append(
        """        <CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="CSV mix CP">
          <stringProp name="filename">data/mix.csv</stringProp>
          <stringProp name="fileEncoding">UTF-8</stringProp>
          <stringProp name="variableNames">row,staffId,password,cpf,itemId,qty,amount,paymentType,tenderTypeId,installments,searchQ,productType,financed</stringProp>
          <boolProp name="ignoreFirstLine">true</boolProp>
          <stringProp name="delimiter">,</stringProp>
          <boolProp name="recycle">true</boolProp>
          <boolProp name="stopThread">false</boolProp>
          <stringProp name="shareMode">shareMode.all</stringProp>
        </CSVDataSet>
        <hashTree/>
"""
    )
    parts.append(think(100, 400))
    parts.append(sampler("Authorize CP", "POST", "/UserAuthentication/api/Authorize", auth_cp, "sale.login"))
    parts.append(sampler("CP Create", "POST", "/PersonalCredit/api/Create", cp_create, "sale.create_cp"))
    parts.append(sampler("CP Proposals", "GET", "/PersonalCredit/api/Proposals", None, "sale.list_proposals"))
    parts.append("      </hashTree>\n")

    # TG4 Noise
    parts.append(
        """      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="04 · Ruido (erros)">
        <stringProp name="ThreadGroup.num_threads">1</stringProp>
        <stringProp name="ThreadGroup.ramp_time">2</stringProp>
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop">
          <stringProp name="LoopController.loops">${__P(noiseLoops,12)}</stringProp>
          <boolProp name="LoopController.continue_forever">false</boolProp>
        </elementProp>
      </ThreadGroup>
      <hashTree>
"""
    )
    parts.append(http_defaults())
    parts.append(headers("-noise", "vendedor1"))
    parts.append(think(50, 150))
    parts.append(sampler("Customer 404", "GET", "/Customer/api/Customer/FindByCpfCnpj?cpf=00000000000", None, "sale.validate_customer"))
    parts.append(sampler("Stock 404", "GET", "/Stock/api/Stock/Find?itemId=SKU-NOPE", None, "sale.check_stock"))
    parts.append(sampler("Bad login", "POST", "/UserAuthentication/api/Authorize", bad_login, "sale.login"))
    parts.append(sampler("Search no hit", "GET", "/Products/api/Products/Search?q=zzzz_no_hit", None, "sale.search_product"))
    parts.append("      </hashTree>\n")

    parts.append(
        """      <ResultCollector guiclass="SummaryReport" testclass="ResultCollector" testname="Summary Report" enabled="true">
        <boolProp name="ResultCollector.error_logging">false</boolProp>
        <objProp>
          <name>saveConfig</name>
          <value class="SampleSaveConfiguration">
            <time>true</time>
            <latency>true</latency>
            <timestamp>true</timestamp>
            <success>true</success>
            <label>true</label>
            <code>true</code>
            <message>true</message>
            <threadName>true</threadName>
            <dataType>true</dataType>
            <encoding>false</encoding>
            <assertions>true</assertions>
            <subresults>true</subresults>
            <responseData>false</responseData>
            <samplerData>false</samplerData>
            <xml>false</xml>
            <fieldNames>true</fieldNames>
            <responseHeaders>false</responseHeaders>
            <requestHeaders>false</requestHeaders>
            <responseDataOnError>false</responseDataOnError>
            <saveAssertionResultsFailureMessage>true</saveAssertionResultsFailureMessage>
            <assertionsResultsToSave>0</assertionsResultsToSave>
            <bytes>true</bytes>
            <sentBytes>true</sentBytes>
            <url>true</url>
            <threadCounts>true</threadCounts>
            <idleTime>true</idleTime>
            <connectTime>true</connectTime>
          </value>
        </objProp>
        <stringProp name="filename"></stringProp>
      </ResultCollector>
      <hashTree/>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
"""
    )

    OUT_JMX.write_text("".join(parts), encoding="utf-8")
    print(f"• {OUT_JMX}")


def main() -> None:
    write_csvs(80)
    write_jmx()


if __name__ == "__main__":
    main()
