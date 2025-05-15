#!/bin/bash

org="$1"
repo="$2"

echo "Удаление всех workflow runs для $org/$repo (включая Dependabot)..."

# Функция для удаления стандартных workflow runs
delete_regular_workflows() {
    echo "🔍 Поиск стандартных workflow..."
    gh api "repos/$org/$repo/actions/workflows" | jq -r '.workflows[] | .name' | while read -r workflow_name; do
        echo "🗑️ Удаляем запуски для workflow: $workflow_name"
        # Получаем ID запусков и удаляем по одному (без флагов подтверждения)
        gh run list --limit 500 --workflow "$workflow_name" --json databaseId \
            | jq -r '.[] | .databaseId' \
            | while read -r run_id; do
                echo "Удаление run $run_id..."
                gh run delete "$run_id" <<< "y"  # Автоматический ответ 'y' на запрос подтверждения
            done
    done
}

# Функция для удаления Dependabot runs через API
delete_dependabot_runs() {
    echo "🔍 Поиск запусков Dependabot..."
    page=1
    while true; do
        runs=$(gh api "repos/$org/$repo/actions/runs?actor=dependabot[bot]&page=$page&per_page=100" | jq -r '.workflow_runs[].id')
        [ -z "$runs" ] && break
        
        echo "🗑️ Удаляем запуски Dependabot (страница $page)..."
        for run_id in $runs; do
            echo "Удаление Dependabot run $run_id..."
            gh api -X DELETE "repos/$org/$repo/actions/runs/$run_id" --silent
        done
        ((page++))
    done
}

# Основной процесс
delete_regular_workflows
delete_dependabot_runs

echo "✅ Готово. Все workflow runs (включая Dependabot) очищены."
