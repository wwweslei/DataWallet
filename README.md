# DataWallet — Tesouro

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
de `database/schema.sql`. Configure `DATAWALLET_DATABASE_PATH` para usar outro
caminho; `TESOURO_DATABASE_PATH` continua aceito por compatibilidade.

## Chaves e tokens

Guarde credenciais locais no arquivo `.env`, que é ignorado pelo Git. Use
`.env.example` como referência e nunca adicione valores reais ao exemplo. O
aplicativo carrega esse arquivo na inicialização; variáveis já definidas no
ambiente têm prioridade. `BRAPI_TOKEN` fica reservado para a futura integração
de ações e não é utilizado pelo módulo Tesouro.

## Atualização de cotações

O botão **Atualizar Cotações** consulta a fonte pública de preços do Tesouro
Direto, sem exigir token ou plano pago. Cada atualização bem-sucedida cria um
novo snapshot em `cotacoes_tesouro`; o histórico não sobrescreve coletas
anteriores. Se a fonte estiver indisponível ou mudar de estrutura, o aplicativo
mantém a carteira funcional e apresenta a última coleta salva no SQLite.
Atualizações não são feitas automaticamente ao abrir o aplicativo.

As posições importadas de títulos do Tesouro usam o preço de venda do último
snapshot correspondente para calcular valor atual, lucro e evolução patrimonial;
se não houver cotação correspondente, o aplicativo mantém o valor investido
como referência. A evolução começa na data da posição importada e acompanha
os snapshots disponíveis a partir daí.

## Funcionalidades

- Cadastro, edição e remoção de posições, com validação de valores.
- Histórico diário SQLite, com atualização da cotação atual.
- Retornos acumulado, diário, semanal, mensal e anual, volatilidade e drawdown.
- Médias móveis 9/21/50/200, RSI (14), MACD, Bollinger e ROC.
- Classificação de tendência, sinais indicativos, scores de tendência e risco.
- Dashboard Plotly, ranking, evolução patrimonial e distribuição da carteira.
- Tabela por posição com valorização, estimativa de juros semestrais e soma dos
  valores para títulos IPCA+ e Prefixados que pagam cupons.
- Análise de tendências para títulos cadastrados manualmente e posições
  importadas que tenham histórico de cotações salvo.
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

Na tabela de desempenho, os juros semestrais são uma estimativa de um cupom:
usa a taxa padrão anual do título (6% para IPCA+ e 10% para Prefixado),
convertida para semestre e aplicada ao valor de mercado atual da posição. O
valor oficial depende do saldo nominal atualizado e da data de pagamento; por
isso, a estimativa não substitui o extrato da instituição. “Rendimentos” é a
valorização ou desvalorização atual em relação ao valor investido e não inclui
cupons já recebidos. “Juros + rendimentos” soma essas duas medidas.

## Testes

```powershell
poetry run pytest -q
```

As dependências e os grupos de desenvolvimento são declarados em
`pyproject.toml`; `poetry.lock` fixa as versões resolvidas para instalações
reprodutíveis.

## Estrutura

O DataWallet organiza cada classe de investimento como um módulo independente:

- `modules/tesouro/` contém páginas, modelos e serviços do módulo Tesouro.
- `modules/fii/` e `modules/acoes/` reservam os mesmos pontos de extensão para
  os futuros módulos de fundos imobiliários e ações.
- `modules/tesouro/utils/` contém formatação e exportação específicas do Tesouro;
  cada novo módulo pode ter seus próprios utilitários.
- `modules/navigation.py` agrega as páginas registradas por cada módulo; módulos
  ainda sem telas não aparecem na navegação.
- `database/repository.py` centraliza o acesso ao SQLite compartilhado, junto
  de `database/schema.sql`. Cada módulo deve manter suas tabelas e regras
  isoladas, evitando dependências de domínio entre Tesouro, FIIs e Ações.