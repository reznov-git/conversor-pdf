import pdfplumber
import pandas as pd
import re
from engine.base import BankParser


class ItauNaoConsolidadoParser(BankParser):

    _REGEX_PERIODO = re.compile(r'\d{2}/\d{2}/(\d{4})')
    _REGEX_DATA_LINHA = re.compile(
        r'^(\d{1,2})\s*/\s*(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)',
        re.IGNORECASE
    )
    _REGEX_VALOR = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')
    _REGEX_AG_ORIGEM = re.compile(r'^\d{1,6}$') 

    _MESES = {
        'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
        'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12,
    }

    _GATILHO_PARADA = 'saldo da conta corrente'

    def identify(self, pdf_path: str) -> bool:
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            return "lançamentos período" in texto or "lancamentos periodo" in texto
        except Exception:
            return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []

        with pdfplumber.open(pdf_path) as pdf:
            linhas = []
            for page in pdf.pages:
                texto = page.extract_text() or ""
                linhas.extend(texto.split('\n'))

        ano_extrato = self._extrair_ano(linhas)

        processando = False

        for linha in linhas:
            linha = linha.strip()
            if not linha:
                continue

            linha_lower = linha.lower()

            if self._GATILHO_PARADA in linha_lower:
                break

            if not processando and 'data' in linha_lower and 'lançamentos' in linha_lower:
                processando = True
                continue

            if not processando:
                continue

            transacao = self._parsear_linha(linha, ano_extrato)
            if transacao:
                transacoes.append(transacao)

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)

    def _extrair_ano(self, linhas: list) -> int:
        for linha in linhas:
            if 'período' in linha.lower() or 'periodo' in linha.lower():
                m = self._REGEX_PERIODO.search(linha)
                if m:
                    return int(m.group(1))
        return pd.Timestamp.now().year

    def _parsear_linha(self, linha: str, ano_extrato: int) -> dict | None:

        m_data = self._REGEX_DATA_LINHA.match(linha)
        if not m_data:
            return None

        dia = m_data.group(1).zfill(2)
        mes_abrev = m_data.group(2).lower()
        mes_num = self._MESES.get(mes_abrev, 1)

        resto = linha[m_data.end():].strip()
        tokens = resto.split()

        if not tokens:
            return None

        prefixos_saldo = ('saldo', '(-) saldo', '(-)saldo', '(+) saldo', '(+)saldo')
        descricao_teste = ' '.join(tokens).lower()
        
        if descricao_teste.startswith(prefixos_saldo):
            return None

        if not self._REGEX_VALOR.match(tokens[-1]):
            return None

        valor_str = tokens.pop()
        valor = self._normalize_value(valor_str)

        if valor == 0:
            return None

        if tokens and self._REGEX_AG_ORIGEM.match(tokens[-1]):
            tokens.pop()

        descricao = ' '.join(tokens).strip()

        if not descricao or len(descricao) < 2 or not re.search(r'[a-zA-Z]', descricao):
            return None

        return {
            'Data': f"{dia}/{mes_num:02d}/{ano_extrato}",
            'Descrição': descricao.upper(),
            'Valor': valor,
        }

    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str:
            return 0.0
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            return 0.0
