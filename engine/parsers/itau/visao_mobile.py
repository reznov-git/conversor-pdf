import pdfplumber
import pandas as pd
import re
from engine.base import BankParser

class ItauMobileParser(BankParser):
    """
    Parser para o "Itaú Visão Mobile".
    Extrato de celular sem tabela fixa. Usa um buffer cumulativo de descrição 
    para lidar com transações multilinha e expurga os "..." decorativos do app.
    Inclui filtro para remover ícones e emoticons da interface do celular.
    """

    def identify(self, pdf_path: str) -> bool:
        """Verifica se é o extrato Itaú Visão Mobile."""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            
            texto_limpo = re.sub(r'\s+', ' ', texto)
            return "dados gerais" in texto_limpo
        except Exception:
            return False

    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        current_date = None
        fallback_date = "01/01/2025" 
        buffer_descricao = []

        headers_inuteis = [
            "itaú empresas", "dados gerais", "nome", "agência/conta",
            "data | horário", "consolidado", "itaú", "extrato completo"
        ]

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(x_tolerance=2)
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

                for t_linha in sorted(linhas_virtuais.keys()):
                    linha_words = sorted(linhas_virtuais[t_linha], key=lambda x: x['x0'])
                    texto_linha = ' '.join(w['text'] for w in linha_words).strip()

                    if not texto_linha:
                        continue

                    texto_limpo = texto_linha.lower().replace('|', '').strip()

                    # 1. Ignora os cabeçalhos fixos do layout
                    if texto_limpo in headers_inuteis or texto_limpo.startswith('agência/conta'):
                        continue

                    # 2. Captura Data de Geração do Arquivo
                    m_gen = re.search(r'^(\d{2}/\d{2}/\d{4})\s*\|?\s*\d{2}:\d{2}', texto_linha)
                    if m_gen:
                        fallback_date = m_gen.group(1)
                        continue

                    # 3. Captura o Cabeçalho de Data de Lançamentos
                    m_date = re.match(r'^(\d{2}/\d{2}/\d{4})', texto_linha)
                    if m_date and 'R$' not in texto_linha:
                        current_date = m_date.group(1)
                        buffer_descricao = [] 
                        continue

                    # 4. Encontrou uma Transação Financeira?
                    if 'R$' in texto_linha:
                        m_trans = re.search(r'^(.*?)\s*\|?\s*(-?\s*R\$\s*-?\s*[\d\.,]+)$', texto_linha, re.IGNORECASE)

                        if m_trans:
                            desc_part = m_trans.group(1)
                            desc_part = self._limpar_descricao(desc_part)

                            if desc_part:
                                buffer_descricao.append(desc_part)

                            desc_final = ' '.join(buffer_descricao).strip()

                            if 'saldo' not in desc_final.lower() and desc_final:
                                data_final = current_date if current_date else fallback_date
                                transacoes.append({
                                    'Data': data_final,
                                    'Descrição': desc_final.upper(),
                                    'Valor': self._normalize_value(val_str=m_trans.group(2))
                                })

                            buffer_descricao = [] 
                            continue

                    # 5. Se não for data nem valor, é um pedaço solto de descrição!
                    desc_part = self._limpar_descricao(texto_linha)
                    if desc_part and 'saldo do dia' not in desc_part.lower():
                        buffer_descricao.append(desc_part)

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)

    def _limpar_descricao(self, texto: str) -> str:
        """Limpa pontilhados e caracteres invisíveis/emoticons do OCR."""
        texto = re.sub(r'^\.+', '', texto)
        # O Regex abaixo mantém apenas letras (incluindo acentos), números, espaços e símbolos básicos (&, *, +, -, /, .)
        texto = re.sub(r'[^\w\s\.\-\/\&\*\+]', '', texto)
        texto = re.sub(r'\s+', ' ', texto)
        return texto.strip()

    def _normalize_value(self, val_str: str) -> float:
        """Trata números como '- R$ 2.971,38' removendo letras e símbolos com precisão."""
        val_str = str(val_str).strip().upper()
        if not val_str: return 0.0

        is_negative = '-' in val_str
        
        # Arranca o R$, os espaços e os pontos, deixando só dígitos e a vírgula
        val_str = re.sub(r'[^\d,]', '', val_str)
        val_str = val_str.replace(',', '.')

        try:
            val_float = float(val_str)
            return -val_float if is_negative else val_float
        except ValueError:
            return 0.0
