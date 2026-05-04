import pdfplumber
import pandas as pd
import re
from collections import defaultdict
from engine.base import BankParser


class BTGParser(BankParser):
    """
    Parser para extratos do BTG Pactual (modelo "Extrato de Conta Corrente").

    Estrutura de layout:
      - Página 1: capa (identificação do cliente, sem transações).
      - Página 2+: tabela com colunas Data | Descrição | Débito | Crédito | Saldo.

    Peculiaridade crítica: cada transação ocupa DOIS Y consecutivos.
      - Linha ímpar (y_desc): data completa (DD/MM/AAAA) + texto da descrição.
      - Linha par   (y_val):  valor do débito OU crédito + saldo (ignorado).

    Diferença em relação a outros bancos: colunas Débito e Crédito NÃO estão
    invertidas — débito vem antes (x0 menor), crédito vem depois (x0 maior).
    O sinal da transação é determinado pela coluna em que o valor aparece.
    """

    # Fronteiras X das colunas (em pts, calibradas no layout do BTG)
    _X_DESC_MAX  = 340   # descrição:  x0 <  340
    _X_DEB_MAX   = 440   # débito:     340 ≤ x0 < 440  → valor negativo
    _X_CRED_MAX  = 520   # crédito:    440 ≤ x0 < 520  → valor positivo
                         # saldo:      x0 ≥ 520         → ignorado

    _REGEX_DATA  = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    _REGEX_VALOR = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')

    _PREFIXOS_IGNORAR = (
        'saldo inicial', 'saldo final',
        'total de créditos', 'total de débitos',
    )

    # -------------------------------------------------------------------------
    # identify
    # -------------------------------------------------------------------------

    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            return "btgpactual" in texto or "btg pactual" in texto
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # extract
    # -------------------------------------------------------------------------

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(x_tolerance=3, y_tolerance=3)
                if not words:
                    continue

                # Agrupa palavras por linha (top arredondado)
                linhas = defaultdict(list)
                for w in words:
                    linhas[round(w['top'])].append(w)

                ys = sorted(linhas.keys())
                i = 0

                while i < len(ys):
                    y = ys[i]
                    linha = sorted(linhas[y], key=lambda w: w['x0'])

                    # Verifica se a linha começa com uma data completa DD/MM/AAAA
                    data_str = linha[0]['text'] if linha else ''
                    if not self._REGEX_DATA.match(data_str):
                        i += 1
                        continue

                    # --- Linha de descrição ---
                    desc_words = [
                        w['text'] for w in linha
                        if 100 <= w['x0'] < self._X_DESC_MAX
                    ]
                    desc_str = ' '.join(desc_words).strip()

                    # Descarta linhas de saldo e totais
                    if desc_str.lower().startswith(self._PREFIXOS_IGNORAR):
                        i += 1
                        continue

                    # --- Linha de valores (próxima linha) ---
                    deb_str = ''; cred_str = ''
                    if i + 1 < len(ys):
                        prox = sorted(linhas[ys[i + 1]], key=lambda w: w['x0'])
                        for w in prox:
                            x, t = w['x0'], w['text']
                            if not self._REGEX_VALOR.match(t):
                                continue
                            if   x < self._X_DEB_MAX:   deb_str  = t
                            elif x < self._X_CRED_MAX:  cred_str = t
                        i += 1  # consome a linha de valores

                    # --- Determina valor e sinal ---
                    if deb_str and not cred_str:
                        valor = -self._normalize_value(deb_str)
                    elif cred_str and not deb_str:
                        valor =  self._normalize_value(cred_str)
                    elif deb_str and cred_str:
                        # Ambos presentes (ex: operação com margem): líquido
                        valor = self._normalize_value(cred_str) - self._normalize_value(deb_str)
                    else:
                        i += 1
                        continue

                    if valor == 0.0:
                        i += 1
                        continue

                    transacoes.append({
                        'Data': data_str,
                        'Descrição': desc_str.upper(),
                        'Valor': valor,
                    })

                    i += 1

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)

    # -------------------------------------------------------------------------
    # Auxiliar
    # -------------------------------------------------------------------------

    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str:
            return 0.0
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            return 0.0
