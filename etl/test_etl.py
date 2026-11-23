"""
Testes do ETL (não precisam de internet nem de banco).
Uso, dentro da pasta etl:  pip install -r requirements-dev.txt && pytest
"""
import json

import pandas as pd
import pytest

from transform import (FalhaDeQualidade, montar_dim_tempo, portao_de_qualidade,
                       transformar_casos, transformar_clima)

COLUNAS = "data_iniSE,SE,casos_est,casos_est_min,casos_est_max,casos,p_inc100k,nivel"


def csv_infodengue(tmp_path, linhas: list[str], nome="dengue.csv"):
    caminho = tmp_path / nome
    caminho.write_text("\n".join([COLUNAS, *linhas]) + "\n", encoding="utf-8")
    return caminho


def semanas(n: int, data_em_ms=False, sem_data=0) -> list[str]:
    """n semanas a partir de 07/01/2024 (um domingo); as últimas `sem_data` ficam sem data."""
    linhas = []
    for i, d in enumerate(pd.date_range("2024-01-07", periods=n, freq="7D")):
        data = str(int(d.timestamp() * 1000)) if data_em_ms else d.strftime("%Y-%m-%d")
        if i >= n - sem_data:
            data = ""
        se = 202400 + i + 1 if i < 52 else 202500 + i - 51
        linhas.append(f"{data},{se},10.0,8.0,12.0,9,0.4,1")
    return linhas


def clima_ok() -> pd.DataFrame:
    return pd.DataFrame({"semana_inicio": [pd.Timestamp("2024-01-07")], "chuva_mm": [10.0]})


# ---------- leitura do CSV do InfoDengue ----------

@pytest.mark.parametrize("data_em_ms", [False, True], ids=["data em texto", "data em milissegundos"])
def test_le_csv_com_data_em_texto_ou_milissegundos(tmp_path, data_em_ms):
    linhas = [
        "1704585600000,202402,12.5,10.0,15.0,11,0.5,2" if data_em_ms else "2024-01-07,202402,12.5,10.0,15.0,11,0.5,2",
        "1703980800000,202401,,,,,0.3,1" if data_em_ms else "2023-12-31,202401,,,,,0.3,1",
    ]
    df, nulos = transformar_casos(csv_infodengue(tmp_path, linhas), "dengue")

    assert nulos == 0
    # ordenado por data, com o domingo de início da semana
    assert list(df["semana_inicio"]) == [pd.Timestamp("2023-12-31"), pd.Timestamp("2024-01-07")]
    assert list(df["ano"]) == [2024, 2024] and list(df["semana_epi"]) == [1, 2]
    assert df.loc[1, "casos_notificados"] == 11 and df.loc[1, "casos_estimados"] == 12.5
    # semana sem nada: notificados vira 0 e a estimativa cai pros notificados
    assert df.loc[0, "casos_notificados"] == 0 and df.loc[0, "casos_estimados"] == 0
    assert (df["id_doenca"] == 1).all()


def test_texto_e_milissegundos_dao_o_mesmo_resultado(tmp_path):
    texto, _ = transformar_casos(csv_infodengue(tmp_path, semanas(60), "a.csv"), "dengue")
    ms, _ = transformar_casos(csv_infodengue(tmp_path, semanas(60, data_em_ms=True), "b.csv"), "dengue")
    pd.testing.assert_frame_equal(texto, ms)


# ---------- portão de qualidade ----------

def test_portao_aceita_lote_bom(tmp_path):
    df, nulos = transformar_casos(csv_infodengue(tmp_path, semanas(60)), "dengue")
    log = portao_de_qualidade({"dengue": df}, clima_ok(), {"dengue": nulos})
    assert ("qualidade", "ok", "dengue: 60 semanas válidas") in log


def test_portao_barra_lote_vazio(tmp_path):
    df, nulos = transformar_casos(csv_infodengue(tmp_path, []), "dengue")  # só o cabeçalho
    with pytest.raises(FalhaDeQualidade, match="só 0 semanas"):
        portao_de_qualidade({"dengue": df}, clima_ok(), {"dengue": nulos})


def test_portao_barra_lote_com_muitas_datas_nulas(tmp_path):
    # 60 semanas boas + 10 sem data (14%): as sem data somem na limpeza,
    # então sobram 60 semanas e só a medição feita na leitura pega o problema
    df, nulos = transformar_casos(csv_infodengue(tmp_path, semanas(70, sem_data=10)), "dengue")
    assert len(df) == 60
    assert nulos == pytest.approx(10 / 70)
    with pytest.raises(FalhaDeQualidade, match="sem data"):
        portao_de_qualidade({"dengue": df}, clima_ok(), {"dengue": nulos})


def test_portao_avisa_explosao_de_casos_sem_remover(tmp_path):
    linhas = semanas(60)
    linhas[30] = linhas[30].replace(",10.0,8.0,12.0,9,", ",900.0,800.0,1000.0,900,")
    df, nulos = transformar_casos(csv_infodengue(tmp_path, linhas), "dengue")
    log = portao_de_qualidade({"dengue": df}, clima_ok(), {"dengue": nulos})
    assert any(status == "aviso" and "salto de casos" in msg for _, status, msg in log)
    assert len(df) == 60  # a semana continua lá


# ---------- clima: agrupamento semanal ----------

def test_clima_agrupa_por_semana_e_descarta_semana_incompleta(tmp_path):
    dias = pd.date_range("2024-01-07", periods=10)  # domingo + 9 dias: 1 semana cheia e 3 dias
    caminho = tmp_path / "clima.json"
    caminho.write_text(json.dumps({"daily": {
        "time": [d.strftime("%Y-%m-%d") for d in dias],
        "precipitation_sum": [1.0] * 10,
        "temperature_2m_mean": [26.0, 28.0] * 5,
        "temperature_2m_min": [22.0, 20.0, 23.0, 24.0, 21.0, 22.0, 23.0, 19.0, 19.0, 19.0],
        "relative_humidity_2m_mean": [80.0] * 10,
    }}), encoding="utf-8")

    semanal = transformar_clima(caminho)

    assert list(semanal["semana_inicio"]) == [pd.Timestamp("2024-01-07")]  # a semana de 3 dias sumiu
    s = semanal.iloc[0]
    assert s["chuva_mm"] == 7.0               # soma dos 7 dias
    assert s["temp_media"] == pytest.approx(26.9)  # média (4x26 + 3x28) / 7
    assert s["temp_min"] == 20.0              # menor mínima da semana
    assert s["umidade_media"] == 80.0


def test_semana_epidemiologica_das_semanas_so_com_clima():
    # SE do InfoDengue: 08/11/2026 = 202645, 28/12/2025 = 202553, 04/01/2026 = 202601
    # (a semana ISO, usada antes, daria 46, 1 de 2026 e 2: erra em anos que começam numa quinta)
    clima = pd.DataFrame({"semana_inicio": pd.to_datetime(["2026-11-08", "2025-12-28", "2026-01-04"])})
    casos = pd.DataFrame({"semana_inicio": [pd.Timestamp("2026-11-01")], "ano": [2026], "semana_epi": [44]})
    tempo = montar_dim_tempo([casos], clima).set_index("semana_inicio")
    assert tempo.loc["2026-11-08", ["ano", "semana_epi"]].tolist() == [2026, 45]
    assert tempo.loc["2025-12-28", ["ano", "semana_epi"]].tolist() == [2025, 53]
    assert tempo.loc["2026-01-04", ["ano", "semana_epi"]].tolist() == [2026, 1]
