"""
L de ETL - CARGA
Grava no Data Warehouse (Postgres) o que passou pela transformação.
Usa "upsert": se a semana já existe, atualiza; se não, insere.
Assim dá pra rodar o ETL toda semana sem duplicar nada.
"""
import os

import pandas as pd
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://observatorio:observatorio@localhost:5432/observatorio")


def _linhas(df: pd.DataFrame, colunas: list[str]) -> list[tuple]:
    out = df[colunas].astype(object).where(df[colunas].notna(), None)
    return [tuple(r) for r in out.itertuples(index=False, name=None)]


def _upsert(cur, tabela: str, df: pd.DataFrame, colunas: list[str], chave: list[str]):
    cols = ", ".join(colunas)
    marcadores = ", ".join(["%s"] * len(colunas))
    atualizar = ", ".join(f"{c} = EXCLUDED.{c}" for c in colunas if c not in chave)
    sql = (f"INSERT INTO {tabela} ({cols}) VALUES ({marcadores}) "
           f"ON CONFLICT ({', '.join(chave)}) DO UPDATE SET {atualizar}")
    cur.executemany(sql, _linhas(df, colunas))


def registrar_log(cur, etapa: str, status: str, mensagem: str):
    cur.execute("INSERT INTO etl_log (etapa, status, mensagem) VALUES (%s, %s, %s)",
                (etapa, status, mensagem))


def carregar_tudo(dados: dict):
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        # dimensão primeiro (as fatos dependem dela)
        _upsert(cur, "dim_tempo", dados["dim_tempo"],
                ["semana_inicio", "ano", "semana_epi", "mes", "estacao_chuvosa"],
                ["semana_inicio"])
        _upsert(cur, "fato_casos", dados["fato_casos"],
                ["semana_inicio", "id_doenca", "casos_notificados", "casos_estimados",
                 "casos_est_min", "casos_est_max", "incidencia_100k", "nivel_alerta"],
                ["semana_inicio", "id_doenca"])
        _upsert(cur, "fato_clima", dados["fato_clima"],
                ["semana_inicio", "chuva_mm", "temp_media", "temp_min", "umidade_media"],
                ["semana_inicio"])
        cur.execute("SELECT to_regclass('dim_municipio') IS NOT NULL "
                    "AND to_regclass('fato_casos_municipio') IS NOT NULL")
        tem_rms = cur.fetchone()[0]
        if tem_rms:
            _upsert(cur, "dim_municipio", dados["dim_municipio"], ["geocode", "nome"], ["geocode"])
            _upsert(cur, "fato_casos_municipio", dados["fato_casos_municipio"],
                    ["semana_inicio", "id_doenca", "geocode", "casos_notificados", "casos_estimados",
                     "incidencia_100k", "nivel_alerta"],
                    ["semana_inicio", "id_doenca", "geocode"])
        else:
            print("[load] tabelas da RMS não existem: aplique db/migracoes/001_rms.sql")
            registrar_log(cur, "carga", "aviso",
                          "tabelas da RMS não existem, mapa não atualizado: aplique db/migracoes/001_rms.sql")
        for etapa, status, msg in dados["log"]:
            registrar_log(cur, etapa, status, msg)
        rms = f"{len(dados['fato_casos_municipio'])} da RMS" if tem_rms else "RMS pulada"
        registrar_log(cur, "carga", "ok",
                      f"{len(dados['fato_casos'])} linhas de casos, {len(dados['fato_clima'])} de clima, {rms}")
    print("[load] Warehouse atualizado")
