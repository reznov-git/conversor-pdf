# Conversor Contábil - PDF para Excel (XLSX)

    Esta ferramenta foi desenvolvida para automatizar a extração de dados de extratos bancários em PDF 
    (frequentemente caóticos) e convertê-los em planilhas padronizadas (.xlsx) prontas para escrituração.

## Funcionalidades

    - Processamento em lote de múltiplos arquivos PDF simultaneamente.

    - Leitura estrutural à prova de falhas de alinhamento e caracteres colados.

    - Filtro inteligente de saldos marginais e lixo de cabeçalho/rodapé.

    - Geração automática das colunas de Cta. Débito, Cta. Crédito, Valor, Data e Histórico
    (Padrão Domínio Sistemas).

    - Interface gráfica minimalista via web browser.

## Layouts Suportados

    Até o momento (versão 1.0.0) o único modelo estável e resiliente ao teste de estresse é o Itaú Consolidado
    (verificar /assets/). Os demais extratos e modelos estão em fase de desenvolvimento.

## Instalação e Configuração

    Este projeto requer Python 3.9 ou superior.

### Clone o repositório:

    git clone https://github.com/reznov-git/conversor-pdf.git
    cd conversor-pdf

### Crie e ative um ambiente virtual (Recomendado):
    python -m venv venv
    source venv/bin/activate  # No Windows, use: venv\Scripts\activate

### Instale as dependências:

    pip install -r requirements.txt

## Como Usar

    Com o ambiente virtual ativado, inicie a interface gráfica rodando o comando abaixo na raiz do projeto:

    streamlit run app.py

    O painel abrirá automaticamente no seu navegador padrão (a princípio, em http://localhost:8501). 
    Selecione o banco, parametrize as contas contábeis, arraste os PDFs e baixe a planilha consolidada.
