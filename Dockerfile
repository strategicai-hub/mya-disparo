FROM python:3.11-slim

# Evita que o Python grave arquivos .pyc no disco e foca os prints de log em tempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instala as dependências
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copia o projeto
COPY . /app/
