import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class ItauMensalConsolidadoParser(BankParser):
    
    def identify(self, pdf_path: str) -> bool:
        with pdfplumber.open(pdf_path) as pdf:
            primeira_pagina = pdf.pages[0].extract_text()
            if not primeira_pagina: return False
            primeira_pagina = primeira_pagina.lower()
            return "extrato mensal" in primeira_pagina and "conta corrente" in primeira_pagina

    def _normalize_value(self, val_str) -> float:
        if pd.isna(val_str) or val_str is None or str(val_str).strip() in ('', 'None'):
            return 0.0
        val_str = str(val_str).strip()
        is_negative = val_str.endswith('-')
        if is_negative: val_str = val_str[:-1].strip()
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            value = float(val_str)
            return -value if is_negative else value
        except ValueError: return 0.0

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        ano_extrato = None
        mes_extrato_num = None
        
        meses_map = {'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6, 
                     'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12}
        
        regex_ano = re.compile(r'(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\s+(\d{4})', re.IGNORECASE)
        regex_data = re.compile(r'^(\d{2})/(\d{2})$')
        regex_valor = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}-?$')

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[0:2]:
                match = regex_ano.search(page.extract_text() or "")
                if match: 
                    mes_texto = match.group(1).lower()
                    mes_extrato_num = meses_map.get(mes_texto)
                    ano_extrato = int(match.group(2))
                    break
            
            if not ano_extrato:
                ano_extrato = pd.Timestamp.now().year
                mes_extrato_num = pd.Timestamp.now().month

            ultima_data_valida = None
            processando_cc = False 
            extracao_concluida = False

            for page in pdf.pages:
                if extracao_concluida: break 
                    
                texto_pagina = page.extract_text()
                if not texto_pagina: continue

                for linha in texto_pagina.split('\n'):
                    linha_limpa = linha.replace('|', ' ').strip()
                    linha_limpa = re.sub(r'([a-zA-Z/])(-?\d{1,3}(?:\.\d{3})*,\d{2}-?)$', r'\1 \2', linha_limpa)
                    linha_limpa = re.sub(r'(,\d{2})\s+-', r'\1-', linha_limpa)
                    
                    legendas_remover = [
                        "agendamento", "ações movimentadas", "pela bolsa de valores",
                        "crédito a compensar", "débito a compensar", "aplicação programada",
                        "poupança automática", "poupanca automatica", "para demais siglas", "consulte as notas",
                        "explicativas no final", "explicativas nofinal", "do extrato", "doextrato",
                        "explicativas no", "final do extrato", "notas explicativas"
                    ]
                    
                    limpando = True
                    while limpando:
                        limpando = False
                        match_prefixo = re.match(r'^(\$?[a-zA-Z]\s*=\s*|^[a-zA-Z]\s+)', linha_limpa)
                        if match_prefixo:
                            linha_limpa = linha_limpa[match_prefixo.end():].strip()
                            limpando = True
                            
                        linha_lower_teste = linha_limpa.lower()
                        for leg in legendas_remover:
                            if linha_lower_teste.startswith(leg):
                                linha_limpa = linha_limpa[len(leg):].strip()
                                if linha_limpa.startswith((',', '-')): linha_limpa = linha_limpa[1:].strip()
                                limpando = True
                                break
                                
                        if linha_limpa.startswith('='):
                            linha_limpa = linha_limpa[1:].strip()
                            limpando = True

                    linha_lower = linha_limpa.lower()

                    
                    if linha_lower.startswith("saldo em c/c"):
                        extracao_concluida = True
                        break 

                    if "conta corrente" in linha_lower and "movimentação" in linha_lower and "siglas" not in linha_lower:
                        processando_cc = True
                    
                    gatilhos_parada = [
                        "resumo mês", "conta corrente compras a", "conta corrente débitos autom", 
                        "cheque especial", "02. investimentos", "notas explicativas",
                        "totalizador de aplicações", "movimentação - aplicações", "saques efetuados"
                    ]
                    
                    if processando_cc and any(x in linha_lower for x in gatilhos_parada):
                        processando_cc = False
                        
                    if not processando_cc: continue

                    tokens = linha_limpa.split()
                    if not tokens: continue
                    if len(tokens[0]) == 1 and tokens[0].isalpha(): tokens = tokens[1:]
                    if not tokens: continue

                    match_data = regex_data.match(tokens[0])
                    if match_data:
                        dia_t, mes_t = match_data.groups()
                        ano_t = ano_extrato - 1 if mes_extrato_num == 1 and int(mes_t) == 12 else (ano_extrato + 1 if mes_extrato_num == 12 and int(mes_t) == 1 else ano_extrato)
                        ultima_data_valida = f"{dia_t}/{mes_t}/{ano_t}"
                        tokens = tokens[1:] 
                    
                    if not ultima_data_valida: continue

                    valores_encontrados = []
                    while tokens and regex_valor.match(tokens[-1]):
                        valores_encontrados.insert(0, tokens.pop())

                    desc_row = " ".join(tokens).strip()

                    desc_row = re.sub(r'^(?:\$[a-zA-Z]=|[a-zA-Z]=|=)\s*', '', desc_row).strip()
                    
                    if not desc_row or len(desc_row) < 3 or not valores_encontrados: continue
                    if not re.search(r'[a-zA-Z]', desc_row): continue
                    
                    palavras_bloqueadas = (
                        "saldo anterior", "saldo em c/c", "saldo final", "total", "descrição", 
                        "entradas r$", "saídas r$", "outras entradas", "outras saídas", "data",
                        "(-) saldo a liberar", "saldo a liberar", "saldo final dispon"
                    )
                    if desc_row.lower().startswith(palavras_bloqueadas): continue

                    if re.match(r'^\d{2}/\d{2}/\d{2,4}', desc_row): continue

                    valor_transacao = self._normalize_value(valores_encontrados[0])
                    if valor_transacao != 0:
                        transacoes.append({'Data': ultima_data_valida, 'Descrição': desc_row.upper(), 'Valor': valor_transacao})

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data')
        return self._clean_dataframe(df)
