"""Persistência SQLite para títulos e histórico de preços."""

from __future__ import annotations

import logging
import math
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from config import DATABASE_PATH, SCHEMA_PATH
from models.titulo import Titulo
from services.consolidador import PosicaoConsolidada

logger = logging.getLogger(__name__)


class Database:
    """Gerencia conexões e operações persistentes do aplicativo."""

    def __init__(
        self,
        database_path: Path = DATABASE_PATH,
        schema_path: Path = SCHEMA_PATH,
    ) -> None:
        """Inicializa o banco e cria as tabelas necessárias."""
        self.database_path = Path(database_path)
        self.schema_path = Path(schema_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._conectar() as conexao:
                conexao.executescript(self.schema_path.read_text(encoding="utf-8"))
                colunas_ativos = {
                    row["name"]
                    for row in conexao.execute("PRAGMA table_info(ativos)")
                }
                if "vencimento" not in colunas_ativos:
                    conexao.execute("ALTER TABLE ativos ADD COLUMN vencimento TEXT")
        except (OSError, sqlite3.Error) as erro:
            logger.exception("Não foi possível inicializar o banco SQLite.")
            raise RuntimeError("Falha ao inicializar o banco de dados.") from erro

    @contextmanager
    def _conectar(self) -> Iterator[sqlite3.Connection]:
        """Abre, confirma e fecha uma conexão SQLite."""
        conexao = sqlite3.connect(self.database_path, timeout=10)
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA foreign_keys = ON")
        try:
            yield conexao
            conexao.commit()
        except Exception:
            conexao.rollback()
            raise
        finally:
            conexao.close()

    @staticmethod
    def _titulo(row: sqlite3.Row) -> Titulo:
        """Converte uma linha SQLite em um título tipado."""
        return Titulo(
            id=int(row["id"]),
            nome=str(row["nome"]),
            tipo=str(row["tipo"]),
            data_compra=date.fromisoformat(str(row["data_compra"])),
            quantidade=float(row["quantidade"]),
            preco_compra=float(row["preco_compra"]),
            preco_atual=float(row["preco_atual"])
            if row["preco_atual"] is not None
            else None,
        )

    def listar_titulos(self) -> list[Titulo]:
        """Lista posições ordenadas pela data da compra mais recente."""
        consulta = """
            SELECT t.id, t.nome, t.tipo, t.data_compra, t.quantidade,
                   t.preco_compra, COALESCE(
                       (SELECT h.preco FROM historico_precos h
                        WHERE h.titulo_id = t.id ORDER BY h.data DESC LIMIT 1),
                       t.preco_atual
                   ) AS preco_atual
            FROM titulos t
            ORDER BY t.data_compra DESC, t.nome
        """
        try:
            with self._conectar() as conexao:
                return [self._titulo(row) for row in conexao.execute(consulta)]
        except sqlite3.Error as erro:
            logger.exception("Erro ao listar títulos.")
            raise RuntimeError("Não foi possível carregar a carteira.") from erro

    def salvar_titulo(self, titulo: Titulo) -> int:
        """Insere um título novo ou atualiza uma posição existente."""
        try:
            with self._conectar() as conexao:
                if titulo.id is None:
                    cursor = conexao.execute(
                        """INSERT INTO titulos
                           (nome, tipo, data_compra, quantidade, preco_compra, preco_atual)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            titulo.nome.strip(),
                            titulo.tipo,
                            titulo.data_compra.isoformat(),
                            titulo.quantidade,
                            titulo.preco_compra,
                            titulo.preco_atual,
                        ),
                    )
                    titulo_id = int(cursor.lastrowid)
                else:
                    cursor = conexao.execute(
                        """UPDATE titulos SET nome = ?, tipo = ?, data_compra = ?,
                           quantidade = ?, preco_compra = ?, preco_atual = ?,
                           atualizado_em = CURRENT_TIMESTAMP WHERE id = ?""",
                        (
                            titulo.nome.strip(),
                            titulo.tipo,
                            titulo.data_compra.isoformat(),
                            titulo.quantidade,
                            titulo.preco_compra,
                            titulo.preco_atual,
                            titulo.id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise LookupError(f"Título {titulo.id} não encontrado.")
                    titulo_id = titulo.id
                if titulo.preco_atual is not None:
                    self._registrar_preco(
                        conexao,
                        titulo_id,
                        date.today(),
                        titulo.preco_atual,
                        "manual",
                    )
                return titulo_id
        except (sqlite3.Error, OSError) as erro:
            logger.exception("Erro ao salvar título.")
            raise RuntimeError("Não foi possível salvar o título.") from erro

    def remover_titulo(self, titulo_id: int) -> None:
        """Remove uma posição e seu histórico associado."""
        try:
            with self._conectar() as conexao:
                cursor = conexao.execute("DELETE FROM titulos WHERE id = ?", (titulo_id,))
                if cursor.rowcount == 0:
                    raise LookupError(f"Título {titulo_id} não encontrado.")
        except sqlite3.Error as erro:
            logger.exception("Erro ao remover título.")
            raise RuntimeError("Não foi possível remover o título.") from erro

    @staticmethod
    def _registrar_preco(
        conexao: sqlite3.Connection,
        titulo_id: int,
        data: date,
        preco: float,
        fonte: str,
    ) -> None:
        """Insere ou substitui a cotação diária da posição."""
        conexao.execute(
            """INSERT INTO historico_precos (titulo_id, data, preco, fonte)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(titulo_id, data) DO UPDATE SET
                   preco = excluded.preco, fonte = excluded.fonte""",
            (titulo_id, data.isoformat(), preco, fonte),
        )
        conexao.execute(
            """UPDATE titulos SET preco_atual = ?, atualizado_em = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (preco, titulo_id),
        )

    def registrar_preco(
        self, titulo_id: int, data: date, preco: float, fonte: str = "manual"
    ) -> None:
        """Registra uma cotação diária e atualiza o valor corrente do título."""
        if not math.isfinite(preco) or preco <= 0:
            raise ValueError("O preço deve ser um número finito maior que zero.")
        try:
            with self._conectar() as conexao:
                existe = conexao.execute(
                    "SELECT 1 FROM titulos WHERE id = ?", (titulo_id,)
                ).fetchone()
                if existe is None:
                    raise LookupError(f"Título {titulo_id} não encontrado.")
                self._registrar_preco(conexao, titulo_id, data, preco, fonte)
        except sqlite3.Error as erro:
            logger.exception("Erro ao registrar cotação.")
            raise RuntimeError("Não foi possível registrar a cotação.") from erro

    def obter_historico(self, titulo_id: int) -> list[dict[str, Any]]:
        """Obtém o histórico de preços em ordem cronológica."""
        try:
            with self._conectar() as conexao:
                rows = conexao.execute(
                    """SELECT data, preco, fonte FROM historico_precos
                       WHERE titulo_id = ? ORDER BY data""",
                    (titulo_id,),
                ).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as erro:
            logger.exception("Erro ao carregar histórico.")
            raise RuntimeError("Não foi possível carregar o histórico.") from erro

    def listar_importacoes(self) -> list[dict[str, Any]]:
        """Lista arquivos importados, mais recentes primeiro."""
        try:
            with self._conectar() as conexao:
                rows = conexao.execute(
                    """SELECT arquivo, data_importacao, linhas_processadas,
                              status, detalhes
                       FROM historico_importacoes
                       ORDER BY data_importacao DESC, arquivo"""
                ).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as erro:
            logger.exception("Erro ao listar histórico de importações.")
            raise RuntimeError("Não foi possível carregar as importações.") from erro

    def obter_importacao_por_hash(self, arquivo_hash: str) -> dict[str, Any] | None:
        """Obtém o registro de importação correspondente ao hash do arquivo."""
        try:
            with self._conectar() as conexao:
                row = conexao.execute(
                    """SELECT arquivo, arquivo_hash, linhas_processadas, status
                       FROM historico_importacoes WHERE arquivo_hash = ?""",
                    (arquivo_hash,),
                ).fetchone()
                return dict(row) if row is not None else None
        except sqlite3.Error as erro:
            logger.exception("Erro ao consultar arquivo importado.")
            raise RuntimeError("Não foi possível verificar o arquivo.") from erro

    def registrar_importacao(
        self,
        arquivo: str,
        arquivo_hash: str,
        status: str,
        linhas_processadas: int = 0,
        detalhes: str | None = None,
    ) -> None:
        """Registra uma tentativa ou falha de importação por hash de conteúdo."""
        try:
            with self._conectar() as conexao:
                conexao.execute(
                    """INSERT INTO historico_importacoes
                       (arquivo, arquivo_hash, linhas_processadas, status, detalhes)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(arquivo_hash) DO UPDATE SET
                           arquivo = excluded.arquivo,
                           data_importacao = CURRENT_TIMESTAMP,
                           linhas_processadas = excluded.linhas_processadas,
                           status = excluded.status,
                           detalhes = excluded.detalhes""",
                    (
                        arquivo,
                        arquivo_hash,
                        linhas_processadas,
                        status,
                        detalhes,
                    ),
                )
        except sqlite3.Error as erro:
            logger.exception("Erro ao registrar tentativa de importação.")
            raise RuntimeError("Não foi possível registrar a importação.") from erro

    def iniciar_importacao(self, arquivo: str, arquivo_hash: str) -> bool:
        """Reserva arquivo para impedir execuções simultâneas duplicadas."""
        try:
            with self._conectar() as conexao:
                cursor = conexao.execute(
                    """INSERT INTO historico_importacoes
                       (arquivo, arquivo_hash, linhas_processadas, status)
                       VALUES (?, ?, 0, 'processando')
                       ON CONFLICT(arquivo_hash) DO UPDATE SET
                           arquivo = excluded.arquivo,
                           data_importacao = CURRENT_TIMESTAMP,
                           linhas_processadas = 0,
                           status = 'processando',
                           detalhes = NULL
                       WHERE historico_importacoes.status != 'sucesso'
                         AND (
                             historico_importacoes.status != 'processando'
                             OR historico_importacoes.data_importacao
                                <= datetime('now', '-1 hour')
                         )""",
                    (arquivo, arquivo_hash),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as erro:
            logger.exception("Erro ao reservar arquivo para importação.")
            raise RuntimeError("Não foi possível reservar o arquivo.") from erro

    def salvar_importacoes_consolidadas(
        self,
        posicoes: list[PosicaoConsolidada],
        arquivos: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Salva posições e conclui importações em uma única transação."""
        novos = 0
        atualizados = 0
        try:
            with self._conectar() as conexao:
                for posicao in posicoes:
                    existente = conexao.execute(
                        "SELECT id FROM ativos WHERE ticker = ?",
                        (posicao.ticker,),
                    ).fetchone()
                    if existente is None:
                        conexao.execute(
                            """INSERT INTO ativos
                               (ticker, nome, categoria, quantidade, preco_medio,
                                valor_investido, vencimento, data_atualizacao)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                posicao.ticker,
                                posicao.nome,
                                posicao.categoria,
                                posicao.quantidade,
                                posicao.preco_medio,
                                posicao.valor_investido,
                                posicao.vencimento,
                                date.today().isoformat(),
                            ),
                        )
                        novos += 1
                    else:
                        conexao.execute(
                            """UPDATE ativos SET nome = ?, categoria = ?,
                               vencimento = COALESCE(?, vencimento),
                               quantidade = quantidade + ?,
                               valor_investido = valor_investido + ?,
                               preco_medio = (valor_investido + ?) /
                                   (quantidade + ?),
                               data_atualizacao = ?
                               WHERE ticker = ?""",
                            (
                                posicao.nome,
                                posicao.categoria,
                                posicao.vencimento,
                                posicao.quantidade,
                                posicao.valor_investido,
                                posicao.valor_investido,
                                posicao.quantidade,
                                date.today().isoformat(),
                                posicao.ticker,
                            ),
                        )
                        atualizados += 1
                for arquivo in arquivos:
                    conexao.execute(
                        """INSERT INTO historico_importacoes
                           (arquivo, arquivo_hash, linhas_processadas, status, detalhes)
                           VALUES (?, ?, ?, 'sucesso', NULL)
                           ON CONFLICT(arquivo_hash) DO UPDATE SET
                               arquivo = excluded.arquivo,
                               data_importacao = CURRENT_TIMESTAMP,
                               linhas_processadas = excluded.linhas_processadas,
                               status = 'sucesso',
                               detalhes = NULL""",
                        (
                            arquivo["arquivo"],
                            arquivo["arquivo_hash"],
                            arquivo["linhas_processadas"],
                        ),
                    )
            return novos, atualizados
        except sqlite3.Error as erro:
            logger.exception("Erro ao salvar posições importadas.")
            raise RuntimeError("Não foi possível salvar as posições importadas.") from erro

    def listar_ativos(self) -> list[dict[str, Any]]:
        """Lista posições consolidadas importadas de arquivos XLSX."""
        try:
            with self._conectar() as conexao:
                rows = conexao.execute(
                    """SELECT ticker, nome, categoria, quantidade, preco_medio,
                              valor_investido, vencimento, data_atualizacao
                       FROM ativos ORDER BY ticker"""
                ).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as erro:
            logger.exception("Erro ao listar ativos importados.")
            raise RuntimeError("Não foi possível carregar os ativos importados.") from erro
