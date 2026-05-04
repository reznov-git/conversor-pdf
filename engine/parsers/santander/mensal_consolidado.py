import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class SantanderConsolidadoParser(BankParser):
    """
    Parser para o "Santander Extrato Consolidado" (Clássico e Inteligente).
    Implementa leitura virtual de blocos gigantes para evitar que o OCR 
    embaralhe os gatilhos de Seppuku em PDFs sem linhas divisórias.
    """

    _REGEX_DATA = re.compile(r'^(\d{2}/\d{2})$')
    _REGEX_VALOR = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}-?$')

    _MESES = {
        'janeiro': '01', 'fevereiro': '02', 'março': '03', 'marco': '03',
        'abril': '04', 'maio': '05', 'junho': '06', 'julho': '07',
        'agosto': '08', 'setembro': '09', 'outubro': '10',
        'novembro': '11', 'dezembro': '12'
    }

    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto_total = ""
                for page in pdf.pages[0:3]:
                    texto_total += (page.extract_text() or "") + " "
                texto_limpo = texto_total.lower()
            
            tem_banco = "santander" in texto_limpo
            tem_tipo = "consolidado" in texto_limpo
            tem_tabela = "movimentação" in texto_limpo or "movimentacao" in texto_limpo
            
            return tem_banco and tem_tipo and tem_tabela
        except Exception:
            return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        ano_extrato = "2025" 
        self.memoria_data = None 

        with pdfplumber.open(pdf_path) as pdf:
            texto_p1 = (pdf.pages[0].extract_text() or "").lower()
            for linha in texto_p1.split('\n'):
                m_periodo = re.search(r'(janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s*/\s*(\d{4})', linha)
                if m_periodo:
                    ano_extrato = m_periodo.group(2)
                    break

            processando = False
            fim_extrato = False 
            contador_saldo_em = 0 

            for page in pdf.pages:
                if fim_extrato:
                    break 

                words = page.extract_words(x_tolerance=1.5)
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
                    if self._REGEX_VALOR.match(w['text']) and w['x0'] > 200:
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
                        
                    # =========================================================
                    # RECONSTRUÇÃO TEXTUAL PERFEITA (Anti-Ovos Mexidos)
                    # =========================================================
                    lv_trigger = {}
                    for w in f_words:
                        matched = False
                        for t in lv_trigger:
                            if abs(w['top'] - t) < 5:
                                matched = t
                                break
                        if not matched:
                            matched = w['top']
                            lv_trigger[matched] = []
                        lv_trigger[matched].append(w)
                        
                    linhas_faixa = []
                    for t_linha in sorted(lv_trigger.keys()):
                        linha_words = sorted(lv_trigger[t_linha], key=lambda x: x['x0'])
                        linhas_faixa.append(' '.join(w['text'] for w in linha_words))
                        
                    texto_faixa = ' '.join(linhas_faixa).lower()
                    
                    if "movimentação" in texto_faixa or "movimentacao" in texto_faixa or ("data" in texto_faixa and "documento" in texto_faixa):
                        processando = True
                        continue
                        
                    if not processando:
                        continue
                        
                    # =========================================================
                    # PROTOCOLO DE SEPPUKU SEGURO
                    # =========================================================
                    morrer_nesta_faixa = False
                    
                    saldos_encontrados = len(re.findall(r'saldo em \d{2}/\d{2}', texto_faixa))
                    if saldos_encontrados > 0:
                        contador_saldo_em += saldos_encontrados
                        if contador_saldo_em >= 2:
                            morrer_nesta_faixa = True
                            
                    gatilhos_morte = ["saldos por período", "saldos por periodo", "a- bloqueio", "a - bloqueio", "se sua empresa não tiver limite"]
                    if any(gatilho in texto_faixa for gatilho in gatilhos_morte):
                        morrer_nesta_faixa = True

                    # Extrai os dados DA FAIXA antes de morrer (para não perder a última transação)
                    ts = self._processar_faixa(f_words, ano_extrato)
                    if ts:
                        transacoes.extend(ts)

                    # E, se a morte foi decretada, puxa a tomada do robô
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
                if self._REGEX_VALOR.match(texto) and w['x0'] > 250:
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
            
            lv_zone = {}
            for w in sorted(words_zone, key=lambda x: x['top']):
                matched = False
                for t in lv_zone:
                    if abs(w['top'] - t) < 4:
                        matched = t
                        break
                if not matched:
                    matched = w['top']
                    lv_zone[matched] = []
                lv_zone[matched].append(w)
                
            data_transacao = ""
            documento = ""
            desc_parts = []
            
            for t_linha in sorted(lv_zone.keys()):
                linha_words = sorted(lv_zone[t_linha], key=lambda w: w['x0'])
                textos_linha = []
                
                for w in linha_words:
                    texto = w['text'].replace('|', '').strip()
                    if not texto: continue
                    
                    if self._REGEX_DATA.match(texto) and w['x0'] < 100:
                        data_transacao = texto
                        continue
                        
                    if self._REGEX_VALOR.match(texto) and w['x0'] > 200:
                        continue
                        
                    textos_linha.append(texto)
                    
                if textos_linha:
                    ultimo_texto = textos_linha[-1]
                    if re.match(r'^\d{5,9}$', ultimo_texto):
                        documento = ultimo_texto
                        textos_linha.pop() 
                        
                if textos_linha:
                    desc_parts.append(' '.join(textos_linha))
                    
            data_final = data_transacao if data_transacao else self.memoria_data
            if data_final:
                self.memoria_data = data_final
                data_completa = f"{data_final}/{ano}"
                
                descricao_base = ' '.join(desc_parts).strip(' -')
                
                descricao_base = re.sub(r'extrato_pj_[^\s]*', '', descricao_base, flags=re.IGNORECASE)
                descricao_base = re.sub(r'balp_[^\s]*', '', descricao_base, flags=re.IGNORECASE)
                descricao_base = re.sub(r'p[aá]gina:\s*\d+/\d+', '', descricao_base, flags=re.IGNORECASE)
                descricao_base = re.sub(r'\s+', ' ', descricao_base).strip(' -')
                
                if documento:
                    descricao_final = f"{descricao_base} CONF. DOCTO. Nº {documento}".upper()
                else:
                    descricao_final = descricao_base.upper()
                    
                if not descricao_final.startswith("SALDO") and descricao_final:
                    transacoes_bloco.append({
                        'Data': data_completa,
                        'Descrição': descricao_final,
                        'Valor': a['valor']
                    })

        return transacoes_bloco

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
