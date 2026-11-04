# build-pipeline Specification

## Purpose

Определяет проверку и сборку поддерживаемых вариантов панели в основном GitHub-репозитории, чтобы распространяемые результаты соответствовали проверенному исходному коду.

## Requirements

### Requirement: Locked Python environments
Проект SHALL хранить зависимости в `pyproject.toml` и `uv.lock`. CI и Docker SHALL использовать locked-установку uv. Dev-зависимости браузерных проверок и инструменты бинарной сборки SHALL быть выделены в отдельные группы; runtime-установка SHALL не включать эти группы. Версии Python и существующие runtime pins SHALL сохраняться при миграции.

#### Scenario: Locked dependency installation
- **WHEN** выполняется проверка, бинарная сборка или сборка Docker
- **THEN** используется закоммиченный uv lockfile и соответствующий набор групп без повторного разрешения зависимостей

### Requirement: Verification and build triggers
Репозиторий SHALL запускать проверки на pull request и сборки с проверками на push в main, push версионного тега `v*` и ручной запуск. Pull request и ручной запуск SHALL работать без публикации релизов и production-тегов контейнеров.

#### Scenario: Pull request verification
- **WHEN** открыт или обновлён pull request
- **THEN** выполняются проверки без записи в Releases и GHCR

#### Scenario: Main and version builds
- **WHEN** отправлен коммит в main или версионный тег `v*`
- **THEN** запускаются проверки и полная поддерживаемая матрица сборок того же коммита

#### Scenario: Manual verification
- **WHEN** оператор вручную запускает workflow для выбранного ref
- **THEN** выполняются проверки и сборки с сохранением файлов в Actions artifacts без релиза и изменения production-тегов

### Requirement: Publication requires successful verification
Публикация SHALL происходить только для push в `akaUNik/Amnezia-Web-Panel` в main или на допустимый версионный тег после успешных проверок того же commit SHA. Опубликованный релиз SHALL требовать успешной сборки всех бинарников и обоих вариантов контейнеров.

#### Scenario: Verification failure
- **WHEN** проверка коммита завершается ошибкой
- **THEN** его релиз и контейнеры не публикуются

#### Scenario: Partial build failure
- **WHEN** одна из обязательных сборок завершается ошибкой
- **THEN** новый релиз не становится опубликованным

#### Scenario: Fork execution
- **WHEN** workflow запускается в другом репозитории
- **THEN** он не публикует результаты в основной репозиторий или его GHCR-пакет

### Requirement: Supported build matrix
Сборка SHALL производить бинарники Linux x86_64 и Windows x86_64 с ресурсами панели; обычный Docker-образ для `linux/amd64,linux/arm64` и WARP-образ для `linux/amd64`. Архитектуры SHALL быть указаны в метаданных сборки и инструкции скачивания.

#### Scenario: Complete matrix
- **WHEN** сборка завершается успешно
- **THEN** доступны два бинарника и оба варианта образов с указанными архитектурами

#### Scenario: Executable verification
- **WHEN** проверяется бинарник на соответствующей ОС
- **THEN** он запускается с временным состоянием и отдаёт страницу входа и ресурсы интерфейса

### Requirement: Existing regression coverage
CI SHALL сохранять unittest discovery, браузерные регрессии с Chromium и существующий набор root-регрессий с временным состоянием. Проверки SHALL использовать тестовые данные без обращения к рабочим серверам и учётным данным.

#### Scenario: Root regression isolation
- **WHEN** CI выполняет проверки с root
- **THEN** файлы состояния создаются во временном каталоге и ошибка проверки завершает job неуспешно

### Requirement: Release version identity
Релизные теги SHALL не содержать build metadata (`+...`), поскольку Docker не поддерживает этот символ в тегах. До публикации версионного тега система SHALL проверять корректность SemVer и соответствие версии приложения тегу с нормализацией префикса `v`. Несоответствие SHALL блокировать публикацию бинарников в Releases и версионных образов.

#### Scenario: Mismatched source version
- **WHEN** тег `v1.9.0` указывает на исходники с версией `v1.8.4`
- **THEN** публикация версии завершается ошибкой до записи релизных файлов и образов

#### Scenario: Invalid version tag
- **WHEN** отправлен тег `vnext`, не являющийся SemVer
- **THEN** workflow явно отклоняет его как релизную версию
