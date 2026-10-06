FROM alpine:3.24.2
RUN apk add --no-cache curl jq
COPY init.sh /usr/local/bin/garage-init
ENTRYPOINT ["garage-init"]
