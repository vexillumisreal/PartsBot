# 🌐 Подключение Google Workspace MCP к PartsBot

Данный проект настроен для работы с **Google Workspace MCP** (Model Context Protocol). Это позволяет AI-ассистенту и инструментам напрямую взаимодействовать с вашим Google Диском, Google Таблицами (Google Sheets), Документами, Gmail и Календарём.

---

## 📁 Структура конфигурации

1. **Раннер MCP-сервера с исправлением upstream-бага**:
   - Файл: [run_google_workspace_mcp.py](file:///e:/Users/vexillum/Documents/Work_2.0/PartsBot/.agents/scripts/run_google_workspace_mcp.py)
   - Решает проблему несовместимости `FastMCP` в последних версиях библиотеки.
2. **Конфигурация проекта**:
   - Файл: [.agents/mcp_config.json](file:///e:/Users/vexillum/Documents/Work_2.0/PartsBot/.agents/mcp_config.json)
3. **Глобальная конфигурация IDE**:
   - Файл: `C:\Users\vexillum\.gemini\config\mcp_config.json`

---

## 🔑 Как получить учетные данные Google Workspace (OAuth 2.0)

Для работы с вашими таблицами и диском требуются 3 ключа:
- `GOOGLE_WORKSPACE_CLIENT_ID`
- `GOOGLE_WORKSPACE_CLIENT_SECRET`
- `GOOGLE_WORKSPACE_REFRESH_TOKEN`

### Шаг 1. Создание проекта в Google Cloud Console
1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/).
2. Создайте новый проект (например, `PartsBot-Workspace`).
3. В разделе **APIs & Services > Library** включите:
   - **Google Sheets API**
   - **Google Drive API**
   - **Google Docs API** (опционально)
   - **Gmail API** (опционально)
   - **Google Calendar API** (опционально)

### Шаг 2. Настройка OAuth Consent Screen
1. Перейдите в **APIs & Services > OAuth consent screen**.
2. Выберите тип **External** (или **Internal**, если у вас корпоративный Google Workspace).
3. Заполните название приложения и контактную почту.
4. В разделе **Scopes** добавьте:
   - `https://www.googleapis.com/auth/spreadsheets`
   - `https://www.googleapis.com/auth/drive`
5. В разделе **Test users** добавьте ваш Google-аккаунт.

### Шаг 3. Создание Client ID и Client Secret
1. Перейдите в **APIs & Services > Credentials**.
2. Нажмите **Create Credentials > OAuth client ID**.
3. Выберите тип приложения: **Web application**.
4. В поле **Authorized redirect URIs** добавьте:
   - `https://developers.google.com/oauthplayground`
5. Сохраните полученные **Client ID** и **Client Secret**.

### Шаг 4. Получение Refresh Token через OAuth 2.0 Playground
1. Откройте [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/).
2. Нажмите на значок шестеренки (⚙️) в правом верхнем углу.
3. Отметьте чекбокс **«Use your own OAuth credentials»**.
4. Вставьте ваши `Client ID` и `Client Secret`.
5. В списке слева выберите нужные API:
   - Google Sheets API v4 (`https://www.googleapis.com/auth/spreadsheets`)
   - Drive API v3 (`https://www.googleapis.com/auth/drive`)
6. Нажмите **Authorize APIs** и выполните вход под вашим Google-аккаунтом.
7. На шаге 2 нажмите **Exchange authorization code for tokens**.
8. Скопируйте значение **Refresh token**.

---

## ⚙️ Установка ключей

Добавьте ключи в файл [.env](file:///e:/Users/vexillum/Documents/Work_2.0/PartsBot/.env) или напрямую в [.agents/mcp_config.json](file:///e:/Users/vexillum/Documents/Work_2.0/PartsBot/.agents/mcp_config.json):

```env
GOOGLE_WORKSPACE_CLIENT_ID="ваш-client-id.apps.googleusercontent.com"
GOOGLE_WORKSPACE_CLIENT_SECRET="ваш-client-secret"
GOOGLE_WORKSPACE_REFRESH_TOKEN="ваш-refresh-token"
GOOGLE_WORKSPACE_ENABLED_CAPABILITIES='["drive", "sheets", "docs", "gmail", "calendar"]'
```

После перезапуска сессии AI-ассистент получит инструменты:
- `sheets_read_range`, `sheets_write_range`, `sheets_append_rows`
- `drive_search_files`, `drive_read_file_content`, `drive_upload_file`
- `docs_create_document`, `gmail_send_email`, `calendar_get_events`
