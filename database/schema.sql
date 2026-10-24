PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS titulos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL,
    data_compra TEXT NOT NULL,
    quantidade REAL NOT NULL CHECK (quantidade > 0),
    preco_compra REAL NOT NULL CHECK (preco_compra > 0),
    preco_atual REAL CHECK (preco_atual IS NULL OR preco_atual > 0),
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS historico_precos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo_id INTEGER NOT NULL REFERENCES titulos(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    preco REAL NOT NULL CHECK (preco > 0),
    fonte TEXT NOT NULL DEFAULT 'manual',
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (titulo_id, data)
);

CREATE INDEX IF NOT EXISTS idx_historico_precos_titulo_data
    ON historico_precos(titulo_id, data);

CREATE TABLE IF NOT EXISTS ativos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    nome TEXT NOT NULL,
    categoria TEXT NOT NULL,
    quantidade REAL NOT NULL CHECK (quantidade > 0),
    preco_medio REAL NOT NULL CHECK (preco_medio > 0),
    valor_investido REAL NOT NULL CHECK (valor_investido > 0),
    vencimento TEXT,
    data_atualizacao TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historico_importacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo TEXT NOT NULL,
    arquivo_hash TEXT NOT NULL UNIQUE,
    data_importacao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    linhas_processadas INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    detalhes TEXT
);

CREATE INDEX IF NOT EXISTS idx_historico_importacoes_data
    ON historico_importacoes(data_importacao);

CREATE TABLE IF NOT EXISTS cotacoes_tesouro (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    tipo TEXT NOT NULL,
    vencimento TEXT,
    taxa_compra TEXT,
    taxa_venda TEXT,
    preco_compra REAL,
    preco_venda REAL,
    data_coleta TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cotacoes_tesouro_titulo_coleta
    ON cotacoes_tesouro(titulo, data_coleta);
