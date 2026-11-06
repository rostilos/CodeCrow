#!/bin/sh
set -eu
# Apply restrictions before accepting input, then permanently drop every capability.
iptables -P OUTPUT DROP
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Docker's embedded resolver uses translated ports within 127.0.0.11.
iptables -A OUTPUT -d 127.0.0.11 -p udp -j ACCEPT
iptables -A OUTPUT -d 127.0.0.11 -p tcp -j ACCEPT
iptables -A OUTPUT -d 127.0.0.1 -p tcp --dport 8090 -m owner --uid-owner 0 -j ACCEPT
for network in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4; do
    iptables -A OUTPUT -d "$network" -j REJECT
done
iptables -A OUTPUT -p tcp --dport 443 -j ACCEPT
# The worker has no IPv6 egress, including IPv4-mapped and link-local routes.
ip6tables -P OUTPUT DROP
ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
exec setpriv --reuid=1001 --regid=1001 --clear-groups --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs python3 /opt/audit/server.py
