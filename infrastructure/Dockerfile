FROM public.ecr.aws/lambda/python:3.12

RUN dnf install -y git && dnf clean all

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements.txt

COPY config ${LAMBDA_TASK_ROOT}/config
COPY src ${LAMBDA_TASK_ROOT}/src
COPY scripts ${LAMBDA_TASK_ROOT}/scripts
COPY lambda_handlers ${LAMBDA_TASK_ROOT}/lambda_handlers
RUN mkdir -p ${LAMBDA_TASK_ROOT}/data
COPY data/city_coords.json ${LAMBDA_TASK_ROOT}/data/city_coords.json

CMD ["lambda_handlers.fetch_daily.handler"]
