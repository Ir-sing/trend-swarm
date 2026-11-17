# 🐝 trend-swarm

> **Автономный рой ИИ-агентов для непрерывного анализа и выявления трендов.**

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**trend-swarm** — это масштабируемый бэкенд на Python, который разворачивает команду автономных агентов (`AutonomousScout`) для мониторинга источников данных, семантического анализа контента и построения математических моделей трендов. Система использует динамическую маршрутизацию LLM (`DynamicModelRouter`) и векторную базу данных для накопления "памяти" и обмена сигналами между агентами через общую доску (`SwarmBlackboard`).

---

## ✨ Ключевые особенности

- **🤖 Автономные агенты (Scouts):** Непрерывный цикл сбора данных, управляемый состоянием `SwarmController`.
- **🧠 Динамическая маршрутизация LLM:** Гибкий выбор моделей через `litellm` для баланса между стоимостью и качеством анализа.
- **📊 Математический движок трендов:** Встроенный `TrendMathEngine` для расчета метрик и выявления значимых паттернов.
- **🔗 Семантическая память:** Использование `pgvector` для хранения и поиска эмбеддингов, что позволяет агенту "помнить" контекст.
- **🔄 Общая доска сигналов (Blackboard):** Агенты координируют действия через базу данных, обмениваясь приоритетными задачами и результатами кросс-проверки.
- **📡 Гибкие коннекторы:** `ResourceConnectorEngine` для подключения различных источников данных (RSS, веб-страницы и др.).
- **🐳 Готов к развертыванию:** Полностью контейнеризирован (Dockerfile) и поставляется с `docker-compose` для быстрого старта.

---

## 🏗️ Архитектура и стек

Проект построен на асинхронном Python-стеке:

- **Веб-фреймворк:** FastAPI + Uvicorn
- **База данных:** PostgreSQL 16 с расширением `pgvector`
- **ORM:** SQLAlchemy 2.0 (async)
- **LLM-интеграция:** LiteLLM
- **Парсинг:** BeautifulSoup4, Feedparser, HTTPX
- **Валидация:** Pydantic v2

### Как это работает (кратко)

1.  **Запуск:** `SwarmController` инициализирует пул агентов `AutonomousScout`.
2.  **Патрулирование:** Агент ищет активные сигналы на `SwarmBlackboard`. Если сигналов нет, он переходит в режим патрулирования ресурсов (`patrol_resources`).
3.  **Анализ:** При обнаружении цели агент захватывает ресурс, извлекает данные через коннекторы, прогоняет их через `TrendMathEngine` и LLM.
4.  **Координация:** Результаты записываются в телеметрию (`AgentTelemetry`) и в семантическую память (`TrendMemory`), после чего сигнал на доске помечается как обработанный.

---

## 🚀 Быстрый старт (Docker Compose)

Самый простой способ запустить проект — использовать Docker Compose.

### 1. Клонируйте репозиторий

```bash
git clone https://github.com/Ir-sing/trend-swarm.git
cd trend-swarm
```

### 2. Настройте переменные окружения

Создайте файл `.env` в корне проекта (рядом с `docker-compose.yml`) и добавьте необходимые ключи. Пример:

```env
# Пример конфигурации (уточните необходимые переменные в коде)
DATABASE_URL=postgresql+asyncpg://swarm_admin:swarm_password@db:5432/trend_swarm
# Ключи для LLM (например, для LiteLLM)
OPENAI_API_KEY=ваш_ключ
# ANTHROPIC_API_KEY=...
```

### 3. Запустите стек

```bash
docker-compose up --build -d
```

- **Приложение (API):** `http://localhost:8000`
- **PostgreSQL:** `localhost:5432` (пользователь: `swarm_admin`, пароль: `swarm_password`)

### 4. Остановка

```bash
docker-compose down
```

> **Примечание:** Файл `docker-compose.yml` использует образ `ghcr.io/ir-sing/trend-swarm:latest`. При необходимости вы можете изменить тег или собрать образ локально (секция `build: .` уже присутствует).

---

## 💻 Локальная разработка (без Docker)

Если вы хотите запустить проект напрямую для отладки:

1.  Убедитесь, что у вас установлен **Python 3.11+** и запущен **PostgreSQL** с расширением `pgvector`.
2.  Создайте виртуальное окружение и установите зависимости:

    ```bash
    python -m venv venv
    source venv/bin/activate  # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

3.  Настройте переменную `DATABASE_URL` (см. `.env` пример выше), указывая на вашу локальную БД.
4.  Запустите приложение:

    ```bash
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    ```

---

## 📂 Структура проекта (основное)

```text
trend-swarm/
├── app/
│   ├── main.py              # Точка входа FastAPI (предположительно)
│   ├── swarm_worker.py      # Ядро: AutonomousScout, логика роя
│   ├── database.py          # Асинхронное подключение к БД (AsyncSessionLocal)
│   ├── models.py            # Модели SQLAlchemy (Resource, TrendMemory и др.)
│   ├── connectors.py        # ResourceConnectorEngine
│   ├── llm_router.py        # DynamicModelRouter
│   └── math_engine.py       # TrendMathEngine
├── .github/workflows/       # CI/CD (docker-publish.yml)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env                     # (создается локально, не коммитится)
```

---

## ⚙️ Конфигурация

Основные параметры задаются через переменные окружения (в файле `.env` или в `docker-compose.yml`):

| Переменная | Описание | Пример |
| :--- | :--- | :--- |
| `DATABASE_URL` | Строка подключения к PostgreSQL (async). | `postgresql+asyncpg://user:pass@db:5432/trend_swarm` |
| `*_API_KEY` | Ключи для провайдеров LLM, используемых в `litellm`. | `OPENAI_API_KEY=sk-...` |

---

## 🤝 Вклад в проект

Проект находится в активной разработке. Если вы хотите предложить улучшения или сообщить об ошибке, создавайте Issue или отправляйте Pull Request.

1.  Форкните репозиторий.
2.  Создайте ветку для вашей фичи (`git checkout -b feature/amazing-feature`).
3.  Закоммитьте изменения (`git commit -m 'Add some amazing feature'`).
4.  Запушьте ветку (`git push origin feature/amazing-feature`).
5.  Откройте Pull Request.

---