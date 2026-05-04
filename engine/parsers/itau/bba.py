import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class ItauBBAParser(BankParser):

    _REGEX_DATA = re.compile(r'^(\d{2}/\d{2}/\d{4})$')
    _REGEX_CNPJ_CPF = re.compile(r'(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})')
    _REGEX_VALOR = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')

    def identify(self, pdf_path: str) -> bool:
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            
            texto_limpo = re.sub(r'\s+', ' ', texto)
            
            iscas_colunas = [
                "lançamentos",
                "razão social",
                "cnpj/cpf",
                "valor (r$)",
                "saldo (r$)"
            ]
            
            pontuacao = sum(1 for isca in iscas_colunas if isca in texto_limpo)
            return pontuacao >= 4
            
        except Exception:
            return False
    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []

        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                words = page.extract_words()
                if not words:
                    continue

                y_cortes = []
                
                for line in page.lines:
                    if line['width'] > 50:
                        y_cortes.append(line['top'])
                for rect in page.rects:
                    if rect['width'] > 50 and rect['height'] < 5:
                        y_cortes.append(rect['top'])

                for w in words:
                    if self._REGEX_DATA.match(w['text']) and w['x0'] < 100:
                        y_cortes.append(w['top'] - 3)

                y_cortes = sorted(y_cortes)
                divisores_limpos = []
                for y in y_cortes:
                    if not divisores_limpos or abs(y - divisores_limpos[-1]) > 5:
                        divisores_limpos.append(y)
                
                divisores_limpos.insert(0, 0)
                divisores_limpos.append(page.height)

                faixas = {idx: [] for idx in range(len(divisores_limpos) - 1)}
                
                for w in words:
                    w_y = (w['top'] + w['bottom']) / 2
                    for idx in range(len(divisores_limpos) - 1):
                        if divisores_limpos[idx] <= w_y < divisores_limpos[idx+1]:
                            faixas[idx].append(w)
                            break

                for idx, f_words in faixas.items():
                    if not f_words:
                        continue
                        
                    f_words.sort(key=lambda x: x['x0'])
                    possivel_data = f_words[0]['text']
                    
                    if self._REGEX_DATA.match(possivel_data):
                        t = self._processar_faixa(f_words)
                        if t:
                            transacoes.append(t)
                    else:
                        texto_faixa = ' '.join(w['text'] for w in f_words).lower()
                        if 'aviso: os saldos' in texto_faixa:
                            break 

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)
            df['Descrição'] = df['Descrição'].apply(lambda x: re.sub(r'\s+', ' ', x).strip())

        return self._clean_dataframe(df)

    def _processar_faixa(self, words: list) -> dict | None:
        cols = []
        for w in words:
            matched = False
            for c in cols:
                avg_x0 = sum(cw['x0'] for cw in c) / len(c)
                if abs(w['x0'] - avg_x0) < 40:
                    c.append(w)
                    matched = True
                    break
            if not matched:
                cols.append([w])
                
        cols.sort(key=lambda c: sum(cw['x0'] for cw in c) / len(c))
        
        data_str = ""
        cnpj_cpf = ""
        valor_str = ""
        palavras_sobra = []
        
        for c in cols:
            c.sort(key=lambda cw: cw['top'])
            col_text = ' '.join([cw['text'] for cw in c])
            
            m_data = self._REGEX_DATA.search(col_text)
            if m_data and not data_str:
                data_str = m_data.group(1)
                
            m_doc = self._REGEX_CNPJ_CPF.search(col_text)
            if m_doc:
                cnpj_cpf = m_doc.group(1)

            for w in c:
                t = w['text']
                t_limpo = t.replace('|', '').strip()
                if not t_limpo: continue
                
                if t_limpo == data_str or (cnpj_cpf and cnpj_cpf in t_limpo):
                    continue
                    
                t_val = t_limpo.replace('R$', '').strip()
                if self._REGEX_VALOR.match(t_val):
                    if not valor_str:
                        valor_str = t_val 
                    continue
                
                if t_limpo not in ('-', '()', '(', ')'):
                    palavras_sobra.append(w)
                    
        if not valor_str or not data_str:
            return None

        linhas_desc = {}
        for w in palavras_sobra:
            matched_top = None
            for t in linhas_desc:
                if abs(w['top'] - t) < 4:
                    matched_top = t
                    break
            if matched_top is None:
                matched_top = w['top']
                linhas_desc[matched_top] = []
            linhas_desc[matched_top].append(w)
            
        todas_as_frases = []
        
        for t_linha in sorted(linhas_desc.keys()):
            lw = sorted(linhas_desc[t_linha], key=lambda x: x['x0'])
            frase_atual = [lw[0]]
            
            for w in lw[1:]:
                x1_anterior = frase_atual[-1].get('x1', frase_atual[-1]['x0'] + len(frase_atual[-1]['text']) * 7)
                
                if w['x0'] - x1_anterior > 25: 
                    todas_as_frases.append(frase_atual)
                    frase_atual = [w]
                else:
                    frase_atual.append(w)
            todas_as_frases.append(frase_atual)
            
        macro_colunas = []
        for frase in todas_as_frases:
            f_x0 = frase[0]['x0']
            texto_frase = ' '.join([fw['text'] for fw in frase]).replace('|', '').strip()
            if not texto_frase: continue
            
            matched = False
            for mc in macro_colunas:
                avg_x0 = sum(f['x0'] for f in mc) / len(mc)

                if abs(f_x0 - avg_x0) < 60:
                    mc.append({'x0': f_x0, 'top': frase[0]['top'], 'text': texto_frase})
                    matched = True
                    break
            if not matched:
                macro_colunas.append([{'x0': f_x0, 'top': frase[0]['top'], 'text': texto_frase}])
                
        macro_colunas.sort(key=lambda mc: sum(f['x0'] for f in mc) / len(mc))
        
        desc_parts = []
        for mc in macro_colunas:
            mc.sort(key=lambda f: f['top'])
            texto_coluna = ' '.join([f['text'] for f in mc]).strip()
            if texto_coluna:
                desc_parts.append(texto_coluna)
                
        texto_lower = ' '.join(desc_parts).lower()
        prefixos_saldo = ('saldo', '(-) saldo', '(+) saldo', '(-)saldo', '(+)saldo')
        if texto_lower.startswith(prefixos_saldo):
            return None
            
        descricao_base = ' - '.join(desc_parts).strip()
        descricao_base = descricao_base.strip(' -')
        
        if cnpj_cpf:
            descricao_final = f"{descricao_base} ({cnpj_cpf})".upper()
        else:
            descricao_final = descricao_base.upper()
            
        return {
            'Data': data_str,
            'Descrição': descricao_final,
            'Valor': self._normalize_value(valor_str)
        }

    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str: return 0.0
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            return 0.0
