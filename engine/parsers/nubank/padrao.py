import pdfplumber
import pandas as pd
import re
from collections import defaultdict
from engine.base import BankParser


class NubankParser(BankParser):
    """
    Parser para extratos do Nubank (conta corrente PJ/PF).
    Layout de 4 colunas: Data | Tipo | Destinatário | Valor
    Sinal da transação determinado pelo bloco "Total de entradas/saídas" do dia.
    """

    # ── Breakpoints de coluna (x0 em pontos) ──────────────────────────────────
    _X_TIPO  = 120   # Data:         x0 < _X_TIPO
    _X_DEST  = 257   # Tipo:  _X_TIPO <= x0 < _X_DEST
    _X_VALOR = 490   # Dest:  _X_DEST <= x0 < _X_VALOR  |  Valor: x0 >= _X_VALOR

    # ── Meses em português ────────────────────────────────────────────────────
    _MESES = {
        'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
        'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12,
    }

    # ── Regex ─────────────────────────────────────────────────────────────────
    _REGEX_ANO = re.compile(r'^20\d{2}$')

    # ── Sentinela de parada ───────────────────────────────────────────────────
    _GATILHO_PARADA = 'o saldo líquido corresponde'

    # ──────────────────────────────────────────────────────────────────────────

    def identify(self, pdf_path: str) -> bool:
        """
        Detecta pela co-ocorrência de:
          • 'nubank.com.br'  – rodapé de todas as páginas
          • 'movimentações'  – cabeçalho da seção de lançamentos
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages[:3]:
                    texto = (page.extract_text() or '').lower()
                    if 'nubank.com.br' in texto and 'movimentações' in texto:
                        return True
        except Exception:
            pass
        return False

    # ──────────────────────────────────────────────────────────────────────────

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes  = []
        current_date = None
        current_sign = -1    # default saída até encontrar "Total de entradas"
        tx_atual     = None

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(x_tolerance=3, y_tolerance=3)
                if not words:
                    continue

                linhas_map = defaultdict(list)
                for w in words:
                    linhas_map[round(w['top'])].append(w)

                for top_y in sorted(linhas_map.keys()):
                    linha     = sorted(linhas_map[top_y], key=lambda w: w['x0'])
                    texto_low = ' '.join(w['text'] for w in linha).lower()

                    # ── Condição de parada ───────────────────────────────────
                    if self._GATILHO_PARADA in texto_low:
                        if tx_atual:
                            t = self._finalizar(tx_atual, current_sign)
                            if t:
                                transacoes.append(t)
                            tx_atual = None
                        break

                    cols     = self._separar_colunas(linha)
                    tipo_low = cols['tipo'].lower()

                    # ── Linha "Total de entradas / saídas" ───────────────────
                    # Marca o bloco e atualiza a data se vier com ela.
                    if tipo_low.startswith('total de'):
                        # Finaliza transação pendente
                        if tx_atual:
                            t = self._finalizar(tx_atual, current_sign)
                            if t:
                                transacoes.append(t)
                            tx_atual = None

                        # Atualiza data se presente na zona esquerda
                        if cols['data']:
                            d = self._parse_date(cols['data'])
                            if d:
                                current_date = d

                        # Define sinal do bloco
                        if 'entrada' in tipo_low:
                            current_sign = 1
                        else:  # saídas
                            current_sign = -1
                        continue

                    # ── Linhas de saldo – ignorar ────────────────────────────
                    if tipo_low.startswith('saldo'):
                        if tx_atual:
                            t = self._finalizar(tx_atual, current_sign)
                            if t:
                                transacoes.append(t)
                            tx_atual = None
                        continue

                    # ── Linha de transação (tem conteúdo na zona Tipo) ────────
                    if cols['tipo']:
                        # Finaliza transação anterior
                        if tx_atual:
                            t = self._finalizar(tx_atual, current_sign)
                            if t:
                                transacoes.append(t)

                        tx_atual = {
                            'data':      current_date,
                            'tipo':      cols['tipo'],
                            'dest':      cols['dest'],
                            'valor_str': cols['valor'],
                        }

                    # ── Linha de continuação (só zona Destinatário) ───────────
                    # Apenda ao destinatário da transação em aberto.
                    elif cols['dest'] and tx_atual:
                        tx_atual['dest'] = (tx_atual['dest'] + ' ' + cols['dest']).strip()

        # Fecha última transação em aberto
        if tx_atual:
            t = self._finalizar(tx_atual, current_sign)
            if t:
                transacoes.append(t)

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)

    # ──────────────────────────────────────────────────────────────────────────

    def _separar_colunas(self, linha: list) -> dict:
        """Distribui as palavras da linha pelas 4 colunas via posição x0."""
        data_w, tipo_w, dest_w, valor_w = [], [], [], []
        for w in linha:
            x = w['x0']
            if   x < self._X_TIPO:  data_w.append(w)
            elif x < self._X_DEST:  tipo_w.append(w)
            elif x < self._X_VALOR: dest_w.append(w)
            else:                    valor_w.append(w)
        return {
            'data':  ' '.join(w['text'] for w in data_w).strip(),
            'tipo':  ' '.join(w['text'] for w in tipo_w).strip(),
            'dest':  ' '.join(w['text'] for w in dest_w).strip(),
            'valor': ' '.join(w['text'] for w in valor_w).strip(),
        }

    # ──────────────────────────────────────────────────────────────────────────

    def _parse_date(self, data_str: str) -> str | None:
        """
        Converte "02 JAN 2024" → "02/01/2024".
        Ignora strings que não se enquadrem no padrão (e.g. cabeçalhos de página).
        """
        parts = data_str.strip().split()
        if len(parts) < 3:
            return None
        dia_str, mes_str, ano_str = parts[0], parts[1].lower(), parts[2]
        if not dia_str.isdigit():
            return None
        mes = self._MESES.get(mes_str)
        if not mes or not self._REGEX_ANO.match(ano_str):
            return None
        return f"{int(dia_str):02d}/{mes:02d}/{ano_str}"

    # ──────────────────────────────────────────────────────────────────────────

    def _finalizar(self, tx: dict, sign: int) -> dict | None:
        """
        Monta o dict final {Data, Descrição, Valor}.
        Retorna None se data ou valor estiverem ausentes/inválidos.
        """
        if not tx.get('data') or not tx.get('valor_str'):
            return None

        valor = self._normalize_value(tx['valor_str'])
        if valor == 0.0:
            return None

        valor *= sign

        tipo = tx['tipo'].strip()
        dest = tx['dest'].strip()

        # ── Descrição: "Tipo - Destinatário" ─────────────────────────────────
        desc = f"{tipo} - {dest}" if dest else tipo

        return {
            'Data':      tx['data'],
            'Descrição': desc.upper(),
            'Valor':     valor,
        }

    # ──────────────────────────────────────────────────────────────────────────

    def _normalize_value(self, val_str: str) -> float:
        """Converte '7.000,00' → 7000.0  |  '+118.617,82' → 0.0 (totais do dia)."""
        val_str = str(val_str).strip()
        if not val_str:
            return 0.0
        # Remove sinais e prefixo R$
        val_str = val_str.replace('+', '').replace('-', '').replace('R$', '').strip()
        # Converte formato brasileiro
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            return 0.0
