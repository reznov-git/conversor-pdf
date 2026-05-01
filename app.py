import streamlit as st
import pandas as pd
import numpy as np
import tempfile
import os
import io
import unicodedata

from engine.parsers.itau.mensal_consolidado import ItauMensalConsolidadoParser

st.set_page_config(
    page_title="Conversor Contábil XLSX",
    layout="wide"
)

MODELOS_POR_BANCO = {
    "Itaú": ["Mensal Consolidado", "30 Horas", "Não Consolidado", "Visão Mobile"],
    "Banco do Brasil": ["Padrão"],
    "Bradesco": ["Padrão"],
    "BTG Pactual": ["Padrão"],
    "PagBank": ["Padrão"],
    "Safra": ["Padrão"]
}

def normalizar_nome(nome: str) -> str:
    nome_norm = unicodedata.normalize('NFKD', nome).encode('ASCII', 'ignore').decode('ASCII')
    return nome_norm.lower().replace(" ", "_")

def encontrar_imagem(nome_base: str) -> str:
    extensoes = ['.png', '.jpg', '.jpeg']
    for ext in extensoes:
        caminho = f"assets/{nome_base}{ext}"
        if os.path.exists(caminho):
            return caminho
    return None

st.title("Conversor de Extratos para Domínio")

with st.sidebar:
    st.header("1. Seleção do Extrato")
    
    banco_selecionado = st.selectbox(
        "Instituição Financeira",
        list(MODELOS_POR_BANCO.keys())
    )
    
    modelo_selecionado = st.selectbox(
        f"Modelo de Layout ({banco_selecionado})",
        MODELOS_POR_BANCO[banco_selecionado]
    )
    
    st.markdown("---")
    st.header("2. Parametrização Contábil")
    cta_banco = st.text_input("Cód. Conta Bancária (Ativo)", value="536")
    cta_trans_deb = st.text_input("Cód. Transitória (Débito)", value="555")
    cta_trans_cred = st.text_input("Cód. Transitória (Crédito)", value="555")

    st.markdown("---")
    st.subheader("Layout Suportado")
    
    nome_base_imagem = f"{normalizar_nome(banco_selecionado)}_{normalizar_nome(modelo_selecionado)}"
    caminho_imagem = encontrar_imagem(nome_base_imagem)
    
    if caminho_imagem:
        st.image(caminho_imagem, width=400) 
    else:
        st.info(f"Imagem não encontrada. Salve um print como 'assets/{nome_base_imagem}.png' ou '.jpg'")

arquivos_pdf = st.file_uploader("Arraste os PDFs dos extratos aqui", type=["pdf"], accept_multiple_files=True)

if arquivos_pdf:
    with st.spinner(f'A processar {len(arquivos_pdf)} arquivo(s) do {banco_selecionado} - {modelo_selecionado}...'):
        
        dfs_brutos = []
        
        for arquivo in arquivos_pdf:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(arquivo.read())
                caminho_pdf_temporario = tmp_file.name

            try:
                if banco_selecionado == "Itaú" and modelo_selecionado == "Mensal Consolidado":
                    parser = ItauMensalConsolidadoParser()
                else:
                    st.warning("O motor para este banco/modelo específico ainda está em desenvolvimento.")
                    st.stop()

                if not parser.identify(caminho_pdf_temporario):
                    st.error(f"Erro: O documento {arquivo.name} não parece corresponder ao layout selecionado.")
                    continue
                    
                df_temp = parser.extract(caminho_pdf_temporario)
                
                if not df_temp.empty:
                    dfs_brutos.append(df_temp)
                
            finally:
                if os.path.exists(caminho_pdf_temporario):
                    os.remove(caminho_pdf_temporario)

        if not dfs_brutos:
            st.warning("Aviso: Nenhuma transação financeira válida foi encontrada nos arquivos processados.")
            st.stop()

        df_bruto = pd.concat(dfs_brutos, ignore_index=True)
        df_bruto = df_bruto.sort_values('Data')

        saldos_para_ignorar = [
            'SALDO APLIC AUT MAIS',
            'SALDO ANTERIOR',
            'SALDO FINAL',
            'SALDO EM C/C'
        ]

        df_bruto = df_bruto[~df_bruto['Descrição'].str.upper().isin(saldos_para_ignorar)].copy()
        
        total_linhas = len(df_bruto)
        total_entradas = df_bruto[df_bruto['Valor'] > 0]['Valor'].sum()
        total_saidas = df_bruto[df_bruto['Valor'] < 0]['Valor'].sum()
        variacao_saldo = total_entradas + total_saidas
        
        df_dominio = pd.DataFrame()
        df_dominio['Data'] = df_bruto['Data'].dt.strftime('%d/%m/%Y')
        df_dominio['Cta. Débito'] = np.where(df_bruto['Valor'] > 0, cta_banco, cta_trans_deb)
        df_dominio['Cta. Crédito'] = np.where(df_bruto['Valor'] > 0, cta_trans_cred, cta_banco)
        df_dominio['Valor'] = df_bruto['Valor'].abs()
        df_dominio['Histórico Padrão'] = ""
        df_dominio['Descrição'] = df_bruto['Descrição'].str.upper()

        buffer_excel = io.BytesIO()
        with pd.ExcelWriter(buffer_excel, engine='openpyxl') as writer:
            df_dominio.to_excel(writer, index=False, sheet_name='Integracao_Dominio')
        
        dados_excel = buffer_excel.getvalue()

    st.success(f"{len(dfs_brutos)} extrato(s) consolidado(s) com sucesso!")
    
    st.markdown("### Asseguração Rápida")
    col1, col2, col3, col4 = st.columns(4)
    
    def formata_moeda(valor):
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
    col1.metric("Lançamentos Extraídos", f"{total_linhas} linhas")
    col2.metric("Soma das Entradas", formata_moeda(total_entradas))
    col3.metric("Soma das Saídas", formata_moeda(total_saidas))
    col4.metric("Variação do Período", formata_moeda(variacao_saldo))

    st.markdown("---")
    st.subheader("Pré-visualização do Layout Domínio")
    st.dataframe(df_dominio, use_container_width=True)

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        st.download_button(
            label="Baixar Planilha Unificada (.xlsx)",
            data=dados_excel,
            file_name=f"importacao_lote_{nome_base_imagem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
