"""
T de ETL - TRANSFORMAÇÃO
Lê os arquivos brutos do Data Lake, limpa, padroniza e agrega por semana.
Também roda o "portão de qualidade": se os dados vierem quebrados, o lote
é barrado e não vai pro Warehouse.

Sobre LGPD: os dados do InfoDengue já chegam agregados por município e semana,
sem nome, CPF, endereço ou idade de ninguém. Mesmo assim, só mantemos as
colunas necessárias (princípio da necessidade, art. 6º, III).
"""
from datetime import date
from pathlib import Path
import json

import numpy as np
import pandas as pd

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
ID_DOENCA = {"dengue": 1, "chikungunya": 2}


class FalhaDeQualidade(Exception):
    """Lote barrado no portão de qualidade."""


def _inicio_semana_epi(d: pd.Series) -> pd.Series:
    # semana epidemiológica começa no domingo
    return (d - pd.to_timedelta((d.dt.weekday + 1) % 7, unit="D")).dt.normalize()


def transformar_casos(caminho: Path, doenca: str) -> tuple[pd.DataFrame, float]:
    """Devolve a tabela limpa e a fração de linhas SEM DATA no arquivo original
    (medida antes da limpeza, porque o filtro de datas futuras descarta essas linhas)."""
    df = pd.read_csv(caminho)
    nulos_data = float(df["data_iniSE"].isna().mean()) if len(df) else 0.0

    # 1. Data de início da semana (vem como texto ou como milissegundos, depende da versão)
    if pd.api.types.is_numeric_dtype(df["data_iniSE"]):
        df["semana_inicio"] = pd.to_datetime(df["data_iniSE"], unit="ms")
    else:
        df["semana_inicio"] = pd.to_datetime(df["data_iniSE"])
    df["semana_inicio"] = df["semana_inicio"].dt.normalize()

    # 2. Ano e número da semana epidemiológica (SE = 202401 -> 2024, semana 1)
    df["ano"] = (df["SE"] // 100).astype(int)
    df["semana_epi"] = (df["SE"] % 100).astype(int)

    # 3. Só as colunas que o projeto usa (necessidade - LGPD)
    df = df.rename(columns={
        "casos": "casos_notificados",
        "casos_est": "casos_estimados",
        "p_inc100k": "incidencia_100k",
        "nivel": "nivel_alerta",
    })
    df = df[["semana_inicio", "ano", "semana_epi", "casos_notificados",
             "casos_estimados", "casos_est_min", "casos_est_max",
             "incidencia_100k", "nivel_alerta"]]

    # 4. Limpeza
    df = df.drop_duplicates(subset="semana_inicio", keep="last")          # unicidade
    df["casos_notificados"] = df["casos_notificados"].fillna(0).clip(lower=0).astype(int)
    df["casos_estimados"] = df["casos_estimados"].fillna(df["casos_notificados"]).clip(lower=0)
    for col in ["casos_est_min", "casos_est_max"]:
        df[col] = df[col].fillna(df["casos_estimados"]).clip(lower=0)
    df = df[df["semana_inicio"] <= pd.Timestamp(date.today())]          # sem datas futuras

    df["id_doenca"] = ID_DOENCA[doenca]
    return df.sort_values("semana_inicio").reset_index(drop=True), nulos_data


def transformar_clima(caminho: Path) -> pd.DataFrame:
    bruto = json.loads(caminho.read_text(encoding="utf-8"))["daily"]
    df = pd.DataFrame(bruto)
    df["data"] = pd.to_datetime(df["time"])
    df["semana_inicio"] = _inicio_semana_epi(df["data"])

    agg = {
        "precipitation_sum": "sum",
        "temperature_2m_mean": "mean",
        "temperature_2m_min": "min",
    }
    if "relative_humidity_2m_mean" in df:
        agg["relative_humidity_2m_mean"] = "mean"

    semanal = df.groupby("semana_inicio").agg(agg).reset_index()
    # descarta semanas incompletas (menos de 7 dias de dado)
    dias = df.groupby("semana_inicio").size().reset_index(name="dias")
    semanal = semanal.merge(dias, on="semana_inicio")
    semanal = semanal[semanal["dias"] == 7].drop(columns="dias")

    semanal = semanal.rename(columns={
        "precipitation_sum": "chuva_mm",
        "temperature_2m_mean": "temp_media",
        "temperature_2m_min": "temp_min",
        "relative_humidity_2m_mean": "umidade_media",
    })
    if "umidade_media" not in semanal:
        semanal["umidade_media"] = np.nan
    return semanal.round({"chuva_mm": 1, "temp_media": 1, "temp_min": 1, "umidade_media": 1})


def semanas_com_explosao(df: pd.DataFrame) -> pd.Series:
    """Semanas com mais de 5x a média das 4 anteriores (e pelo menos 50 casos).
    Na epidemia de 2024 a maior alta de uma semana pra outra foi ~3x, então isso
    costuma ser erro de digitação ou lote de notificações atrasadas."""
    media_4_antes = df["casos_notificados"].shift(1).rolling(4).mean()
    explosao = (df["casos_notificados"] > 5 * media_4_antes) & (df["casos_notificados"] >= 50)
    return df.loc[explosao, "semana_inicio"]


def portao_de_qualidade(casos: dict[str, pd.DataFrame], clima: pd.DataFrame,
                        nulos_data: dict[str, float]) -> list[tuple]:
    """Regras simples. Erro = barra o lote. Aviso = segue, mas registra."""
    log = []
    for doenca, df in casos.items():
        nulos = nulos_data[doenca]
        if nulos > 0.05:                                            # completude
            raise FalhaDeQualidade(f"{doenca}: {nulos:.0%} das linhas sem data")
        if len(df) < 52:
            raise FalhaDeQualidade(f"{doenca}: só {len(df)} semanas (esperado 52+)")
        explosoes = semanas_com_explosao(df)                        # consistência (só avisa)
        if len(explosoes):
            datas = ", ".join(str(d.date()) for d in explosoes)
            log.append(("qualidade", "aviso", f"{doenca}: salto de casos suspeito em {datas} (mantido)"))
        ultima = df["semana_inicio"].max()
        atraso = (pd.Timestamp(date.today()) - ultima).days
        if atraso > 21:                                             # atualidade
            log.append(("qualidade", "aviso", f"{doenca}: última semana é {ultima.date()} ({atraso} dias atrás)"))
        log.append(("qualidade", "ok", f"{doenca}: {len(df)} semanas válidas"))

    if clima["chuva_mm"].lt(0).any():                               # validade
        raise FalhaDeQualidade("clima: chuva negativa")
    log.append(("qualidade", "ok", f"clima: {len(clima)} semanas válidas"))
    return log


def montar_dim_tempo(casos: dict[str, pd.DataFrame], clima: pd.DataFrame) -> pd.DataFrame:
    partes = [df[["semana_inicio", "ano", "semana_epi"]] for df in casos.values()]
    tempo = pd.concat(partes).drop_duplicates("semana_inicio")

    # semanas que só existem no clima: calcula ano/semana epidemiológica pela quarta-feira
    # (a semana é do ano que tem 4+ dos seus 7 dias, ou seja, o ano da quarta;
    #  semana 1 = quarta entre 1 e 7 de janeiro). Bate com o SE do InfoDengue.
    so_clima = clima.loc[~clima["semana_inicio"].isin(tempo["semana_inicio"]), ["semana_inicio"]].copy()
    if len(so_clima):
        quarta = so_clima["semana_inicio"] + pd.Timedelta(days=3)
        so_clima["ano"] = quarta.dt.year
        so_clima["semana_epi"] = (quarta.dt.dayofyear - 1) // 7 + 1
        tempo = pd.concat([tempo, so_clima])

    tempo["mes"] = (tempo["semana_inicio"] + pd.Timedelta(days=3)).dt.month
    tempo["estacao_chuvosa"] = tempo["mes"].between(4, 7)
    return tempo.sort_values("semana_inicio").reset_index(drop=True)


def transformar_tudo(arquivos: dict) -> dict:
    casos, nulos_data = {}, {}
    for d, p in arquivos["infodengue"].items():
        casos[d], nulos_data[d] = transformar_casos(p, d)
    clima = transformar_clima(arquivos["clima"])
    log = portao_de_qualidade(casos, clima, nulos_data)
    tempo = montar_dim_tempo(casos, clima)

    # camada "prata": dado limpo salvo no lake também (bom pra auditoria)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    fato_casos = pd.concat(casos.values(), ignore_index=True)
    fato_casos.to_csv(PROCESSED_DIR / "fato_casos.csv", index=False)
    clima.to_csv(PROCESSED_DIR / "fato_clima.csv", index=False)
    tempo.to_csv(PROCESSED_DIR / "dim_tempo.csv", index=False)

    print(f"[transform] {len(fato_casos)} linhas de casos, {len(clima)} semanas de clima")
    return {"fato_casos": fato_casos, "fato_clima": clima, "dim_tempo": tempo, "log": log}
