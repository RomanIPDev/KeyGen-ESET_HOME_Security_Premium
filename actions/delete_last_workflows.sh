#!/bin/bash

org="$1"
repo="$2"

echo "Удаление всех workflow runs для $org/$repo (включая Dependabot)..."

# Удаляем стандартные workflow runs
echo "🔍 Поиск стандартных workflow..."
gh api "repos/$org/$repo/actions/workflows" | jq -r '.workflows[] | .name' | while read -r workflow_name; do
    echo "🗑️ Удаляем запуски для workflow: $workflow_name"
    gh run list --limit 500 --workflow "$workflow_name" --json databaseId \
        | jq -r '.[] | .databaseId' \
        | xargs -I{} gh run delete {} --confirm
done

# Удаляем Dependabot runs (через API, так как `gh run list` их не видит)
echo "🔍 Поиск запусков Dependabot..."
dependabot_runs=$(gh api "repos/$org/$repo/actions/runs?actor=dependabot[bot]" | jq -r '.workflow_runs[].id')

if [ -n "$dependabot_runs" ]; then
    echo "🗑️ Удаляем запуски Dependabot..."
    for run_id in $dependabot_runs; do
        echo "Удаление Dependabot run $run_id..."
        gh api -X DELETE "repos/$org/$repo/actions/runs/$run_id"
    done
else
    echo "✅ Нет запусков Dependabot для удаления."
fi

echo "✅ Готово. Все workflow runs (включая Dependabot) очищены."
