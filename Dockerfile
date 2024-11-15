FROM python:3

ARG WAND_MEMORY_LIMIT=512MiB

RUN apt-get update -y && \
  apt-get -y --no-install-recommends install vim libmagickwand-dev && \
  rm -rf /var/lib/apt/lists/*

RUN sed -i -e 's|\(resource.*memory.*value="\).*\(".*\)|\1'${WAND_MEMORY_LIMIT}'\2|g' /etc/ImageMagick-6/policy.xml

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV BIND_PORT=8080
EXPOSE ${BIND_PORT}/tcp

CMD [ "python3", "./slippy-tile-proxy-server.py" ]
