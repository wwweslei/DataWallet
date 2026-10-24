# Tesouro Tracker

Aplicação local em Streamlit para registrar posições do Tesouro Direto, acompanhar
preços históricos e visualizar métricas de desempenho e tendência. A fonte de
preços não é simulada: o usuário pode registrar cotações manualmente ou configurar
um endpoint compatível.

O projeto requer Python 3.12 ou superior.

## Executar

Com Poetry (recomendado):

```powershell
poetry env use 3.12
poetry install
poetry run streamlit run app.py
```

Alternativamente, usando `pip`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Na primeira execução, o SQLite é criado em `database/tesouro.db` com o esquema
de `database/schema.sql`. Configure `TESOURO_DATABASE_PATH` para usar outro
caminho.

## Atualização de cotações

O botão **Atualizar Cotações** consulta a página pública de rendimentos do
Tesouro Direto com timeout e retries HTTP. Cada atualização bem-sucedida cria
um novo snapshot em `cotacoes_tesouro` por SQLAlchemy; o histórico não sobrescreve
coletas anteriores. Se a página não publicar cotações, estiver indisponível ou
mudar de estrutura, o aplicativo mantém a carteira funcional e apresenta a
última coleta salva no SQLite. O snapshot local também é usado como cache.
Atualizações não são feitas automaticamente ao abrir o aplicativo.

## Funcionalidades

- Cadastro, edição e remoção de posições, com validação de valores.
- Histórico diário SQLite, com atualização da cotação atual.
- Retornos acumulado, diário, semanal, mensal e anual, volatilidade e drawdown.
- Médias móveis 9/21/50/200, RSI (14), MACD, Bollinger e ROC.
- Classificação de tendência, sinais indicativos, scores de tendência e risco.
- Dashboard Plotly, ranking, evolução patrimonial e distribuição da carteira.
- Exportação em CSV e Excel e relatório resumido em PDF.
- Projeção linear simples, identificada como estimativa e não como recomendação.
- Importação de posições de arquivos Excel na pasta `xlsx`, com detecção
  automática de colunas, consolidação de preço médio ponderado e histórico
  idempotente de arquivos processados.

## Importar posições de corretora

Coloque os extratos `.xlsx` diretamente na pasta `xlsx` da raiz do projeto e
abra a página **Importação XLSX** no Streamlit. O botão **Importar Arquivos
XLSX** processa arquivos novos, mostra o progresso e apresenta o relatório. A
importação não é disparada automaticamente na inicialização: ela ocorre quando
o usuário aciona o botão, evitando alterações inesperadas na carteira.

Arquivos com conteúdo idêntico são reconhecidos por SHA-256 e ignorados após
uma importação bem-sucedida. Arquivos com falha podem ser corrigidos e tentados
novamente. Posições de arquivos novos com o mesmo ticker são somadas, usando
`valor investido / quantidade` para calcular o preço médio ponderado. Portanto,
importe extratos incrementais; reimportar extratos periódicos que repetem o
saldo integral pode somar posições repetidas, mesmo quando os arquivos têm
conteúdo diferente.

O importador grava em `ativos` e `historico_importacoes`, no mesmo SQLite da
aplicação. A planilha não altera a tabela de títulos cadastrados manualmente;
as posições importadas e o histórico podem ser consultados na página de
importação.

Os indicadores técnicos são calculados sobre as cotações efetivamente salvas.
Com histórico curto, médias e osciladores permanecem indisponíveis até haver
observações suficientes. As rentabilidades de curto prazo usam o último preço
disponível no histórico; quando há menos observações que o período, usam o
histórico disponível. As métricas não consideram impostos, taxas, cupons ou
reinvestimentos automaticamente.

## Testes

```powershell
poetry run pytest -q
```

As dependências e os grupos de desenvolvimento são declarados em
`pyproject.toml`; `poetry.lock` fixa as versões resolvidas para instalações
reprodutíveis.

## Estrutura

`models/` contém o modelo do título; `database.py` e `database/schema.sql`
respondem pela persistência; `services/` implementa indicadores, carteira,
tendências e adaptador de cotações; `pages/` contém as telas Streamlit; `utils/`
reúne exportadores e formatação.