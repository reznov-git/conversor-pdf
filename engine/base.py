import pandas as pd
from abc import ABC, abstractmethod

class BankParser(ABC):
    """
    Classe abstrata que define o padrão para todos os conversores de banco.
    """
    
    @abstractmethod
    def identify(self, pdf_path: str) -> bool:
        """Verifica se o PDF pertence a este banco e modelo."""
        pass
    
    @abstractmethod  
    def extract(self, pdf_path: str) -> pd.DataFrame:
        """Extrai as transações e retorna um DataFrame padronizado."""
        pass
        
    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Padroniza as colunas de saída para o Streamlit/Excel"""
        # Garante que o output final tenha sempre a mesma cara, independente do banco
        return df[['Data', 'Descrição', 'Valor']]
