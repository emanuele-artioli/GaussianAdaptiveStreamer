#!/usr/bin/env bash
if [ -z "$3" ]; then 
    echo "Usage: $0 <interface> <latency> <loss_rate (percentage)>"
    exit 1
fi

if [ "$1" = "lo" ]; then
    rtt=$(echo "scale=1; $2 / 2" | bc -q)
else
    rtt="$2"
fi

sudo tc qdisc del dev $1 root 
sudo tc qdisc add dev $1 root handle 1: htb default 1 r2q 20
sudo tc class add dev $1 parent 1: classid 1:1 htb rate 20mbit 
sudo tc filter add dev $1 protocol ip parent 1:0 prio 1 u32 match ip dst 0.0.0.0/0 flowid 1:1 
sudo tc qdisc add dev $1 parent 1:1 handle 10: netem delay $rtt"ms" loss $3"%"

while true
do
    i=1 
    while IFS=, read -r tput 
    do 
        sudo tc class change dev $1 parent 1: classid 1:1 htb rate "$tput"kbit 
        echo "$tput" 
        sleep 1 
        
        if [ "$i" -eq 150 ]; then 
            exit 
        fi 
        i=$((i+1)) 
    done < traces/lte_cascading.csv 
done