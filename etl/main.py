"""
Roda o pipeline completo: Extração -> Transformação -> Carga -> Previsão.
Uso:  python main.py
Na VPS:  docker compose run --rm etl
Se uma etapa falhar, o erro vai pro etl_log e o painel continua com a última carga boa.
"""
import sys

import psycopg

from extract import extrair_tudo
from transform import transformar_tudo, FalhaDeQualidade
from load import carregar_tudo, registrar_log, DATABASE_URL
from previsao import gerar_previsoes


def falhou(etapa: str, erro: Exception):
    print(f"[{etapa}] FALHOU: {erro}")
    try:
        # conexão nova: a da carga (se foi ela que falhou) já desfez tudo com rollback
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            registrar_log(cur, etapa, "erro", f"{type(erro).__name__}: {erro}")
    except psycopg.Error as e:
        print(f"[{etapa}] nem deu pra gravar no etl_log (banco fora do ar?): {e}")
    sys.exit(1)


def main():
    try:
        arquivos = extrair_tudo()
    except Exception as e:  # fonte fora do ar, timeout, sem internet...
        falhou("extracao", e)

    try:
        dados = transformar_tudo(arquivos)
    except FalhaDeQualidade as e:
        # lote barrado: nada vai pro Warehouse, o painel continua com os dados da semana anterior
        falhou("qualidade", e)
    except Exception as e:  # ex.: o InfoDengue mudou o nome de uma coluna (KeyError)
        falhou("transformacao", e)

    try:
        carregar_tudo(dados)
    except Exception as e:
        falhou("carga", e)

    try:
        gerar_previsoes()
    except Exception as e:
        falhou("previsao", e)
    print("Pipeline concluído.")


if __name__ == "__main__":
    main()
