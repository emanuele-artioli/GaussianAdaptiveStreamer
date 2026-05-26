if [ -z "$1" ]; then 
    echo "Usage: ./reset_interface.sh <interface_name>"
    exit 1
fi

sudo tc qdisc del dev $1 root 