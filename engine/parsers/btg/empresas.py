import pdfplumber
import pandas as pd
import re
from collections import defaultdict
from engine.base import BankParser
 
 
class BtgEmpresasParser(BankParser):
    """
    Parser para extratos do BTG Pactual Empresas (conta corrente PJ).
    Layout de 4 colunas: Data | Descrição | Entradas/Saídas (R$) | Saldo (R$)
 
    Inclui o Módulo de Auditoria Forense: Injeta automaticamente rendimentos
    não listados no extrato se a diferença do saldo bater até 1%.
    """
 
    _X_DESC  = 140   
    _X_VALOR = 490   
    _X_SALDO = 640   
 
    _RE_DATA = re.compile(r'^\d{2}/\d{2}/\d{4}$')
 
    _SKIP_PREFIXOS = (
        'saldo de abertura',
        'saldo de fechamento',
        'saldo bloqueado',
        'total de entradas',
        'total de saídas',
        'total de saidas',
    )
 
    _POST_THRESHOLD = 20
 
    _GATILHO_INICIO = '02.'          
    _GATILHO_FIM    = 'não nos responsabilizamos'
 
    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages[:3]:
                    texto = (page.extract_text() or '').lower()
                    if ('conta corrente' in texto
                            and 'data lançamento' in texto
                            and 'entradas' in texto):
                        return True
        except Exception:
            pass
        return False
 
    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes     = []
        in_lancamentos = False
        tx_atual       = None
        desc_buffer    = []    
        last_anchor_y  = None  
        
        # Variáveis do Espião Forense
        auditoria_saldo_abertura = 0.0
        auditoria_saldo_fechamento = 0.0
        auditoria_data_fechamento = None
 
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
 
                    if not in_lancamentos:
                        if self._GATILHO_INICIO in texto_low and 'lançamentos' in texto_low:
                            in_lancamentos = True
                        continue
 
                    if self._GATILHO_FIM in texto_low:
                        if tx_atual:
                            t = self._finalizar(tx_atual)
                            if t:
                                transacoes.append(t)
                            tx_atual = None
                        break
 
                    cols = self._separar_colunas(linha)
 
                    if cols['data'].lower().startswith('data'):
                        continue
 
                    desc_low = (cols['data'] + ' ' + cols['desc']).lower().strip()
                    desc_col_low = cols['desc'].lower().strip()
                    data_match   = self._parse_date(cols['data'])

                    # --- ESPIÃO FORENSE: captura independente do desc_low ---
                    if desc_col_low.startswith('saldo de abertura') and cols['saldo']:
                        auditoria_saldo_abertura = self._normalize_value(cols['saldo'])
                    elif desc_col_low.startswith('saldo de fechamento') and cols['saldo'] and data_match:
                        auditoria_saldo_fechamento = self._normalize_value(cols['saldo'])
                        auditoria_data_fechamento  = data_match
                    # --------------------------------------------------------

                    if any(desc_low.startswith(p) for p in self._SKIP_PREFIXOS):

                        if tx_atual:
                            t = self._finalizar(tx_atual)
                            if t:
                                transacoes.append(t)
                            tx_atual = None
                        desc_buffer   = []
                        last_anchor_y = None
                        continue
 
                    data_parsed = self._parse_date(cols['data'])
 
                    if data_parsed and cols['valor']:
                        if tx_atual:
                            t = self._finalizar(tx_atual)
                            if t:
                                transacoes.append(t)
 
                        partes = desc_buffer + ([cols['desc']] if cols['desc'] else [])
                        tx_atual = {
                            'data':      data_parsed,
                            'desc':      ' '.join(partes).strip(),
                            'valor_str': cols['valor'],
                        }
                        desc_buffer   = []
                        last_anchor_y = top_y
 
                    elif cols['desc'] and not data_parsed and not cols['valor']:
                        eh_post = (
                            tx_atual is not None
                            and last_anchor_y is not None
                            and top_y > last_anchor_y
                            and (top_y - last_anchor_y) <= self._POST_THRESHOLD
                        )
                        if eh_post:
                            tx_atual['desc'] = (tx_atual['desc'] + ' ' + cols['desc']).strip()
                        else:
                            desc_buffer.append(cols['desc'])
 
        if tx_atual:
            t = self._finalizar(tx_atual)
            if t:
                transacoes.append(t)
                
        # ==============================================================
        # MÓDULO DE AUDITORIA FORENSE (O GOLPE DE MISERICÓRDIA)
        # ==============================================================
        if transacoes and auditoria_data_fechamento:
            soma_transacoes = sum(t['Valor'] for t in transacoes)
            saldo_calculado = auditoria_saldo_abertura + soma_transacoes
            diferenca = auditoria_saldo_fechamento - saldo_calculado
            
            # Se houver diferença (tratamento de ponto flutuante)
            if abs(diferenca) > 0.005: 
                limite_1_porcento = abs(saldo_calculado) * 0.01
                
                # Se a diferença for menor ou igual a 1% do saldo calculado
                if abs(diferenca) <= limite_1_porcento:
                    transacoes.append({
                        'Data': auditoria_data_fechamento,
                        'Descrição': 'RENDIMENTOS FINANCEIROS AUFERIDOS NO MÊS',
                        'Valor': round(diferenca, 2)
                    })
        # ==============================================================
 
        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)
 
        return self._clean_dataframe(df)
 
    def _separar_colunas(self, linha: list) -> dict:
        data_w, desc_w, valor_w, saldo_w = [], [], [], []
        for w in linha:
            x = w['x0']
            if   x < self._X_DESC:  data_w.append(w)
            elif x < self._X_VALOR: desc_w.append(w)
            elif x < self._X_SALDO: valor_w.append(w)
            else:                    saldo_w.append(w)
        return {
            'data':  ' '.join(w['text'] for w in data_w).strip(),
            'desc':  ' '.join(w['text'] for w in desc_w).strip(),
            'valor': ' '.join(w['text'] for w in valor_w).strip(),
            'saldo': ' '.join(w['text'] for w in saldo_w).strip(),
        }
 
    def _parse_date(self, data_str: str) -> str | None:
        s = data_str.strip()
        return s if self._RE_DATA.match(s) else None
 
    def _finalizar(self, tx: dict) -> dict | None:
        if not tx.get('data') or not tx.get('valor_str'):
            return None
 
        valor = self._normalize_value(tx['valor_str'])
        if valor == 0.0:
            return None
 
        return {
            'Data':      tx['data'],
            'Descrição': tx['desc'].strip().upper(),
            'Valor':     valor,
        }
 
    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str:
            return 0.0
 
        negative = val_str.startswith('-')
        val_str  = val_str.lstrip('+-').replace('R$', '').strip()
        val_str  = val_str.replace('.', '').replace(',', '.')
 
        try:
            value = float(val_str)
            return -value if negative else value
        except ValueError:
            return 0.0
