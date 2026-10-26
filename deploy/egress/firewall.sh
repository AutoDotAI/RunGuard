#!/bin/sh
set -eu

# The worker shares this container's network namespace. Fail closed if the
# host does not grant NET_ADMIN or IPv6 filtering cannot be installed.
iptables -w -F OUTPUT
iptables -w -P OUTPUT DROP
iptables -w -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Only Docker's embedded DNS is reachable over loopback. No local web or
# metadata endpoint can be contacted through 127/8.
# Docker NAT may translate port 53 to an ephemeral local resolver port before
# this chain sees the packet; restrict by its fixed resolver address instead.
iptables -w -A OUTPUT -o lo -d 127.0.0.11 -j ACCEPT

# Deny every non-global IPv4 class even if DNS changes between validation
# and connection. Docker bridge, host, and cloud metadata addresses are here.
for cidr in \
    0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 \
    169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 \
    192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 \
    198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4; do
    iptables -w -A OUTPUT -d "$cidr" -j REJECT
done

# No direct DNS, SMTP, or arbitrary sockets. Docker's embedded DNS runs on
# 127.0.0.11 and is reached via loopback; the application pins validated A
# answers to the actual TCP socket. IPv6 is blocked until an equivalent
# reviewed IPv6 egress policy is provided.
iptables -w -A OUTPUT -p tcp -m multiport --dports 80,443 -j ACCEPT
ip6tables -w -F OUTPUT
ip6tables -w -P OUTPUT DROP
ip6tables -w -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

touch /run/runguard-egress-ready
exec tail -f /dev/null
