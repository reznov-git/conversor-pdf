# Changelog

    Todas as mudanças notáveis neste projeto serão documentadas neste arquivo.

    O formato é baseado no Keep a Changelog (https://keepachangelog.com/pt-BR/1.0.0/), 
    e este projeto adere ao Semantic Versioning(https://semver.org/lang/pt-BR/).

## [1.0.0] - 2026-05-01

### Adicionado

    - Interface gráfica interativa construída com Streamlit.
    - Motor de extração para o Banco Itaú (Modelo: Mensal Consolidado).
    - Expressões regulares (Regex) avançadas para correção de falhas de OCR e descolamento de caracteres.
    - Filtros automáticos de exclusão para linhas de salto estrutural (ex: "SALDO FINAL", "SALDO APLIC AUT MAIS").
    - Conversão de saída nativa para o layout de integração do Domínio Sistemas (.xlsx).
    - Cálculos automáticos de asseguração rápida (soma de entradas, saídas e variação de período).
