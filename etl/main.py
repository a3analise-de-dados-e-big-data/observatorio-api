"""
Roda o pipeline completo: Extração -> Transformação -> Carga -> Previsão.
Uso:  python main.py
Na VPS:  docker compose run --rm etl
"""
import sys

import psycopg

from extract import extrair_tudo
from transform import transformar_tudo, FalhaDeQualidade
from load import carregar_tudo, registrar_log, DATABASE_URL
from previsao import gerar_previsoes


def main():
    arquivos = extrair_tudo()
    try:
        dados = transformar_tudo(arquivos)
    except FalhaDeQualidade as e:
        # lote barrado: nada vai pro Warehouse, o painel continua com os dados da semana anterior
        print(f"[qualidade] LOTE BARRADO: {e}")
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            registrar_log(cur, "qualidade", "erro", str(e))
        sys.exit(1)

    carregar_tudo(dados)
    gerar_previsoes()
    print("Pipeline concluído.")


if __name__ == "__main__":
    main()
