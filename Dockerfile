# Используем официальный образ Python 3.11
FROM python:3.11-slim

# Обновляем pip и устанавливаем зависимости
RUN pip install --upgrade pip

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект внутрь контейнера
COPY . .
#
# Экспортируем порт, который будет слушать uvicorn
EXPOSE 5005

# Команда запуска FastAPI приложения
CMD ["python", "bot.py"]