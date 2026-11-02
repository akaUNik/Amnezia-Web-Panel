# Spec Delta

## Purpose

Определяет хранение и получение собственных файлов сборки, версионных релизов и связанных контейнерных пакетов из основного GitHub-репозитория панели.

## ADDED Requirements

### Requirement: Downloadable workflow artifacts
Каждый успешный запуск полной сборки SHALL сохранять два именованных набора бинарников в Actions artifacts репозитория `akaUNik/Amnezia-Web-Panel` на 30 дней. Пустой набор SHALL считаться ошибкой. Артефакты SHALL содержать сведения о commit SHA, версии, ОС и архитектуре.

#### Scenario: Successful main build
- **WHEN** полная сборка main завершается успешно
- **THEN** оператор может скачать два набора бинарников из соответствующего запуска Actions и установить их принадлежность коммиту

#### Scenario: Missing binary
- **WHEN** ожидаемый бинарник не создан
- **THEN** сохранение его набора и итоговая сборка завершаются ошибкой

### Requirement: Complete persistent releases
Для корректного версионного тега система SHALL публиковать в GitHub Releases основного репозитория два бинарника, `SHA256SUMS` и `build-info.json`. Комплект SHALL относиться к одному коммиту и сохраняться независимо от срока Actions artifacts. До проверки полноты релиз SHALL оставаться черновиком.

#### Scenario: Complete version release
- **WHEN** проверки и все обязательные сборки версионного тега успешны
- **THEN** релиз содержит Linux и Windows бинарники, checksums, версии/архитектуры и digest обоих контейнеров

#### Scenario: Partial upload failure
- **WHEN** загрузка одного файла не удалась или комплект смешивает разные коммиты
- **THEN** релиз не становится опубликованным

### Requirement: Repeatable release publication
Повтор публикации SHALL позволять завершить черновик того же тега и коммита. Опубликованный комплект SHALL не перезаписываться другим содержимым; конфликт SHA или checksums SHALL завершать повтор ошибкой.

#### Scenario: Retry after interruption
- **WHEN** повторён запуск с неполным черновиком того же коммита
- **THEN** публикация может дозагрузить недостающие файлы и завершиться после проверки полного комплекта

#### Scenario: Existing published release
- **WHEN** для тега уже опубликован совпадающий комплект
- **THEN** повтор подтверждает его целостность без замены файлов

### Requirement: Repository-associated container images
Контейнеры SHALL публиковаться в `ghcr.io/akaunik/amnezia-panel` с привязкой к `akaUNik/Amnezia-Web-Panel` и публичным скачиванием. Обычный вариант SHALL поддерживать amd64/arm64, WARP — amd64; WARP-теги SHALL иметь суффикс `-warp`.

#### Scenario: Public image download
- **WHEN** пользователь скачивает образ по опубликованному тегу без учётных данных
- **THEN** GHCR отдаёт соответствующий вариант для поддерживаемой архитектуры

### Requirement: Stable and prerelease distribution channels
Main SHALL обновлять `latest` и `latest-warp` только после успеха обоих вариантов. Стабильный тег SHALL дополнительно публиковать точную SemVer-версию и алиасы major/minor. Предварительный тег SHALL публиковать точную prerelease-версию и предварительный релиз без изменения стабильных алиасов и стабильной версии для обновлений.

#### Scenario: Prerelease build
- **WHEN** публикуется `v1.9.0-rc.1`
- **THEN** доступны предварительный релиз и точные контейнерные теги, но стабильные алиасы и источник стабильных обновлений не меняются

#### Scenario: One container variant fails
- **WHEN** обычный вариант собран, а WARP завершился ошибкой
- **THEN** прежние `latest` и `latest-warp` не изменяются

### Requirement: Installation uses own distribution
README и стандартные Compose-конфигурации SHALL использовать собственный GHCR-пакет и релизы основного репозитория. Переход SHALL сохранять тома состояния; локальная сборка WARP SHALL оставаться доступной отдельной документированной командой.

#### Scenario: Published WARP installation
- **WHEN** пользователь применяет документированный Compose-режим опубликованного WARP-образа
- **THEN** используется `ghcr.io/akaunik/amnezia-panel:latest-warp` с прежними томами и параметрами WARP

#### Scenario: Rollback
- **WHEN** оператор выбирает ранее известную версию или digest образа
- **THEN** он может запустить её с существующим томом данных без удаления состояния процедурой переключения образа
