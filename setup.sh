#!/bin/zsh
rm -rf my_env
python3 -m venv my_env
source my_env/bin/activate
pip3 install --upgrade pip
pip3 install -r requirements.txt
