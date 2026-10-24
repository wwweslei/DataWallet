"""Scraper com histórico local para cotações públicas do Tesouro Direto."""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from sqlalchemy import DateTime, Float, Integer, String, URL, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from urllib3.util.retry import Retry

from config import DATABASE_PATH
from database import Database
from models.titulo import Titulo

logger = logging.getLogger(__name__)

URL_COTACOES = (
    "https://www.tesourodireto.com.br/"
    "produtos/dados-sobre-titulos/rendimento-dos-titulos"
)
USER_AGENT = (
    "Mozilla/5.0 (compatible; TesouroTracker/1.0; "
    "+https://www.tesourodireto.com.br/)"
)


class ScraperError(RuntimeError):
    """Indica falha HTTP ou mudança incompatível na página pública."""


class Base(DeclarativeBase):
    """Classe-base dos modelos SQLAlchemy do histórico do Tesouro."""


class CotacaoTesouroORM(Base):
    """Representa uma coleta imutável de preço e taxa de um título."""

    __tablename__ = "cotacoes_tesouro"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    titulo: Mapped[str] = mapped_column(String, nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String, nullable=False)
    vencimento: Mapped[str | None] = mapped_column(String, nullable=True)
    taxa_compra: Mapped[str | None] = mapped_column(String, nullable=True)
    taxa_venda: Mapped[str | None] = mapped_column(String, nullable=True)
    preco_compra: Mapped[float | None] = mapped_column(Float, nullable=True)
    preco_venda: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_coleta: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


@dataclass(frozen=True, slots=True)
class CotacaoTesouro:
    """Dados extraídos da página oficial em uma coleta."""

    titulo: str
    tipo: str
    vencimento: str | None
    taxa_compra: str | None
    taxa_venda: str | None
    preco_compra: float | None
    preco_venda: float | None
    data_coleta: datetime


@dataclass(frozen=True, slots=True)
class ResultadoAtualizacao:
    """Resultado da atualização, incluindo o fallback local quando necessário."""

    cotacoes: tuple[CotacaoTesouro, ...]
    usadas_cache: bool
    usou_fallback: bool
    erro: str | None = None


def _normalizar(valor: object) -> str:
    """Normaliza rótulos HTML para comparação sem acentos."""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(
        caractere for caractere in texto if not unicodedata.combining(caractere)
    )
    return re.sub(r"[^a-z0-9]+", " ", texto.casefold()).strip()


def _numero(valor: object) -> float | None:
    """Interpreta moeda, percentuais e números formatados no Brasil."""
    texto = str(valor).strip().replace("\u00a0", "").replace(" ", "")
    if not texto or texto in {"-", "—", "–"}:
        return None
    negativo = texto.startswith("(") and texto.endswith(")")
    texto = re.sub(r"[^0-9,.\-]", "", texto.strip("()"))
    if not texto or texto in {"-", ".", ","}:
        return None
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") > 1:
        partes = texto.split(".")
        texto = "".join(partes[:-1]) + "." + partes[-1]
    elif "." in texto and len(texto.rsplit(".", 1)[1]) == 3:
        texto = texto.replace(".", "")
    try:
        numero = float(texto)
    except ValueError:
        return None
    if not math.isfinite(numero):
        return None
    return -abs(numero) if negativo else numero


