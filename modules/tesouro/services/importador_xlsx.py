"""Importação idempotente de posições de arquivos Excel de corretoras."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import ParseError

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException

from config import BASE_DIR
from database.repository import Database
from modules.tesouro.services.consolidador import (
    PosicaoConsolidada,
    PosicaoImportada,
    consolidar_posicoes,
    normalizar_ticker,
)
from modules.tesouro.services.mapeador_colunas import (
    mapear_colunas,
    normalizar_texto,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]
Aliases = dict[str, int]


@dataclass(slots=True)
class RelatorioImportacao:
    """Resumo dos resultados e erros de uma execução do importador."""

    arquivos_processados: int = 0
    arquivos_ignorados: int = 0
    ativos_encontrados: int = 0
    novos_ativos: int = 0
    ativos_atualizados: int = 0
    valor_total_importado: float = 0.0
    erros: list[str] = field(default_factory=list)
    posicoes: list[PosicaoConsolidada] = field(default_factory=list)

    def formatar(self) -> str:
        """Gera o relatório final no formato solicitado."""
        return (
            "===================================\n"
            "IMPORTAÇÃO CONCLUÍDA\n"
            "===================================\n"
            f"Arquivos processados: {self.arquivos_processados}\n"
            "Arquivos ignorados (já importados/em processamento): "
            f"{self.arquivos_ignorados}\n"
            f"Ativos encontrados: {self.ativos_encontrados}\n"
            f"Novos ativos: {self.novos_ativos}\n"
            f"Ativos atualizados: {self.ativos_atualizados}\n"
            f"Valor total importado: R$ {self.valor_total_importado:,.2f}"
            .replace(",", "_")
            .replace(".", ",")
            .replace("_", ".")
        )


@dataclass(frozen=True, slots=True)
class _ArquivoProcessado:
    """Dados de arquivo validado prontos para gravação transacional."""

    caminho: Path
    arquivo_hash: str
    linhas: int
    posicoes: tuple[PosicaoImportada, ...]


def _numero(valor: object) -> float | None:
    """Interpreta números nativos, decimais brasileiros e formatos com moeda."""
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
        return numero if math.isfinite(numero) else None
    texto = str(valor).strip()
    if not texto or normalizar_texto(texto) in {"", "nan", "none", "n a"}:
        return None
    negativo = texto.startswith("(") and texto.endswith(")")
    texto = texto.strip("()").replace("\u00a0", "").replace(" ", "")
    texto = re.sub(r"[^0-9,.\-]", "", texto)
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
    if negativo:
        numero = -abs(numero)
    return numero if math.isfinite(numero) else None


def _data_valida(valor: object) -> date | None:
    """Converte uma célula em data sem interromper a importação da posição."""
    if valor is None or pd.isna(valor):
        return None
    try:
        data = pd.to_datetime(valor, dayfirst=True, errors="coerce")
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(data):
        return None
    return data.date()


def _texto_celula(valor: object) -> str:
    """Retorna texto limpo para uso como identificação de ativo."""
    if valor is None or pd.isna(valor):
        return ""
    return re.sub(r"\s+", " ", str(valor)).strip()


def _informacoes_titulo(frame: pd.DataFrame, caminho: Path) -> tuple[str, str | None]:
    """Extrai descrição e vencimento do cabeçalho de extratos do Tesouro."""
    nome = ""
    vencimento: str | None = None
    for valor in frame.head(20).to_numpy().flat:
        texto = _texto_celula(valor)
        texto_normalizado = normalizar_texto(texto)
        if not nome and "extrato analitico" in texto_normalizado:
            partes = re.split(r"\s+-\s+", texto, maxsplit=1)
            nome = partes[-1].strip() if len(partes) > 1 else texto
        if vencimento is None and texto_normalizado.startswith("vencimento"):
            data = _data_valida(texto.split(":", maxsplit=1)[-1].strip())
            if data is not None:
                vencimento = data.isoformat()
    if not nome:
        nome = caminho.stem.strip()
    if vencimento and not re.search(r"\b20\d{2}\b", nome):
        nome = f"{nome} {vencimento[:4]}"
    return nome, vencimento


def _categoria(nome: str, ticker: str, categoria_planilha: str) -> str:
    """Classifica Tesouro Direto e classes de renda variável conhecidas."""
    texto = normalizar_texto(f"{nome} {ticker} {categoria_planilha}")
    if "selic" in texto:
        return "Tesouro Selic"
    if "ipca" in texto:
        return "Tesouro IPCA+"
    if "prefixado" in texto:
        return "Tesouro Prefixado"
    if (
        "renda mais" in texto
        or "rend mais" in texto
        or ("tesouro" in texto and "renda" in texto)
    ):
        return "Tesouro RendA+"
    if "tesouro" in texto:
        return "Tesouro Direto"
    if any(termo in texto for termo in ("bdr", "brazilian depositary")):
        return "BDR"
    if any(termo in texto for termo in ("fii", "fundo imobiliario", "imobiliario")):
        return "FII"
    if any(termo in texto for termo in ("etf", "fundo de indice")):
        return "ETF"
    if any(termo in texto for termo in ("acao", "acoes", "renda variavel")):
        return "Ação"
    if re.fullmatch(r"[a-z]{4}\d{1,2}", normalizar_texto(ticker).replace(" ", "")):
        return "Ação"
    return categoria_planilha.strip() or "Outro"


def _cabecalho_planilha(frame: pd.DataFrame) -> tuple[int, Aliases] | None:
    """Encontra a linha de cabeçalho, incluindo cabeçalhos em duas linhas."""
    limite = min(len(frame), 40)
    for indice in range(limite):
        linha_atual = frame.iloc[indice].tolist()
        linha_seguinte = (
            frame.iloc[indice + 1].tolist()
            if indice + 1 < len(frame)
            else [""] * len(linha_atual)
        )
        cabecalho = [
            " ".join(
                texto
                for texto in (_texto_celula(atual), _texto_celula(seguinte))
                if texto
            )
            for atual, seguinte in zip(linha_atual, linha_seguinte)
        ]
        mapeamento = mapear_colunas(cabecalho)
        tem_preco = any(
            campo in mapeamento
            for campo in ("preco_medio", "preco_atual", "valor_investido")
        )
        if "quantidade" in mapeamento and tem_preco:
            return indice, mapeamento
    return None


def _posicoes_planilha(
    frame: pd.DataFrame,
    caminho: Path,
    nome_aba: str,
) -> list[PosicaoImportada]:
    """Lê linhas de posições de uma aba com cabeçalhos detectados."""
    cabecalho = _cabecalho_planilha(frame)
    if cabecalho is None:
        logger.warning(
            "Planilha sem colunas de quantidade e preço/valor: %s (%s)",
            caminho.name,
            nome_aba,
        )
        return []
    linha_cabecalho, colunas = cabecalho
    titulo_extrato, vencimento = _informacoes_titulo(frame, caminho)
    linhas: list[PosicaoImportada] = []
    indice_ticker = colunas.get("ticker")
    indice_nome = colunas.get("nome")
    for indice_linha in range(linha_cabecalho + 1, len(frame)):
        valores = frame.iloc[indice_linha].tolist()
        identificador_linha = " ".join(
            _texto_celula(valores[indice])
            for indice in (indice_ticker, indice_nome)
            if indice is not None and indice < len(valores)
        )
        if normalizar_texto(identificador_linha).strip() in {"total", "totais"}:
            break
        if any(
            normalizar_texto(_texto_celula(valor)) in {"total", "totais"}
            for valor in valores[:3]
        ):
            break
        quantidade = _valor_coluna(valores, colunas.get("quantidade"))
        if quantidade is None:
            continue
        if quantidade <= 0:
            logger.warning(
                "Quantidade não positiva ignorada em %s, aba %s, linha %s.",
                caminho.name,
                nome_aba,
                indice_linha + 1,
            )
            continue

        ticker_origem = _texto_coluna(valores, indice_ticker)
        nome_origem = _texto_coluna(valores, indice_nome)
        nome = nome_origem or ticker_origem or titulo_extrato
        ticker = ticker_origem or nome_origem or titulo_extrato
        if vencimento and normalizar_texto(nome) == normalizar_texto(titulo_extrato):
            nome = f"{nome} (vencimento {vencimento})"
            if not ticker_origem and not re.search(r"\b20\d{2}\b", ticker):
                ticker = f"{ticker} {vencimento[:4]}"
        try:
            ticker = normalizar_ticker(ticker)
        except ValueError:
            logger.warning(
                "Linha sem identificador ignorada em %s, aba %s, linha %s.",
                caminho.name,
                nome_aba,
                indice_linha + 1,
            )
            continue
        nome = nome.strip() or ticker

        categoria_origem = _texto_coluna(valores, colunas.get("categoria"))
        categoria = _categoria(nome, ticker, categoria_origem)
        vencimento_posicao = (
            _data_coluna(valores, colunas.get("vencimento")) or vencimento
        )
        valor_investido = _valor_coluna(valores, colunas.get("valor_investido"))
        preco_medio = _valor_coluna(valores, colunas.get("preco_medio"))
        if preco_medio is None:
            preco_medio = _valor_coluna(valores, colunas.get("preco_atual"))
        if preco_medio is None and valor_investido is not None:
            preco_medio = valor_investido / quantidade
        if preco_medio is None or preco_medio <= 0:
            logger.warning(
                "Preço ausente ou inválido em %s, aba %s, linha %s.",
                caminho.name,
                nome_aba,
                indice_linha + 1,
            )
            continue
        if valor_investido is None:
            valor_investido = quantidade * preco_medio
        if valor_investido <= 0 or not math.isfinite(valor_investido):
            logger.warning(
                "Valor investido inválido em %s, aba %s, linha %s.",
                caminho.name,
                nome_aba,
                indice_linha + 1,
            )
            continue
        indice_data = colunas.get("data_compra")
        if indice_data is not None and indice_data < len(valores):
            data = _data_valida(valores[indice_data])
            if valores[indice_data] not in (None, "") and data is None:
                logger.warning(
                    "Data inválida ignorada em %s, aba %s, linha %s.",
                    caminho.name,
                    nome_aba,
                    indice_linha + 1,
                )
        linhas.append(
            PosicaoImportada(
                ticker=ticker,
                nome=nome,
                categoria=categoria,
                quantidade=quantidade,
                preco_medio=preco_medio,
                valor_investido=valor_investido,
                vencimento=vencimento_posicao,
            )
        )
    return linhas


def _valor_coluna(valores: list[object], indice: int | None) -> float | None:
    """Lê e converte o valor numérico de uma coluna mapeada."""
    if indice is None or indice >= len(valores):
        return None
    return _numero(valores[indice])


def _texto_coluna(valores: list[object], indice: int | None) -> str:
    """Lê texto de uma coluna mapeada sem assumir sua existência."""
    if indice is None or indice >= len(valores):
        return ""
    return _texto_celula(valores[indice])


def _data_coluna(valores: list[object], indice: int | None) -> str | None:
    """Lê uma data mapeada e a normaliza para ISO-8601."""
    if indice is None or indice >= len(valores):
        return None
    data = _data_valida(valores[indice])
    return data.isoformat() if data is not None else None


def _ler_arquivo(caminho: Path) -> list[PosicaoImportada]:
    """Lê todas as abas XLSX e exige ao menos uma posição válida."""
    abas = pd.read_excel(
        caminho,
        sheet_name=None,
        header=None,
        dtype=object,
        engine="openpyxl",
    )
    if not abas:
        raise ValueError("O arquivo não contém planilhas.")
    posicoes: list[PosicaoImportada] = []
    for nome_aba, frame in abas.items():
        if frame.empty:
            logger.warning("Aba vazia ignorada: %s (%s).", caminho.name, nome_aba)
            continue
        posicoes.extend(_posicoes_planilha(frame, caminho, nome_aba))
    if not posicoes:
        raise ValueError(
            "Nenhuma posição válida encontrada; verifique cabeçalhos e dados."
        )
    return posicoes


def _registrar_erro(
    database: Database,
    caminho: Path,
    arquivo_hash: str,
    erro: Exception,
) -> str:
    """Registra falha de um arquivo e devolve uma mensagem para o relatório."""
    mensagem = f"{caminho.name}: {erro}"
    logger.exception("Falha ao importar %s.", caminho.name)
    database.registrar_importacao(
        caminho.name,
        arquivo_hash,
        status="erro",
        detalhes=str(erro),
    )
    return mensagem


def importar_planilhas_corretora(
    pasta: Path | str | None = None,
    database: Database | None = None,
    progresso: ProgressCallback | None = None,
) -> RelatorioImportacao:
    """Importa e consolida arquivos XLSX novos na pasta configurada."""
    pasta_xlsx = Path(pasta) if pasta is not None else BASE_DIR / "xlsx"
    banco = database or Database()
    relatorio = RelatorioImportacao()
    if not pasta_xlsx.exists():
        pasta_xlsx.mkdir(parents=True, exist_ok=True)
    if not pasta_xlsx.is_dir():
        raise NotADirectoryError(f"O caminho não é uma pasta: {pasta_xlsx}")

    arquivos = sorted(
        caminho
        for caminho in pasta_xlsx.glob("*.xlsx")
        if caminho.is_file() and not caminho.name.startswith("~$")
    )
    if not arquivos:
        logger.info("Nenhum arquivo XLSX encontrado em %s.", pasta_xlsx)
        return relatorio

    validos: list[_ArquivoProcessado] = []
    for numero, caminho in enumerate(arquivos, start=1):
        if progresso is not None:
            progresso(numero - 1, len(arquivos), caminho.name)
        try:
            arquivo_hash = hashlib.sha256(caminho.read_bytes()).hexdigest()
        except OSError as erro:
            mensagem = f"{caminho.name}: não foi possível ler o arquivo ({erro})."
            logger.exception("Falha ao calcular hash do arquivo %s.", caminho.name)
            relatorio.erros.append(mensagem)
            continue

        if not banco.iniciar_importacao(caminho.name, arquivo_hash):
            relatorio.arquivos_ignorados += 1
            logger.info(
                "Arquivo já importado ou em processamento, ignorando: %s.",
                caminho.name,
            )
            continue

        try:
            posicoes = _ler_arquivo(caminho)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            zipfile.BadZipFile,
            InvalidFileException,
            ParseError,
            ImportError,
        ) as erro:
            relatorio.erros.append(
                _registrar_erro(banco, caminho, arquivo_hash, erro)
            )
            continue
        validos.append(
            _ArquivoProcessado(
                caminho=caminho,
                arquivo_hash=arquivo_hash,
                linhas=len(posicoes),
                posicoes=tuple(posicoes),
            )
        )
        logger.info(
            "Arquivo analisado: %s; posições válidas: %d.",
            caminho.name,
            len(posicoes),
        )

    if progresso is not None:
        progresso(len(arquivos), len(arquivos), "Consolidando posições")
    if not validos:
        if relatorio.erros:
            logger.warning("Nenhum arquivo válido foi importado.")
        return relatorio

    relatorio.posicoes = consolidar_posicoes(
        posicao for arquivo in validos for posicao in arquivo.posicoes
    )
    arquivos_db = [
        {
            "arquivo": arquivo.caminho.name,
            "arquivo_hash": arquivo.arquivo_hash,
            "linhas_processadas": arquivo.linhas,
        }
        for arquivo in validos
    ]
    try:
        relatorio.novos_ativos, relatorio.ativos_atualizados = (
            banco.salvar_importacoes_consolidadas(relatorio.posicoes, arquivos_db)
        )
    except RuntimeError as erro:
        logger.exception("Falha ao persistir o lote de importação.")
        for arquivo in validos:
            mensagem = f"{arquivo.caminho.name}: {erro}"
            relatorio.erros.append(mensagem)
            banco.registrar_importacao(
                arquivo.caminho.name,
                arquivo.arquivo_hash,
                status="erro",
                linhas_processadas=arquivo.linhas,
                detalhes=str(erro),
            )
        return relatorio

    relatorio.arquivos_processados = len(validos)
    relatorio.ativos_encontrados = len(relatorio.posicoes)
    relatorio.valor_total_importado = sum(
        posicao.valor_investido for posicao in relatorio.posicoes
    )
    logger.info("%s", relatorio.formatar())
    return relatorio
