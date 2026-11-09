#!/usr/bin/env bash
set -e

AUDIT_ID="${1:?Usage: $0 <audit_id>}"

curl -X GET "http://127.0.0.1:8000/api/v1/audits/${AUDIT_ID}"
