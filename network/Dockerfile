FROM alpine:3.21
RUN apk add --no-cache iptables jq
COPY restore-isolation.sh /app/restore-isolation.sh
RUN chmod +x /app/restore-isolation.sh
