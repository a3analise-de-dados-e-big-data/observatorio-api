"""
API do Observatório - lê o Data Warehouse e devolve JSON pro front.
Docs automáticas em /api/docs (o FastAPI gera sozinho - bom pra mostrar na apresentação).
"""
import os
import statistics
from contextlib import contextmanager
from datetime import date, timedelta

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Query

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://observatorio:observatorio@localhost:5432/observatorio")
DOENCAS = {"dengue": 1, "chikungunya": 2}
NIVEIS = {1: "verde", 2: "amarelo", 3: "laranja", 4: "vermelho"}

app = FastAPI(
    title="Observatório de Arboviroses de Salvador",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@contextmanager
def banco():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def consulta(sql: str, params: tuple | dict = ()) -> list[dict]:
    with banco() as conn:
        return conn.execute(sql, params).fetchall()


def id_doenca(nome: str) -> int:
    if nome not in DOENCAS:
        raise HTTPException(404, f"Doença inválida. Use: {', '.join(DOENCAS)}")
    return DOENCAS[nome]


@app.get("/api/saude")
def saude():
    ultima = consulta("SELECT max(executado_em) AS ultima_execucao FROM etl_log WHERE etapa = 'carga'")
    return {"status": "ok", **ultima[0]}


@app.get("/api/resumo")
def resumo(doenca: str = "dengue"):
    """Números em destaque do painel."""
    d = id_doenca(doenca)
    atual = consulta("""
        SELECT semana_inicio, casos_notificados AS notificados,
               casos_estimados::float AS estimados, casos_est_min::float AS est_min,
               casos_est_max::float AS est_max, incidencia_100k::float AS incidencia, nivel_alerta
        FROM fato_casos WHERE id_doenca = %s ORDER BY semana_inicio DESC LIMIT 1
    """, (d,))
    if not atual:
        raise HTTPException(404, "Sem dados. Rode o ETL primeiro.")
    prev = consulta("""
        SELECT coalesce(sum(casos_previstos), 0)::float AS total,
               coalesce(sum(limite_inferior), 0)::float AS minimo,
               coalesce(sum(limite_superior), 0)::float AS maximo
        FROM fato_previsao WHERE id_doenca = %s
    """, (d,))[0]
    # faixa normal da semana atual, tirada do canal endêmico
    semana = consulta("SELECT semana_epi FROM dim_tempo WHERE semana_inicio = %s",
                      (atual[0]["semana_inicio"],))
    canal_semana = None
    if semana:
        canal_semana = next((c for c in canal(d) if c["semana_epi"] == semana[0]["semana_epi"]), None)

    return {
        "doenca": doenca,
        "semana_atual": atual[0]["semana_inicio"],
        # o que já foi notificado (contagem) e quanto o InfoDengue estima que a semana vai fechar,
        # porque notificação chega com semanas de atraso
        "casos_notificados_semana": atual[0]["notificados"],
        "estimativa_infodengue": {
            "casos": atual[0]["estimados"],
            "minimo": atual[0]["est_min"],
            "maximo": atual[0]["est_max"],
        },
        "incidencia_100k": atual[0]["incidencia"],
        "nivel_alerta": NIVEIS.get(atual[0]["nivel_alerta"], "indefinido"),
        # compare com estimativa_infodengue.casos, não com os notificados (que ainda vão crescer)
        "canal_endemico_semana": canal_semana,
        "previsao_4_semanas": prev,
    }


def canal(d: int) -> list[dict]:
    """Canal endêmico: para cada semana do ano, mediana e quartis dos casos ESTIMADOS
    nos anos anteriores ao atual.
    - mediana/quartis em vez de média: um ano de epidemia (2024) puxa a média pra cima
    - estimados em vez de notificados: a semana atual ainda não fechou (atraso de notificação)
    - entre q1 e q3 fica a metade "do meio" dos anos: é a faixa normal"""
    return consulta("""
        SELECT t.semana_epi,
               percentile_cont(0.25) WITHIN GROUP (ORDER BY c.casos_estimados)::float AS q1,
               percentile_cont(0.50) WITHIN GROUP (ORDER BY c.casos_estimados)::float AS mediana,
               percentile_cont(0.75) WITHIN GROUP (ORDER BY c.casos_estimados)::float AS q3
        FROM fato_casos c JOIN dim_tempo t USING (semana_inicio)
        WHERE c.id_doenca = %(d)s
          AND t.ano < (SELECT max(t2.ano) FROM fato_casos c2 JOIN dim_tempo t2 USING (semana_inicio)
                       WHERE c2.id_doenca = %(d)s)
        GROUP BY t.semana_epi ORDER BY t.semana_epi
    """, {"d": d})


@app.get("/api/canal-endemico")
def canal_endemico(doenca: str = "dengue"):
    """Faixa normal (q1 a q3) e mediana de cada semana epidemiológica, pra desenhar atrás da curva."""
    return canal(id_doenca(doenca))


@app.get("/api/casos/semanal")
def casos_semanal(doenca: str = "dengue", desde: date = Query(date(2015, 1, 1))):
    """Curva de casos (observados) + previsão, pra o gráfico de linha."""
    d = id_doenca(doenca)
    # semana_epi serve pra casar cada ponto com o canal endêmico no gráfico
    observados = consulta("""
        SELECT c.semana_inicio, t.semana_epi, c.casos_notificados AS notificados,
               c.casos_estimados::float AS estimados
        FROM fato_casos c JOIN dim_tempo t USING (semana_inicio)
        WHERE c.id_doenca = %s AND c.semana_inicio >= %s ORDER BY c.semana_inicio
    """, (d, desde))
    # semanas futuras ainda não estão na dim_tempo: calcula como o ETL
    # (a semana epidemiológica é do ano da sua quarta-feira; semana 1 = quarta entre 1 e 7/jan)
    previsao = consulta("""
        SELECT semana_inicio, (extract(doy FROM semana_inicio + 3)::int - 1) / 7 + 1 AS semana_epi,
               casos_previstos::float AS previstos,
               limite_inferior::float AS minimo, limite_superior::float AS maximo
        FROM fato_previsao WHERE id_doenca = %s ORDER BY semana_inicio
    """, (d,))
    return {"observados": observados, "previsao": previsao}


@app.get("/api/clima-vs-casos")
def clima_vs_casos(doenca: str = "dengue", desde: date = Query(date(2023, 1, 1))):
    """Chuva semanal × casos, pra mostrar o atraso entre chuva e surto."""
    d = id_doenca(doenca)
    return consulta("""
        SELECT t.semana_inicio, cl.chuva_mm::float AS chuva_mm, cl.temp_media::float AS temp_media,
               c.casos_estimados::float AS casos
        FROM dim_tempo t
        LEFT JOIN fato_clima cl USING (semana_inicio)
        LEFT JOIN fato_casos c ON c.semana_inicio = t.semana_inicio AND c.id_doenca = %s
        WHERE t.semana_inicio >= %s
        ORDER BY t.semana_inicio
    """, (d, desde))


def spearman(x: list, y: list) -> float | None:
    """Correlação de Spearman: a de Pearson calculada sobre as POSIÇÕES (ranks) dos valores.
    Não exige relação linear e um ano de epidemia não domina o resultado."""
    try:
        return round(statistics.correlation(x, y, method="ranked"), 3)
    except statistics.StatisticsError:  # menos de 2 pares ou série constante
        return None


@app.get("/api/correlacao-defasagem")
def correlacao_defasagem(doenca: str = "dengue"):
    """Análise DIAGNÓSTICA: correlação entre os casos de uma semana e a chuva/temperatura
    de 0 a 12 semanas ANTES. Se a chuva cria criadouros e o mosquito leva semanas pra virar
    surto, a correlação cresce com a defasagem. Atenção: correlação não prova causa
    (chuva e casos também sobem juntos só por serem da mesma época do ano)."""
    d = id_doenca(doenca)
    casos = consulta("SELECT semana_inicio, casos_estimados::float AS casos FROM fato_casos "
                     "WHERE id_doenca = %s AND casos_estimados IS NOT NULL", (d,))
    clima = {r["semana_inicio"]: r for r in consulta(
        "SELECT semana_inicio, chuva_mm::float AS chuva, temp_media::float AS temp FROM fato_clima")}

    resultado = []
    for defasagem in range(13):
        # casos da semana s  x  clima da semana s - defasagem
        pares = [(c["casos"], clima.get(c["semana_inicio"] - timedelta(weeks=defasagem))) for c in casos]
        chuva = [(n, cl["chuva"]) for n, cl in pares if cl and cl["chuva"] is not None]
        temp = [(n, cl["temp"]) for n, cl in pares if cl and cl["temp"] is not None]
        resultado.append({
            "defasagem_semanas": defasagem,
            "chuva": spearman(*zip(*chuva)) if chuva else None,
            "temperatura": spearman(*zip(*temp)) if temp else None,
            "semanas_usadas": len(chuva),
        })
    return resultado


@app.get("/api/anual")
def anual(doenca: str = "dengue"):
    """Total de casos notificados por ano (pra contextualizar: 'em 2024 foi recorde').
    O ano em andamento vem com parcial = true e a data da última semana com dado."""
    d = id_doenca(doenca)
    anos = consulta("""
        SELECT t.ano, sum(c.casos_notificados)::int AS casos, count(*)::int AS semanas,
               max(c.semana_inicio) AS ultima_semana
        FROM fato_casos c JOIN dim_tempo t USING (semana_inicio)
        WHERE c.id_doenca = %s GROUP BY t.ano ORDER BY t.ano
    """, (d,))
    for a in anos:
        a["parcial"] = a["ano"] >= date.today().year
    return anos
