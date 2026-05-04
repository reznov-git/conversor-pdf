import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class SafraPadraoParser(BankParser):
    """
    Parser para o "Banco Safra - Padrão".
    Implementa a 'Fronteira X240' para isolamento perfeito de colunas,
    além de um incinerador rigoroso para blocos compostos apenas por zeros.
    """

    _REGEX_DATA = re.compile(r'^(\d{2}/\d{2})$')
    _REGEX_VALOR = re.compile(r'^-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}$')

    def identify(self, pdf_path: str) -> bool:
        """Verifica se é o extrato do Banco Safra."""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto_total = ""
                for page in pdf.pages[0:3]:
                    texto_total += (page.extract_text() or "") + " "
                texto_limpo = re.sub(r'\s+', ' ', texto_total.lower())
            
            return "banco safra" in texto_limpo
        except Exception:
            return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        ano_extrato = "2025" 
        self.memoria_data = None 

        with pdfplumber.open(pdf_path) as pdf:
            texto_p1 = (pdf.pages[0].extract_text() or "").lower()
            m_ano = re.search(r'período de \d{2}/\d{2}/\d{4} a \d{2}/\d{2}/(\d{4})', texto_p1)
            if m_ano:
                ano_extrato = m_ano.group(1)

            processando = False
            fim_extrato = False 

            for page in pdf.pages:
                if fim_extrato:
                    break 

                words = page.extract_words(x_tolerance=2)
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
                        y_cortes.append(w['top'] - 3)
                    if self._REGEX_VALOR.match(w['text']) and w['x0'] > 300:
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
                        
                    lv_trigger = self._agrupar_por_linha_perfeita(f_words)
                    texto_faixa = ' '.join(lv_trigger).lower()
                    
                    if "lançamentos realizados" in texto_faixa or ("lançamento" in texto_faixa and "documento" in texto_faixa):
                        processando = True
                        continue
                        
                    if not processando:
                        continue
                        
                    morrer_nesta_faixa = False
                    # Seppuku restrito
                    gatilhos_morte = ["lançamentos futuros"]
                    if any(gatilho in texto_faixa for gatilho in gatilhos_morte):
                        morrer_nesta_faixa = True

                    ts = self._processar_faixa(f_words, ano_extrato)
                    if ts:
                        transacoes.extend(ts)

                    if morrer_nesta_faixa:
                        fim_extrato = True
                        break

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)

    def _processar_faixa(self, words: list, ano: str) -> list:
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

        ancoras = []
        for t_linha in sorted(linhas_virtuais.keys()):
            linha_words = sorted(linhas_virtuais[t_linha], key=lambda w: w['x0'])
            
            valores_linha = []
            for w in linha_words:
                texto = w['text'].replace('|', '').strip()
                if self._REGEX_VALOR.match(texto) and w['x0'] > 400:
                    valores_linha.append(texto)
            
            if valores_linha:
                ancoras.append({
                    'top': t_linha,
                    'valor': self._normalize_value(valores_linha[-1]), 
                    'words': []
                })

        if not ancoras:
            return []

        for i in range(len(ancoras)):
            zone_start = -float('inf') if i == 0 else ancoras[i]['top'] - 5
            zone_end = ancoras[i+1]['top'] - 5 if i < len(ancoras) - 1 else float('inf')
            
            ancoras[i]['zone_start'] = zone_start
            ancoras[i]['zone_end'] = zone_end

        for w in words:
            for a in ancoras:
                if a['zone_start'] <= w['top'] < a['zone_end']:
                    a['words'].append(w)
                    break

        transacoes_bloco = []
        for a in ancoras:
            words_zone = a['words']
            
            data_transacao = ""
            middle_words = []
            
            for w in words_zone:
                txt = w['text'].replace('|', '').strip()
                x0 = w['x0']
                
                if not data_transacao and self._REGEX_DATA.match(txt) and x0 < 100:
                    data_transacao = txt
                    continue
                    
                if self._REGEX_VALOR.match(txt) and x0 > 400:
                    continue 
                    
                middle_words.append(w)
                
            # =========================================================
            # A LINHA IMAGINÁRIA (Fronteira Absoluta X=240)
            # =========================================================
            lancamento_words = [w for w in middle_words if w['x0'] < 240]
            comp_doc_words = [w for w in middle_words if w['x0'] >= 240]
                
            lancamento_str = ' '.join(self._agrupar_por_linha_perfeita(lancamento_words)).strip().upper()
            
            # Exterminador de Zeros
            lancamento_str = self._exterminar_zeros(lancamento_str)
            
            comp_str_parts = []
            doc_str_parts = []
            
            linhas_comp = self._agrupar_por_linha_perfeita(comp_doc_words)
            for linha in linhas_comp:
                
                # Exterminador de Zeros antes de processar
                linha = self._exterminar_zeros(linha.strip())
                if not linha: continue
                
                linha_sem_espaco = linha.replace(' ', '')
                
                # Defesa de Documento vs. CNPJ
                if re.fullmatch(r'\d{5,30}', linha_sem_espaco):
                    if len(linha_sem_espaco) not in (11, 14):
                        doc_str_parts.append(linha_sem_espaco)
                    else:
                        comp_str_parts.append(linha)
                else:
                    m_end = re.search(r'\s(\d{5,30})$', linha)
                    if m_end:
                        doc_candidate = m_end.group(1)
                        if len(doc_candidate) not in (11, 14):
                            doc_str_parts.append(doc_candidate)
                            comp_str_parts.append(linha[:m_end.start()].strip())
                        else:
                            comp_str_parts.append(linha)
                    else:
                        comp_str_parts.append(linha)
                        
            comp_str = ' '.join(comp_str_parts).strip().upper()
            doc_str = ' '.join(doc_str_parts).strip()
            
            # Guilhotina do Fogo Amigo
            if lancamento_str == "SALDO CONTA CORRENTE":
                continue 
                
            if lancamento_str == "CONTA CORRENTE" and not comp_str and not doc_str:
                continue 
                
            if comp_str and doc_str:
                desc_final = f"{lancamento_str} CONF. DOCTO. Nº {doc_str} - {comp_str}"
            elif not comp_str and doc_str:
                desc_final = f"{lancamento_str} CONF. DOCTO. Nº {doc_str}"
            elif comp_str and not doc_str:
                desc_final = f"{lancamento_str} - {comp_str}"
            else:
                desc_final = lancamento_str

            data_final = data_transacao if data_transacao else self.memoria_data
            if data_final:
                self.memoria_data = data_final
                data_completa = f"{data_final}/{ano}"
                
                if desc_final:
                    transacoes_bloco.append({
                        'Data': data_completa,
                        'Descrição': desc_final,
                        'Valor': a['valor']
                    })

        return transacoes_bloco

    def _agrupar_por_linha_perfeita(self, words: list) -> list:
        if not words: return []
        linhas_virtuais = {}
        for w in sorted(words, key=lambda x: x['top']):
            matched = False
            for t in linhas_virtuais:
                if abs(w['top'] - t) < 5: 
                    matched = t
                    break
            if not matched:
                matched = w['top']
                linhas_virtuais[matched] = []
            linhas_virtuais[matched].append(w)

        linhas_str = []
        for t in sorted(linhas_virtuais.keys()):
            lw = sorted(linhas_virtuais[t], key=lambda x: x['x0'])
            linhas_str.append(' '.join(w['text'] for w in lw))
        return linhas_str
        
    def _exterminar_zeros(self, texto: str) -> str:
        """Divide o texto e desintegra palavras feitas EXCLUSIVAMENTE de zeros."""
        tokens = texto.split()
        # Se for totalmente preenchido por 0s, exclui. Senão, mantém.
        tokens_filtrados = [t for t in tokens if not re.fullmatch(r'0+', t)]
        return ' '.join(tokens_filtrados).strip()

    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str: return 0.0
        
        is_negative = val_str.endswith('-') or val_str.startswith('-')
        val_str = val_str.replace('-', '')
        
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            val_float = float(val_str)
            return -val_float if is_negative else val_float
        except ValueError:
            return 0.0
