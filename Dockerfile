# Dockerfile
# Використовуємо офіційний образ Playwright з встановленим Python та системними залежностями для браузерів
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Встановлюємо робочу директорію додатку
WORKDIR /app

# Налаштовуємо змінні середовища Python (вимкнення буферизації та створення pyc файлів)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Копіюємо requirements.txt окремо для кешування шарів Docker
COPY requirements.txt .

# Встановлюємо залежності Python
RUN pip install --no-cache-dir -r requirements.txt

# Встановлюємо офіційну стабільну версію Google Chrome для Playwright
RUN playwright install chrome

# Копіюємо всі файли проекту в контейнер
COPY . .

# Створюємо директорії під дані та логи (вони будуть перезаписані монтуванням volumes)
RUN mkdir -p data logs

# Експонуємо порт 8000 для FastAPI сервера / API дашборду
EXPOSE 8000

# Запускаємо оркестратор сканера та API
CMD ["python", "main.py"]