def _texto_taxa(valor: object) -> str | None:
    """Preserva a taxa como publicada, inclusive indexador e spread."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto if texto and texto not in {"-", "—", "–"} else None


def _vencimento(valor: object) -> str | None:
    """Normaliza datas e anos de vencimento para texto ISO."""
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        data = pd.to_datetime(texto, dayfirst=True, errors="coerce")
    except (TypeError, ValueError, OverflowError):
        data = pd.NaT
    if not pd.isna(data):
        return data.date().isoformat()
    ano = re.search(r"\b20\d{2}\b", texto)
    return ano.group(0) if ano else None


def categorizar_titulo(titulo: str) -> str:
    """Classifica o título em um dos tipos conhecidos do Tesouro Direto."""
    nome = _normalizar(titulo)
    if "selic" in nome:
        return "Tesouro Selic"
    if "prefixado" in nome:
        return "Tesouro Prefixado"
    if "ipca" in nome:
        return "Tesouro IPCA+"
    if (
        "renda mais" in nome
        or "rend mais" in nome
        or ("tesouro" in nome and "renda" in nome)
    ):
        return "Tesouro Renda+"
    return "Outros"


def _mapear_cabecalhos(cabecalhos: list[str]) -> dict[str, int]:
    """Mapeia cabeçalhos da tabela oficial para os campos de cotação."""
    aliases: dict[str, tuple[str, ...]] = {
        "titulo": ("titulo", "nome do titulo", "produto"),
        "vencimento": ("vencimento", "data de vencimento"),
        "taxa_compra": ("taxa de compra", "taxa compra", "compra taxa"),
        "taxa_venda": ("taxa de venda", "taxa venda", "venda taxa"),
        "preco_compra": (
            "preco unitario de compra",
            "preco unitario compra",
            "preco compra unitario",
            "preco de compra",
            "preco compra",
            "compra preco unitario",
        ),
        "preco_venda": (
            "preco unitario de venda",
            "preco unitario venda",
            "preco venda unitario",
            "preco de venda",
            "preco venda",
            "venda preco unitario",
        ),
    }
    resultado: dict[str, int] = {}
    normalizados = [_normalizar(cabecalho) for cabecalho in cabecalhos]
    for campo, opcoes in aliases.items():
        for alias in opcoes:
            for indice, cabecalho in enumerate(normalizados):
                if cabecalho == alias or (
                    len(alias) > 5 and alias in cabecalho
                ):
                    resultado[campo] = indice
                    break
            if campo in resultado:
                break
    return resultado


class TesouroDiretoScraper:
    """Obtém, registra e analisa cotações públicas do Tesouro Direto."""

    def __init__(
        self,
        database: Database | None = None,
        url: str = URL_COTACOES,
        timeout: tuple[float, float] = (5.0, 15.0),
        retries: int = 3,
        cache_ttl_seconds: int = 300,
    ) -> None:
        """Configura o endpoint, retries HTTP, banco SQLAlchemy e cache."""
        self.database = database or Database(DATABASE_PATH)
        self.url = url
        self.timeout = timeout
        self.cache_ttl_seconds = max(0, cache_ttl_seconds)
        self._cache_expira_em = 0.0
        self._cache_cotacoes: tuple[CotacaoTesouro, ...] = ()
        caminho_db = Path(self.database.database_path).resolve()
        self.engine: Engine = create_engine(
            URL.create("sqlite", database=str(caminho_db)),
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        politica_retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
        )
        self.session.mount("https://", HTTPAdapter(max_retries=politica_retry))
        self.session.mount("http://", HTTPAdapter(max_retries=politica_retry))

    @staticmethod
    def _parsear_tabela(
        soup: BeautifulSoup,
        coletada_em: datetime,
    ) -> list[CotacaoTesouro]:
        """Extrai linhas tabulares somente quando os campos necessários existem."""
        cotacoes: list[CotacaoTesouro] = []
        for tabela in soup.find_all("table"):
            linhas_html = tabela.find_all("tr")
            if len(linhas_html) < 2:
                continue
            linhas = [
                [celula.get_text(" ", strip=True) for celula in linha.find_all(["th", "td"])]
                for linha in linhas_html
            ]
            linhas = [linha for linha in linhas if linha]
            if len(linhas) < 2:
                continue
            cabecalho_indice = next(
                (
                    indice
                    for indice, linha in enumerate(linhas[:3])
                    if "titulo" in _mapear_cabecalhos(linha)
                    and (
                        "preco_compra" in _mapear_cabecalhos(linha)
                        or "taxa_compra" in _mapear_cabecalhos(linha)
                    )
                ),
                None,
            )
            if cabecalho_indice is None:
                continue
            colunas = _mapear_cabecalhos(linhas[cabecalho_indice])
            for linha in linhas[cabecalho_indice + 1 :]:
                titulo = (
                    linha[colunas["titulo"]].strip()
                    if colunas.get("titulo", len(linha)) < len(linha)
                    else ""
                )
                if not titulo or _normalizar(titulo) in {"total", "mercado fechado"}:
                    continue

                def obter_indice(campo: str) -> str | None:
                    """Retorna o texto da célula para um campo detectado."""
                    indice = colunas.get(campo)
                    return linha[indice] if indice is not None and indice < len(linha) else None

                taxa_compra = _texto_taxa(obter_indice("taxa_compra"))
                taxa_venda = _texto_taxa(obter_indice("taxa_venda"))
                preco_compra = _numero(obter_indice("preco_compra"))
                preco_venda = _numero(obter_indice("preco_venda"))
                if all(
                    valor is None
                    for valor in (taxa_compra, taxa_venda, preco_compra, preco_venda)
                ):
                    continue
                cotacoes.append(
                    CotacaoTesouro(
                        titulo=titulo,
                        tipo=categorizar_titulo(titulo),
                        vencimento=_vencimento(obter_indice("vencimento") or titulo),
                        taxa_compra=taxa_compra,
                        taxa_venda=taxa_venda,
                        preco_compra=preco_compra,
                        preco_venda=preco_venda,
                        data_coleta=coletada_em,
                    )
                )
        return cotacoes

    @staticmethod
    def _parsear_rotulos(soup: BeautifulSoup, coletada_em: datetime) -> list[CotacaoTesouro]:
        """Extrai cards HTML que apresentam rótulos e valores como pares."""
        cotacoes: list[CotacaoTesouro] = []
        for elemento in soup.select("[data-titulo], .titulo-card, .produto-card"):
            titulo = elemento.get("data-titulo")
            titulo = str(titulo or "").strip()
            if not titulo:
                cabecalho = elemento.find(["h2", "h3", "h4"])
                titulo = cabecalho.get_text(" ", strip=True) if cabecalho else ""
            if not titulo:
                continue
            rotulos: dict[str, str] = {}
            for item in elemento.find_all(["dt", "th", "label", "[data-label]"]):
                rotulo = item.get("data-label") or item.get_text(" ", strip=True)
                valor = item.find_next(["dd", "td"])
                if valor is None:
                    valor = item.find_next(attrs={"data-value": True})
                if valor is not None:
                    rotulos[_normalizar(rotulo)] = valor.get_text(" ", strip=True)
            texto = " ".join(elemento.stripped_strings)
            for campo, termos in {
                "taxa_compra": ("taxa de compra", "taxa compra"),
                "taxa_venda": ("taxa de venda", "taxa venda"),
                "preco_compra": ("preco unitario de compra", "preco de compra"),
                "preco_venda": ("preco unitario de venda", "preco de venda"),
            }.items():
                if any(_normalizar(termo) in _normalizar(texto) for termo in termos):
                    match = re.search(
                        r"(?i)"
                        + "|".join(map(re.escape, termos))
                        + r"\s*:?\s*((?:(?:SELIC|IPCA)\s*\+\s*)?[\d.,]+\s*%?)",
                        texto,
                    )
                    if match:
                        rotulos[campo] = match.group(1)

            def localizar(termos: tuple[str, ...]) -> str | None:
                for rotulo, valor in rotulos.items():
                    if any(_normalizar(termo) in rotulo for termo in termos):
                        resultado = _texto_taxa(valor)
                        if resultado is not None:
                            return resultado
                return None

            cotacoes.append(
                CotacaoTesouro(
                    titulo=titulo,
                    tipo=categorizar_titulo(titulo),
                    vencimento=_vencimento(titulo),
                    taxa_compra=localizar(("taxa de compra", "taxa compra")),
                    taxa_venda=localizar(("taxa de venda", "taxa venda")),
                    preco_compra=localizar(
                        ("preco unitario de compra", "preco de compra")
                    ),
                    preco_venda=localizar(
                        ("preco unitario de venda", "preco de venda")
                    ),
                    data_coleta=coletada_em,
                )
            )
        return [
            cotacao
            for cotacao in cotacoes
            if any(
                valor is not None
                for valor in (
                    cotacao.taxa_compra,
                    cotacao.taxa_venda,
                    cotacao.preco_compra,
                    cotacao.preco_venda,
                )
            )
        ]

    def _baixar_e_parsear(self) -> tuple[CotacaoTesouro, ...]:
        """Consulta a página pública com timeout e reporta mudanças do HTML."""
        try:
            resposta = self.session.get(self.url, timeout=self.timeout)
            resposta.raise_for_status()
        except requests.RequestException as erro:
            logger.exception("Falha ao consultar página pública do Tesouro Direto.")
            raise ScraperError("Não foi possível acessar a página do Tesouro Direto.") from erro

        soup = BeautifulSoup(resposta.content, "html.parser")
        coletada_em = datetime.now().replace(microsecond=0)
        cotacoes = self._parsear_tabela(soup, coletada_em)
        if not cotacoes:
            cotacoes = self._parsear_rotulos(soup, coletada_em)
        if not cotacoes:
            texto_pagina = _normalizar(soup.get_text(" ", strip=True))
            if "mercado fechado" in texto_pagina:
                motivo = "O mercado está fechado e a página não publicou preços atuais."
            else:
                motivo = "Não foram encontrados campos de cotação no HTML da página."
            logger.warning("Não foi possível extrair cotações: %s", motivo)
            raise ScraperError(
                f"{motivo} O formato da página pode ter mudado; usando o histórico local."
            )
        unicas: dict[str, CotacaoTesouro] = {}
        for cotacao in cotacoes:
            unicas[_normalizar(cotacao.titulo)] = cotacao
        logger.info("Cotações extraídas da página oficial: %d.", len(unicas))
        return tuple(unicas.values())

    def obter_titulos(self, forcar_atualizacao: bool = False) -> list[CotacaoTesouro]:
        """Obtém os títulos públicos, usando cache em memória durante seu TTL."""
        if (
            not forcar_atualizacao
            and self._cache_cotacoes
            and monotonic() < self._cache_expira_em
        ):
            logger.info("Usando cache local em memória das cotações.")
            return list(self._cache_cotacoes)
        cotacoes = self._baixar_e_parsear()
        self._cache_cotacoes = cotacoes
        self._cache_expira_em = monotonic() + self.cache_ttl_seconds
        return list(cotacoes)

    def salvar_cotacoes(self, cotacoes: list[CotacaoTesouro]) -> int:
        """Insere snapshots sem sobrescrever coletas anteriores."""
        if not cotacoes:
            return 0
        try:
            with self._sessions.begin() as sessao:
                sessao.add_all(
                    [
                        CotacaoTesouroORM(
                            titulo=cotacao.titulo,
                            tipo=cotacao.tipo,
                            vencimento=cotacao.vencimento,
                            taxa_compra=cotacao.taxa_compra,
                            taxa_venda=cotacao.taxa_venda,
                            preco_compra=cotacao.preco_compra,
                            preco_venda=cotacao.preco_venda,
                            data_coleta=cotacao.data_coleta,
                        )
                        for cotacao in cotacoes
                    ]
                )
            return len(cotacoes)
        except SQLAlchemyError as erro:
            logger.exception("Falha ao persistir snapshots de cotações.")
            raise ScraperError("Não foi possível salvar as cotações no SQLite.") from erro

    def obter_historico(self, titulo: str) -> pd.DataFrame:
        """Retorna snapshots imutáveis de um título em ordem cronológica."""
        consulta = (
            select(CotacaoTesouroORM)
            .where(CotacaoTesouroORM.titulo == titulo)
            .order_by(CotacaoTesouroORM.data_coleta)
        )
        try:
            with self._sessions() as sessao:
                linhas = sessao.scalars(consulta).all()
                return pd.DataFrame(
                    [
                        {
                            "titulo": linha.titulo,
                            "tipo": linha.tipo,
                            "vencimento": linha.vencimento,
                            "taxa_compra": _texto_taxa(linha.taxa_compra),
                            "taxa_venda": _texto_taxa(linha.taxa_venda),
                            "preco_compra": linha.preco_compra,
                            "preco_venda": linha.preco_venda,
                            "data_coleta": linha.data_coleta,
                        }
                        for linha in linhas
                    ]
                )
        except SQLAlchemyError as erro:
            logger.exception("Falha ao consultar histórico de %s.", titulo)
            raise ScraperError("Não foi possível consultar o histórico local.") from erro

    def obter_ultimas_cotacoes(self) -> list[CotacaoTesouro]:
        """Obtém a coleta mais recente para cada título salvo."""
        consulta = select(CotacaoTesouroORM).order_by(
            CotacaoTesouroORM.data_coleta.desc(), CotacaoTesouroORM.id.desc()
        )
        with self._sessions() as sessao:
            linhas = sessao.scalars(consulta).all()
        ultimas: dict[str, CotacaoTesouro] = {}
        for linha in linhas:
            chave = _normalizar(linha.titulo)
            if chave not in ultimas:
                ultimas[chave] = CotacaoTesouro(
                    titulo=linha.titulo,
                    tipo=linha.tipo,
                    vencimento=linha.vencimento,
                    taxa_compra=_texto_taxa(linha.taxa_compra),
                    taxa_venda=_texto_taxa(linha.taxa_venda),
                    preco_compra=linha.preco_compra,
                    preco_venda=linha.preco_venda,
                    data_coleta=linha.data_coleta,
                )
        return list(ultimas.values())

    def atualizar_cotacoes(self) -> ResultadoAtualizacao:
        """Scrapa e persiste preços; em falhas devolve a última coleta local."""
        try:
            cotacoes = self.obter_titulos(forcar_atualizacao=True)
            coletadas_agora = [
                replace(cotacao, data_coleta=datetime.now().replace(microsecond=0))
                for cotacao in cotacoes
            ]
            self.salvar_cotacoes(coletadas_agora)
            return ResultadoAtualizacao(tuple(coletadas_agora), False, False)
        except ScraperError as erro:
            logger.warning("Atualização online falhou; usando fallback local: %s", erro)
            return ResultadoAtualizacao(
                tuple(self.obter_ultimas_cotacoes()),
                False,
                True,
                str(erro),
            )

    def obter_variacoes(self, titulo: str) -> dict[str, float | None]:
        """Calcula retornos aproximados em relação a snapshots anteriores."""
        historico = self.obter_historico(titulo)
        coluna_preco = "preco_venda"
        if historico.empty:
            return {periodo: None for periodo in ("diaria", "semanal", "mensal", "anual")}
        if historico[coluna_preco].dropna().empty:
            coluna_preco = "preco_compra"
        dados = historico.dropna(subset=[coluna_preco]).copy()
        if dados.empty:
            return {periodo: None for periodo in ("diaria", "semanal", "mensal", "anual")}
        dados["data_coleta"] = pd.to_datetime(dados["data_coleta"])
        dados = dados.sort_values("data_coleta").drop_duplicates(
            subset=["data_coleta"], keep="last"
        )
        atual = float(dados.iloc[-1][coluna_preco])
        data_atual = dados.iloc[-1]["data_coleta"]
        variacoes: dict[str, float | None] = {}
        for nome, dias in (
            ("diaria", 1),
            ("semanal", 7),
            ("mensal", 30),
            ("anual", 365),
        ):
            corte = data_atual - pd.Timedelta(days=dias)
            anteriores = dados[dados["data_coleta"] <= corte]
            if anteriores.empty:
                variacoes[nome] = None
            else:
                anterior = float(anteriores.iloc[-1][coluna_preco])
                variacoes[nome] = (atual / anterior - 1) * 100 if anterior else None
        return variacoes

    def obter_tendencia(self, titulo: str) -> dict[str, float | str | None]:
        """Calcula médias móveis sobre o último preço de cada dia de coleta."""
        historico = self.obter_historico(titulo)
        if historico.empty:
            return {
                "mm7": None,
                "mm21": None,
                "mm50": None,
                "tendencia": "Dados insuficientes",
            }
        coluna = "preco_venda"
        if historico[coluna].dropna().empty:
            coluna = "preco_compra"
        dados = historico.dropna(subset=[coluna]).copy()
        if dados.empty:
            return {
                "mm7": None,
                "mm21": None,
                "mm50": None,
                "tendencia": "Dados insuficientes",
            }
        dados["data_coleta"] = pd.to_datetime(dados["data_coleta"])
        serie = (
            dados.sort_values("data_coleta")
            .set_index("data_coleta")[coluna]
            .resample("D")
            .last()
            .dropna()
        )
        medias = {
            f"mm{periodo}": float(serie.rolling(periodo, min_periods=periodo).mean().iloc[-1])
            if len(serie) >= periodo
            else None
            for periodo in (7, 21, 50)
        }
        mm7, mm21 = medias["mm7"], medias["mm21"]
        if mm7 is None or mm21 is None:
            tendencia = "Dados insuficientes"
        elif mm7 > mm21:
            tendencia = "ALTA"
        elif mm7 < mm21:
            tendencia = "BAIXA"
        else:
            tendencia = "LATERAL"
        return {**medias, "tendencia": tendencia}

    def comparar_carteira(self, cotacoes: list[CotacaoTesouro]) -> pd.DataFrame:
        """Compara preços coletados com posições cadastradas manualmente."""
        titulos = self.database.listar_titulos()
        if not titulos or not cotacoes:
            return pd.DataFrame()
        linhas: list[dict[str, Any]] = []
        for titulo in titulos:
            cotacao = self._localizar_cotacao(titulo, cotacoes)
            if cotacao is None:
                linhas.append(
                    {
                        "Título": titulo.nome,
                        "Situação": "Sem cotação correspondente",
                    }
                )
                continue
            preco_atual = cotacao.preco_venda or cotacao.preco_compra
            if preco_atual is None:
                linhas.append(
                    {
                        "Título": titulo.nome,
                        "Cotação": cotacao.titulo,
                        "Situação": "Preço de venda indisponível",
                    }
                )
                continue
            variacoes = self.obter_variacoes(cotacao.titulo)
            investimento = titulo.valor_investido
            atual = titulo.quantidade * preco_atual
            linha: dict[str, Any] = {
                "Título": titulo.nome,
                "Cotação": cotacao.titulo,
                "Investido (R$)": investimento,
                "Atual (R$)": atual,
                "Lucro / prejuízo (R$)": atual - investimento,
                "Rentabilidade (%)": (atual / investimento - 1) * 100
                if investimento
                else None,
                "Preço atual (R$)": preco_atual,
                "Situação": "Atualizado",
            }
            for periodo in ("diaria", "semanal", "mensal", "anual"):
                variacao = variacoes[periodo]
                ganho = (
                    titulo.quantidade
                    * preco_atual
                    * variacao
                    / (100 + variacao)
                    if variacao is not None and variacao != -100
                    else None
                )
                rotulo = periodo.capitalize()
                linha[f"Ganho {rotulo} (R$)"] = ganho
                linha[f"Variação {rotulo} (%)"] = variacao
            linhas.append(linha)
        return pd.DataFrame(linhas)

    @staticmethod
    def _localizar_cotacao(
        titulo: Titulo,
        cotacoes: list[CotacaoTesouro],
    ) -> CotacaoTesouro | None:
        """Associa título cadastrado ao nome e vencimento da cotação."""
        nome_cadastrado = _normalizar(titulo.nome)
        nome_tipo = _normalizar(titulo.tipo)
        ano = re.search(r"\b20\d{2}\b", titulo.nome)
        for cotacao in cotacoes:
            nome_cotacao = _normalizar(cotacao.titulo)
            if nome_cadastrado == nome_cotacao:
                return cotacao
            mesmo_ano = not ano or ano.group(0) in nome_cotacao or (
                cotacao.vencimento and ano.group(0) in cotacao.vencimento
            )
            tipo_cotacao = _normalizar(cotacao.tipo)
            mesmo_tipo = nome_tipo in nome_cotacao or tipo_cotacao in nome_tipo
            if mesmo_ano and mesmo_tipo:
                return cotacao
        return None
