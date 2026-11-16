"""
API do Observatório - lê o Data Warehouse e devolve JSON pro front.
Docs automáticas em /api/docs (o FastAPI gera sozinho - bom pra mostrar na apresentação).
"""
import os
from contextlib import contextmanager
from datetime import date

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


def consulta(sql: str, params: tuple = ()) -> list[dict]:
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
    # canal endêmico simplificado: média da mesma semana nos anos anteriores
    semana = consulta("SELECT semana_epi FROM dim_tempo WHERE semana_inicio = %s",
                      (atual[0]["semana_inicio"],))
    media_hist = None
    if semana:
        r = consulta("""
            SELECT avg(c.casos_notificados)::float AS media
            FROM fato_casos c JOIN dim_tempo t USING (semana_inicio)
            WHERE c.id_doenca = %s AND t.semana_epi = %s AND c.semana_inicio < %s
        """, (d, semana[0]["semana_epi"], atual[0]["semana_inicio"]))
        media_hist = r[0]["media"]

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
        "media_historica_semana": media_hist,
        "previsao_4_semanas": prev,
    }


@app.get("/api/casos/semanal")
def casos_semanal(doenca: str = "dengue", desde: date = Query(date(2015, 1, 1))):
    """Curva de casos (observados) + previsão, pra o gráfico de linha."""
    d = id_doenca(doenca)
    observados = consulta("""
        SELECT semana_inicio, casos_notificados AS notificados, casos_estimados::float AS estimados
        FROM fato_casos WHERE id_doenca = %s AND semana_inicio >= %s ORDER BY semana_inicio
    """, (d, desde))
    previsao = consulta("""
        SELECT semana_inicio, casos_previstos::float AS previstos,
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
