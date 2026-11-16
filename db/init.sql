-- =====================================================================
-- DATA WAREHOUSE - Observatório de Arboviroses de Salvador
-- Modelo estrela: tabelas fato no centro, dimensões em volta.
-- Este arquivo roda sozinho na primeira vez que o Postgres sobe.
-- =====================================================================

-- ---------- DIMENSÕES ----------

CREATE TABLE IF NOT EXISTS dim_tempo (
    semana_inicio  DATE PRIMARY KEY,      -- domingo que abre a semana epidemiológica
    ano            SMALLINT NOT NULL,
    semana_epi     SMALLINT NOT NULL,     -- 1 a 53
    mes            SMALLINT NOT NULL,
    estacao_chuvosa BOOLEAN NOT NULL      -- em Salvador: abril a julho
);

CREATE TABLE IF NOT EXISTS dim_doenca (
    id_doenca  SMALLINT PRIMARY KEY,
    nome       TEXT NOT NULL UNIQUE
);

-- zika ficou de fora: o InfoDengue parou de publicar a série de Salvador em fev/2024
INSERT INTO dim_doenca (id_doenca, nome) VALUES
    (1, 'dengue'), (2, 'chikungunya')
ON CONFLICT DO NOTHING;

-- dim_bairro e dim_faixa_etaria existem no modelo do documento, mas
-- não há dado público por bairro/idade na API do InfoDengue.
-- No protótipo trabalhamos no nível da cidade inteira.

-- ---------- FATOS ----------

CREATE TABLE IF NOT EXISTS fato_casos (
    semana_inicio    DATE     NOT NULL REFERENCES dim_tempo(semana_inicio),
    id_doenca        SMALLINT NOT NULL REFERENCES dim_doenca(id_doenca),
    casos_notificados INTEGER NOT NULL,
    casos_estimados  NUMERIC(10,1),        -- estimativa do InfoDengue com correção de atraso (nowcasting)
    casos_est_min    NUMERIC(10,1),        -- faixa da estimativa (só faz diferença nas semanas recentes)
    casos_est_max    NUMERIC(10,1),
    incidencia_100k  NUMERIC(10,2),        -- calculada pelo InfoDengue sobre a estimativa
    nivel_alerta     SMALLINT,             -- 1 verde, 2 amarelo, 3 laranja, 4 vermelho
    PRIMARY KEY (semana_inicio, id_doenca)
);

CREATE TABLE IF NOT EXISTS fato_clima (
    semana_inicio  DATE PRIMARY KEY REFERENCES dim_tempo(semana_inicio),
    chuva_mm       NUMERIC(7,1),
    temp_media     NUMERIC(4,1),
    temp_min       NUMERIC(4,1),
    umidade_media  NUMERIC(4,1)
);

CREATE TABLE IF NOT EXISTS fato_previsao (
    semana_inicio    DATE     NOT NULL,     -- semana prevista (futura)
    id_doenca        SMALLINT NOT NULL REFERENCES dim_doenca(id_doenca),
    casos_previstos  NUMERIC(10,1) NOT NULL,
    limite_inferior  NUMERIC(10,1) NOT NULL,
    limite_superior  NUMERIC(10,1) NOT NULL,
    gerado_em        TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (semana_inicio, id_doenca)
);

-- ---------- LOG DO ETL (ajuda a mostrar o "portão de qualidade") ----------

CREATE TABLE IF NOT EXISTS etl_log (
    id          SERIAL PRIMARY KEY,
    executado_em TIMESTAMP NOT NULL DEFAULT now(),
    etapa       TEXT NOT NULL,
    status      TEXT NOT NULL,              -- ok | aviso | erro
    mensagem    TEXT
);
