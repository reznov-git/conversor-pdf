import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class SantanderIBEDiarioParser(BankParser):
    """
    Parser definitivo para o Santander Internet Banking Empresarial (IBE) Diário.
    Contorna o bug de FontBBox usando extract_words, aplica descontaminação Unicode
    para remover ícones do Santander e possui uma Trava Global (Hard Stop) no rodapé.
    """

    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            return "internet banking" in texto
        except Exception:
            return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        t_atual = []
        data_atual = None
        stop_parsing = False

        _REGEX_DATA = re.compile(r'^(\d{2}/\d{2}/\d{4})')
        _REGEX_VALOR = re.compile(r'(-\s*)?(?:R\$|R\s*\$)?\s*(-?[\d\.]*,\d{2})\s*$', re.IGNORECASE)

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                if stop_parsing:
                    break

                words = page.extract_words(x_tolerance=2, y_tolerance=2)
                if not words:
                    continue

                # 1. Agrupamento em Linhas Virtuais (Eixo Y)
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

                # 2. Varredura da Máquina de Estados
                for t in sorted(linhas_virtuais.keys()):
                    if stop_parsing:
                        break

                    linha = sorted(linhas_virtuais[t], key=lambda x: x['x0'])
                    texto_linha = ' '.join(w['text'] for w in linha).strip()

                    # --- DESCONTAMINAÇÃO UNICODE ---
                    # Remove caracteres especiais (ícones de calendário/cartão do Santander)
                    # Mantém apenas caracteres ASCII visíveis e acentuações do Latin-1
                    texto_linha = re.sub(r'[^\x20-\x7E\xA0-\xFF]', '', texto_linha).strip()

                    if not texto_linha:
                        continue

                    linha_lower = texto_linha.lower()
                    
                    # Filtra cabeçalhos isolados
                    if "internet banking" in linha_lower or "agência:" in linha_lower or ("data" in linha_lower and "histórico" in linha_lower):
                        continue
                        
                    # --- TRAVA GLOBAL (HARD STOP) ---
                    # Se achar o "A - Saldo", ativa o stop_parsing para ignorar o resto do documento inteiro
                    if "a-saldo" in linha_lower or "a - saldo" in linha_lower or "saldo de conta corrente" in linha_lower:
                        stop_parsing = True
                        break

                    # Gatilho: A linha contém uma nova Data?
                    m_data = _REGEX_DATA.match(texto_linha)
                    if m_data:
                        data_atual = m_data.group(1)
                        # Remove a data recém capturada do texto para não poluir o histórico
                        texto_linha = texto_linha[10:].strip()

                    if not texto_linha:
                        continue

                    # Joga o resto do texto na gaveta aberta
                    t_atual.append(texto_linha)

                    # Verifica se a linha encerra com a assinatura de valor
                    m_val = _REGEX_VALOR.search(texto_linha)
                    
                    # Se achou o valor, a transação acabou! Tranca a gaveta.
                    if m_val:
                        if data_atual:
                            transacoes.append((data_atual, t_atual))
                        t_atual = []

        # ==========================================
        # MONTAGEM FINAL
        # ==========================================
        transacoes_finais = []
        for data_str, t_lines in transacoes:
            bloco_str = " ".join(t_lines).strip()
            
            # Fogo amigo contra Saldos e Lixos isolados
            if "SALDO DO DIA" in bloco_str.upper() or "SALDO DE CONTA" in bloco_str.upper():
                continue

            m_vals = list(_REGEX_VALOR.finditer(bloco_str))
            if not m_vals:
                continue 
                
            m_val = m_vals[-1]
            val_str = m_val.group(0)
            
            # Tudo que vem antes do valor é o Histórico
            hist_str = bloco_str[:m_val.start()].strip()
            
            # Limpeza final de segurança (garantindo que datas residuais e espaços duplos morram)
            hist_str = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '', hist_str)
            hist_str = re.sub(r'\s+', ' ', hist_str).replace("- -", "-").strip(' -').upper()

            if not hist_str:
                continue

            # Extração Matemática
            is_negative = '-' in val_str
            val_limpo = re.sub(r'[^\d\.,]', '', val_str)
            v_num = self._normalize_value(val_limpo)

            if v_num == 0.0:
                continue
            if is_negative and v_num > 0:
                v_num = -v_num

            transacoes_finais.append({
                'Data': data_str,
                'Descrição': hist_str,
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
