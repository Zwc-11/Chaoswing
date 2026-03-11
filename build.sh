#!/usr/bin/env bash
set -o errexit

pip install --upgrade pip
pip install -e .

python manage.py collectstatic --noinput
python manage.py migrate
