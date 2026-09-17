FROM hashicorp/terraform:1.13.5 AS terraform
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssh-client sshpass \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --system --home-dir /var/lib/cloudportal-backed --shell /usr/sbin/nologin cloudportal
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
WORKDIR /opt/cloudportal-backed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt 'ansible>=10,<13'
COPY app app
COPY migrations migrations
COPY alembic.ini .
COPY terraform terraform
COPY ansible ansible
RUN mkdir -p /etc/cloudportal-backed /var/lib/cloudportal-backed \
    && chown 10001:10001 /etc/cloudportal-backed /var/lib/cloudportal-backed \
    && chmod 0700 /etc/cloudportal-backed /var/lib/cloudportal-backed
USER 10001:10001
EXPOSE 8765
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "*"]
