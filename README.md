# Observatório de Arboviroses de Salvador · API

Back-end do trabalho A3 (Análise de Dados e Big Data · UNIFACS): ETL, Data Warehouse e API.
O painel fica em outro repositório, [`observatorio-front`](https://github.com/a3analise-de-dados-e-big-data/observatorio-front), hospedado na Vercel.

```
InfoDengue + Open-Meteo
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
cp .env.example .env
nano .env          # troque a senha
```

**3. Subir banco e API**

```bash
docker compose up -d --build
```

**4. Rodar o ETL pela primeira vez** (baixa ~11 anos de dados, leva 1 a 2 minutos)

```bash
docker compose run --rm etl
```

Saída esperada: `[extract] ...`, `[transform] ...`, `[load] Warehouse atualizado`, `[previsao] dengue: MAE ...`.

**5. Ligar no Caddy:** cole o conteúdo de `deploy/Caddyfile.add` no fim de `/opt/edge/Caddyfile` e recarregue:

```bash
docker exec -w /etc/caddy caddy caddy reload
```

**6. Conferir:** `https://observatorio-api.rafaelrangelalmeida.com/api/saude`
Documentação automática da API: `https://observatorio-api.rafaelrangelalmeida.com/api/docs`

**7. Agendar o ETL toda segunda às 6h**

```bash
crontab -e
# adicione a linha:
0 6 * * 1 cd /root/observatorio-api && docker compose run --rm etl >> /var/log/observatorio-etl.log 2>&1
```

## Atualizar depois de um push

```bash
cd /root/observatorio-api && git pull && docker compose up -d --build
```

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

## Se der erro

- **Erro no ETL sobre alguma coluna (`KeyError: 'data_iniSE'`, `'p_inc100k'` etc.):** o InfoDengue mudou o nome de alguma coluna. Abra o CSV em `data/raw/infodengue/dengue/` e ajuste o `rename` em `etl/transform.py`.
- **"LOTE BARRADO":** o portão de qualidade segurou os dados (ex.: API devolveu vazio). O painel continua com a última carga boa. Rode de novo mais tarde.
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

No documento, o projeto também usa dados por bairro (SMS Salvador), denúncias do 156 e LIRAa. Esses dados não são públicos, então o protótipo trabalha no nível da cidade inteira. O mapa por bairro fica como proposta.

## LGPD no protótipo

- Só usa dados já agregados por cidade e semana: nenhuma pessoa identificável entra no sistema.
- O ETL só mantém as colunas necessárias (art. 6º, III).
- O banco não fica exposto na internet (só a API acessa, pela rede interna do Docker).
- A senha fica no `.env`, fora do código e fora do Git.
