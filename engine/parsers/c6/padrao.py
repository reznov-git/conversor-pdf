import pdfplumber
import pandas as pd
import re
from collections import defaultdict
from engine.base import BankParser

class C6PadraoParser(BankParser):

    _X_DATA_CONT = 80
    _X_TIPO      = 140
    _X_DESC      = 220
    _X_VALOR     = 510

    _REGEX_DATA_LINHA = re.compile(r'^\d{2}/\d{2}$')
    _REGEX_VALOR      = re.compile(r'^-?R\$\s*[\d.,]+$')
    _REGEX_ANO        = re.compile(r'(\d{2}/\d{2}/\d{4})')

    _MESES_PT = {
        'janeiro': 1, 'fevereiro': 2, 'março': 3, 'marco': 3,
        'abril': 4, 'maio': 5, 'junho': 6, 'julho': 7,
        'agosto': 8, 'setembro': 9, 'outubro': 10,
        'novembro': 11, 'dezembro': 12,
    }

    _PREFIXOS_SALDO = (
        'saldo', '(-) saldo', '(+) saldo', '(-)saldo', '(+)saldo',
    )
    _GATILHO_PARADA = 'informações sujeitas a alteração'


    def identify(self, pdf_path: str) -> bool:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                texto = (pdf.pages[0].extract_text() or "").lower()
            return "extrato exportado no dia" in texto
        except Exception:
            return False


    def extract(self, pdf_path: str) -> pd.DataFrame:
        transacoes = []
        ano = None
        processando = False     

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(x_tolerance=3, y_tolerance=3)
                if not words:
                    continue

                linhas_map = defaultdict(list)
                for w in words:
                    linhas_map[round(w['top'])].append(w)

                if ano is None:
                    ano = self._extrair_ano(linhas_map)

                for top_y in sorted(linhas_map.keys()):
                    linha = sorted(linhas_map[top_y], key=lambda w: w['x0'])
                    texto_linha = ' '.join(w['text'] for w in linha).lower()

                    if self._GATILHO_PARADA in texto_linha:
                        processando = False
                        break

                    if not processando:
                        if 'lançamento' in texto_linha and 'contábil' in texto_linha:
                            processando = True
                        continue

                    if texto_linha.startswith('saldo contábil do dia'):
                        continue

                    t = self._parsear_linha(linha, ano)
                    if t:
                        transacoes.append(t)

        df = pd.DataFrame(transacoes, columns=['Data', 'Descrição', 'Valor'])
        if not df.empty:
            df['Data'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df = df.dropna(subset=['Data']).sort_values('Data').reset_index(drop=True)

        return self._clean_dataframe(df)


    def _extrair_ano(self, linhas_map: dict) -> int:
        for top_y in sorted(linhas_map.keys()):
            for w in linhas_map[top_y]:
                m = self._REGEX_ANO.search(w['text'])
                if m:
                    return int(m.group(1).split('/')[-1])

        for top_y in sorted(linhas_map.keys()):
            texto = ' '.join(w['text'].lower() for w in linhas_map[top_y])
            for mes_nome in self._MESES_PT:
                if mes_nome in texto:
                    m_ano = re.search(r'\b(\d{4})\b', texto)
                    if m_ano:
                        return int(m_ano.group(1))

        return pd.Timestamp.now().year

    def _parsear_linha(self, linha: list, ano: int) -> dict | None:

        data_words  = []
        tipo_words  = []
        desc_words  = []
        valor_words = []

        for w in linha:
            x = w['x0']
            if   x < self._X_DATA_CONT: pass                    
            elif x < self._X_TIPO:       data_words.append(w)
            elif x < self._X_DESC:       tipo_words.append(w)
            elif x < self._X_VALOR:      desc_words.append(w)
            else:                         valor_words.append(w)

        data_str  = ' '.join(w['text'] for w in data_words).strip()
        tipo_str  = ' '.join(w['text'] for w in tipo_words).strip()
        desc_str  = ' '.join(w['text'] for w in desc_words).strip()
        valor_str = ' '.join(w['text'] for w in valor_words).strip()

        if not self._REGEX_DATA_LINHA.match(data_str):
            return None

        desc_lower = desc_str.lower()
        if desc_lower.startswith(self._PREFIXOS_SALDO):
            return None
        if tipo_str.lower().startswith(self._PREFIXOS_SALDO):
            return None

        if not tipo_str or not desc_str:
            return None

        valor = self._normalize_value(valor_str)
        if valor == 0.0 and valor_str:
            return None
        if not valor_str:
            return None

        dia, mes = data_str.split('/')
        data_completa = f"{dia}/{mes}/{ano}"

        descricao = f"{tipo_str} - {desc_str}".upper()

        return {
            'Data': data_completa,
            'Descrição': descricao,
            'Valor': valor,
        }

    def _normalize_value(self, val_str: str) -> float:
        val_str = str(val_str).strip()
        if not val_str:
            return 0.0
        is_negative = val_str.startswith('-')
        val_str = val_str.replace('-', '').replace('R$', '').strip()
        val_str = val_str.replace('.', '').replace(',', '.')
        try:
            v = float(val_str)
            return -v if is_negative else v
        except ValueError:
            return 0.0
