"""
ANÁLISE PREDITIVA - previsão de casos para as próximas 4 semanas.

Ideia (fácil de explicar na apresentação):
  "Os casos da semana que vem dependem dos casos de agora e da chuva e do
   calor das últimas semanas, porque a chuva cria criadouros e o mosquito
   leva algumas semanas pra virar surto."

Modelo: regressão linear (mínimos quadrados) sobre log(casos), um modelo
para cada horizonte (1, 2, 3 e 4 semanas à frente). Variáveis:
  - casos desta semana e das 2 anteriores
  - chuva somada de 3 a 6 semanas atrás (o tempo de a chuva virar criadouro e mosquito)
  - temperatura média de 3 a 6 semanas atrás
  - época do ano (seno/cosseno da semana)
A faixa de incerteza (mín-máx) é um intervalo de ~90% calculado pelo erro do modelo.

Validação: o modelo é treinado sem o último ano e testado nele, comparando com o
modelo "ingênuo" (daqui a h semanas = casos de hoje). Se não ganhar do ingênuo,
o modelo não está ajudando.
"""
import numpy as np
import pandas as pd
import psycopg

from load import DATABASE_URL, registrar_log

HORIZONTES = [1, 2, 3, 4]
SEMANAS_TESTE = 52
# série parada há mais que isso = a fonte deixou de publicar; prever a partir dela
# seria apresentar como "próximas semanas" semanas que já passaram
MAX_DIAS_SEM_DADO = 60


def _carregar(conn, id_doenca: int) -> pd.DataFrame:
    sql = """
        SELECT c.semana_inicio, c.casos_estimados::float AS casos,
               cl.chuva_mm::float AS chuva, cl.temp_media::float AS temp
        FROM fato_casos c
        LEFT JOIN fato_clima cl USING (semana_inicio)
        WHERE c.id_doenca = %s
        ORDER BY c.semana_inicio
    """
    with conn.cursor() as cur:
        cur.execute(sql, (id_doenca,))
        df = pd.DataFrame(cur.fetchall(), columns=["semana_inicio", "casos", "chuva", "temp"])
    df["semana_inicio"] = pd.to_datetime(df["semana_inicio"])
    # o clima chega com ~1 semana de atraso, mas o modelo só usa clima de 3+ semanas atrás,
    # então não precisa inventar (repetir) o valor das últimas semanas
    return df


def _features(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    logc = np.log1p(df["casos"])
    X["casos_0"] = logc
    X["casos_1"] = logc.shift(1)
    X["casos_2"] = logc.shift(2)
    # shift(3) + rolling(4) = semanas 3, 4, 5 e 6 antes desta
    X["chuva_3a6sem"] = df["chuva"].shift(3).rolling(4).sum()
    X["temp_3a6sem"] = df["temp"].shift(3).rolling(4).mean()
    semana = df["semana_inicio"].dt.isocalendar().week.astype(float)
    X["sazonal_sin"] = np.sin(2 * np.pi * semana / 52)
    X["sazonal_cos"] = np.cos(2 * np.pi * semana / 52)
    X["intercepto"] = 1.0
    return X


def _ajustar(X: np.ndarray, y: np.ndarray):
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    sigma = np.std(y - X @ coef)
    return coef, sigma


def prever_doenca(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    X = _features(df)
    if X.iloc[-1].isna().any():
        raise ValueError("falta clima de 3 a 6 semanas atrás (o Open-Meteo atrasou?)")
    logc = np.log1p(df["casos"])
    previsoes, erros = [], {}

    for h in HORIZONTES:
        alvo = logc.shift(-h)
        validos = X.notna().all(axis=1) & alvo.notna()
        Xv, yv = X[validos], alvo[validos]

        # validação: testa no último ano. O treino para h semanas antes do teste:
        # sem essa folga, os últimos exemplos de treino teriam o alvo (semana + h) dentro do teste
        inicio_teste = Xv.index[-SEMANAS_TESTE]
        treino = Xv.index < inicio_teste - h
        teste = Xv.index >= inicio_teste
        coef_t, _ = _ajustar(Xv[treino].to_numpy(), yv[treino].to_numpy())
        real = np.expm1(yv[teste].to_numpy())
        pred_modelo = np.expm1(Xv[teste].to_numpy() @ coef_t)
        pred_ingenuo = np.expm1(Xv.loc[teste, "casos_0"].to_numpy())  # daqui a h semanas = casos de hoje
        erros[h] = (float(np.mean(np.abs(pred_modelo - real))),
                    float(np.mean(np.abs(pred_ingenuo - real))))

        # modelo final com todo o histórico
        coef, sigma = _ajustar(Xv.to_numpy(), yv.to_numpy())
        x_hoje = X.iloc[-1].to_numpy()
        mu = float(x_hoje @ coef)
        previsoes.append({
            "semana_inicio": df["semana_inicio"].iloc[-1] + pd.Timedelta(weeks=h),
            "casos_previstos": max(np.expm1(mu), 0),
            "limite_inferior": max(np.expm1(mu - 1.645 * sigma), 0),
            "limite_superior": max(np.expm1(mu + 1.645 * sigma), 0),
        })
    return pd.DataFrame(previsoes).round({"casos_previstos": 1, "limite_inferior": 1, "limite_superior": 1}), erros


def gerar_previsoes():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_doenca, nome FROM dim_doenca ORDER BY id_doenca")
            doencas = cur.fetchall()

        for id_doenca, nome in doencas:
            df = _carregar(conn, id_doenca)
            if len(df) < 2 * SEMANAS_TESTE + 10 or df["casos"].sum() == 0:
                print(f"[previsao] {nome}: dados insuficientes, pulando")
                continue

            atraso = (pd.Timestamp.today().normalize() - df["semana_inicio"].iloc[-1]).days
            if atraso > MAX_DIAS_SEM_DADO:
                msg = f"{nome}: último dado há {atraso} dias, previsão não gerada"
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM fato_previsao WHERE id_doenca = %s", (id_doenca,))
                    registrar_log(cur, "previsao", "aviso", msg)
                print(f"[previsao] {msg}")
                continue

            try:
                prev, erros = prever_doenca(df)
            except ValueError as e:
                # sem previsão é melhor que previsão errada: tira a antiga e avisa
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM fato_previsao WHERE id_doenca = %s", (id_doenca,))
                    registrar_log(cur, "previsao", "aviso", f"{nome}: {e}")
                print(f"[previsao] {nome}: {e}")
                continue
            with conn.cursor() as cur:
                cur.execute("DELETE FROM fato_previsao WHERE id_doenca = %s", (id_doenca,))
                cur.executemany(
                    "INSERT INTO fato_previsao (semana_inicio, id_doenca, casos_previstos, "
                    "limite_inferior, limite_superior) VALUES (%s, %s, %s, %s, %s)",
                    [(r.semana_inicio.date(), id_doenca, r.casos_previstos,
                      r.limite_inferior, r.limite_superior) for r in prev.itertuples()],
                )
                resumo = ", ".join(f"{h} sem: {m:.0f} vs {i:.0f}" for h, (m, i) in erros.items())
                registrar_log(cur, "previsao", "ok", f"{nome} - MAE modelo vs ingênuo no último ano: {resumo}")
            print(f"[previsao] {nome}: MAE modelo vs ingênuo no último ano -> {resumo}")


if __name__ == "__main__":
    gerar_previsoes()
