#!/usr/bin/env bash
#
# Deploys the factory assets in adf/ to an existing Azure Data Factory.
#
# Two ways to get these into ADF:
#   1. Git integration (preferred) - point the factory at this repo and the
#      assets appear in the authoring canvas. The folder layout here matches
#      what ADF expects.
#   2. This script - useful for a factory that is not Git-linked, or from CI.
#
# The JSON files are in ADF's Git format ({ "name": ..., "properties": ... }),
# while the az CLI wants just the inner object, so each file is unwrapped with
# jq before it is sent.
#
# Requires: az CLI >= 2.50 with the datafactory extension, and jq
#   az extension add --name datafactory
#
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-data-platform}"
FACTORY_NAME="${FACTORY_NAME:-adf-oracle-snowflake}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR_LOCAL="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

unwrap() {
    local src="$1" dest="$TMPDIR_LOCAL/$(basename "$1")"
    jq '.properties' "$src" > "$dest"
    echo "$dest"
}

deploy_linked_services() {
    log "Linked services"
    for file in "$ROOT"/adf/linkedService/*.json; do
        local name; name="$(basename "$file" .json)"
        echo "    $name"
        az datafactory linked-service create \
            --resource-group "$RESOURCE_GROUP" --factory-name "$FACTORY_NAME" \
            --linked-service-name "$name" --properties "@$(unwrap "$file")" --output none
    done
}

deploy_datasets() {
    log "Datasets"
    for file in "$ROOT"/adf/dataset/*.json; do
        local name; name="$(basename "$file" .json)"
        echo "    $name"
        az datafactory dataset create \
            --resource-group "$RESOURCE_GROUP" --factory-name "$FACTORY_NAME" \
            --dataset-name "$name" --properties "@$(unwrap "$file")" --output none
    done
}

deploy_pipelines() {
    # Children before the master: ADF validates pipeline references on create.
    log "Pipelines"
    for name in PL_CREATE_SNOWFLAKE_TABLE PL_COPY_TABLE_DATA PL_DISCOVER_ORACLE_SCHEMA PL_MASTER_ORACLE_TO_SNOWFLAKE; do
        echo "    $name"
        az datafactory pipeline create \
            --resource-group "$RESOURCE_GROUP" --factory-name "$FACTORY_NAME" \
            --name "$name" --pipeline "@$(unwrap "$ROOT/adf/pipeline/$name.json")" --output none
    done
}

deploy_triggers() {
    log "Triggers"
    for file in "$ROOT"/adf/trigger/*.json; do
        local name; name="$(basename "$file" .json)"
        echo "    $name"
        az datafactory trigger create \
            --resource-group "$RESOURCE_GROUP" --factory-name "$FACTORY_NAME" \
            --name "$name" --properties "@$(unwrap "$file")" --output none
    done
}

deploy_linked_services
deploy_datasets
deploy_pipelines
deploy_triggers

log "Done. The trigger is deployed stopped; start it when you are ready:"
echo "    az datafactory trigger start -g $RESOURCE_GROUP --factory-name $FACTORY_NAME -n TR_Nightly_Oracle_To_Snowflake"
