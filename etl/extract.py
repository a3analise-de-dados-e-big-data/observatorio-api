"""
E de ETL - EXTRAÇÃO
Baixa os dados das fontes e salva o arquivo ORIGINAL, sem mexer em nada,
no Data Lake (pasta data/raw). Cada execução gera um arquivo novo com a data,
então dá pra reprocessar o histórico quando quiser.

Fontes:
  - InfoDengue (Fiocruz/FGV): casos semanais de Salvador por doença
    e dos outros 12 municípios da Região Metropolitana (pro mapa da RMS)
  - Open-Meteo (histórico ERA5): chuva, temperatura e umidade diárias de Salvador
    (no documento a fonte de clima é o INMET; aqui usamos o Open-Meteo porque
     tem API pronta e gratuita, sem precisar baixar CSV na mão)
"""
from datetime import date, timedelta
from pathlib import Path
import json

import requests

GEOCODE_SALVADOR = 2927408
# Região Metropolitana de Salvador (RM 04201), códigos de município do IBGE.
# Fonte: https://servicodados.ibge.gov.br/api/v1/localidades/estados/29/regioes-metropolitanas
MUNICIPIOS_RMS = {
    2905701: "Camaçari",
    2906501: "Candeias",
    2910057: "Dias d'Ávila",
    2916104: "Itaparica",
    2919207: "Lauro de Freitas",
    2919926: "Madre de Deus",
    2921005: "Mata de São João",
    2925204: "Pojuca",
    2927408: "Salvador",
    2929206: "São Francisco do Conde",
    2929503: "São Sebastião do Passé",
    2930709: "Simões Filho",
    2933208: "Vera Cruz",
}
LAT, LON = -12.97, -38.50
# zika ficou de fora: o InfoDengue parou de publicar a série de Salvador em fev/2024
DOENCAS = ["dengue", "chikungunya"]
ANO_INICIO = 2015

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
HEADERS = {"User-Agent": "observatorio-arboviroses-a3/1.0 (trabalho academico)"}


def extrair_infodengue(doenca: str, hoje: date, geocode: int = GEOCODE_SALVADOR) -> Path:
    url = "https://info.dengue.mat.br/api/alertcity"
    params = {
        "geocode": geocode,
        "disease": doenca,
        "format": "csv",
        "ew_start": 1,
        "ew_end": 53,
        "ey_start": ANO_INICIO,
        "ey_end": hoje.year,
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=120)
    resp.raise_for_status()

    if geocode == GEOCODE_SALVADOR:
        destino = RAW_DIR / "infodengue" / doenca / f"{hoje.isoformat()}.csv"
    else:
        destino = RAW_DIR / "infodengue_rms" / doenca / f"{geocode}_{hoje.isoformat()}.csv"
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
    arquivos = {"infodengue": {}, "rms": {}, "clima": None}
    for doenca in DOENCAS:
        arquivos["infodengue"][doenca] = extrair_infodengue(doenca, hoje)
        print(f"[extract] {doenca}: {arquivos['infodengue'][doenca]}")

        # RMS: Salvador já veio acima. O mapa é um extra, então um município fora do ar
        # não trava o pipeline: fica None e a transformação registra um aviso
        arquivos["rms"][doenca] = {}
        for geocode, nome in MUNICIPIOS_RMS.items():
            if geocode == GEOCODE_SALVADOR:
                continue
            try:
                arquivos["rms"][doenca][geocode] = extrair_infodengue(doenca, hoje, geocode)
            except requests.RequestException as e:
                print(f"[extract] {doenca} / {nome}: falhou ({e})")
                arquivos["rms"][doenca][geocode] = None
        baixados = sum(p is not None for p in arquivos["rms"][doenca].values())
        print(f"[extract] {doenca}: {baixados} de {len(MUNICIPIOS_RMS) - 1} municípios da RMS")
    arquivos["clima"] = extrair_clima(hoje)
    print(f"[extract] clima: {arquivos['clima']}")
    return arquivos


if __name__ == "__main__":
    extrair_tudo()
