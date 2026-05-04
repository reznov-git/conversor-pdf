import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class ItauEmpresasParser(BankParser):

    _REGEX_DATA = re.compile(r'^(\d{2}/\d{2})')
    
    _REGEX_VALOR = re.compile(r'(-?(?:\d{1,3}(?:[\.,]\d{3})*[\.,]\d{2}|\d+[\.,]\d{2}))$')

    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            return "itaúempresas" in texto
        except Exception:
            return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        ano_extrato = "2025"

        with pdfplumber.open(pdf_path) as pdf:
            linhas_brutas = []
            for i, page in enumerate(pdf.pages):
                texto = page.extract_text() or ""
                
                if i == 0:
                    m_ano = re.search(r'extrato de \d{2}/\d{2}/(\d{4})', texto, re.IGNORECASE)
                    if m_ano:
                        ano_extrato = m_ano.group(1)

                linhas_brutas.extend(texto.split('\n'))

        for linha in linhas_brutas:
            linha = linha.strip()
            if not linha:
                continue

            m = re.search(r'^(\d{2}/\d{2})\s+(.*?)\s+(-?[\d\.,]+)$', linha)
            
            if m:
                data_str = m.group(1)
                resto = m.group(2).strip()
                valor_str = m.group(3)
                
                if 'saldo' in resto.lower().replace(' ', ''):
                    continue
                    
                m_ag = re.search(r'^(.*?)\s+(\d{4})$', resto)
                if m_ag:
                    desc_final = m_ag.group(1)
                else:
                    desc_final = resto

                data_completa = f"{data_str}/{ano_extrato}" 

                transacoes.append({
                    'Data': data_completa,
                    'Descrição': desc_final.upper(),
                    'Valor': self._normalize_value(valor_str)
                })

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)

    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str: return 0.0
        
        is_negative = val_str.startswith('-')
        val_str = val_str.replace('-', '')
        
        last_sep_idx = max(val_str.rfind(','), val_str.rfind('.'))
        
        if last_sep_idx != -1 and len(val_str) - last_sep_idx == 3:
            integer_part = val_str[:last_sep_idx].replace('.', '').replace(',', '')
            decimal_part = val_str[last_sep_idx+1:]
            val_float = float(f"{integer_part}.{decimal_part}")
        else:
            val_float = float(val_str.replace('.', '').replace(',', ''))
            
        return -val_float if is_negative else val_float
