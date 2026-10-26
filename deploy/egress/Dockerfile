FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends iptables ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY firewall.sh /usr/local/bin/firewall.sh
RUN chmod 0755 /usr/local/bin/firewall.sh
CMD ["/usr/local/bin/firewall.sh"]
