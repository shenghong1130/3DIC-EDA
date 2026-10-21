# eda-elite-challenge2026

## Download ubuntu22.04 image
docker pull ubuntu:22.04

## Create image:
docker build -t ubuntu2204-python3.10:1.0.0 -f Dockerfile-python3 ./

## Start container:
docker run -it --name score -e LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/app/eda-elite-challenge2026/evaluator -e PYTHONPATH=/app/eda-elite-challenge2026/evaluator ubuntu2204-python3.10:1.0.0 /bin/bash

## Copy evaluator to container:
docker cp eda-elite-challenge2026.tar.gz score:/app

## Score (In container):
tar -zxf eda-elite-challenge2026.tar.gz

python ./eda-elite-challenge2026/evaluator/score.py ./eda-elite-challenge2026/cases/sample001/sample001.output.guide --lefs ./eda-elite-challenge2026/cases/sample001/sample001.input.lef --input-def ./eda-elite-challenge2026/cases/sample001/sample001.input.def --thermal-json ./eda-elite-challenge2026/cases/sample001/sample001.thermal.input.json --output-dir ./output/sample001