"""
E de ETL - EXTRAÇÃO
Baixa os dados das fontes e salva o arquivo ORIGINAL, sem mexer em nada,
no Data Lake (pasta data/raw). Cada execução gera um arquivo novo com a data,
então dá pra reprocessar o histórico quando quiser.

Fontes:
  - InfoDengue (Fiocruz/FGV): casos semanais de Salvador por doença
  - Open-Meteo (histórico ERA5): chuva, temperatura e umidade diárias de Salvador
    (no documento a fonte de clima é o INMET; aqui usamos o Open-Meteo porque
     tem API pronta e gratuita, sem precisar baixar CSV na mão)
"""
from datetime import date, timedelta
from pathlib import Path
import json

import requests

GEOCODE_SALVADOR = 2927408
LAT, LON = -12.97, -38.50
DOENCAS = ["dengue", "chikungunya", "zika"]
ANO_INICIO = 2015

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
HEADERS = {"User-Agent": "observatorio-arboviroses-a3/1.0 (trabalho academico)"}


def extrair_infodengue(doenca: str, hoje: date) -> Path:
    url = "https://info.dengue.mat.br/api/alertcity"
    params = {
        "geocode": GEOCODE_SALVADOR,
        "disease": doenca,
        "format": "csv",
        "ew_start": 1,
        "ew_end": 53,
        "ey_start": ANO_INICIO,
        "ey_end": hoje.year,
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=120)
    resp.raise_for_status()

    destino = RAW_DIR / "infodengue" / doenca / f"{hoje.isoformat()}.csv"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(resp.text, encoding="utf-8")
    return destino


def extrair_clima(hoje: date) -> Path:
    # o arquivo histórico do Open-Meteo tem alguns dias de atraso
    fim = hoje - timedelta(days=7)
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "start_date": f"{ANO_INICIO}-01-01",
        "end_date": fim.isoformat(),
        "daily": "precipitation_sum,temperature_2m_mean,temperature_2m_min,relative_humidity_2m_mean",
        "timezone": "America/Bahia",
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=120)
    resp.raise_for_status()

    destino = RAW_DIR / "clima" / f"{hoje.isoformat()}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(resp.json()), encoding="utf-8")
    return destino


def extrair_tudo(hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    arquivos = {"infodengue": {}, "clima": None}
    for doenca in DOENCAS:
        arquivos["infodengue"][doenca] = extrair_infodengue(doenca, hoje)
        print(f"[extract] {doenca}: {arquivos['infodengue'][doenca]}")
    arquivos["clima"] = extrair_clima(hoje)
    print(f"[extract] clima: {arquivos['clima']}")
    return arquivos


if __name__ == "__main__":
    extrair_tudo()
