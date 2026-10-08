FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY ticketing ./ticketing
COPY tests ./tests
RUN pip install --no-cache-dir -c requirements.lock '.[test]'
CMD ["uvicorn", "ticketing.main:app", "--host", "0.0.0.0", "--port", "8000"]
