-- =====================================================================
-- Migração 001: mapa da Região Metropolitana de Salvador (RMS)
-- O init.sql só roda na PRIMEIRA subida do banco. Num banco que já existe
-- (a VPS), aplique este arquivo uma vez, na mão:
--   docker compose exec -T db psql -U observatorio -v ON_ERROR_STOP=1 < db/migracoes/001_rms.sql
-- Pode rodar de novo sem problema (IF NOT EXISTS). Os municípios e os casos
-- entram na próxima execução do ETL.
-- =====================================================================

-- os 13 municípios da RMS (a lista fica em etl/extract.py; o ETL preenche esta tabela)
CREATE TABLE IF NOT EXISTS dim_municipio (
    geocode  INTEGER PRIMARY KEY,           -- código IBGE do município (7 dígitos)
    nome     TEXT NOT NULL
);

-- casos por município e semana (grão mais fino que fato_casos, que é só Salvador)
CREATE TABLE IF NOT EXISTS fato_casos_municipio (
    semana_inicio     DATE     NOT NULL REFERENCES dim_tempo(semana_inicio),
    id_doenca         SMALLINT NOT NULL REFERENCES dim_doenca(id_doenca),
    geocode           INTEGER  NOT NULL REFERENCES dim_municipio(geocode),
    casos_notificados INTEGER  NOT NULL,
    casos_estimados   NUMERIC(10,1),
    incidencia_100k   NUMERIC(10,2),
    nivel_alerta      SMALLINT,
    PRIMARY KEY (semana_inicio, id_doenca, geocode)
);
