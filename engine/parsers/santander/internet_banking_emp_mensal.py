import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class SantanderIBEParser(BankParser):
    """
    Parser para o Santander Internet Banking Empresarial (Mensal e Diário).
    Retorno à arquitetura estável. Lê as frases linearmente e saca o 
    documento do final da string, preservando o Histórico sem embaralhamentos,
    e mantendo o incinerador de zeros (000000) ativo.
    """

    _REGEX_DATA = re.compile(r'^(\d{2}/\d{2}/\d{4})$')
    _REGEX_VALOR = re.compile(r'^-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}$')

    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto_inicio = (pdf.pages[0].extract_text() or "").lower()
                texto_fim = (pdf.pages[-1].extract_text() or "").lower()
                
            texto_limpo = re.sub(r'\s+', ' ', (texto_inicio + " " + texto_fim))
            
            tem_banco = "santander" in texto_limpo
            tem_tipo = "internet banking empresarial" in texto_limpo
            tem_coluna = bool(re.search(r'hist.*?rico', texto_limpo)) and "documento" in texto_limpo
            
            return tem_banco and tem_tipo and tem_coluna
        except Exception:
            return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        self.memoria_data = None 

        with pdfplumber.open(pdf_path) as pdf:
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
                        
                    lv_trigger = self._agrupar_por_linha(f_words)
                    texto_faixa = ' '.join(lv_trigger).lower()
                    
                    if re.search(r'hist.*?rico', texto_faixa) and "documento" in texto_faixa and "valor" in texto_faixa:
                        processando = True
                        continue
                        
                    if not processando:
                        continue
                        
                    morrer_nesta_faixa = False
                    
                    gatilhos_morte = ["bloqueio dia / adm", "lançamento provisionado", "a-saldo de conta corrente", "a - saldo de conta corrente"]
                    if any(gatilho in texto_faixa for gatilho in gatilhos_morte):
                        morrer_nesta_faixa = True

                    ts = self._processar_faixa(f_words)
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

    def _processar_faixa(self, words: list) -> list:
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
                if self._REGEX_VALOR.match(texto) and w['x0'] > 300:
                    valores_linha.append(texto)
            
            if valores_linha:
                ancoras.append({
                    'top': t_linha,
                    'valor': self._normalize_value(valores_linha[0]), 
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
                    
                if self._REGEX_VALOR.match(txt) and x0 > 300:
                    continue 
                    
                middle_words.append(w)
                
            # =========================================================
            # EXTRAÇÃO LINEAR COM SAQUE DE DOCUMENTO (A Arquitetura Vencedora)
            # =========================================================
            linhas_middle = self._agrupar_por_linha(middle_words)
            
            historico_parts = []
            doc_str_bruto = ""
            
            for linha in linhas_middle:
                linha = linha.strip()
                if not linha: continue
                
                # Se a linha inteira for só um número (ocorre em alguns formatos), saca como documento
                if re.fullmatch(r'\d{5,15}', linha.replace(' ', '')):
                    if not doc_str_bruto:
                        doc_str_bruto = linha.replace(' ', '')
                    continue
                    
                # Saca o documento colado no final da frase (ex: "CR COB BLOQ... 000000")
                m_doc = re.search(r'\s(\d{5,15})$', linha)
                if m_doc:
                    if not doc_str_bruto:
                        doc_str_bruto = m_doc.group(1)
                    linha = linha[:m_doc.start()].strip()
                    
                if linha:
                    historico_parts.append(linha)
                    
            historico_str = ' '.join(historico_parts).strip().upper()
            doc_str = self._limpar_documento_zerado(doc_str_bruto)
            
            # Guilhotina de Saldo
            if "SALDO ANTERIOR" in historico_str or historico_str.startswith("SALDO"):
                continue 
                
            if doc_str:
                desc_final = f"{historico_str} CONF. DOCTO. Nº {doc_str}"
            else:
                desc_final = historico_str

            data_final = data_transacao if data_transacao else self.memoria_data
            if data_final:
                self.memoria_data = data_final
                
                if desc_final:
                    transacoes_bloco.append({
                        'Data': data_final,
                        'Descrição': desc_final,
                        'Valor': a['valor']
                    })

        return transacoes_bloco

    def _limpar_documento_zerado(self, doc_str: str) -> str:
        """Desintegra documentos que são compostos apenas de zeros."""
        doc_limpo = doc_str.replace(' ', '').strip()
        if not doc_limpo: return ""
        if re.fullmatch(r'0+', doc_limpo): return ""
        return doc_str

    def _agrupar_por_linha(self, words: list) -> list:
        if not words: return []
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

        linhas_str = []
        for t in sorted(linhas_virtuais.keys()):
            lw = sorted(linhas_virtuais[t], key=lambda x: x['x0'])
            linhas_str.append(' '.join(w['text'] for w in lw))
        return linhas_str

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
