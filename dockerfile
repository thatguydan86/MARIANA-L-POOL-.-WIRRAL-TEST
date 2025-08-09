FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Force Python to run in unbuffered mode so that log output is flushed
# immediately. This helps ensure Railway shows logs instead of getting
# stuck at "Starting Container".
CMD ["python", "-u", "main.py"]
