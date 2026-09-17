FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN groupadd --system app && useradd --system --gid app --home /srv app
WORKDIR /srv/frontend
COPY frontend/requirements.txt .
RUN pip install -r requirements.txt
COPY frontend/ .
USER app
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
