"""Modelo e validações para títulos da carteira."""

from dataclasses import dataclass
from datetime import date
import math


@dataclass(frozen=True, slots=True)
class Titulo:
    """Representa uma posição de um título do Tesouro Direto."""

    nome: str
    tipo: str
    data_compra: date
    quantidade: float
    preco_compra: float
    id: int | None = None
    preco_atual: float | None = None

    def __post_init__(self) -> None:
        """Valida dados essenciais e valores financeiros da posição."""
        if not self.nome.strip():
            raise ValueError("O nome do título é obrigatório.")
        if not self.tipo.strip():
            raise ValueError("O tipo do título é obrigatório.")
        if not math.isfinite(self.quantidade) or self.quantidade <= 0:
            raise ValueError("A quantidade deve ser maior que zero.")
        if not math.isfinite(self.preco_compra) or self.preco_compra <= 0:
            raise ValueError("O preço de compra deve ser maior que zero.")
        if self.preco_atual is not None and (
            not math.isfinite(self.preco_atual) or self.preco_atual <= 0
        ):
            raise ValueError("O preço atual deve ser maior que zero.")

    @property
    def valor_investido(self) -> float:
        """Retorna o valor investido na posição."""
        return self.quantidade * self.preco_compra

    @property
    def valor_atual(self) -> float:
        """Retorna o valor de mercado ou, sem cotação, o custo de compra."""
        return self.quantidade * (self.preco_atual or self.preco_compra)
