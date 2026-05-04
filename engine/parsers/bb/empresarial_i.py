import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class BancoBrasilEmpresarialIParser(BankParser):

    _REGEX_DATA = re.compile(r'^(\d{2}/\d{2}/\d{4})')

    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            return "extrato de conta corrente" in texto and "saldo anterior" in texto
        except Exception:
            return False

    def _is_lixo_cabecalho(self, linha: str) -> bool:
        linha_lower = linha.lower()
        lixos = [
            "empresa", "consultas - extrato", "expansaoas", "versões anteriores",
            "extrato de conta corrente", "cliente", "agência", "conta corrente",
            "período do extrato", "lançamentos", "dt. balancete", "saldo anterior",
            "transação efetuada com sucesso", "serviço de atendimento",
            "ouvidoria bb", "para deficientes auditivos", "valor r$",
            "limite ouro", "taxa lim", "custo efetivo", "data vencimento",
            "informações complementares", "valor total devido", "valor liberado",
            "despesas vinculadas", "- tributos", "- tarifa", "(*) simulação"
        ]
        for l in lixos:
            if linha_lower.startswith(l): return True
        
        if re.match(r'^g\d{16}', linha_lower): return True
        if re.match(r'^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}', linha_lower): return True
        
        return False

    def _limpar_historico(self, miolo_str: str, prox_linha_str: str) -> str:
        miolo = re.sub(r'^\d{2}/\d{2}/\d{4}\s+\d{4}\s+', '', miolo_str).strip()
        
        m_lote = re.search(r'^(\d+)\s+(.+)', miolo)
        if m_lote:
            nums = m_lote.group(1)
            rest = m_lote.group(2)
            if len(nums) > 5:
                rest = f"{nums[5:]} {rest}"
        else:
            rest = miolo
            
        rest = re.sub(r'\d{2}/\d{2}\s+\d{2}:\d{2}\s+', '', rest)
        words = rest.split()
        clean_words = []
        for w in words:
            if re.match(r'^[\d\.]+$', w) and (len(w) >= 5 or '.' in w):
                continue
            clean_words.append(w)
        
        hist_1 = ' '.join(clean_words)
        
        hist_2 = ""
        if prox_linha_str:
            p = re.sub(r'^\d{2}/\d{2}\s+\d{2}:\d{2}\s*', '', prox_linha_str).strip()
            words_p = p.split()
            clean_words_p = []
            for w in words_p:
                if re.match(r'^[\d\.]+$', w) and (len(w) >= 5 or '.' in w):
                    continue
                clean_words_p.append(w)
            hist_2 = ' '.join(clean_words_p)
            
        if hist_2:
            return f"{hist_1} - {hist_2}".strip(" -").upper()
        else:
            return hist_1.strip(" -").upper()

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        linhas_uteis = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(x_tolerance=2, y_tolerance=2, extra_attrs=['non_stroking_color'])
                if not words: continue

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
                    linha_words = sorted(linhas_virtuais[t], key=lambda x: x['x0'])
                    texto_linha = ' '.join(w['text'] for w in linha_words)
                    
                    if not texto_linha.strip(): continue
                    if self._is_lixo_cabecalho(texto_linha): continue
                    linhas_uteis.append(texto_linha)

        i = 0
        while i < len(linhas_uteis):
            linha = linhas_uteis[i]
            linha_lower = linha.lower()

            if '999 s a l d o' in linha_lower or '999 saldo' in linha_lower:
                break

            m_data = self._REGEX_DATA.match(linha)
            
            if m_data:
                if 'saldo anterior' in linha_lower:
                    i += 1
                    continue

                data_str = m_data.group(1)
                
                matches_valor = list(re.finditer(r'((?:-?\d{1,3}(?:\.\d{3})*|\d+),\d{2})\s*([DC])', linha))
                
                if matches_valor:
                    valor_str = matches_valor[0].group(1)
                    tipo_dc = matches_valor[0].group(2)
                    
                    valor = self._normalize_value(valor_str)
                    if tipo_dc == 'D':
                        valor = -valor
                        
                    miolo = linha[m_data.end():matches_valor[0].start()].strip()
                    
                    prox_linha = ""
                    if i + 1 < len(linhas_uteis):
                        teste_prox = linhas_uteis[i + 1]
                        if not self._REGEX_DATA.match(teste_prox) and not teste_prox.lower().startswith('999'):
                            prox_linha = teste_prox
                            i += 1
                            
                    descricao_final = self._limpar_historico(miolo, prox_linha)
                            
                    transacoes.append({
                        'Data': data_str,
                        'Descrição': descricao_final,
                        'Valor': valor
                    })
            i += 1

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)

    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str: return 0.0
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            return 0.0
