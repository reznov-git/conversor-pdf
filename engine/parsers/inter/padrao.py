import pdfplumber
import pandas as pd
import re
from engine.base import BankParser
 
 
class InterParser(BankParser):
    """
    Parser para extratos do Banco Inter (conta PJ/PF).
 
    Layout por linha:
        • Cabeçalho de data:  "18 de Novembro de 2024  Saldo do dia: R$ 2.557,23"
        • Transação:          "Descrição -R$ 123,00  R$ 456,78"
                               ^ primeiro valor = transação (sinal embutido)
                               ^ segundo valor  = saldo acumulado (ignorado)
 
    O sinal da transação é determinado pelo próprio valor:
        -R$ → saída  |  R$ (sem sinal) → entrada
    """
 
    # ── Identificação ─────────────────────────────────────────────────────────
    _IDENTIFICADOR = '0800 979 7099'
 
    # ── Meses em português (por extenso) ──────────────────────────────────────
    _MESES = {
        'janeiro':   1, 'fevereiro':  2, 'março':    3,
        'abril':     4, 'maio':       5, 'junho':    6,
        'julho':     7, 'agosto':     8, 'setembro': 9,
        'outubro':  10, 'novembro':  11, 'dezembro': 12,
    }
 
    # ── Regex: linha de data por extenso ──────────────────────────────────────
    # Captura "8 de Janeiro de 2025" ou "18 de Novembro de 2024"
    _RE_DATA = re.compile(
        r'^(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})',
        re.IGNORECASE,
    )
 
    # ── Regex: valor monetário com sinal opcional antes de R$ ─────────────────
    # Captura: "-R$ 1.234,56"  |  "R$ 1.234,56"  |  "-R$ 144,36"
    _RE_VALOR = re.compile(r'(-R\$|R\$)\s*([\d.,]+)')
 
    # ── Linhas a ignorar (não são transações) ─────────────────────────────────
    _RE_IGNORAR = re.compile(
        r'saldo\s+do\s+dia|valor\s+saldo\s+por\s+transa|saldo\s+total'
        r'|saldo\s+dispon[ií]vel|saldo\s+bloqueado'
        r'|solicitado\s+em|per[ií]odo:|fale\s+com|sac:|ouvidoria'
        r'|defici[eê]ncia',
        re.IGNORECASE,
    )
 
    # ──────────────────────────────────────────────────────────────────────────
 
    def identify(self, pdf_path: str) -> bool:
        """
        Detecta pela presença do número de atendimento exclusivo do Inter:
        '0800 979 7099' (Deficiência de fala e audição).
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages[:4]:
                    texto = page.extract_text() or ''
                    if self._IDENTIFICADOR in texto:
                        return True
        except Exception:
            pass
        return False
 
    # ──────────────────────────────────────────────────────────────────────────
 
    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes:  list[dict] = []
        current_date: str | None = None
        extracting:  bool = False   # só começa após a 1ª data por extenso
 
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                texto = page.extract_text() or ''
                linhas = texto.splitlines()
 
                for linha in linhas:
                    linha = linha.strip()
                    if not linha:
                        continue
 
                    # ── 1. Detecta cabeçalho de data ─────────────────────────
                    m = self._RE_DATA.match(linha)
                    if m:
                        data = self._parse_date(m.group(1), m.group(2), m.group(3))
                        if data:
                            current_date = data
                            extracting   = True
                        continue   # o restante da linha (saldo do dia) é ignorado
 
                    # ── 2. Ainda não chegou na seção de movimentações ─────────
                    if not extracting:
                        continue
 
                    # ── 3. Linhas de metadados / totais / rodapé ─────────────
                    if self._RE_IGNORAR.search(linha):
                        continue
 
                    # ── 4. Tenta extrair transação ───────────────────────────
                    tx = self._parse_transacao(linha, current_date)
                    if tx:
                        transacoes.append(tx)
 
        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = (
                df.dropna(subset=['Data'])
                  .sort_values('Data')
                  .reset_index(drop=True)
            )
 
        # ── Remove aspas remanescentes da coluna Descrição ───────────────────
        if not df.empty:
            df['Descrição'] = df['Descrição'].str.replace('"', '', regex=False)
 
        return self._clean_dataframe(df)
 
    # ──────────────────────────────────────────────────────────────────────────
 
    def _parse_date(self, dia: str, mes_str: str, ano: str) -> str | None:
        """Converte "8", "Janeiro", "2025" → "08/01/2025"."""
        mes = self._MESES.get(mes_str.lower())
        if not mes:
            return None
        return f"{int(dia):02d}/{mes:02d}/{ano}"
 
    # ──────────────────────────────────────────────────────────────────────────
 
    def _parse_transacao(self, linha: str, data: str | None) -> dict | None:
        """
        Extrai (descrição, valor) de uma linha de transação do Inter.
 
        A linha tem o formato:
            <Descrição>  [-]R$ <valor_tx>  [-]R$ <saldo_acum>
 
        O primeiro par (sinal + número) é o valor da transação.
        O segundo par é o saldo acumulado após o lançamento — ignorado.
        """
        if not data:
            return None
 
        matches = list(self._RE_VALOR.finditer(linha))
        if not matches:
            return None
 
        # Primeiro match = valor da transação
        m_valor = matches[0]
        sinal_str  = m_valor.group(1)   # "-R$" ou "R$"
        numero_str = m_valor.group(2)   # "1.234,56"
 
        valor = self._normalize_value(numero_str)
        if valor == 0.0:
            return None
 
        if sinal_str.startswith('-'):
            valor = -valor
 
        # Descrição: tudo antes do primeiro match de valor
        desc = linha[: m_valor.start()].strip()
        if not desc:
            return None
 
        # Remove aspas duplas que o Inter usa em PIX
        # Ex.: Pix enviado: "Cp :60746948-PATRICIA BETINA GRINBERG"
        desc = desc.strip('"').strip()
 
        return {
            'Data':      data,
            'Descrição': desc.upper(),
            'Valor':     valor,
        }
 
    # ──────────────────────────────────────────────────────────────────────────
 
    def _normalize_value(self, val_str: str) -> float:
        """Converte '1.234,56' → 1234.56."""
        val_str = val_str.strip().replace('.', '').replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            return 0.0
 

