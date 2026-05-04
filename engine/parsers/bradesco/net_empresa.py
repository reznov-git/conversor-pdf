import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class BradescoNetEmpresaParser(BankParser):
    """
    Parser Definitivo para o "Bradesco Net Empresa".
    Lida com omissão de datas, OCR rebelde e executa um shutdown absoluto 
    ao encontrar o final do período principal.
    """

    _REGEX_DATA = re.compile(r'^(\d{2}/\d{2}/\d{4})$')
    _REGEX_VALOR = re.compile(r'^-?(?:\d{1,3}(?:[\.,]\d{3})*[\.,]\d{2}|\d+[\.,]\d{2})$')

    def identify(self, pdf_path: str) -> bool:
        """Verifica se é o extrato do Bradesco Net Empresa usando assinaturas estruturais únicas."""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            
            assinatura_cabecalho = re.search(r'extrato de:\s*ag:.*?cc:.*?entre\s*\d{2}/\d{2}/\d{4}\s*e\s*\d{2}/\d{2}/\d{4}', texto)
            assinatura_colunas = "agência | conta" in texto and "total dispon" in texto
            
            return bool(assinatura_cabecalho or assinatura_colunas)
        except Exception:
            return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        self.current_date = None 

        with pdfplumber.open(pdf_path) as pdf:
            fim_extrato = False # O Botão de Autodestruição (Shutdown)

            for page in pdf.pages:
                if fim_extrato:
                    break # Seppuku multihistórico. Aborta as páginas seguintes!

                words = page.extract_words()
                if not words:
                    continue

                y_cortes = []
                for line in page.lines:
                    if line['width'] > 50:
                        y_cortes.append(line['top'])
                for rect in page.rects:
                    if rect['width'] > 50 and rect['height'] < 15:
                        y_cortes.append(rect['top'])
                        y_cortes.append(rect['bottom'])

                for w in words:
                    if self._REGEX_DATA.match(w['text']) and w['x0'] < 100:
                        y_cortes.append(w['top'] - 4)
                    
                    w_val_limpo = w['text'].replace('R$', '').strip()
                    if self._REGEX_VALOR.match(w_val_limpo) and w['x0'] > 250:
                        y_cortes.append(w['top'] - 4)

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
                        
                    texto_faixa = ' '.join(w['text'] for w in f_words).lower()
                    
                    # =========================================================
                    # PROTOCOLO DE SEPPUKU: Ao ver qualquer uma dessas iscas,
                    # ele aciona a flag e pulveriza o loop de extração.
                    # =========================================================
                    if texto_faixa.startswith('total') or \
                       'os dados acima têm como base' in texto_faixa or \
                       'últimos lançamentos' in texto_faixa or \
                       'saldos invest' in texto_faixa:
                        fim_extrato = True
                        break 
                        
                    # Ignora Cabeçalhos Mortos residuais
                    if 'saldo anterior' in texto_faixa:
                        continue
                        
                    t = self._processar_faixa(f_words)
                    if t:
                        transacoes.append(t)

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)
            df['Descrição'] = df['Descrição'].apply(lambda x: re.sub(r'\s+', ' ', x).strip())

        return self._clean_dataframe(df)

    def _processar_faixa(self, words: list) -> dict | None:
        linhas_virtuais = {}
        for w in sorted(words, key=lambda x: x['top']):
            matched = False
            for t in linhas_virtuais:
                if abs(w['top'] - t) < 4:
                    matched = t
                    break
            if not matched:
                matched = w['top']
                linhas_virtuais[matched] = []
            linhas_virtuais[matched].append(w)

        frases = []
        for t in sorted(linhas_virtuais.keys()):
            lw = sorted(linhas_virtuais[t], key=lambda x: x['x0'])
            frase_atual = [lw[0]]
            for w in lw[1:]:
                x1_ant = frase_atual[-1].get('x1', frase_atual[-1]['x0'] + len(frase_atual[-1]['text']) * 6)
                if w['x0'] - x1_ant > 20: 
                    frases.append(frase_atual)
                    frase_atual = [w]
                else:
                    frase_atual.append(w)
            frases.append(frase_atual)

        data_str = ""
        dcto_str = ""
        valores = []
        desc_parts = []

        for frase in frases:
            # Limpeza do OCR
            texto = ' '.join([fw['text'] for fw in frase]).replace('|', '').replace('R$', '').strip()
            if not texto or texto in ('-', '()', '(', ')'): 
                continue
            
            x0 = frase[0]['x0']
            
            if self._REGEX_DATA.search(texto) and x0 < 100:
                m_data = self._REGEX_DATA.search(texto)
                data_str = m_data.group(1)
                texto = texto.replace(data_str, '').strip()
                if not texto or texto in ('-', '()', '(', ')'): 
                    continue

            if self._REGEX_VALOR.match(texto) and x0 > 250:
                valores.append(texto)
                continue
                
            if re.match(r'^\d+$', texto) and 200 < x0 < 450:
                dcto_str = texto
                continue
                
            desc_parts.append(texto)

        if data_str:
            self.current_date = data_str
        else:
            data_str = self.current_date

        if not valores or not data_str:
            return None

        # Guilhotina de Saldos
        texto_lower = ' '.join(desc_parts).lower()
        prefixos_saldo = ('saldo', '(-) saldo', '(+) saldo', '(-)saldo', '(+)saldo')
        if texto_lower.startswith(prefixos_saldo):
            return None

        valor_str = valores[0]
        valor = self._normalize_value(valor_str)

        descricao_base = ' - '.join(desc_parts).strip()
        descricao_base = descricao_base.strip(' -')

        if dcto_str:
            descricao_final = f"{descricao_base} CONF. DOCTO. Nº {dcto_str}".upper()
        else:
            descricao_final = descricao_base.upper()

        return {
            'Data': data_str,
            'Descrição': descricao_final,
            'Valor': valor
        }

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
