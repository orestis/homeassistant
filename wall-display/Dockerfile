ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python and pip
RUN apk add --no-cache python3 py3-pip

# Copy application
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app.py ha_client.py dashboard_config.json ./
COPY templates/ templates/
COPY static/ static/
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
