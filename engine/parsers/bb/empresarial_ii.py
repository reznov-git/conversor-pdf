import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class BancoBrasilEmpresarialIIParser(BankParser):
    """
    Parser definitivo para o BB Empresarial II.
    Motor de Data e Valor Intocável.
    Inclui o 'Pente Fino Final' de controle de qualidade das descrições.
    """

    _X_DATA = 90
    _X_LOTE = 145
    _X_DOC = 250
    _X_HIST = 450

    _REGEX_DATA_FLEX = re.compile(r'^\d{2}/\d{2}(?:/\d{1,4})?$')

    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            
            tem_bb = "extrato de conta corrente" in texto and "agência:" in texto and "conta:" in texto
            return tem_bb
        except Exception:
            return False

    def _is_lixo_cabecalho(self, texto_linha: str) -> bool:
        txt = texto_linha.strip()
        lixos_exatos = ["extrato de conta corrente", "lançamentos"]
        
        if txt in lixos_exatos: return True
        if txt.startswith("cliente"): return True
        if "agência:" in txt and "conta:" in txt: return True
        if "dia" in txt and "lote" in txt and "documento" in txt: return True
        
        if txt.startswith("total aplicações financeiras"): return True
        if txt.startswith("saldos por dia base"): return True
        if txt.startswith("sujeitos a confirmação"): return True
        if txt == "0,00": return True
        
        return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        t_atual = None
        
        ano_global = str(pd.Timestamp.now().year)

        with pdfplumber.open(pdf_path) as pdf:
            texto_p1 = (pdf.pages[0].extract_text() or "").lower()
            
            m_ano = re.search(r'\b(20\d{2})\b', texto_p1)
            if m_ano:
                ano_global = m_ano.group(1)

            for page in pdf.pages:
                words = page.extract_words(x_tolerance=2, y_tolerance=2)
                if not words:
                    continue

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

                for t in sorted(linhas_virtuais.keys()):
                    linha = sorted(linhas_virtuais[t], key=lambda x: x['x0'])
                    texto_linha = ' '.join(w['text'] for w in linha).lower()
                    
                    if self._is_lixo_cabecalho(texto_linha):
                        continue
                    
                    data_linha = None
                    for w in linha:
                        if w['x0'] < self._X_DATA:
                            txt = w['text'].strip()
                            if self._REGEX_DATA_FLEX.match(txt) or txt == "00/00/0000":
                                # --- ANTI-FALSO-GATILHO PARA HISTÓRICOS ---
                                texto_completo = ' '.join(wx['text'] for wx in linha)
                                if re.search(r'^\d{2}/\d{2}\s+\d{2}:\d{2}', texto_completo):
                                    pass # É o histórico do Pix (DD/MM HH:MM), ignora o falso alarme de data
                                else:
                                    data_linha = txt
                                    break
                                # ------------------------------------------
                                
                    if data_linha:
                        if t_atual and t_atual['data'] != "00/00/0000":
                            transacoes.append(t_atual)
                            
                        if data_linha != "00/00/0000":
                            dia_mes = data_linha[:5] 
                            data_linha = f"{dia_mes}/{ano_global}"
                            
                        t_atual = {'data': data_linha, 'doc': [], 'hist': [], 'valor': []}

                    if t_atual is not None:
                        for w in linha:
                            txt = w['text'].strip()
                            x = w['x0']
                            
                            # Ignora as datas que serviram de gatilho, mas DEIXA o 01/07 do histórico passar!
                            if x < self._X_DATA and not re.search(r'^\d{2}/\d{2}\s+\d{2}:\d{2}', ' '.join(wx['text'] for wx in linha)):
                                pass 
                            elif x < self._X_LOTE:
                                pass 
                            elif x < self._X_DOC:
                                t_atual['doc'].append(txt)
                            elif x < self._X_HIST:
                                t_atual['hist'].append(txt)
                            else:
                                t_atual['valor'].append(txt)

            if t_atual and t_atual['data'] != "00/00/0000":
                transacoes.append(t_atual)

        # ==========================================
        # MONTAGEM FINAL COM FILTRO SEMÂNTICO E SNIPER
        # ==========================================
        transacoes_finais = []
        for t in transacoes:
            doc_str = "".join(t['doc']).strip()
            val_flat = "".join(t['valor']).strip()
            hist_flat = " ".join(t['hist']).upper()

            hist_clean = hist_flat.replace(' ', '')
            if not val_flat or "SALDO" in hist_clean:
                continue

            hist_flat = hist_flat.replace("- ", " ").replace(" - ", " ")
            hist_flat = re.sub(r'\s+', ' ', hist_flat).strip()

            for tarifa in ["TARIFA PIX ENVIADO", "TARIFA PIX RECEBIDO"]:
                idx = hist_flat.find(tarifa)
                if idx > 0:
                    hist_flat = hist_flat[:idx].strip()
            
            m_time = re.search(r'(\d{2}/\d{2})\s+(\d{2}:\d{2})\s*', hist_flat)
            
            if m_time:
                time_str = m_time.group(0)
                rest_hist = hist_flat.replace(time_str, ' ').strip()
                
                known_types = [
                    "PIX RECEBIDO QR CODE", "PIX-RECEBIDO QR CODE", 
                    "PIX RECEBIDO", "PIX-RECEBIDO", "PIX ENVIADO", "PIX-ENVIADO"
                ]
                
                type_found = ""
                for k_type in known_types:
                    if k_type in rest_hist:
                        type_found = k_type
                        rest_hist = rest_hist.replace(k_type, ' ').strip()
                        break
                        
                if not type_found:
                    type_found = "PIX"
                    
                rest_hist = re.sub(r'\s+', ' ', rest_hist).strip()
                type_found = type_found.replace("-", " ") 
                type_found = re.sub(r'\s+', ' ', type_found)
                
                if rest_hist:
                    desc = f"{type_found} - {rest_hist}".strip()
                else:
                    desc = type_found
            else:
                desc = hist_flat
                
            if doc_str:
                desc += f" CONF. DOCTO. Nº {doc_str}"

            is_negative = '-' in val_flat or '(-)' in val_flat
            val_limpo = re.sub(r'[^\d\.,]', '', val_flat)
            v_num = self._normalize_value(val_limpo)
            
            if v_num == 0.0:
                continue
            if is_negative and v_num > 0:
                v_num = -v_num

            # ==========================================
            # CAMADA EXTRA: PENTE FINO (Conforme Regras)
            # ==========================================
            pix_terms = ["PIX RECEBIDO QR CODE", "PIX RECEBIDO", "PIX ENVIADO", "PIX"]
            
            prefixo_inicial = ""
            for pt in pix_terms:
                if desc.startswith(pt):
                    prefixo_inicial = pt
                    break
                    
            if prefixo_inicial:
                # Regra 1 (Parte A): Protege o prefixo inicial e lima o restante
                resto = desc[len(prefixo_inicial):]
                for pt in pix_terms:
                    resto = resto.replace(pt, "")
                desc = prefixo_inicial + resto
                
                # Regra 2 e 3: Verificação de Sanidade da Polaridade
                if v_num < 0 and ("RECEBIDO" in prefixo_inicial):
                    # É saída, mas começa com recebido (ex: PIX RECEBIDO). Excluí-se o RECEBIDO.
                    desc = desc.replace("RECEBIDO", "", 1)
                elif v_num > 0 and ("ENVIADO" in prefixo_inicial):
                    # É entrada, mas começa com enviado (ex: PIX ENVIADO). Excluí-se o ENVIADO.
                    desc = desc.replace("ENVIADO", "", 1)
            else:
                # Regra 1 (Parte B): Se não começou com Pix, elimina qualquer vestígio de "PIX" infiltrado no meio
                for pt in pix_terms:
                    desc = desc.replace(pt, "")

            # Limpeza final de formatação gerada pela extração
            desc = re.sub(r'\s+', ' ', desc).strip()
            desc = desc.replace(" - -", " -").replace("- CONF", "CONF")
            if desc.startswith("- "):
                desc = desc[2:]

            transacoes_finais.append({
                'Data': t['data'],
                'Descrição': desc.strip(),
                'Valor': v_num
            })

        df = pd.DataFrame(transacoes_finais, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)

    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str:
            return 0.0
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            return 0.0
