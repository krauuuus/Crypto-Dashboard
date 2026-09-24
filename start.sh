#!/bin/bash
set -e

mkdir -p data/cache

# Pull pre-computed data from the data repo (public)
echo "Fetching data repo..."
git clone --depth=1 https://github.com/krauuuus/Crypto-Dashboard-Data.git /tmp/crypto-data 2>/dev/null || \
    git -C /tmp/crypto-data pull --ff-only 2>/dev/null || \
    echo "Warning: data repo unavailable, app will run with empty cache"

# Copy parquet files to local cache
if [ -d /tmp/crypto-data ]; then
    cp /tmp/crypto-data/crypto_stability/*.parquet data/cache/ 2>/dev/null || true
    cp /tmp/crypto-data/cbdc_speeches/*.parquet   data/cache/ 2>/dev/null || true
    echo "Data loaded."
fi

exec gunicorn app:server --bind "0.0.0.0:${PORT:-8050}" --workers 1 --timeout 120
