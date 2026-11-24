# Observatório de Arboviroses de Salvador · API

Back-end do trabalho A3 (Análise de Dados e Big Data · UNIFACS): ETL, Data Warehouse e API.
O painel fica em outro repositório, [`observatorio-front`](https://github.com/a3analise-de-dados-e-big-data/observatorio-front), hospedado na Vercel.

```
InfoDengue (Salvador + RMS) + Open-Meteo
      │  etl/extract.py      (E) baixa e guarda o original
      ▼
data/raw/                     ← DATA LAKE (arquivos brutos, um por execução)
      │  etl/transform.py    (T) limpa, padroniza, checa qualidade
      ▼
data/processed/               ← camada "prata" (dado limpo, ainda em arquivo)
      │  etl/load.py         (L) grava no banco
      ▼
PostgreSQL                    ← DATA WAREHOUSE (modelo estrela, db/init.sql)
      │  etl/previsao.py     previsão das próximas 4 semanas
      ▼
api/ (FastAPI)                → JSON em https://observatorio-api.rafaelrangelalmeida.com/api/...
      ▼
observatorio-front (Vercel)   → painel
```

| Pasta | O que é |
| --- | --- |
| `db/init.sql` | Tabelas do Warehouse (modelo estrela). Roda sozinho na 1ª subida. |
| `db/migracoes/` | Mudanças nas tabelas pra aplicar na mão num banco que já existe (ver "Atualizar depois de um push") |
| `etl/` | Pipeline: `extract.py`, `transform.py`, `load.py`, `previsao.py`, `main.py` (roda tudo) |
| `api/main.py` | Endpoints que leem o Warehouse |
| `deploy/Caddyfile.add` | Bloco pra colar no Caddy compartilhado da VPS |
| `data/raw/` | Data Lake (criado pelo ETL) |

## Como encaixa na VPS

A VPS tem um Caddy compartilhado em `/opt/edge`. Ele é o único container que publica
porta no host (80/443) e cuida do HTTPS de todos os projetos. Cada projeto entra na
rede Docker externa `edge` e ganha um bloco no `Caddyfile`.

| Container | Redes | Responde em |
| --- | --- | --- |
| `observatorio-api` | `interno`, `edge` | `observatorio-api.rafaelrangelalmeida.com` (via Caddy) |
| `observatorio-db` | `interno` | só pra API e pro ETL, nunca na internet |
| ETL | `interno` | roda pelo cron e desliga |

## Primeira instalação na VPS

**1. DNS:** crie um registro **A** `observatorio-api` → IP da VPS. **Espere propagar**:
o Caddy só emite o certificado se o domínio já resolver.

**2. Clonar e configurar a senha do banco**

```bash
cd /root
git clone https://github.com/a3analise-de-dados-e-big-data/observatorio-api.git
cd observatorio-api
echo "DB_PASSWORD=$(openssl rand -hex 24)" > .env    # gera uma senha aleatória
```

A senha só existe nesse `.env`, na VPS. Ninguém precisa decorar: a API e o ETL leem dali.

**3. Subir banco e API**

```bash
docker compose up -d --build
```

**4. Rodar o ETL pela primeira vez** (baixa ~11 anos de dados, leva 1 a 2 minutos)

```bash
docker compose run --rm etl
```

Saída esperada:

```
[extract] dengue: ...
[extract] dengue: 12 municípios da RMS
[extract] chikungunya: ...
[extract] chikungunya: 12 municípios da RMS
[extract] clima: ...
[transform] 1238 linhas de casos, 619 semanas de clima, 16094 linhas da RMS
[load] Warehouse atualizado
[previsao] dengue: MAE modelo vs ingênuo no último ano -> 1 sem: ... vs ..., ...
[previsao] chikungunya: MAE modelo vs ingênuo no último ano -> ...
Pipeline concluído.
```

(O número de linhas cresce uma semana por vez, conforme o InfoDengue publica. São 27 downloads
do InfoDengue: Salvador e os outros 12 municípios da RMS, para dengue e chikungunya.)

No MAE, "modelo vs ingênuo" compara o erro do nosso modelo com o de chutar que daqui a h semanas
os casos serão iguais aos de hoje. O modelo só está ajudando se o primeiro número for menor.

**5. Ligar no Caddy** (só depois que o DNS do passo 1 propagar)

O Caddy é compartilhado com os outros projetos da VPS, então **valide antes de recarregar**:
se o arquivo tiver erro, o `validate` falha e o `reload` nem roda, e os outros sites continuam no ar.

```bash
grep -q observatorio-api /opt/edge/Caddyfile || cat deploy/Caddyfile.add >> /opt/edge/Caddyfile   # não duplica o bloco
docker exec -w /etc/caddy caddy caddy validate --config Caddyfile
docker exec -w /etc/caddy caddy caddy reload
```

**6. Conferir:** `https://observatorio-api.rafaelrangelalmeida.com/api/saude`
Documentação automática da API: `https://observatorio-api.rafaelrangelalmeida.com/api/docs`

**7. Agendar o ETL toda segunda**

```bash
crontab -e
# adicione a linha:
0 6 * * 1 cd /root/observatorio-api && docker compose run --rm etl >> /var/log/observatorio-etl.log 2>&1
```

`0 6 * * 1` = minuto 0, hora 6, toda segunda-feira. **A hora é a do relógio da VPS, que está em UTC**
(`timedatectl | grep "Time zone"`), então o ETL roda às **3h de Salvador**. O InfoDengue atualiza
uma vez por semana, então o horário não muda nada. Pra rodar às 6h de Salvador, use `0 9` no lugar de `0 6`.

## Conferir se o ETL agendado está funcionando

```bash
# a linha está no crontab?
crontab -l | grep observatorio

# o comando funciona? (roda o mesmo que o cron roda, sem esperar segunda)
cd /root/observatorio-api && docker compose run --rm etl >> /var/log/observatorio-etl.log 2>&1
tail -5 /var/log/observatorio-etl.log          # última linha: "Pipeline concluído."

# depois de uma segunda: o cron disparou?
grep CRON /var/log/syslog | grep observatorio | tail -5

# a carga chegou no banco? (a prova mais confiável)
docker compose exec db psql -U observatorio -c \
  "SELECT executado_em, etapa, status, mensagem FROM etl_log ORDER BY id DESC LIMIT 5;"
```

No `etl_log`, `executado_em` também está em UTC: uma carga de segunda às 3h de Salvador aparece como `06:00`.

## Atualizar depois de um push

```bash
cd /root/observatorio-api && git pull && docker compose up -d --build
```

O `db/init.sql` só roda na **primeira** subida do banco. Se um push mudar as tabelas, o banco que já
existe não muda sozinho: cada mudança vem num arquivo em `db/migracoes/`, que você aplica **uma vez**,
na ordem do número, **antes** de rodar o ETL novo. Os arquivos usam `IF NOT EXISTS`, então rodar de
novo não estraga nada.

| Migração | O que faz |
| --- | --- |
| `001_rms.sql` | Cria `dim_municipio` e `fato_casos_municipio` (mapa da Região Metropolitana) |

```bash
cd /root/observatorio-api && git pull
docker compose exec -T db psql -U observatorio -v ON_ERROR_STOP=1 < db/migracoes/001_rms.sql
docker compose up -d --build          # API nova
docker compose build etl && docker compose run --rm etl   # ETL novo: preenche as tabelas novas
# conferir:
docker compose exec db psql -U observatorio -c "SELECT count(*) FROM fato_casos_municipio;"
```

Se o ETL novo rodar antes da migração, Salvador atualiza normalmente e só o mapa da RMS fica de
fora, com um aviso no `etl_log`. É só aplicar a migração e rodar o ETL de novo.

## Comandos úteis

```bash
docker compose logs -f api                      # ver erros da API
docker compose exec db psql -U observatorio     # entrar no banco
#   SELECT * FROM etl_log ORDER BY id DESC LIMIT 10;   -- histórico do ETL
docker compose down                             # desligar (os dados ficam salvos)
```

## Rodar no seu PC

**Com Docker:** a rede `edge` só existe na VPS, então crie uma vazia antes (só na primeira vez):

```bash
docker network create edge
cp .env.example .env
docker compose up -d --build && docker compose run --rm etl
```

A API não publica porta no host. Pra testar, use `docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/saude').read())"`
ou rode sem Docker, como abaixo.

**Sem Docker:** precisa de Python 3.11+ e um Postgres local com o banco `observatorio` criado e o `db/init.sql` aplicado.

```bash
pip install -r etl/requirements.txt -r api/requirements.txt
export DATABASE_URL=postgresql://observatorio:SENHA@localhost:5432/observatorio
cd etl && python main.py && cd ..
cd api && uvicorn main:app --reload    # API em http://localhost:8000/api/docs
```

## Testes

Testam a leitura do CSV do InfoDengue (data em texto e em milissegundos), o portão de qualidade
(lote vazio, datas nulas, salto de casos) e o agrupamento semanal do clima. Não precisam de
internet nem de banco.

```bash
cd etl
pip install -r requirements-dev.txt
pytest
```

## Se der erro

- **Erro no ETL sobre alguma coluna (`KeyError: 'data_iniSE'`, `'p_inc100k'` etc.):** o InfoDengue mudou o nome de alguma coluna. Abra o CSV em `data/raw/infodengue/dengue/` e ajuste o `rename` em `etl/transform.py`.
- **`[qualidade] FALHOU`:** o portão de qualidade segurou os dados (ex.: API devolveu vazio). O painel continua com a última carga boa. Rode de novo mais tarde.
- **`[extracao] FALHOU` / `[carga] FALHOU`:** fonte fora do ar ou banco com problema. Nada pela metade fica no banco (a carga é uma transação só) e o erro fica registrado no `etl_log` com `status = 'erro'`.
- **`network edge declared as external, but could not be found`:** você está fora da VPS. Rode `docker network create edge`.
- **HTTPS não sobe / erro de certificado:** o DNS ainda não propagou. Confira com `dig observatorio-api.rafaelrangelalmeida.com` e veja `docker logs caddy`.
- **Painel em branco / alerta "Não consegui carregar os dados":** o ETL ainda não rodou (passo 4).

## Por que não tem zika

O InfoDengue parou de publicar a série de zika de Salvador na semana de 11/02/2024 (última versão do modelo: 26/02/2024).
Com o painel mostrando "próximas 4 semanas" e "nível de alerta", manter zika exibiria dado de 2024 como se fosse atual,
então a doença saiu do ETL, da API e do painel. Se a série voltar, é só devolver `"zika"` em `etl/extract.py`,
`etl/transform.py`, `api/main.py` e `db/init.sql`.

Pela mesma razão, `etl/previsao.py` não gera previsão pra uma doença cujo último dado tenha mais de 60 dias.

## O que é cenário (não implementado)

No documento, o projeto também usa dados por bairro (SMS Salvador), denúncias do 156 e LIRAa. Esses dados não são públicos, então o protótipo trabalha no nível da cidade inteira. O mapa por bairro fica como proposta (veja abaixo por quê); no lugar dele, o painel tem o mapa por **município da Região Metropolitana** (13 municípios, códigos do IBGE em `etl/extract.py`), que usa a mesma API do InfoDengue.

### Por que não tem mapa por bairro de Salvador

Conferido em nov/2026 nas fontes públicas:

- **InfoDengue:** só publica por município (`alertcity` recebe o código IBGE da cidade).
- **SINAN aberto** (notificações individuais anonimizadas do Ministério da Saúde, arquivo
  `DENGBR25.csv` em `s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SINAN/Dengue/csv/`): tem 121
  colunas, e o recorte geográfico mais fino é o **município de residência** (`ID_MN_RESI`). Não tem
  bairro, endereço, CEP nem setor censitário: isso é retirado na anonimização.
- **SMS Salvador:** conta os casos por **distrito sanitário** (12 distritos), mas não publica essa série
  como dado aberto.

Os **contornos** dos bairros existem (Malha de Bairros do Censo 2022 do IBGE e a base cartográfica
da prefeitura); o que falta são os casos. Usar a unidade de saúde que notificou (`ID_UNIDADE`) como
"bairro" seria enganoso: mostra onde a pessoa foi atendida, não onde mora. O caminho seria pedir
à SMS, pela Lei de Acesso à Informação, os casos por bairro (ou distrito sanitário) e semana.

## LGPD no protótipo

- Só usa dados já agregados por cidade e semana: nenhuma pessoa identificável entra no sistema.
- O ETL só mantém as colunas necessárias (art. 6º, III).
- O banco não fica exposto na internet (só a API acessa, pela rede interna do Docker).
- A senha fica no `.env`, fora do código e fora do Git.
